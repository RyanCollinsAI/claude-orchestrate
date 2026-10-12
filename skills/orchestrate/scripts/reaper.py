r"""Reaper: close finished panes, kill orphan helper processes and dead poll keepers. No LLM.

  py reaper.py --dry-run          # show what it would do, touch nothing
  py reaper.py                    # one real pass
  py reaper.py --auto --log FILE  # what the scheduled task runs (every 2 min; see below)

Born 2026-10-05: memory fell to 4 GB free. 17 sessions cost ~1 GB each, 11 poll_keeper.ps1
watchers and many npx/node helpers stayed, and finished panes (ctx 0k, model <synthetic>) lingered.
`orch.py reap` only closes idle+DONE panes whose owner is NOT live (nearly none), and nothing
killed a process. This does three jobs:

  1. PANES   a pane that finished (idle, last words DONE, report sent to its owner) or whose claude
             process is dead, older than --older (20m; 5m when RAM is low). Never the seat, a mate,
             a lead with live crew, a held pane, a pane with an open board question, an unsent draft,
             a permission prompt, a live keeper that needs it, or a worktree with unpushed work.
  2. PROCS   helper processes (npx / MCP node servers and their cmd shells) whose whole parent chain
             is helpers and ends at a parent that no longer exists - the claude that spawned them is
             gone. A chain that touches claude, herdr, explorer, a browser or anything else that is
             not a plain helper is never touched, and no browser process is ever killed.
  3. KEEPERS poll_keeper.ps1 watchers whose Bridge page is closed (session ended, page file gone, or
             no session for it). They are also unregistered, or routines\learn-poll-keeper would
             relaunch them.

Chrome is REPORT ONLY: instance counts per profile, a FLAG (first pass only) on any portless
instance over 2 h that is not in PORTLESS_OK, and a HEAVY line on any browser over 8 GB.
It never closes a tab: the agent Chrome's tabs belong to the panes that opened them.

Every kill is one line in board\reaper.log: pid, command, MB and the reason. The last line printed
is `reaped N panes, M procs, X MB`. --auto runs a full pass when 10 min passed since the last one,
or when free RAM is under 6 GB and 2 min passed - so the 2 minute task covers both triggers.
Off switch: create board\reaper.off.
"""
import argparse
import datetime
import json
import os
import re
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import orchlib as L

STATE_PATH = os.path.join(L.BOARD_DIR, "reaper-state.json")
OFF_PATH = os.path.join(L.BOARD_DIR, "reaper.off")
LOG_PATH = os.path.join(L.BOARD_DIR, "reaper.log")
EVERY = 120               # full pass this often, so a Done pane closes ~10-12 min after its report
LOW_EVERY = 120           # ...or this often while RAM is low
LOW_GB = 6.0              # same line orch spawn refuses under
KEEP_PREFIXES = ("grill-tutor",)   # keepers that must outlive their last Done report
OLDER = "10m"
OLDER_LOW = "5m"
PROC_MIN_AGE = 600        # never touch a helper younger than this
KEEPER_NO_SESSION_AGE = 1800
KEEPER_SCRIPT = os.path.join(os.path.expanduser("~"), "Vault", ".claude", "skills", "learn",
                             "tools", "poll_keeper.ps1")
KEEPERS_JSON = os.path.join(os.path.expanduser("~"), "Vault", "routines", "learn-poll-keeper",
                            "state", "keepers.json")

# Process names that may appear in an orphan helper chain. A chain with any other name in it has a
# real owner (claude, herdr, explorer, a browser, a service) and is not an orphan.
HELPER_NAMES = {"node.exe", "cmd.exe", "conhost.exe", "npx.cmd", "npx.exe", "npm.cmd", "npm.exe"}
ROOT_NAMES = {"node.exe", "cmd.exe"}
HELPER_RE = re.compile(r"_npx|npx-cli|npm-cache|@playwright/mcp|playwright-mcp|mcp-server|"
                       r"server-(filesystem|memory|github|puppeteer|fetch|sequential)|context7|"
                       r"\bmcp\b", re.I)
# Long-lived shared things that are detached on purpose. A match here is never a helper.
NEVER_RE = re.compile(r"lavish|herdr|reloader|watch|orch|reaper|chrome-devtools|remote-debugging|"
                      r"9333|9334|poll_keeper|secret-vault|obsidian", re.I)


# ---------------------------------------------------------------- pure rules (tested)

def parse_age(s):
    m = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*([smh]?)\s*", str(s))
    if not m:
        raise ValueError(f"bad age {s!r}: use 20m, 1h or 90s")
    return float(m.group(1)) * {"s": 1, "m": 60, "h": 3600, "": 60}[m.group(2)]


def ancestors(table, pid):
    """The live parent chain of `pid`, nearest first, and whether it ended at a parent that is gone.
    `table` is {pid: {ppid, name, ctime}}. A parent born AFTER its child is a reused pid, so the
    real parent is gone."""
    chain, seen, cur = [], {pid}, table.get(pid)
    broken = cur is None
    while cur:
        par = table.get(cur["ppid"])
        if par is None or cur["ppid"] in seen or par["ctime"] > cur["ctime"]:
            broken = True
            break
        chain.append(par)
        seen.add(cur["ppid"])
        cur = par
    return chain, broken


def is_orphan_helper(table, pid, cmdline, now):
    """(True, reason) when `pid` is an npx/MCP helper whose spawner is gone. Ancestry first: the
    whole chain must be plain helpers and must end at a missing parent."""
    me = table.get(pid)
    if not me or me["name"].lower() not in ROOT_NAMES:
        return False, ""
    if not HELPER_RE.search(cmdline) or NEVER_RE.search(cmdline):
        return False, ""
    if now - me["ctime"] < PROC_MIN_AGE:
        return False, ""
    chain, broken = ancestors(table, pid)
    if not broken:
        return False, ""
    if any(a["name"].lower() not in HELPER_NAMES for a in chain):
        return False, ""
    names = " <- ".join(a["name"] for a in chain) or "no parent"
    return True, f"orphan helper: parent chain ({names}) ends at a parent that is gone"


def descendants(table, pid):
    kids = {}
    for p, r in table.items():
        kids.setdefault(r["ppid"], []).append(p)
    out, todo = [], [pid]
    while todo:
        for k in kids.get(todo.pop(), []):
            if k != pid and k not in out and table[k]["ctime"] >= table[pid]["ctime"]:
                out.append(k)
                todo.append(k)
    return out


def keeper_page(cmdline):
    """The -Page value of a poll_keeper.ps1 command line, or ''."""
    m = re.search(r'-Page\s+"([^"]+)"|-Page\s+(\S+)', cmdline)
    return (m.group(1) or m.group(2)) if m else ""


def page_closed(page, sessions, age_s):
    """Why the Bridge page is closed, or ''. `sessions` is the lavish state.json `sessions` dict."""
    if not os.path.exists(page):
        return "page file is gone"
    mine = [s for s in sessions.values() if os.path.normcase(s.get("file", "")) == os.path.normcase(page)]
    if any(s.get("status") != "ended" for s in mine):
        return ""
    if mine:
        return "Bridge session ended"
    if age_s > KEEPER_NO_SESSION_AGE:
        return "no Bridge session for the page"
    return ""


WAITS_RE = re.compile(r"waits? (for|on) (his|ryan|your)|waiting (for|on) (his|ryan|your)|needs? your (answer|yes|ok|ap)|"
                      r"ryan (approves|answers|says yes|decides)|his (yes|approval|answer)|"
                      r"bridge page|poll(ing)?\b|asks? ryan|needs? ryan|open question", re.I)
QUEUE_DONE_RE = re.compile(r"queue (is )?(finished|empty|drained|done)|nothing left|no (queued|pending) jobs", re.I)


def waits_on_ryan(said):
    return bool(WAITS_RE.search(said or ""))


def queue_pending(said):
    """True when the last report talks about a queue that is not stated finished."""
    return bool(re.search(r"\bqueue", said or "", re.I)) and not QUEUE_DONE_RE.search(said)


def pane_verdict(f):
    """(ok, reason) for one pane. `f` holds the facts gathered by `pane_facts`; pure so every
    refusal is testable. The first failing gate names itself."""
    if f.get("protected"):
        return False, "seat or mate (protected)"
    if f.get("held"):
        return False, "held"
    if f.get("board_named"):
        return False, "an open board question names it"
    if f.get("keeper_needs"):
        return False, "a live poll keeper serves it"
    if f.get("draft"):
        return False, "unsent draft in its prompt box"
    if f.get("prompt"):
        return False, "parked on a prompt"
    if f.get("idle_s", 0) < f.get("older_s", 0):
        return False, "not idle long enough"   # also covers a restart whose registry pid is stale
    if f.get("dead"):
        return True, "claude process is dead, herdr pane still open"
    if waits_on_ryan(f.get("said", "")):
        return False, "its last report waits on Ryan (question, approval, Bridge page or poll)"
    if queue_pending(f.get("said", "")):
        return False, "it still has queued jobs"
    if f.get("bg"):
        return False, "background tasks still running"
    if f.get("status") != "idle":
        return False, f"not idle ({f.get('status')})"
    if f.get("kind") != "DONE" or not f.get("said", "").strip():
        return False, f"not finished clean ({f.get('kind')})"
    return True, f"finished: idle {int(f['idle_s'] // 60)} min after its Done report; owner and files do not matter"


def should_run(state, now, free_gb, force=False):
    """(run, low): `--auto` pacing. A pass every EVERY seconds, or every LOW_EVERY while RAM is low."""
    low = free_gb < LOW_GB
    gap = now - state.get("last_run", 0)
    return (force or gap >= EVERY or (low and gap >= LOW_EVERY)), low


# ---------------------------------------------------------------- reading the box

def proc_table():
    """({pid: {ppid, name, ctime, rss}}, {pid: cmdline}). A process we may not read is kept in the
    table (it still anchors ancestry) with an empty command line. One CIM query (`L.proc_snapshot`):
    the psutil walk this replaced took 41 s per pass, every 2 minutes."""
    return L.proc_snapshot()


def tree_mb(table, pid):
    if pid not in table:
        return 0
    return round((table[pid]["rss"] + sum(table[d]["rss"] for d in descendants(table, pid))) / 1048576)


def free_gb():
    import psutil
    return psutil.virtual_memory().available / 2 ** 30


def lavish_sessions():
    d = os.environ.get("LAVISH_AXI_STATE_DIR") or os.path.join(os.path.expanduser("~"), ".lavish-axi")
    try:
        with open(os.path.join(d, "state.json"), encoding="utf-8") as fh:
            return json.load(fh).get("sessions") or {}
    except (OSError, ValueError):
        return {}


def keepers_load():
    try:
        with open(KEEPERS_JSON, encoding="utf-8") as fh:
            d = json.load(fh)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def live_keeper_texts(table, cmds, sessions):
    """Lowercased page+log text of every keeper whose Bridge page is still open - a pane whose name
    or cwd appears in one is the pane that serves that page."""
    out = []
    for pid, c in cmds.items():
        if "poll_keeper.ps1" not in c or pid not in table:
            continue
        page = keeper_page(c)
        if page and not page_closed(page, sessions, time.time() - table[pid]["ctime"]):
            out.append((page + " " + _log_of(c)).lower())
    return out


def _log_of(cmdline):
    m = re.search(r'-Log\s+"([^"]+)"|-Log\s+(\S+)', cmdline)
    return (m.group(1) or m.group(2)) if m else ""


# ---------------------------------------------------------------- panes

def plan_panes(older_s, table, cmds, sessions_b):
    """([(name, mb, reason)], [(name, why)]) - what to close and what was left alone."""
    import psutil
    import orch
    import restart_idle
    sess = L.sessions()
    by_sid = L.panes_by_sid()
    owners = L.owners_load()
    open_named = orch._board_open_question_names()
    keepers = live_keeper_texts(table, cmds, sessions_b)
    try:
        with open(os.path.join(L.BOARD_DIR, "holds.json"), encoding="utf-8") as fh:
            holds = json.load(fh)
    except (OSError, ValueError):
        holds = {}
    reap, skip = [], []
    for sid, m in sess.items():
        name = m["name"]
        pane = by_sid.get(sid, {}).get("pane_id", "")
        if not pane:
            continue                                         # headless row, nothing to close
        pid = m.get("pid")
        f = {"protected": orch._is_protected_pane(name, sid, sess) or name.startswith(KEEP_PREFIXES),
             "held": name in holds, "board_named": orch._board_names_pane(name, open_named),
             "keeper_needs": any(name.lower() in t or (m.get("cwd") and os.path.basename(m["cwd"]).lower() in t
                                                       and len(os.path.basename(m["cwd"])) > 4)
                                 for t in keepers),
             "bg": L.bg_tasks(by_sid.get(sid))[0], "status": m.get("status"), "older_s": older_s,
             "dead": bool(pid) and pid not in table and not psutil.pid_exists(pid)}
        stamp = (m.get("statusUpdatedAt") or m.get("updatedAt") or 0) / 1000
        f["idle_s"] = time.time() - max(stamp, L.last_assistant_ts(sid) or 0)
        if not f["dead"] and f["status"] == "idle" and f["idle_s"] >= older_s and not (
                f["protected"] or f["held"] or f["board_named"] or f["keeper_needs"]):
            f["kind"], f["said"] = L.classify(sid)
        ok, why = pane_verdict(f)
        if ok:                          # last, because it reads the screen: a typed draft or a prompt
            txt = L.pane_read(pane, 30)
            f["draft"], f["prompt"] = restart_idle.has_draft(txt), L.pending_prompt(txt)
            ok, why = pane_verdict(f)
        if ok:
            reap.append((name, tree_mb(table, pid) if pid else 0, why))
        else:
            skip.append((name, why))
    return reap, skip


def empty_containers():
    """[('tab'|'workspace', id)] for tabs and workspaces herdr still lists with zero panes."""
    out = []
    for kind, key, idk in (("tab", "tabs", "tab_id"), ("workspace", "workspaces", "workspace_id")):
        try:
            rows = L.herdr(kind, "list", soft=True)["result"][key]
        except (KeyError, TypeError):
            continue
        out += [(kind, r[idk]) for r in rows if r.get("pane_count") == 0]
    return out


# ---------------------------------------------------------------- procs and keepers

def plan_procs(table, cmds, now):
    """[(pid, victims, mb, cmd, reason)] - one row per orphan helper root, victims = root + the
    helper descendants under it (children never include a browser or anything off the helper list)."""
    rows, taken = [], set()
    for pid in sorted(table, key=lambda p: table[p]["ctime"]):
        if pid in taken or pid == os.getpid():
            continue
        ok, why = is_orphan_helper(table, pid, cmds.get(pid, ""), now)
        if not ok:
            continue
        vic = [pid] + [d for d in descendants(table, pid) if table[d]["name"].lower() in HELPER_NAMES]
        taken.update(vic)
        rows.append((pid, vic, round(sum(table[v]["rss"] for v in vic) / 1048576), cmds[pid], why))
    return rows


def plan_keepers(table, cmds, sessions_b, now):
    """([(pid, victims, mb, cmd, page, reason)], [page of a registry entry with no process]) for
    every keeper whose Bridge page is closed."""
    rows, seen_pages = [], set()
    for pid, c in cmds.items():
        if "poll_keeper.ps1" not in c or pid not in table or "reaper" in c.lower():
            continue
        page = keeper_page(c)
        why = page_closed(page, sessions_b, now - table[pid]["ctime"]) if page else ""
        if not why:
            continue
        vic = [pid] + descendants(table, pid)
        seen_pages.add(os.path.normcase(page))
        rows.append((pid, vic, round(sum(table[v]["rss"] for v in vic) / 1048576), c, page, why))
    ghosts = []
    for e in keepers_load().values():
        page = e.get("page", "")
        if page and os.path.normcase(page) not in seen_pages and page_closed(page, sessions_b, 1 << 30):
            ghosts.append(page)
    return rows, ghosts


# ---------------------------------------------------------------- chrome (report only)

PORTLESS_FLAG_AGE = 2 * 3600
HEAVY_MB = 8 * 1024        # one browser holding this much is named as the RAM hog (2026-10-07: 17 GB)

# Portless Chromes that are SUPPOSED to live for days, by profile. Never flagged: the flag told
# the seat to "check the owner" 795 times in 7 days (2026-10-07) and the owner is fine.
PORTLESS_OK = {
    "channela": r"playwright-cli session routines\cc-strike-watch keeps open and reopens if closed",
    "channelb": r"playwright-cli session routines\cc-strike-watch keeps open and reopens if closed",
    "default": "Ryan's everyday Chrome (no --user-data-dir), windows opened by Bridge and links",
}


def chrome_instances(table, cmds, now):
    """One row per main chrome.exe (no --type=): profile, debug port ('' = portless), process count,
    MB, age, the parent that owns it ('' = none alive), whether to flag it and whether it is the
    RAM hog (`heavy`). Report only - the reaper never kills a browser and never closes a tab:
    Ryan's :9333 and the agent :9334 are never even candidates, and :9334 tabs belong to the panes
    that opened them."""
    rows = []
    for pid, r in table.items():
        c = cmds.get(pid, "")
        if r["name"].lower() != "chrome.exe" or "--type=" in c:
            continue
        m = re.search(r"--user-data-dir=(.+?)(?:\s--|$)", c.replace('"', ""))
        prof = os.path.basename(m.group(1).rstrip("\\/")) if m else "default"
        p = re.search(r"--remote-debugging-port=(\d+)", c)
        port = p.group(1) if p else ""
        kids = descendants(table, pid)
        par = table.get(r["ppid"])
        owner = par["name"] if par and par["ctime"] <= r["ctime"] else ""
        age = now - r["ctime"]
        mb = round((r["rss"] + sum(table[k]["rss"] for k in kids)) / 1048576)
        rows.append({"pid": pid, "profile": prof, "port": port, "procs": 1 + len(kids), "mb": mb,
                     "age_s": age, "owner": owner,
                     "flag": not port and age > PORTLESS_FLAG_AGE and prof not in PORTLESS_OK,
                     "heavy": mb >= HEAVY_MB})
    return sorted(rows, key=lambda x: (x["port"] == "", x["profile"]))


CHROME_SEEN_PATH = os.path.join(L.BOARD_DIR, "reaper-chrome-flagged.json")


def chrome_keys(r):
    """The seen-set keys a row raises: its pid when flagged, 'pid:heavy' when it is the RAM hog."""
    return ([str(r["pid"])] if r["flag"] else []) + ([f"{r['pid']}:heavy"] if r.get("heavy") else [])


def chrome_new_flags(rows, seen):
    """Rows with an alert not logged before. 2026-10-05: the same three portless Chromes were
    logged 98 times in 7 hours; one line per instance is enough."""
    return [r for r in rows if any(k not in seen for k in chrome_keys(r))]


def chrome_seen_load():
    try:
        with open(CHROME_SEEN_PATH, encoding="utf-8") as fh:
            return set(json.load(fh))
    except (OSError, ValueError, TypeError):
        return set()


def chrome_seen_save(pids):
    os.makedirs(L.BOARD_DIR, exist_ok=True)
    with open(CHROME_SEEN_PATH, "w", encoding="utf-8") as fh:
        json.dump(sorted(pids), fh)


def chrome_tab_count(port):
    """Open page tabs on a debug port, or None. One plain HTTP GET - no CDP client is attached."""
    import urllib.request
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/list", timeout=3) as fh:
            return sum(1 for t in json.load(fh) if t.get("type") == "page")
    except Exception:
        return None


def chrome_lines(rows, seen=()):
    """Console lines. A portless FLAG prints on its first pass only: the same instance printed it
    795 times in 7 days. `seen` is the set of keys already raised (`chrome_keys`)."""
    out = [f"chrome: {sum(r['procs'] for r in rows)} procs in {len(rows)} instances"]
    for r in rows:
        flag = ""
        if r["flag"]:
            flag = ("  (flagged earlier, still open)" if str(r["pid"]) in seen
                    else "  FLAG: portless over 2 h - not touched, check its owner")
        heavy = ""
        if r.get("heavy"):
            tabs = f" in {r['tabs']} tabs" if r.get("tabs") is not None else ""
            heavy = (f"  HEAVY: {r['mb'] / 1024:.0f} GB{tabs} - the RAM hog; its tabs belong to the "
                     f"panes that opened them, ask those panes to close finished ones")
        out.append(f"  chrome {r['profile']:<12} {('port ' + r['port']) if r['port'] else 'portless':<10} "
                   f"{r['procs']:>3} procs {r['mb']:>5} MB  {int(r['age_s'] // 60)} min"
                   + (f"  owner {r['owner']}" if r["owner"] else "") + flag + heavy)
    return out


# ---------------------------------------------------------------- acting

def log_line(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    stamp = datetime.datetime.now().astimezone().replace(microsecond=0).isoformat()
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(f"{stamp}  {text}\n")


def kill_pids(pids, ctimes):
    """Kill `pids` (deepest first), skipping any whose create time changed - a reused pid."""
    import psutil
    for pid in reversed(pids):
        try:
            p = psutil.Process(pid)
            if abs(p.create_time() - ctimes[pid]) > 1:
                continue
            p.kill()
        except psutil.Error:
            pass


def unregister_keeper(page):
    """Drop the keeper's registry entry so the 2 min restart routine does not relaunch it."""
    if not os.path.exists(page):
        return
    subprocess.run(["pwsh", "-NoProfile", "-File", KEEPER_SCRIPT, "-Page", page, "-Unregister"],
                   capture_output=True, timeout=60)


def run_pass(older=OLDER, dry=False, log=LOG_PATH, quiet=False):
    """One pass. -> (panes, procs, mb). Prints and logs every action, then the summary line."""
    import orch
    now = time.time()
    table, cmds = proc_table()
    sessions_b = lavish_sessions()
    verb = "WOULD " if dry else ""
    panes_n = procs_n = mb_total = 0

    def note(kind, text):
        print(f"{verb}{kind} {text}", flush=True)
        if not dry:
            log_line(log, f"{kind} {text}")

    reap, skip = plan_panes(parse_age(older), table, cmds, sessions_b)
    for name, mb, why in reap:
        if not dry:
            orch.cmd_kill(name, quiet=True, force=True)
        note("CLOSE-PANE", f"name={name} mb={mb} reason={why}")
        panes_n += 1
        mb_total += mb
    if not quiet:
        for name, why in skip:
            print(f"  left {name}: {why}", flush=True)
    for kind, ident in empty_containers():
        if not dry:
            L.herdr(kind, "close", ident)
        note("CLOSE-EMPTY", f"{kind}={ident} reason=no panes left in it")

    if not dry and reap:
        table, cmds = proc_table()                       # closing panes changed the tree
    for pid, vic, mb, cmd, why in plan_procs(table, cmds, now):
        if not dry:
            kill_pids(vic, {v: table[v]["ctime"] for v in vic})
        note("KILL-PROC", f"pid={pid} mb={mb} cmd={cmd[:160]!r} reason={why}")
        procs_n += len(vic)
        mb_total += mb

    krows, ghosts = plan_keepers(table, cmds, sessions_b, now)
    for pid, vic, mb, cmd, page, why in krows:
        if not dry:
            kill_pids(vic, {v: table[v]["ctime"] for v in vic})
            unregister_keeper(page)
        note("KILL-KEEPER", f"pid={pid} mb={mb} cmd={cmd[:160]!r} reason={why}")
        procs_n += len(vic)
        mb_total += mb
    for page in ghosts:
        if not dry:
            unregister_keeper(page)
        note("UNREGISTER-KEEPER", f"page={page!r} reason=Bridge page closed, keeper already dead")

    chrome = chrome_instances(table, cmds, now)
    for r in chrome:
        if r["heavy"] and r["port"]:
            r["tabs"] = chrome_tab_count(r["port"])
    seen = chrome_seen_load()
    for line in chrome_lines(chrome, seen):
        print(line, flush=True)
    if not dry:
        for r in chrome_new_flags(chrome, seen):
            log_line(log, chrome_lines([r])[1].strip())
        chrome_seen_save({k for r in chrome for k in chrome_keys(r)})

    summary = f"reaped {panes_n} panes, {procs_n} procs, {mb_total} MB"
    print(("would have " + summary[len("reaped "):] if dry else summary), flush=True)
    if not dry:
        log_line(log, "SUMMARY " + summary)
    return panes_n, procs_n, mb_total


def state_load():
    try:
        with open(STATE_PATH, encoding="utf-8") as fh:
            d = json.load(fh)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--auto", action="store_true", help="scheduled mode: pace itself (10 min, or 2 min on low RAM)")
    ap.add_argument("--older", default=None, help=f"pane idle age; default {OLDER}, {OLDER_LOW} on low RAM")
    ap.add_argument("--log", default=LOG_PATH)
    a = ap.parse_args(argv)
    if os.path.exists(OFF_PATH):
        print("reaper: off switch present, skipping", flush=True)
        return
    gb = free_gb()
    run, low = should_run(state_load(), time.time(), gb, force=not a.auto)
    if not run:
        return
    older = a.older or (OLDER_LOW if low else OLDER)
    print(f"reaper: {gb:.1f} GB free{' (LOW)' if low else ''}, panes idle over {older}", flush=True)
    if not a.dry_run:
        os.makedirs(L.BOARD_DIR, exist_ok=True)
        with open(STATE_PATH, "w", encoding="utf-8") as fh:
            json.dump({"last_run": time.time()}, fh)
    run_pass(older, a.dry_run, a.log, quiet=a.auto)


if __name__ == "__main__":
    main()
