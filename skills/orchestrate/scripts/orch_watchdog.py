r"""Fleet watchdog: notice a tasked pane that died, errored, stalled or got stuck - without an LLM.

  py orch_watchdog.py --once --log FILE    # one pass; the scheduled routine runs this every 3 min
  py orch_watchdog.py --once --dry-run     # one pass, print what it would flag
  py orch_watchdog.py                      # a resident loop, if ever wanted (dies with its session)

It LIVES as a Windows scheduled task (routines\orch-watchdog, see SCHEDULERS.md), outside every
session, so an orchestrator restart cannot take it down. `orch.py kill` calls `mark_killed` so a
deliberate close is never a VANISH.

Born 2026-10-02: ci-upgrade vanished mid-task and sc-mate sat in ERROR with 4 tasks
unreported, and nobody noticed until a manual status check.

It keeps its OWN registry of tasked panes (board/watchdog-state.json), because the real ones forget:
`owners.json` prunes a session the moment it is gone, which is exactly when this needs the row.
Each pass it snapshots every tasked pane, then flags four things:

  VANISHED   a tasked pane that was live last pass is gone, and it never reported or finished clean
  ERROR      a tasked pane stopped on an ERROR, or its model reads `<synthetic>` with ctx 0k AND
             its herdr pane is gone (right after a restart the pane is there: not an error)
  STALLED    idle more than 10 min after its task, and no SendMessage / report file reached its owner
  PROMPT     parked on a permission prompt on two passes in a row

ERROR and STALLED must hold two passes in a row, and a pane with a background task running is
neither (it is waiting on that task).
Each flag goes to the pane's OWNER (`orch.py prompt`, stamped `[from watchdog]`), to the Bridge
Updates feed, and (VANISHED/ERROR/STALLED/PROMPT) to `unstick-mate`, which acts on it (see
references/unstick-mate.md). One notice per pane per condition: the same flag is not told again
until the pane works or a new condition shows. A word-matched ERROR on a pane that reported is
not a flag.

THE LOOP IT MUST NOT START (memory grok-orchestrator-seat-2026-09-21): a seat woken about its own
pane answers, the answer is classified DONE, and that wakes it again. So the seat itself is never
a subject, and a pane is never told about itself.

A pane under a declared wait (`paused: <why> until <UTC>` in its own reply, or `hold --until`) is
not STALLED until the time passes or the wait is 4 h old (declared_wait.py).

Wedge alarm (run_wedge, below): while board/afk.json says "afk": true, an open board question
nobody answered or acknowledged in N minutes sends ONE alert through phone_alerts.py. Quiet channel
unless afk.json has "loud": true. Shape of afk.json: board/schema.md.

Off switch: create `board/watchdog.off`. Optional auto-respawn of a VANISHED claude task from its
saved task file: `--respawn` (off by default; one respawn per vanished pane).
"""
import argparse
import datetime
import glob
import json
import os
import re
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import orchlib as L
import declared_wait as DW

SCRIPTS = os.path.dirname(os.path.abspath(__file__))
STATE_PATH = os.path.join(L.BOARD_DIR, "watchdog-state.json")
OFF_PATH = os.path.join(L.BOARD_DIR, "watchdog.off")
INTERVAL = 180            # seconds between passes
IDLE_LIMIT = 10 * 60      # idle this long after a task with no report = STALLED
NOTIFY_GAP = 30 * 60      # one notice per pane per this many seconds
PROMPT_PASSES = 2         # a permission prompt must hold this many passes in a row
PERSIST_PASSES = 2        # ERROR and STALLED must hold this many passes in a row (~3-6 min) before a flag
PRUNE_AFTER = 24 * 3600   # forget a handled vanished row after a day
SYNTHETIC = "<synthetic>"


# ---------------------------------------------------------------- pure rules (tested)

def evaluate(rows, state, now, seat_keys=(), killed=()):
    """(flags, new_state). `rows` is the snapshot of every tasked pane live right now.

    A row is a dict: key, name, owner, harness, status ("busy"|"idle"|...), idle_secs, kind
    (ERROR|DONE|...), synthetic (bool), reported (bool), prompt (str), held (bool), plus whatever
    `respawn` needs (task, cwd, model, pane, sid). Pure - no herdr, no disk - so the rules are
    testable with the exact shapes a real pass sees.

    A flag is {"key", "name", "owner", "cond": [..], "detail": [..], "row": row}. State carries the
    registry (`tasked`), the notice clock (`notified`) and the prompt streak (`prompt_seen`).
    """
    tasked = {k: dict(v) for k, v in (state.get("tasked") or {}).items()}
    notified = dict(state.get("notified") or {})
    streak = dict(state.get("prompt_seen") or {})
    cond_seen = dict(state.get("cond_seen") or {})
    held_now = set()
    live = {r["key"]: r for r in rows}
    flags = {}

    def flag(key, row, cond, detail):
        f = flags.setdefault(key, {"key": key, "name": row["name"], "owner": row.get("owner", ""),
                                   "cond": [], "detail": [], "row": row})
        f["cond"].append(cond)
        f["detail"].append(detail)

    def persistent(key, row, cond, detail):
        """Raise `cond` only once it held PERSIST_PASSES passes in a row. A pane between two
        turns reads idle for one pass; 2026-10-06/07: 292 flags in 7 days, 0 lasted 3 checks."""
        sk = f"{key}|{cond}"
        held_now.add(sk)
        cond_seen[sk] = cond_seen.get(sk, 0) + 1
        if cond_seen[sk] >= PERSIST_PASSES:
            flag(key, row, cond, detail)

    # (a) gone without a report. Only a pane we saw live last pass can vanish; a pane that
    # finished clean (DONE) or reported is a normal exit, not an alarm.
    for key, old in list(tasked.items()):
        if key in live or old.get("gone"):
            continue
        if (key in killed or old.get("reported")
                or (old.get("status") == "idle" and old.get("kind") == "DONE")):
            tasked.pop(key)
            continue
        old["gone"] = now
        flag(key, old, "VANISHED",
             f"pane is gone and never reported (last seen {int(now - old.get('last_seen', now))}s "
             f"ago, last state {old.get('status', '?')}/{old.get('kind', '?')})")

    # The seat pair's two panes are standing roles, not tasks: idle with no report is how they
    # wait (seat-watcher was flagged STALLED 13 min after its first review, 2026-10-11).
    try:
        import seat_pair
        pair_names = {v.get("label") for v in seat_pair.load_pair().values() if isinstance(v, dict)}
    except Exception:
        pair_names = set()
    for key, r in live.items():
        if key in seat_keys:
            continue
        if r.get("ghost"):
            flag(key, r, "GHOST", f"registry row with no herdr pane, sharing the name "
                 f"{r['name']!r} with a live pane (a --resume / --fork-session / --bg copy). "
                 f"Messages to that name reach both. Stop it: `claude stop {r['key'][:8]}`")
            continue
        tasked[key] = {k: r.get(k) for k in ("name", "owner", "harness", "pane", "sid", "task",
                                              "cwd", "model", "status", "kind", "reported")}
        tasked[key]["last_seen"] = now
        # (b) ERROR rows. A pane still working is not an error yet - only a stopped one is.
        # <synthetic> + ctx 0k is what a session reads right after a restart, before its first
        # reply, so it only means "died" once the herdr pane is gone too (2026-10-05 false flags).
        dead_synthetic = r.get("synthetic") and not r.get("pane")
        # A word-matched ERROR ("failed", "checkout" in old text) on a pane that already reported
        # is not news: 2026-10-05 cs549-review and reaper-upgrade were flagged 3 times each after
        # they had finished and reported.
        # A pane with a background task running (a Bridge poll, a Monitor, a test run) is waiting
        # on that task, not stuck: rcai-brand was flagged STALLED 61 times with a lavish poll up.
        word_error = (r.get("kind") == "ERROR" and r.get("status") != "busy"
                      and not r.get("reported") and not r.get("bg"))
        if dead_synthetic or word_error:
            persistent(key, r, "ERROR", "model is <synthetic> with ctx 0k and no herdr pane - the "
                       "session died under it" if dead_synthetic else "stopped on an ERROR")
        # (c) idle past the limit with no report to the owner.
        # A pane under a valid declared wait ("paused: ... until T", see declared_wait.py) is idle on
        # purpose until T. Past T, or past 4 h old (`wait_note` set, `waiting` false), it counts.
        if (r.get("status") == "idle" and not r.get("reported") and not r.get("held")
                and r.get("name") not in pair_names
                and not r.get("waiting") and not r.get("bg")
                and r.get("idle_secs", 0) > IDLE_LIMIT and r.get("kind") != "ERROR"):
            persistent(key, r, "STALLED", f"idle {int(r['idle_secs'] // 60)} min after its task, no "
                       f"report sent to {r.get('owner') or 'its owner'}"
                       + (f"; its declared wait is over 4 h old ({r['wait_note']})"
                          if r.get("wait_note") else ""))
        # (d) a permission prompt that is still there on the next pass.
        if r.get("prompt"):
            streak[key] = streak.get(key, 0) + 1
            if streak[key] >= PROMPT_PASSES:
                flag(key, r, "PROMPT", f"parked on a permission prompt: {r['prompt'][:120]}")
        else:
            streak.pop(key, None)
    for key in [k for k in streak if k not in live]:
        streak.pop(key)
    cond_seen = {k: v for k, v in cond_seen.items() if k in held_now}

    for key in [k for k, v in tasked.items() if v.get("gone") and now - v["gone"] > PRUNE_AFTER]:
        tasked.pop(key)

    # One notice per pane per condition: the same flag on the same pane is told once, and again
    # only after the pane works (goes busy) or a different condition shows. 2026-10-05: 19 of 23
    # flags were repeats or false, each one a wake-up for the owner and unstick-mate.
    for key, r in live.items():
        if r.get("status") == "busy":
            notified.pop(r.get("pane") or key, None)
    out = []
    for key, f in flags.items():
        pane_key = f["row"].get("pane") or key
        sig = "+".join(f["cond"])
        last = notified.get(pane_key)
        if isinstance(last, dict) and last.get("sig") == sig:
            continue
        if isinstance(last, (int, float)) and now - last < NOTIFY_GAP:
            continue
        notified[pane_key] = {"at": now, "sig": sig}
        out.append(f)
    return out, {"tasked": tasked, "notified": notified, "prompt_seen": streak, "cond_seen": cond_seen}


def notice_text(f):
    return (f"[watchdog] {f['name']}: {'+'.join(f['cond'])} - " + "; ".join(f["detail"])
            + ". Automated check. Run `py orch.py ls --format text` to look. It will not repeat this flag until "
            "the pane works again.")


def notice_target(f, orchestrator):
    """Who hears about it: the owner, else the orchestrator - but never the pane itself and never
    a seat that is itself the subject. None means stay quiet (board only)."""
    owner = f.get("owner") or orchestrator
    if not owner or owner == f["name"]:
        owner = orchestrator
    if not owner or owner == f["name"]:
        return None
    return owner


UNSTICK_MATE = "unstick-mate"
UNSTICK_CONDS = {"VANISHED", "ERROR", "STALLED", "PROMPT"}


def unstick_target(f, orchestrator, owner):
    """The unstick-mate hears every flag too (it acts on it; the owner is only told). Never when
    the pane is the mate itself or the seat, and never for a GHOST (a duplicate row, not a stall).
    Skipped when the mate already IS the owner, so it is not told twice."""
    if not UNSTICK_CONDS & set(f["cond"]):
        return None
    if f["name"] in (UNSTICK_MATE, orchestrator) or owner == UNSTICK_MATE:
        return None
    return UNSTICK_MATE


def unstick_text(f):
    return (f"[watchdog] {f['name']}: {'+'.join(f['cond'])} - " + "; ".join(f["detail"])
            + f". Start with `py \"{os.path.join(SCRIPTS, 'unstick.py')}\" check {f['name']} "
            f"--flag {'+'.join(f['cond'])}`.")


# ---------------------------------------------------------------- reading the fleet

def _reported_claude(sid, owner, entries, seat=""):
    """True if the session sent a SendMessage to its owner (any target when it names none) or to
    the current seat in its log tail. The seat counts because seat names go stale: on 2026-10-05
    five panes were owned by dead seats (vault-e5, -ac) and reported to the new one."""
    for l in entries:
        if '"SendMessage"' not in l:
            continue
        e = L._load(l)
        c = ((e or {}).get("message") or {}).get("content")
        if not isinstance(c, list):
            continue
        for b in c:
            if isinstance(b, dict) and b.get("type") == "tool_use" and b.get("name") == "SendMessage":
                to = (b.get("input") or {}).get("to", "")
                if not owner or to in (owner, seat):
                    return True
    return False


def _tier(model):
    low = (model or "").lower()
    return next((t for t in ("fable", "opus", "sonnet", "haiku") if t in low), "sonnet")


def _task_file(name):
    hits = sorted(glob.glob(os.path.join(L.HANDOFFS, f"task-{name}-*.md")))
    return hits[-1] if hits else ""


def snapshot(now=None):
    """Rows for every tasked pane live right now: spawned through orch.py, with an owner, not a
    domain mate (a mate has no task to lose)."""
    now = now or time.time()
    by_sid = L.panes_by_sid()
    owners = L.owners_load()
    seat = L.orchestrator_name()
    holds = {}
    try:
        with open(os.path.join(L.BOARD_DIR, "holds.json"), encoding="utf-8") as fh:
            holds = json.load(fh)
    except (OSError, ValueError):
        pass
    holds = DW.active_holds(holds, now)       # a `hold --until` that passed no longer holds
    rows = []
    for sid, m in L.sessions().items():
        o = owners.get(sid) or {}
        if not o.get("spawned") or not m.get("owner") or m.get("domain"):
            continue
        ents = L.tail_entries(sid)
        ctx, model = L.last_usage(sid, ents)
        kind, _ = L.classify(sid, ents)
        p = by_sid.get(sid, {})
        status = m.get("status", "?")
        stamp = (m.get("statusUpdatedAt") or m.get("updatedAt") or 0) / 1000
        prompt = ""
        if p.get("pane_id") and status != "busy":
            prompt = L.pending_prompt(L.pane_read(p["pane_id"], 30))
        wst, decl = DW.waiting(m["name"], ents, holds, now)
        rows.append({"key": sid, "sid": sid, "name": m["name"], "owner": m["owner"],
                     "waiting": wst == "active",
                     "wait_note": DW.describe(decl) if wst == "stale" else "",
                     "harness": "claude", "pane": p.get("pane_id", ""), "status": status,
                     "idle_secs": now - stamp if stamp else 0, "kind": kind,
                     "synthetic": model == SYNTHETIC and ctx == 0,
                     "reported": _reported_claude(sid, m["owner"], ents, seat), "prompt": prompt,
                     "held": m["name"] in holds, "bg": L.bg_tasks(p)[0], "task": _task_file(m["name"]),
                     "cwd": m.get("cwd", ""), "model": model})
    rows.extend(ghost_rows(by_sid))
    return rows


def ghost_rows(by_sid):
    """Registry rows with no herdr pane whose name a PANED row also carries.

    Born 2026-10-02: `claude --resume` of the orchestrator left a `--bg --fork-session` copy
    named `orchestrator` (kind "bg", no pane, 347k ctx). Two rows, one name, so every message to
    it went to both. A pane-less row is normal on its own (headless runs); a pane-less row that
    SHARES a name with a live pane is the ghost."""
    sess = L.sessions()
    paned = {m["name"] for sid, m in sess.items() if sid in by_sid}
    return [{"key": sid, "sid": sid, "name": m["name"], "owner": "", "harness": "claude",
             "pane": "", "ghost": True, "status": m.get("status", "?")}
            for sid, m in sess.items() if sid not in by_sid and m["name"] in paned]


def seat_keys():
    seat = L.orchestrator_seat() or {}
    keys = {seat.get("pane", "")} - {""}
    sid = L.orchestrator_sid()
    if sid:
        keys.add(sid)
    return keys


# ---------------------------------------------------------------- acting

def board_write(f):
    try:
        import board as B
        B.sync_board(new_updates=[{"ts": B.now_iso(), "pane": f["name"], "kind": "WATCHDOG",
                                    "text": " ".join(notice_text(f).split())[:200],
                                    "group": B.group_for_who(f["name"])}])
        return True
    except Exception as e:
        print(f"  -> board write failed ({type(e).__name__}: {e})", flush=True)
        return False


def send(owner, text):
    env = dict(os.environ, ORCH_SENDER="watchdog")
    r = subprocess.run([sys.executable, os.path.join(SCRIPTS, "orch.py"), "prompt", owner, text],
                       capture_output=True, text=True, timeout=90, env=env)
    return r.returncode == 0, (r.stdout + r.stderr).strip()[-200:]


def respawn(f):
    """Reopen a VANISHED claude task from its saved task file. One try; returns a status line."""
    old = f["row"]
    if old.get("harness") != "claude" or not old.get("task") or not os.path.exists(old["task"]):
        return "no respawn: not a claude task with a saved task file"
    label = re.sub(r"-r\d+$", "", f["name"]) + "-r" + str(int(time.time()) % 10000)
    try:
        log = L.log_path(old.get("sid", ""))
    except SystemExit:
        log = ""
    prompt = (f"Read {old['task']} and do exactly what it says. You are a RESPAWN: {f['name']} "
              f"vanished mid-task."
              + (f" Its transcript is {log} - read the tail first and continue from there." if log
                 else ""))
    r = subprocess.run([sys.executable, os.path.join(SCRIPTS, "orch.py"), "spawn", label, prompt,
                        "--model", _tier(old.get("model")), "--cwd", old.get("cwd") or L.DEFAULT_CWD,
                        "--owner", old.get("owner", ""),
                        "--rule", "override", "--reason", "watchdog respawn of a vanished pane, same model"],
                       capture_output=True, text=True, timeout=300)
    return f"respawn {label}: " + ("ok" if r.returncode == 0 else "FAILED " + r.stdout[-150:])


def respawn_named(name):
    """Respawn ONE vanished pane by name, once. Only a row this watchdog marked `gone` qualifies,
    so a live pane can never be duplicated by it."""
    state = state_load()
    for key, old in (state.get("tasked") or {}).items():
        if old.get("name") == name and old.get("gone"):
            if old.get("respawned"):
                return f"no respawn: {name} was already respawned"
            old["respawned"] = time.time()
            state_save(state)
            return respawn({"name": name, "row": old})
    return f"no respawn: {name} is not a vanished pane in the watchdog registry"


KILLED_PATH = os.path.join(L.BOARD_DIR, "watchdog-killed.json")


def killed_load():
    try:
        with open(KILLED_PATH, encoding="utf-8") as fh:
            d = json.load(fh)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def mark_killed(*keys):
    """`orch.py kill` calls this: a deliberate close is not a VANISH. Keys are a claude session id
    or a non-claude pane id. Its own file, so it never races the pass that owns the state file.
    Never raises - a kill must not fail because the watchdog could not write."""
    try:
        d = {k: t for k, t in killed_load().items() if time.time() - t < PRUNE_AFTER}
        d.update({k: time.time() for k in keys if k})
        os.makedirs(L.BOARD_DIR, exist_ok=True)
        with open(KILLED_PATH + ".tmp", "w", encoding="utf-8") as fh:
            json.dump(d, fh)
        os.replace(KILLED_PATH + ".tmp", KILLED_PATH)
    except Exception:
        pass


def log_line(path, text):
    if not path:
        return
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(time.strftime("%Y-%m-%d %H:%M:%S  ") + text + "\n")


def state_load():
    try:
        with open(STATE_PATH, encoding="utf-8") as fh:
            d = json.load(fh)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def state_save(d):
    os.makedirs(L.BOARD_DIR, exist_ok=True)
    with open(STATE_PATH + ".tmp", "w", encoding="utf-8") as fh:
        json.dump(d, fh, indent=1)
    os.replace(STATE_PATH + ".tmp", STATE_PATH)


# ---------------------------------------------------------------- wedge alarm
#
# While Ryan is AFK (board/afk.json says so), a question the fleet raised for him that nobody has
# acknowledged in N minutes means the escalation path is stuck: one alert goes out through
# phone_alerts.py, once per question. Off unless afk.json has "afk": true. Quiet channel by default
# (phone_alerts' own rules: mute, quiet hours, daily cap); only "loud": true in afk.json lets it
# wake him through quiet hours (the 'loud is for the visa only' ruling, 2026-10-08).
# Idea from Kun's First Mate docs/wedge-alarm.md (MIT); the code is ours.

AFK_PATH = os.path.join(L.BOARD_DIR, "afk.json")
WEDGE_MINUTES = 30


def afk_load(path=None):
    """board/afk.json -> dict, {} when absent or unreadable. `_mtime` is the file's own time, the
    fallback for AFK having started when the file has no "since"."""
    path = path or AFK_PATH
    try:
        with open(path, encoding="utf-8") as fh:
            d = json.load(fh)
        if not isinstance(d, dict):
            return {}
        d["_mtime"] = os.path.getmtime(path)
        return d
    except (OSError, ValueError):
        return {}


def _epoch(v):
    """An epoch number, or an ISO time (Z or offset; no offset = local) -> epoch seconds, else None."""
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return float(v)
    if isinstance(v, str) and v.strip():
        try:
            return datetime.datetime.fromisoformat(v.strip().replace("Z", "+00:00")).timestamp()
        except ValueError:
            return None
    return None


def wedge_due(questions, afk, alerted, now):
    """Pure. -> (due, alerted'). `due` is the open questions that crossed the line this pass and
    were not alerted before: [{"id", "title", "age_min"}].

    A question counts when it is unanswered, not marked `acked`, not `auto` (the board answers
    those itself), and not older than Ryan's last `ack_ts` in afk.json. Its clock starts at the
    later of when it was raised and when AFK began, so going AFK with an old question open does
    not alert at once. Not AFK -> nothing is due and the alerted set resets for the next AFK."""
    if not afk.get("afk"):
        return [], {}
    try:
        minutes = float(afk.get("wedge_minutes") or WEDGE_MINUTES)
    except (TypeError, ValueError):
        minutes = WEDGE_MINUTES
    if minutes <= 0:
        minutes = WEDGE_MINUTES
    since = _epoch(afk.get("since")) or afk.get("_mtime") or 0
    ack = _epoch(afk.get("ack_ts")) or 0
    open_ids, due = set(), []
    for q in questions or []:
        qid = q.get("id") or ""
        if not qid or q.get("answered") or q.get("acked") or q.get("auto"):
            continue
        open_ids.add(qid)
        start = max(_epoch(q.get("created")) or now, since)
        if ack >= start or qid in alerted or now - start < minutes * 60:
            continue
        due.append({"id": qid, "title": (q.get("title") or "")[:60], "age_min": int((now - start) // 60)})
    return due, {k: v for k, v in alerted.items() if k in open_ids}


def wedge_event(due, afk):
    """The phone_alerts event for one wedge: blocked-on-Ryan lane, loud only on request."""
    oldest = max(d["age_min"] for d in due)
    items = "; ".join(f"{d['id'].upper()} {d['title']}" for d in due[:3]) + (
        f"; +{len(due) - 3} more" if len(due) > 3 else "")
    return {"kind": "WEDGE", "key": "wedge|" + ",".join(sorted(d["id"] for d in due)),
            "text": f"Wedge: {len(due)} escalation(s) unanswered {oldest}+ min while you are AFK - {items}",
            "blocked_secs": max(oldest * 60, 600), "loud": bool(afk.get("loud"))}


def _board_questions():
    try:
        import board as B
        return B.load().get("questions", [])
    except (Exception, SystemExit) as e:
        print(f"  -> wedge: board unreadable ({type(e).__name__})", flush=True)
        return []


def run_wedge(now, alerted, dry_run=False, questions=None, afk=None):
    """One pass of the wedge alarm. -> (event or None, alerted'). Never raises."""
    try:
        afk = afk_load() if afk is None else afk
        if not afk.get("afk"):
            return None, {}
        due, alerted2 = wedge_due(_board_questions() if questions is None else questions,
                                  afk, alerted, now)
        if not due:
            return None, alerted2
        ev = wedge_event(due, afk)
        if dry_run:
            print(f"  (dry-run) wedge alarm would send ({'loud' if ev['loud'] else 'quiet'}): "
                  f"{ev['text']}", flush=True)
            return ev, alerted
        import phone_alerts
        res = phone_alerts.handle(ev, now)
        print(f"  -> wedge alarm: {res} ({'loud' if ev['loud'] else 'quiet'}): {ev['text']}", flush=True)
        if res not in ("drop:slack-failed", "drop:error"):     # those retry next pass
            alerted2.update({d["id"]: now for d in due})
        return ev, alerted2
    except Exception as e:
        print(f"  -> wedge alarm failed ({type(e).__name__}: {e})", flush=True)
        return None, alerted


def run_once(dry_run=False, do_respawn=False, rows=None, state=None, now=None, log=None):
    if os.path.exists(OFF_PATH):
        print("watchdog: off switch present, skipping", flush=True)
        log_line(log, "off switch present, skipped")
        return []
    now = now or time.time()
    rows = snapshot(now) if rows is None else rows
    state = state_load() if state is None else state
    flags, new_state = evaluate(rows, state, now, seat_keys(), killed_load())
    log_line(log, f"{len(rows)} tasked pane(s), {len(flags)} flag(s)"
             + "".join(f"  [{f['name']} {'+'.join(f['cond'])}]" for f in flags))
    orchestrator = L.orchestrator_name()
    for f in flags:
        line = notice_text(f)
        print(f"{f['name']} {'+'.join(f['cond'])} | {'; '.join(f['detail'])}", flush=True)
        if dry_run:
            print(f"  (dry-run) would tell {notice_target(f, orchestrator)} and write the board",
                  flush=True)
            continue
        board_write(f)
        target = notice_target(f, orchestrator)
        if target and target == orchestrator:
            import afk
            if afk.digest_flag(f["name"], "+".join(f["cond"]), "; ".join(f["detail"])):
                target = None
        if target:
            ok, out = send(target, line)
            print(f"  -> {target}: {'sent' if ok else 'FAILED ' + out}", flush=True)
        mate = unstick_target(f, orchestrator, target)
        if mate:
            ok, out = send(mate, unstick_text(f))
            print(f"  -> {mate}: {'sent' if ok else 'FAILED ' + out}", flush=True)
        if do_respawn and "VANISHED" in f["cond"]:
            print("  -> " + respawn(f), flush=True)
    if not dry_run:
        import afk
        afk.tick()
    _, new_state["wedge_alerted"] = run_wedge(now, state.get("wedge_alerted") or {}, dry_run)
    if not dry_run:
        state_save(new_state)
        import phone_alerts
        phone_alerts.tick(flags)
    return flags


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--respawn", action="store_true")
    ap.add_argument("--respawn-pane", metavar="NAME",
                    help="respawn one VANISHED pane by name from its saved task file (unstick-mate)")
    ap.add_argument("--interval", type=int, default=INTERVAL)
    ap.add_argument("--log", help="append one line per pass here (the scheduled routine's log)")
    a = ap.parse_args(argv)
    if a.respawn_pane:
        print(respawn_named(a.respawn_pane))
        return
    if a.once:
        try:
            run_once(a.dry_run, a.respawn, log=a.log)
        except Exception as e:
            log_line(a.log, f"pass FAILED ({type(e).__name__}: {e})")
            raise
        return
    print(f"orch_watchdog: every {a.interval}s; off switch {OFF_PATH}", flush=True)
    while True:
        try:
            run_once(a.dry_run, a.respawn, log=a.log)
        except Exception as e:
            print(f"orch_watchdog: pass failed ({type(e).__name__}: {e})", flush=True)
        time.sleep(a.interval)


if __name__ == "__main__":
    main()
