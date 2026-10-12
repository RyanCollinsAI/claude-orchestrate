#!/usr/bin/env python
"""scorecard.py - the one table that says whether the orchestrator seat is doing a good job.

Measure first, judge second. Every number here is counted from a file that already exists:
`board/state.json`, the herdr session registry, the `*.jsonl` transcripts under
`~/.claude/projects/*/`, and `git log` across `digest_repos`. Nothing new is instrumented, so the
scorecard can be run against any past week without having planned for it.

    py scorecard.py [--days 7] [--md] [--json] [--snapshot]

`--snapshot` appends the run to `board/scorecard-history.json`; once two runs of the same window
length exist, every judgement line grows a week-over-week delta. `board.py digest` calls
`scorecard_block()` for a short version of the same numbers.

What each number means, and what a bad one looks like: `references/scorecard.md`.
"""

import argparse
import datetime
import glob
import json
import os
import re
import statistics
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import orchlib as L                                                    # noqa: E402

HISTORY = os.path.join(L.BOARD_DIR, "scorecard-history.json")
STATE = os.path.join(L.BOARD_DIR, "state.json")
PROJECTS = L.PROJECTS_DIR

# A gap between two transcript entries no longer than this means the pane was mid-turn: tool call,
# model latency, a subagent finishing. Anything longer is the pane sitting still.
GAP_WORK = 300
# One gap is capped here so a pane parked over a night does not swamp the waiting column with a
# single 10-hour number. Measured in seconds.
GAP_CAP = 8 * 3600

TS_RE = re.compile(r'"timestamp":\s*"([^"]+)"')
VERB_RE = re.compile(r'(board|orch)\.py"?\s+([a-z][a-z0-9-]*)')
QID_RE = re.compile(r'\bq(\d+)\b', re.I)
ANSWER_RE = re.compile(r'board\.py"?\s+answer\s+(q\d+)\s+("([^"]*)"|\'([^\']*)\'|(\S+))', re.I)
ADDQ_RE = re.compile(r'board\.py"?\s+add-question\b([^\n]*)', re.I)
TARGET_RE = re.compile(r'orch\.py"?\s+(kill|spawn|task)\s+("[^"]+"|\S+)', re.I)
# A real pane label. Rules out the flag that follows a bare verb (`orch.py spawn --help`) and the
# `<label>` placeholder that appears in every usage line the seat pasted into a brief.
LABEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{1,63}$")

# An approval the seat granted on the human's behalf. Deliberately narrow: these are the phrasings
# the seat actually uses when it tells a lead to go, not every sentence containing "yes". Matched
# only in APPROVE_HEAD characters, because an approval leads with its verdict - the same words
# halfway down a long brief are usually describing something else.
APPROVE_MARKS = ("approved", "approve it", "go ahead", "green light", "ship it", "ship them",
                 "you may", "proceed", "yes -", "yes,", "that is a yes", "call stands",
                 "calls stand", "do it", "merge it", "confirmed - go")
APPROVE_HEAD = 400

# A later message that takes an approval back. Every phrase here can only mean undo. A bare "stop"
# is deliberately NOT in this list: measured 2026-09-09, "then stop", "stop only if" and "right to
# stop" are ordinary scope instructions and they made 34 of 35 matches false. "stop" counts only as
# the first word of a message, which is how a real recall actually reads ("STOP now.").
REVERT_MARKS = ("undo", "revert that", "revert it", "roll back", "rolled back", "back that out",
                "disregard", "scratch that", "ignore my last", "ignore that last", "cancel that",
                "stand down", "belay", "do not proceed", "hold off", "that was wrong",
                "i was wrong", "wrong call", "unmerge", "put it back", "do not ship it",
                "should not have approved")
REVERT_LEAD = re.compile(r"^\W{0,4}stop\b", re.I)
# The pane's own last words admitting a second pass.
REWORK_MARKS = ("again", "re-run", "rerun", "re-ran", "redo", "redid", "fixed my", "my mistake",
                "second pass", "had to back out", "reverted my")
# A question that was closed without a real decision.
WITHDRAW_MARKS = ("withdraw", "superseded", "duplicate", "obsolete", "moot", "no longer",
                  "not needed", "stale")


# ---------------------------------------------------------------- small helpers

def parse_ts(s):
    """ISO string (Z or offset) -> aware datetime, or None. Never raises."""
    if not s:
        return None
    try:
        return datetime.datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except Exception:
        return None


def pct(n, d):
    return 0.0 if not d else round(100.0 * n / d, 1)


def hours(sec):
    return round(sec / 3600.0, 1)


def median(xs):
    return round(statistics.median(xs), 1) if xs else None


def p90(xs):
    if not xs:
        return None
    xs = sorted(xs)
    i = min(len(xs) - 1, int(round(0.9 * (len(xs) - 1))))
    return round(xs[i], 1)


def is_orchestrator(name):
    return bool(name) and name.lower().startswith("orchestrator")


def strip_quotes(s):
    s = (s or "").strip()
    return s[1:-1] if len(s) > 1 and s[0] == s[-1] and s[0] in "\"'" else s


def label_root(name):
    """`app-lead-5-3-2` -> `app-lead-5-3`. One trailing `-<digits>` is the respawn counter `orch.py`
    adds when a label is taken, so two names with the same root are the same seat spawned twice."""
    return re.sub(r"-\d+$", "", name or "")


# ---------------------------------------------------------------- the log scan

def log_files(dirs, since_dt):
    """Every transcript that could hold an entry in the window, newest last.

    Filtered on mtime, which is safe in one direction only: a file last written before the window
    opened cannot contain an entry inside it."""
    cut = since_dt.timestamp()
    out = []
    for d in dirs:
        for pat in ("*.jsonl", os.path.join("*", "*.jsonl")):
            for p in glob.glob(os.path.join(d, pat)):
                try:
                    if os.path.getmtime(p) >= cut:
                        out.append(p)
                except OSError:
                    continue
    return sorted(set(out))


def scan_file(path, since_dt, until_dt):
    """One streaming pass over one transcript.

    Returns a session dict, or None when nothing in the file falls inside the window. Only lines
    that are already known to matter are handed to `json.loads`; the rest are matched with
    substrings and one regex, which is what keeps a gigabyte of transcript to a few seconds."""
    sess = {"path": path, "name": "", "model": "", "ctx_k": 0, "last_text": "",
            "first": None, "last": None, "work_s": 0.0, "wait_s": 0.0, "waits": 0,
            "cmds": [], "messages": [], "entries": 0}
    prev = None
    last_usage_line = last_text_line = None
    try:
        fh = open(path, encoding="utf-8", errors="replace")
    except OSError:
        return None
    with fh:
        for line in fh:
            if has_type(line, "agent-name") or has_type(line, "custom-title"):
                e = _loads(line)
                if e:
                    sess["name"] = e.get("agentName") or e.get("customTitle") or sess["name"]
                continue

            m = TS_RE.search(line)
            if not m:
                continue
            ts = parse_ts(m.group(1))
            if not ts or ts < since_dt or ts > until_dt:
                continue

            is_asst = has_type(line, "assistant")
            is_user = has_type(line, "user")
            if is_asst or is_user:
                sess["entries"] += 1
                sess["first"] = sess["first"] or ts
                sess["last"] = ts
                if prev is not None:
                    gap = (ts - prev).total_seconds()
                    if gap <= GAP_WORK:
                        sess["work_s"] += gap
                    else:
                        sess["wait_s"] += min(gap, GAP_CAP)
                        sess["waits"] += 1
                prev = ts
                if is_asst:
                    if '"usage"' in line:
                        last_usage_line = line
                    if '"text"' in line:
                        last_text_line = line

            if '"tool_use"' not in line:
                continue
            if not ("board.py" in line or "orch.py" in line or '"SendMessage"' in line):
                continue
            e = _loads(line)
            if not e or e.get("type") != "assistant":
                continue
            for c in (e.get("message") or {}).get("content") or []:
                if not (isinstance(c, dict) and c.get("type") == "tool_use"):
                    continue
                inp = c.get("input") or {}
                if c.get("name") == "SendMessage":
                    sess["messages"].append({"ts": ts, "to": str(inp.get("to") or ""),
                                             "text": str(inp.get("message") or "")})
                    continue
                cmd = inp.get("command") or ""
                if "board.py" in cmd or "orch.py" in cmd:
                    sess["cmds"].append({"ts": ts, "cmd": cmd})

    if sess["last"] is None:
        return None
    if last_usage_line:
        e = _loads(last_usage_line) or {}
        u = ((e.get("message") or {}).get("usage")) or {}
        sess["ctx_k"] = (u.get("input_tokens", 0) + u.get("cache_read_input_tokens", 0)
                         + u.get("cache_creation_input_tokens", 0)) // 1000
        sess["model"] = ((e.get("message") or {}).get("model") or "").replace("claude-", "")
    if last_text_line:
        e = _loads(last_text_line) or {}
        c = ((e.get("message") or {}).get("content")) or []
        if isinstance(c, list):
            sess["last_text"] = " ".join(
                b.get("text", "") for b in c
                if isinstance(b, dict) and b.get("type") == "text").strip()
        elif isinstance(c, str):
            sess["last_text"] = c.strip()
    if not sess["name"]:
        # No `agent-name` entry: a session nobody labelled (a bare `claude` window, a one-shot
        # skill run). Kept, because it still spent pane time, but flagged so the rework list never
        # shows a hex blob where a label should be.
        sess["name"] = "sid:" + os.path.basename(path)[:8]
    return sess


def _loads(line):
    try:
        return json.loads(line)
    except Exception:
        return None


def has_type(line, kind):
    """`"type":"assistant"` in a real transcript, `"type": "assistant"` from anything that wrote it
    with `json.dumps` defaults. Two substring tests stay fast enough to run on every line of a
    gigabyte; a regex here does not."""
    return ('"type":"%s"' % kind) in line or ('"type": "%s"' % kind) in line


def scan(dirs, since_dt, until_dt):
    return [s for s in (scan_file(p, since_dt, until_dt)
                        for p in log_files(dirs, since_dt)) if s]


# ---------------------------------------------------------------- questions

def question_rows(sessions, state, since_dt, until_dt):
    """One row per question id seen in the window, merged from three sources.

    `board.py prune` drops answered questions after a day, so `state.json` alone cannot answer a
    seven-day question. The transcripts hold every `add-question` and `answer` the seat typed, and
    `done[]` holds the answer line for anything answered in the last day. A question the watcher
    posted by itself (`add_question_dict`, no shell command) has no `add-question` row, so its
    `asked` time is unknown and it lands in the "wait unknown" bucket rather than skewing a median.
    """
    q = {}

    def row(qid):
        return q.setdefault(qid.lower(), {"id": qid.lower(), "asked": None, "answered": None,
                                          "answer": "", "auto": False, "auto_flag": False,
                                          "open": False, "source": ""})

    for s in sessions:
        for c in s["cmds"]:
            cmd = c["cmd"]
            for m in ADDQ_RE.finditer(cmd):
                tail = m.group(1)
                idm = re.search(r"--id\s+(q\d+)", tail, re.I)
                r = row(idm.group(1) if idm else "q?" + c["ts"].isoformat())
                r["asked"] = r["asked"] or c["ts"]
                r["auto_flag"] = r["auto_flag"] or bool(re.search(r"--auto\b", tail))
                r["source"] = r["source"] or "cmd"
            for m in ANSWER_RE.finditer(cmd):
                r = row(m.group(1))
                r["answered"] = r["answered"] or c["ts"]
                r["answer"] = r["answer"] or strip_quotes(m.group(2))

    for line in state.get("done", []):
        ts = parse_ts(line.get("ts"))
        if not ts or not (since_dt <= ts <= until_dt):
            continue
        text = line.get("text") or ""
        if text.lower().startswith("auto-picked "):
            m = QID_RE.search(text)
            if m:
                r = row("q" + m.group(1))
                r["answered"] = r["answered"] or ts
                r["auto"] = True
                r["answer"] = r["answer"] or text.split(":", 1)[-1].strip()
            continue
        m = re.match(r"^[Qq](\d+)\b", text)
        if m:
            r = row("q" + m.group(1))
            r["answered"] = r["answered"] or ts
            r["answer"] = r["answer"] or text.rsplit(": ", 1)[-1].strip()

    for qq in state.get("questions", []):
        qid = (qq.get("id") or "").lower()
        if not qid:
            continue
        r = row(qid)
        r["asked"] = r["asked"] or parse_ts(qq.get("created"))
        r["auto_flag"] = r["auto_flag"] or bool(qq.get("auto"))
        if qq.get("answered"):
            r["answered"] = r["answered"] or parse_ts(qq.get("answered"))
            r["answer"] = r["answer"] or (qq.get("answer") or "")
            r["auto"] = r["auto"] or qq.get("answered_by") == "auto"
        else:
            r["open"] = True

    out = []
    for r in q.values():
        if r["asked"] and not (since_dt <= r["asked"] <= until_dt):
            continue
        if not r["asked"] and not (r["answered"] and since_dt <= r["answered"] <= until_dt):
            continue
        out.append(r)
    return sorted(out, key=lambda r: r["asked"] or r["answered"])


def question_metrics(rows, until_dt):
    waits = [(r["answered"] - r["asked"]).total_seconds() / 3600.0
             for r in rows if r["asked"] and r["answered"]]
    answered = [r for r in rows if r["answered"]]
    auto = [r for r in answered if r["auto"]]
    withdrawn = [r for r in answered
                 if any(k in (r["answer"] or "").lower() for k in WITHDRAW_MARKS)]
    open_rows = [r for r in rows if r["open"]]
    oldest = min((r for r in open_rows if r["asked"]), key=lambda r: r["asked"], default=None)
    return {
        "asked": len(rows),
        "answered_by_human": len(answered) - len(auto),
        "auto_picked": len(auto),
        "withdrawn": len(withdrawn),
        "still_open": len(open_rows),
        "wait_median_h": median(waits),
        "wait_p90_h": p90(waits),
        "wait_unknown": len(answered) - len(waits),
        "oldest_open_id": oldest["id"] if oldest else "",
        "oldest_open_h": round((until_dt - oldest["asked"]).total_seconds() / 3600.0, 1)
                         if oldest else None,
    }


# ---------------------------------------------------------------- decisions

def decision_metrics(sessions, since_dt, until_dt):
    """Approvals the seat sent to panes, and how many it took back inside a day.

    Counted from `SendMessage` calls in any orchestrator-named transcript, because that is the only
    place the seat's own calls exist - the board records what the human decided, never what the
    seat decided for them."""
    msgs = []
    for s in sessions:
        if not is_orchestrator(s["name"]):
            continue
        for m in s["messages"]:
            if since_dt <= m["ts"] <= until_dt:
                msgs.append(m)
    msgs.sort(key=lambda m: m["ts"])

    approvals, reverts = [], []
    for i, m in enumerate(msgs):
        if not any(k in m["text"].lower()[:APPROVE_HEAD] for k in APPROVE_MARKS):
            continue
        approvals.append(m)
        who = m["to"].split("[")[0].strip()
        for n in msgs[i + 1:]:
            if (n["ts"] - m["ts"]).total_seconds() > 86400:
                break
            if n["to"].split("[")[0].strip() != who:
                continue
            low = n["text"].lower()[:800]
            if any(k in low for k in REVERT_MARKS) or REVERT_LEAD.match(n["text"].strip()):
                reverts.append((m, n))
                break
    return {
        "messages_sent": len(msgs),
        "approvals": len(approvals),
        "reverted_24h": len(reverts),
        "revert_pct": pct(len(reverts), len(approvals)),
        # Printed under the table so the rate is auditable: a word-matched revert is a suspect,
        # not a verdict, and reading the pair is the only way to be sure.
        "_revert_pairs": [(m["to"].split("[")[0].strip(),
                           n["ts"].strftime("%m-%d %H:%M"),
                           " ".join(n["text"].split())[:90]) for m, n in reverts[:6]],
    }


# ---------------------------------------------------------------- panes

def pane_metrics(sessions, since_dt, until_dt):
    verbs = {"spawn": 0, "task": 0, "kill": 0}
    targets = {"kill": [], "spawn": []}
    for s in sessions:
        for c in s["cmds"]:
            if not (since_dt <= c["ts"] <= until_dt):
                continue
            for _, verb in VERB_RE.findall(c["cmd"]):
                if verb in verbs:
                    verbs[verb] += 1
            for verb, target in TARGET_RE.findall(c["cmd"]):
                verb = "spawn" if verb.lower() == "task" else verb.lower()
                target = strip_quotes(target)
                if verb in targets and LABEL_RE.match(target):
                    targets[verb].append(target)

    work = sum(s["work_s"] for s in sessions)
    wait = sum(s["wait_s"] for s in sessions)
    return {
        "spawned": verbs["spawn"] + verbs["task"],
        "killed": verbs["kill"],
        "panes_seen": len(sessions),
        "work_h": hours(work),
        "wait_h": hours(wait),
        "wait_pct": pct(wait, work + wait),
        "_targets": targets,
    }


def rework_metrics(sessions, targets):
    """Two independent signals, reported separately because they catch different failures.

    `done_rework` is a pane admitting a second pass in its own last words. `respawned` is the same
    label handed out twice inside a day, which is the seat re-doing the pane rather than the pane
    re-doing the work."""
    named = [s for s in sessions if s["last_text"]]
    done_rework = [s["name"] for s in named
                   if any(k in s["last_text"].lower()[-600:] for k in REWORK_MARKS)]
    labelled = [n for n in done_rework if not n.startswith("sid:")]

    by_root = {}
    for name in targets.get("spawn", []):
        by_root.setdefault(label_root(name), []).append(name)
    respawned = {k: v for k, v in by_root.items() if len(v) > 1}
    return {
        "done_rework": len(done_rework),
        "done_rework_pct": pct(len(done_rework), len(named)),
        "respawned_labels": len(respawned),
        "_rework_names": sorted(set(labelled))[:12],
        "_respawned": sorted(respawned)[:12],
    }


def token_metrics(sessions, targets, orchestrator_names):
    """Context carried by every pane the seat retired.

    A pane's context at kill is the closest thing to what that pane cost: it is the window that
    had to be re-read on every turn until the seat retired it."""
    by_name = {}
    for s in sessions:
        by_name.setdefault(s["name"], []).append(s)
    retired = targets["kill"]
    total, matched = 0, 0
    for name in retired:
        for s in by_name.get(name, []):
            total += s["ctx_k"]
            matched += 1
            break
    return {
        "retire_events": len(retired),
        "retire_ctx_k": total,
        "retire_matched": matched,
        "peak_ctx_k": max((s["ctx_k"] for s in sessions), default=0),
        "orchestrator_seats": len(orchestrator_names),
    }


# ---------------------------------------------------------------- commits

def is_repo(path):
    """`.git` is a FILE, not a directory, in any repo whose git dir was separated - the vault is
    one (`C:/Users/YOURNAME/vault-repos/vault-notes.git`). An `isdir` test silently drops it, which is
    how the digest reported zero vault commits for weeks. Ask git instead."""
    if not os.path.isdir(path):
        return False
    try:
        r = subprocess.run(["git", "-C", path, "rev-parse", "--git-dir"],
                           capture_output=True, text=True, timeout=20)
        return r.returncode == 0
    except Exception:
        return False


def commits(repos, since_dt, until_dt):
    out = {}
    for repo in repos:
        if not is_repo(repo):
            continue
        try:
            r = subprocess.run(
                ["git", "-C", repo, "log", "--all", "--no-merges",
                 "--since", since_dt.isoformat(), "--until", until_dt.isoformat(),
                 "--pretty=%h"],
                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)
        except Exception:
            continue
        n = len([l for l in (r.stdout or "").splitlines() if l.strip()])
        if n:
            out[os.path.basename(repo.rstrip("\\/")) or repo] = n
    return out


# ---------------------------------------------------------------- build + judge

def build(days=7, dirs=None, state_path=STATE, repos=None, now=None, projects=None):
    now = now or datetime.datetime.now().astimezone()
    since = now - datetime.timedelta(days=days)
    dirs = dirs if dirs is not None else _default_dirs(projects or PROJECTS)
    repos = repos if repos is not None else L.cfg("digest_repos", [])
    try:
        state = json.load(open(state_path, encoding="utf-8"))
    except Exception:
        state = {"questions": [], "done": []}

    sessions = scan(dirs, since, now)
    qrows = question_rows(sessions, state, since, now)
    panes = pane_metrics(sessions, since, now)
    targets = panes.pop("_targets")
    orch_names = sorted({s["name"] for s in sessions if is_orchestrator(s["name"])})

    m = {
        "window_days": days,
        "window_start": since.isoformat(),
        "window_end": now.isoformat(),
        "questions": question_metrics(qrows, now),
        "decisions": decision_metrics(sessions, since, now),
        "panes": panes,
        "rework": rework_metrics(sessions, targets),
        "tokens": token_metrics(sessions, targets, orch_names),
        "commits": commits(repos, since, now),
    }
    m["commits_total"] = sum(m["commits"].values())
    return m


def _default_dirs(projects):
    dirs = [projects]
    dirs += [p for p in glob.glob(os.path.join(projects, "*")) if os.path.isdir(p)]
    return dirs


WATCH = [
    ("p90 question wait (h)", lambda m: m["questions"]["wait_p90_h"], "lower",
     "how long the slowest question left Ryan blocked"),
    ("revert rate (%)", lambda m: m["decisions"]["revert_pct"], "lower",
     "approvals the seat gave itself that it then took back"),
    ("pane time waiting (%)", lambda m: m["panes"]["wait_pct"], "lower",
     "share of pane-hours spent sitting still"),
]


def judge(m, prev=None):
    """The three numbers to watch, each with its week-over-week delta once a prior run exists."""
    out = []
    for label, get, want, why in WATCH:
        cur = get(m)
        delta = ""
        if prev:
            try:
                old = get(prev)
            except Exception:
                old = None
            if old is not None and cur is not None:
                d = round(cur - old, 1)
                arrow = "flat" if d == 0 else ("worse" if (d > 0) == (want == "lower") else "better")
                delta = f"  ({d:+} vs last week, {arrow})"
        out.append((label, "n/a" if cur is None else cur, why, delta))
    return out


# ---------------------------------------------------------------- history

def history_load(path=HISTORY):
    try:
        return json.load(open(path, encoding="utf-8")).get("runs", [])
    except Exception:
        return []


def history_prev(runs, days, now=None, min_gap_frac=0.5):
    """The newest earlier run worth comparing against.

    Same window length, and far enough back to mean something: two runs of the same week taken an
    hour apart cover almost the same days, so their delta is near zero and reads as "flat" when
    nothing was actually measured. A candidate must end at least half a window before this one."""
    same = [r for r in runs if r.get("window_days") == days]
    if not same:
        return None
    end = parse_ts(now) if isinstance(now, str) else now
    if end is None:
        end = parse_ts(same[-1].get("window_end")) if len(same) == 1 else None
        end = end or datetime.datetime.now().astimezone()
    gap = datetime.timedelta(days=days * min_gap_frac)
    older = [r for r in same if (parse_ts(r.get("window_end")) or end) <= end - gap]
    return older[-1] if older else None


def history_append(m, path=HISTORY):
    runs = history_load(path)
    runs.append(m)
    runs = runs[-52:]
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump({"runs": runs}, fh, indent=2, default=str)
    os.replace(tmp, path)
    return path


# ---------------------------------------------------------------- render

def _rows(m):
    q, d, p, r, t = m["questions"], m["decisions"], m["panes"], m["rework"], m["tokens"]
    return [
        ("Questions", "asked", q["asked"]),
        ("Questions", "answered by Ryan", q["answered_by_human"]),
        ("Questions", "auto-picked", q["auto_picked"]),
        ("Questions", "withdrawn / duplicate", q["withdrawn"]),
        ("Questions", "still open", q["still_open"]),
        ("Questions", "wait median (h)", q["wait_median_h"]),
        ("Questions", "wait p90 (h)", q["wait_p90_h"]),
        ("Questions", "wait not computable", q["wait_unknown"]),
        ("Questions", "oldest open",
         f"{q['oldest_open_id'] or '-'} ({q['oldest_open_h']}h)" if q["oldest_open_id"] else "-"),
        ("Decisions", "messages the seat sent", d["messages_sent"]),
        ("Decisions", "approvals it gave itself", d["approvals"]),
        ("Decisions", "reverted within 24h", d["reverted_24h"]),
        ("Decisions", "revert rate (%)", d["revert_pct"]),
        ("Panes", "spawned", p["spawned"]),
        ("Panes", "killed", p["killed"]),
        ("Panes", "transcripts in window", p["panes_seen"]),
        ("Pane time", "working (h)", p["work_h"]),
        ("Pane time", "waiting (h)", p["wait_h"]),
        ("Pane time", "waiting (%)", p["wait_pct"]),
        ("Rework", "panes whose last words admit a redo", r["done_rework"]),
        ("Rework", "redo rate (%)", r["done_rework_pct"]),
        ("Rework", "labels spawned more than once", r["respawned_labels"]),
        ("Shipped", "commits in window", m["commits_total"]),
        ("Shipped", "repos touched", len(m["commits"])),
        ("Tokens", "panes retired (kill)", t["retire_events"]),
        ("Tokens", "context carried at retire (k)", t["retire_ctx_k"]),
        ("Tokens", "peak pane context (k)", t["peak_ctx_k"]),
        ("Tokens", "orchestrator seats in window", t["orchestrator_seats"]),
    ]


def render(m, md=False, prev=None):
    rows = _rows(m)
    start = parse_ts(m["window_start"])
    end = parse_ts(m["window_end"])
    head = (f"Orchestrator scorecard - {m['window_days']} days, "
            f"{start.strftime('%b %d %H:%M')} to {end.strftime('%b %d %H:%M')}")
    out = []
    if md:
        out += [f"### {head}", "", "| section | measure | value |", "|---|---|---|"]
        seen = set()
        for sec, k, v in rows:
            out.append(f"| {'' if sec in seen else sec} | {k} | {'-' if v is None else v} |")
            seen.add(sec)
        out += ["", "**Watch these three:**", ""]
        for label, val, why, delta in judge(m, prev):
            out.append(f"- **{label}: {val}**{delta} - {why}")
        if m["commits"]:
            out += ["", "Commits: " + ", ".join(f"{k} {v}" for k, v in sorted(m["commits"].items()))]
    else:
        w1 = max(len(r[0]) for r in rows)
        w2 = max(len(r[1]) for r in rows)
        out += [head, "=" * len(head)]
        seen = set()
        for sec, k, v in rows:
            lead = "" if sec in seen else sec
            seen.add(sec)
            out.append(f"{lead:<{w1}}  {k:<{w2}}  {'-' if v is None else v}")
        out += ["", "Watch these three:"]
        for label, val, why, delta in judge(m, prev):
            out.append(f"  {label}: {val}{delta}")
            out.append(f"      {why}")
        if m["commits"]:
            out += ["", "Commits: " + ", ".join(f"{k} {v}" for k, v in sorted(m["commits"].items()))]
        if m["rework"]["_rework_names"]:
            out += ["", "Redo suspects: " + ", ".join(m["rework"]["_rework_names"])]
        if m["rework"]["_respawned"]:
            out += ["Labels spawned twice: " + ", ".join(m["rework"]["_respawned"])]
        if m["decisions"]["_revert_pairs"]:
            out += ["", "Reverted approvals (read these before believing the rate):"]
            out += [f"  {who} {when}  {text}" for who, when, text in m["decisions"]["_revert_pairs"]]
    out += ["", "The watcher keeps no agent-state history (ctx_hist is in memory and trimmed to an "
            "hour), so pane time is computed from transcript gaps: <=5 min between entries counts "
            "as working, longer counts as waiting, one gap capped at 8h."]
    return "\n".join(out)


def scorecard_block(days=7, **kw):
    """The markdown `board.py digest` embeds. Never raises: a digest must still post."""
    try:
        m = build(days=days, **kw)
        return render(m, md=True,
                      prev=history_prev(history_load(), days, now=m["window_end"]))
    except Exception as e:                                   # noqa: BLE001 - a digest outranks this
        return f"### Scorecard\n\n- scorecard unavailable: {e}"


# ---------------------------------------------------------------- cli

def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--md", action="store_true", help="markdown table instead of plain text")
    ap.add_argument("--json", action="store_true", help="raw metrics dict")
    ap.add_argument("--snapshot", action="store_true",
                    help="append this run to scorecard-history.json for next week's delta")
    a = ap.parse_args(argv)

    m = build(days=a.days)
    prev = history_prev(history_load(), a.days, now=m["window_end"])
    if a.json:
        print(json.dumps(m, indent=2, default=str))
    else:
        print(render(m, md=a.md, prev=prev))
    if a.snapshot:
        print("\nsnapshot -> " + history_append(m))
    return 0


if __name__ == "__main__":
    sys.exit(main())
