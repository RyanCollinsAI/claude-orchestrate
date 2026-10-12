"""Shared helpers for the orchestrate skill: config, registry, herdr, log reading, stop-reason
classifier, the Chrome lock.

Imported by orch.py, watch_sessions.py, board.py, fix_mode.py. Keep every rule that both the board
and the watcher need in here so the two can never disagree about what DONE means.

Nothing personal is hardcoded. `config.json` next to this skill supplies the machine-specific
values; every one of them has a default derived from `~/.claude` and the current directory, so the
skill runs on a fresh clone with no config file at all. See `config.example.json`.
"""
import contextlib, datetime, glob, json, os, platform, re, subprocess, sys, threading, time

SKILL = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CLAUDE_HOME = os.environ.get("CLAUDE_CONFIG_DIR") or os.path.join(os.path.expanduser("~"), ".claude")
CONFIG_PATH = os.path.join(SKILL, "config.json")
WINDOWS = platform.system() == "Windows"


def _read_config():
    try:
        with open(CONFIG_PATH, encoding="utf-8") as fh:
            d = json.load(fh)
    except (OSError, ValueError):
        d = {}
    return d if isinstance(d, dict) else {}


CONFIG = _read_config()


def cfg(key, default=None):
    """A config value, with the environment able to override it (ORCH_<KEY>)."""
    v = os.environ.get("ORCH_" + key.upper())
    if v:
        return v
    v = CONFIG.get(key)
    return default if v in (None, "") else v


def project_slug(path):
    """Claude Code's log-directory name for a working directory: every `:`, `\\` and `/` becomes a
    `-`. `C:\\Users\\me\\Work` -> `C--Users-me-Work`."""
    s = os.path.abspath(path)
    for ch in (":", "\\", "/"):
        s = s.replace(ch, "-")
    return s


DEFAULT_CWD = os.path.abspath(cfg("default_cwd", os.getcwd()))
PROJECTS_DIR = cfg("projects_dir", os.path.join(CLAUDE_HOME, "projects"))
LOGS = os.path.join(PROJECTS_DIR, project_slug(DEFAULT_CWD))
SESS = cfg("sessions_dir", os.path.join(CLAUDE_HOME, "sessions"))
HANDOFFS = cfg("handoffs_dir", os.path.join(CLAUDE_HOME, "handoffs"))
BOARD_DIR = cfg("board_dir", os.path.join(SKILL, "board"))
TEMPLATES = os.path.join(SKILL, "templates")
SETTINGS = os.path.join(CLAUDE_HOME, "settings.json")

# Claude Code names a session `<cwd-basename-lowercased>-xx`, so that is the default prefix.
SESSION_PREFIX = cfg("session_prefix", os.path.basename(DEFAULT_CWD).lower())
ORCHESTRATOR_TAB = cfg("orchestrator_tab", "orchestrator").lower()
ACCOUNTS_TOOL = cfg("accounts_tool", "")       # empty = account switching is off

MODELS = {
    "fable": "claude-fable-5-1",
    "opus": "claude-opus-5-5",
    "sonnet": "claude-sonnet-5-5",
    "haiku": "claude-haiku-5-5",
}

# Over-calling QUESTION is cheap (the watcher says "waiting on you" and reap skips it); calling a
# waiting session DONE is what loses work, so these lists lean wide.
QUESTION_MARKS = ("do you want", "which one", "which of", "your call", "let me know",
                  "should i ", "say ", "confirm before", "pick one", "tell me ", "fill in ",
                  "reply with", "answer me", "waiting on", "waiting for", "need you to",
                  "if you want", "if you'd rather", "or i can")
OFFER_MARKS = ("i can ", "want me to", "shall i ", "i could ", "happy to ")
ERROR_MARKS = ("traceback", "error", "failed", "exception", "could not")

# A session that hits the permission classifier says so in a stable phrase. That is not an error to
# report and forget - it is a one-line command a human has to paste, so it goes on the board.
BLOCKED_MARKS = ("blocked by the auto mode classifier", "auto mode classifier",
                 "classifier blocked", "requires approval to run")

# classify_words()'s QUESTION_MARKS deliberately leans wide - "waiting on"/"waiting for"/"let me
# know" catch a real question buried mid-message, and that's fine for `ls`'s one-line stop-reason
# where a false positive costs nothing. Auto-posting to the board is not that cheap: three
# non-questions ("I am holding for go", "nothing to do") landed on it in one hour (2026-09-04)
# because a builder's routine status update happened to contain one of those phrases. Narrower on
# purpose - a literal question mark, or an unambiguous ask for a decision - for that one call site.
DECISION_MARKS = ("do you want", "which one", "which of", "your call", "should i ",
                  "confirm before", "pick one", "need you to", "if you'd rather")


def looks_like_a_real_question(text):
    """True only for a genuine question worth interrupting a human for - used solely to gate an
    auto-post to the board, never `classify()`/`classify_words()` itself."""
    return "?" in text or any(k in text.lower()[-600:] for k in DECISION_MARKS)


# ---------------------------------------------------------------- herdr

def kill_pane_tree(pane):
    """Kill the process tree under a pane's shell before the pane is closed.

    herdr's `pane close` hangs the pane up, waits ~0.8s for the tree to exit, then force-kills it,
    and the server does all of that on its one loop thread: every client (keystrokes, pane
    switches, `herdr` CLI calls) freezes for that long, per pane. A claude pane's tree is ~11
    processes and about a third of closes hit the slow path (measured 2026-09-30: 6 closes, 2
    took 830ms and stalled a probe call 780ms; after this pre-kill 6 of 6 took ~15ms, no stall).
    Best effort: any failure just falls through to the plain close.
    """
    try:
        info = herdr("pane", "process-info", "--pane", pane, soft=True, timeout=10)
        pid = ((info or {}).get("result") or {}).get("process_info", {}).get("shell_pid")
        if pid:
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)],
                           capture_output=True, timeout=15)
    except Exception:
        pass


_HERDR_EXE = None


def herdr_exe():
    """Path to herdr.exe. PATH first; over SSH `Programs\\Herdr\\bin` is a junction an sshd logon cannot
    see through, so `herdr` is not found and every pane looked gone (2026-10-10). Fall back to the real
    newest release folder, the same lookup as tools\\herdr-remote.ps1."""
    global _HERDR_EXE
    if _HERDR_EXE is None:
        import shutil
        found = shutil.which("herdr")
        if not found:
            rel = glob.glob(os.path.join(os.path.expanduser("~"), ".herdr", "packages", "standalone",
                                         "releases", "*", "herdr.exe"))
            found = max(rel, key=os.path.getmtime) if rel else "herdr"
        _HERDR_EXE = found
    return _HERDR_EXE


FOCUS_NEUTRAL = {("pane", "close"), ("pane", "move"), ("pane", "split"), ("workspace", "create"),
                 ("workspace", "close"), ("tab", "create"), ("tab", "close")}


def focused_view():
    """(workspace_id, tab_id) of the pane the human's herdr client is on, or None."""
    try:
        r = subprocess.run([herdr_exe(), "pane", "list"], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=10)
        panes = json.loads(r.stdout)["result"]["panes"]
        p = next((p for p in panes if p.get("focused")), None)
        return (p["workspace_id"], p["tab_id"]) if p else None
    except Exception:
        return None


def restore_focused_view(view):
    """Put the human's view back on `view` if a call moved it. Best effort, never raises.

    herdr moves the client to another workspace on some closes and moves (2026-10-01 server log:
    `workspace.focus` on the dying workspace inside a `pane.close`, which left Ryan's view off the
    orchestrator tab). `--no-focus` does not cover that, so every call that can do it is wrapped.
    """
    if not view:
        return
    now = focused_view()
    if now is None or now == view:
        return
    try:
        for argv in (["workspace", "focus", view[0]], ["tab", "focus", view[1]]):
            subprocess.run([herdr_exe(), *argv], capture_output=True, timeout=10)
    except Exception:
        pass


_PROC_PS = ("[Console]::OutputEncoding = [Text.Encoding]::UTF8; "
            "Get-CimInstance Win32_Process | ForEach-Object { [pscustomobject]@{ pid=[int]$_.ProcessId; "
            "ppid=[int]$_.ParentProcessId; name=$_.Name; "
            "ctime=($_.CreationDate.ToUniversalTime().Ticks - 621355968000000000) / 1e7; "
            "rss=[int64]$_.WorkingSetSize; cmd=$_.CommandLine } } | ConvertTo-Json -Compress")


def proc_snapshot():
    """({pid: {ppid, name, ctime, rss}}, {pid: command line}) for every process, in ONE call.

    psutil.process_iter with create_time / memory_info took 10-40 s for ~1000 processes on this box
    (2026-10-07, measured five times; reaper.py --dry-run took 41 s), and the reaper runs every 2
    minutes while RAM is low. One CIM query takes 0.5 s. Falls back to psutil if PowerShell fails."""
    try:
        r = subprocess.run(["pwsh", "-NoProfile", "-NonInteractive", "-Command", _PROC_PS],
                           capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        rows = json.loads(r.stdout)
        if isinstance(rows, dict):
            rows = [rows]
        if len(rows) < 20:
            raise ValueError("implausibly short process list")
        table = {p["pid"]: {"ppid": p["ppid"], "name": p["name"] or "", "ctime": p["ctime"],
                            "rss": p["rss"] or 0} for p in rows}
        return table, {p["pid"]: p["cmd"] or "" for p in rows}
    except Exception:
        import psutil
        table, cmds = {}, {}
        for p in psutil.process_iter(["pid", "ppid", "name", "create_time", "cmdline", "memory_info"]):
            i = p.info
            table[i["pid"]] = {"ppid": i["ppid"] or 0, "name": i["name"] or "",
                               "ctime": i["create_time"] or 0,
                               "rss": (i["memory_info"].rss if i["memory_info"] else 0)}
            cmds[i["pid"]] = subprocess.list2cmdline(i["cmdline"] or [])
        return table, cmds


def herdr(*args, timeout=90, soft=False):
    """Run herdr and return parsed JSON. soft=True returns None instead of exiting.

    Calls in FOCUS_NEUTRAL hand the human's focused workspace and tab back if herdr moved them.
    `pane move --focus` is the one deliberate exception (`orch.py show`)."""
    if tuple(args[:2]) in FOCUS_NEUTRAL and "--focus" not in args:
        view = focused_view()
        try:
            return herdr_raw(*args, timeout=timeout, soft=soft)
        finally:
            restore_focused_view(view)
    return herdr_raw(*args, timeout=timeout, soft=soft)


def herdr_raw(*args, timeout=90, soft=False):
    if tuple(args[:2]) == ("pane", "close") and len(args) > 2:
        kill_pane_tree(args[2])
    try:
        r = subprocess.run([herdr_exe(), *args], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=timeout)
    except Exception as e:
        if soft:
            return None
        sys.exit(f"herdr {' '.join(args)} crashed: {e}")
    out = (r.stdout or "").strip()
    err = (r.stderr or "").strip()
    # herdr prints its {"error": {...}} JSON on stderr. Swallowing it as None cost six spawns and
    # twelve minutes on 2026-09-07: `agent start orchestrator` failed with agent_name_taken and
    # the caller only ever saw "claude did not come up in pane w3:p78: None".
    for text in (out, err):
        if not text:
            continue
        try:
            return json.loads(text)
        except ValueError:
            continue
    if soft:
        return None
    sys.exit(f"herdr {' '.join(args)} failed: {out or err}")


def agent_names():
    """{pane_id: herdr agent name} for every pane `herdr agent start` registered a name on. A pane
    started by hand (the human typed `claude` into a shell) has no entry here."""
    d = herdr("agent", "list", soft=True) or {}
    return {a["pane_id"]: a["name"] for a in d.get("result", {}).get("agents", [])
            if a.get("name") and a.get("pane_id")}


def free_agent_name(label):
    """`label` if no live herdr agent holds it, else the first free `label-2`, `label-3`, ...
    herdr refuses `agent start` with a name a live agent already owns (agent_name_taken)."""
    taken = set(agent_names().values())
    if label not in taken:
        return label
    for n in range(2, 50):
        cand = f"{label}-{n}"
        if cand not in taken:
            return cand
    return f"{label}-{int(time.time()) % 100000}"


def pane_read(pane, lines=30, source="visible", fmt=None):
    r = subprocess.run([herdr_exe(), "pane", "read", pane, "--lines", str(lines), "--source", source]
                       + (["--format", fmt] if fmt else []),
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    return r.stdout or ""


def send_keys(pane, *k):
    subprocess.run([herdr_exe(), "pane", "send-keys", pane, *k], capture_output=True)


def panes():
    d = herdr("pane", "list", soft=True) or {}
    return d.get("result", {}).get("panes", [])


def bg_tasks(p):
    """(count, text) of the background tasks a pane's bg-visible plugin reported, e.g.
    (2, "2 bg: pytest, render"). (0, "") when it reported none. The plugin writes herdr pane
    tokens `bgn` and `bg` with a TTL, so a pane that died stops claiming tasks by itself."""
    t = (p or {}).get("tokens") or {}
    try:
        n = int(t.get("bgn") or 0)
    except ValueError:
        n = 0
    return (n, t.get("bg") or f"{n} bg") if n > 0 else (0, "")


def effective_status(p):
    """The pane's herdr agent_status, except an idle/done pane with live background tasks is
    "working": its seat looks idle while a Monitor, a test run or a render runs."""
    st = (p or {}).get("agent_status", "-")
    return "working" if st in ("idle", "done") and bg_tasks(p)[0] else st


def tabs():
    d = herdr("tab", "list", soft=True) or {}
    return d.get("result", {}).get("tabs", [])


def panes_by_sid():
    """{claude sessionId: herdr pane}. An ACP pane's child claude is a real registry session, but
    herdr never links its session to the pane (measured live, A1 review), so `agent_session` is empty
    there. The client's state file (found by the sidecar's persistent `acp_key`) names the child's
    sessionId; map that to the live ACP pane too, only while the client itself is live.
    A caller that TYPES into the pane must still skip `acp_pane_ids()` - the terminal is the client's."""
    live = panes()
    out = {}
    for p in live:
        s = p.get("agent_session") or {}
        if s.get("value"):
            out[s["value"]] = p
    side = {pane: e for pane, e in sidecar_load().items() if e.get("kind") == "acp"}
    if side:
        by_pane = {p.get("pane_id"): p for p in live}
        for pane in side:
            st = acp_state(pane, side)         # a stopped or dead client maps nothing
            if st and st.get("sessionId") and pane in by_pane:
                out.setdefault(st["sessionId"], by_pane[pane])
    return out


def tab_label(tab_id):
    return next((t.get("label", "") for t in tabs() if t.get("tab_id") == tab_id), "")


def workspaces():
    d = herdr("workspace", "list", soft=True) or {}
    return d.get("result", {}).get("workspaces", [])


def workspace_label(workspace_id):
    return next((w.get("label", "") for w in workspaces() if w.get("workspace_id") == workspace_id), "")


def find_workspace(ref):
    """The workspace matching `ref` - a bare id ("w5") or a label, case-insensitive - or None.
    `--workspace` and `space <mate>` both take either form (Ryan 2026-09-23: give each domain
    mate its own herdr workspace, labeled with the mate's name, instead of one shared space)."""
    if not ref:
        return None
    ws = workspaces()
    hit = next((w for w in ws if w.get("workspace_id") == ref), None)
    if hit:
        return hit
    return next((w for w in ws if w.get("label", "").lower() == ref.lower()), None)


def ensure_workspace(label, cwd, env_args=()):
    """The workspace labeled `label`: reused if one already exists (so a respawned mate keeps its
    space), else created at `cwd`. -> (workspace_id, root_pane_or_None).

    `workspace create` always makes a root pane for its first tab (herdr's own agent-guide:
    "`workspace create` returns `.result.workspace`, `.result.tab`, and `.result.root_pane`").
    That pane is handed back only when this call just created the workspace, so the caller can run
    the new agent IN it instead of leaving it as a stray empty shell and opening a second pane
    beside it - or close it, when there is no agent to put there (`space` moving existing panes).
    """
    w = find_workspace(label)
    if w:
        return w["workspace_id"], None
    r = herdr("workspace", "create", "--cwd", cwd, "--label", label, "--no-focus", *env_args)
    res = (r or {}).get("result", {})
    return res["workspace"]["workspace_id"], (res.get("root_pane") or {}).get("pane_id")


SEAT_FILE = os.path.join(BOARD_DIR, "orchestrator.json")


def orchestrator_seat():
    """The seat recorded by `orch.py seat`, or {} when none is set or its pane is gone.

    The tab-label scan below can only ever find a CLAUDE pane: it requires `agent_session.value`,
    and a pi pane has none (its sidecar `sid` is always ""). So a pi seat was unrepresentable, and
    `orchestrator_name()` fell back to the bare session prefix - which is the name every spawned
    agent is told speaks for Ryan. This file is the explicit answer, harness-agnostic, and it
    survives the pane rename that an account autoswitch causes (see `pane_current`).

    Self-healing: a seat whose pane is no longer live is ignored rather than returned, so a stale
    file degrades to the old behaviour instead of pointing every agent at a dead pane.
    """
    try:
        with open(SEAT_FILE, encoding="utf-8") as fh:
            d = json.load(fh)
    except (OSError, ValueError):
        return {}
    if not isinstance(d, dict) or not d.get("pane"):
        return {}
    return d if d["pane"] in {p.get("pane_id") for p in panes()} else {}


def orchestrator_pane():
    """Pane id of the orchestrator seat, whichever harness holds it, or None."""
    seat = orchestrator_seat()
    if seat:
        return seat["pane"]
    sid = orchestrator_sid()
    p = panes_by_sid().get(sid) if sid else None
    return p.get("pane_id") if p else None


def orchestrator_sid():
    """Session id of the pane sitting in the tab labelled with `orchestrator_tab`.

    An explicitly recorded seat wins. A pi seat has no session id, so this correctly returns None
    for one - callers that need to ADDRESS the seat want `orchestrator_pane()`, and callers that
    are excluding it from the claude registry (reap, the watcher) are right to get None, because a
    pi pane is not in that registry to begin with.
    """
    seat = orchestrator_seat()
    if seat:
        return seat.get("sid") or None
    ids = {t["tab_id"] for t in tabs() if t.get("label", "").lower() == ORCHESTRATOR_TAB}
    found = []
    for p in panes():
        label = (p.get("label") or "").lower()
        if p.get("tab_id") in ids and not label.startswith("orch-"):
            s = p.get("agent_session") or {}
            if s.get("value"):
                title = (p.get("terminal_title_stripped") or "").lower()
                found.append((label == ORCHESTRATOR_TAB, title == ORCHESTRATOR_TAB, s["value"]))
    if not found:
        return None
    # Two sessions can share the tab and often the label; the live orchestrator is the one whose
    # session title is the tab label too. A pane labelled `orch-retiring` is skipped above.
    # Taking the first pane named the wrong seat in four task files (2026-09-03).
    found.sort(key=lambda t: (not t[0], not t[1]))
    return found[0][2]


NOISE = os.path.join(BOARD_DIR, "noise.json")


def noise_gate(key, threshold=3, window=1800, path=None):
    """Swallow a routine line until it has repeated `threshold` times inside `window` seconds.

    Ryan picked this (screen audit 0913, answer E): RELOAD, REAP and LAYOUT are housekeeping. One
    of them is never something he acts on, so it is pure noise on his screen - but the SAME one
    firing over and over means something is stuck, and that he does want. So the first hits are
    silent and the one that crosses the threshold speaks, carrying the count.

    Returns (say_it, count). State lives in board/noise.json so a watcher restart does not reset
    every counter to zero and hand him the whole backlog again. Never raises: a corrupt or
    unwritable file degrades to "say it", because losing a real alarm is worse than one noisy line.
    """
    path = path or NOISE
    now = time.time()
    try:
        with open(path, encoding="utf-8") as fh:
            d = json.load(fh)
        if not isinstance(d, dict):
            d = {}
    except (OSError, ValueError):
        d = {}
    hits = [t for t in (d.get(key) or []) if isinstance(t, (int, float)) and t > now - window]
    hits.append(now)
    say = len(hits) >= threshold
    d[key] = [] if say else hits          # speaking resets the window, so it is not a stuck siren
    d = {k: v for k, v in d.items() if v}
    try:
        os.makedirs(BOARD_DIR, exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(d, fh)
    except OSError:
        return True, len(hits)
    return say, len(hits)


def pane_current():
    """The pane this command is running inside, straight from herdr, or {}.

    The only seat resolution that cannot go stale. `orchestrator_sid()` finds the seat by TAB
    LABEL, so an account autoswitch that restarts and renames the seat's pane leaves it returning
    None, and a command that trusted it silently did nothing (2026-09-14). A command that acts on
    the pane it is running in should ask herdr which pane that is, not infer it from a label
    somebody else can rename.
    """
    r = herdr("pane", "current") or {}
    return ((r.get("result") or {}).get("pane")) or {}


def orchestrator_name():
    """Display name of the orchestrator seat; falls back to the bare session prefix.

    This is the name every spawned agent is told speaks for Ryan (`orch.py` report_rule), so it
    must answer for a pi seat too - hence the explicit seat first.
    """
    seat = orchestrator_seat()
    if seat.get("label"):
        return seat["label"]
    sid = orchestrator_sid()
    if sid:
        for m in sessions().values():
            if m.get("sessionId") == sid:
                return m.get("name") or SESSION_PREFIX
    return SESSION_PREFIX


# ---------------------------------------------------------------- registry

# Claude Code owns `<SESS>/<pid>.json` and rewrites the whole object every status change, so an
# `owner` key written in there is gone within seconds. The owner column lives in our own file
# instead and is spliced into `sessions()` below, so every caller reads it as a registry field.
OWNERS = os.path.join(BOARD_DIR, "owners.json")


def owners_load():
    try:
        with open(OWNERS, encoding="utf-8") as fh:
            d = json.load(fh)
    except (OSError, ValueError):
        return {}
    return d if isinstance(d, dict) else {}


def set_owner(sid, name, owner, domain="", workspace=None, spawned=None):
    """Record who assigned work to session `sid`. Rows for sessions that are gone are pruned here,
    so the file never outgrows the live board.

    `domain` is the plain-language subject a session OWNS - "every ChannelA pipeline change",
    "everything Ryan is learning". A session with one is a **domain mate**: it outlives the task it
    was spawned for, `reap` leaves it alone, and `ls` prints its crew indented underneath it.
    Empty (the default) means an ordinary crew pane that dies when its work is done.
    Ryan's call 2026-09-14, memory `second-mate-layer-decision-2026-09-14`.

    `workspace` is the herdr workspace id this session's OWN spawn landed in - recorded so crew can
    resolve their mate's space by IDENTITY (this field) rather than by label match, which two
    mates sharing a `--group` would collide on (2026-09-23 review). `None` (the default) leaves
    whatever was already recorded alone, so `own`'s re-parent/promote calls - which never pass a
    workspace - do not silently erase it; pass `""` to explicitly clear it.

    `spawned` is True only when `cmd_spawn` itself is the caller - it is what `auto_close_candidates`
    reads to tell "this pane came up through orch.py" from "this row exists because `own` later
    re-parented or promoted an already-live pane" (2026-09-24 review: `own` calls this on ANY live
    session, hand-opened ones included, and a bare owners.json row was being read as proof of
    orch.py spawning it, which it is not). `None` (the default, same shape as `workspace`) leaves
    whatever was already recorded alone - so a mate `own` later re-parents keeps its true spawned
    status, and a hand-opened pane `own` touches for the first time gets a row that correctly reads
    as never-spawned rather than defaulting to spawned.
    """
    d = owners_load()
    prev = d.get(sid) or {}
    row = {"name": name, "owner": owner, "domain": domain, "ts": int(time.time())}
    ws = prev.get("workspace", "") if workspace is None else workspace
    if ws:
        row["workspace"] = ws
    sp = prev.get("spawned", False) if spawned is None else spawned
    if sp:
        row["spawned"] = True
    d[sid] = row
    live = {m.get("sessionId") for m in _registry().values()} | {sid}
    d = {k: v for k, v in d.items() if k in live}
    os.makedirs(BOARD_DIR, exist_ok=True)
    with open(OWNERS, "w", encoding="utf-8") as fh:
        json.dump(d, fh, indent=2)
    return d[sid]


def drop_owner(sid):
    d = owners_load()
    if d.pop(sid, None) is None:
        return
    with open(OWNERS, "w", encoding="utf-8") as fh:
        json.dump(d, fh, indent=2)


# ---------------------------------------------------------------- who is calling

# A CLAUDE pane is identified by `CLAUDE_CODE_SESSION_ID`, which maps to a registry row. A pi pane
# has no such id and no registry row at all, so until 2026-09-15 every one of the helpers below
# fell through to the orchestrator seat and a pi mate was invisible as a caller: its crew was
# recorded owner=<orchestrator>, and the four-rung cap in `cmd_spawn` could never fire from it.
#
# MEASURED 2026-09-15 in a live pi pane (`envprobe.py` run through pi's own shell tool): a command
# pi launches inherits `HERDR_PANE_ID` ("w3:pMC", exactly the pane `orch.py spawn` opened) and
# carries NO `CLAUDE_CODE_SESSION_ID` whatsoever. So the pane id IS a pi pane's identity, and the
# sidecar row keyed on it carries `label`, `owner` and `domain` (a pi row's `sid` is always "").
#
# Order matters: a claude pane's shell has BOTH variables, so the session id is tried first and
# the claude path is byte-for-byte what it always was.
_LIVE_PANES_CACHE = {"ts": 0.0, "ids": set()}


def live_pane_ids(max_age=5.0):
    """The pane ids herdr shows right now, cached for `max_age` seconds.

    `owner_is_live` and `session_domain` are called once per session per watcher poll (8 s) and
    each needs to know whether a sidecar row's pane is still open. Uncached that is one
    `herdr pane list` subprocess per session per poll; cached it is at most one per poll.
    Tests patch this rather than `panes()`, so the cache can never leak between them.
    """
    now = time.time()
    if now - _LIVE_PANES_CACHE["ts"] > max_age:
        _LIVE_PANES_CACHE.update(
            ts=now, ids={p["pane_id"] for p in panes() if p.get("pane_id")})
    return _LIVE_PANES_CACHE["ids"]


def live_sidecar_rows():
    """[(pane_id, entry)] for every sidecar row whose herdr pane is still open. A closed pane's
    row lingers until something prunes it, and a stale row must never answer for a live name."""
    live = live_pane_ids()
    return [(pane, e) for pane, e in sidecar_load().items() if pane in live]


def pi_mate(name):
    """The live sidecar row of a non-claude DOMAIN MATE called `name`, plus its pane, or None.

    This is what makes a footer owner-aware (GAP 3): a crew pane owned by one of these has no
    `SendMessage` address to answer on, because herdr never registers a pi pane as an agent here
    and peer messaging is keyed on a claude session id the row does not have.
    """
    for pane, e in live_sidecar_rows():
        if e.get("label") == name and e.get("domain") and e.get("kind") != "claude":
            return {**e, "pane": pane}
    return None


def caller_row():
    """("claude"|<other kind>|"", row) for the session running this command.

    A claude caller answers with its registry row (`name` key); a pi or codex caller answers with
    its sidecar row (`label` key). "" means the command came from outside any managed pane.
    """
    sid = os.environ.get("CLAUDE_CODE_SESSION_ID", "")
    if sid:
        m = _registry().get(sid)
        if m:
            return "claude", m
    pane = os.environ.get("HERDR_PANE_ID", "")
    if pane:
        e = sidecar_load().get(pane)
        if e:
            return e.get("kind") or "pi", e
    return "", {}


def owner_is_live(owner):
    """True while something with that display name is still open. An owner that has been killed or
    has crashed cannot be asked, so its panes stop being protected.

    Both registries, because a pi mate's name protects its panes too: the claude registry by
    `name`, and the sidecar (live panes only) by `label`."""
    if not owner:
        return False
    if any(m.get("name") == owner for m in _registry().values()):
        return True
    return any(e.get("label") == owner for _pane, e in live_sidecar_rows())


def caller_name():
    """Display name of the session running this command: its own registry row, else its sidecar
    row when it is a pi/codex pane, else the orchestrator seat, else the literal 'orchestrator'
    for a call from outside any session."""
    kind, row = caller_row()
    if kind == "claude" and row.get("name"):
        return row["name"]
    if kind and row.get("label"):
        return row["label"]
    return orchestrator_name() or "orchestrator"


def caller_is_seat():
    """True only when the command is running IN the orchestrator seat's own pane.

    Matched by SESSION IDENTITY - session id for a claude seat, pane id for a pi/other seat -
    against the explicitly recorded seat (`orch.py seat`, `board/orchestrator.json`), never by
    display name: a name can drift (an account-switch rename) or collide (two live sessions can
    share one), and either would let an impostor pass or refuse the real seat (2026-09-23 review).

    No seat recorded at all -> a caller with NO LIVE OWNER counts as the seat. A pane Ryan opened
    by hand (never spawned through `orch.py spawn`) has no owner row at all, and the fallback must
    not refuse the one command he is actually typing just because nobody has set a seat yet.

    Ryan 2026-09-23: 25% of a week's quota gone in about 2 hours to opus mates and opus crew - the
    `--model opus`/`fable` guard uses this to require the seat's own yes before anything but the
    seat reaches for the expensive tiers.
    """
    seat = orchestrator_seat()
    if seat:
        my_sid = os.environ.get("CLAUDE_CODE_SESSION_ID", "")
        my_pane = os.environ.get("HERDR_PANE_ID", "")
        if seat.get("sid"):
            return bool(my_sid) and my_sid == seat["sid"]
        return bool(my_pane) and my_pane == seat.get("pane", "")
    kind, row = caller_row()
    if not kind:
        return True
    if kind == "claude":
        sid = os.environ.get("CLAUDE_CODE_SESSION_ID", "")
        owner = (owners_load().get(sid) or {}).get("owner", "")
    else:
        owner = row.get("owner", "")
    return not owner


def session_domain(name):
    """The subject the live session called `name` owns, or "" - used by the four-rung cap when the
    mate in question is not the caller (`orch.py own`). Empty for a name nothing live answers to.

    Reads both registries: a claude mate's domain is spliced into `sessions()` from owners.json,
    a pi mate's lives in its sidecar row because it has no registry entry to splice into."""
    d = next((m.get("domain", "") for m in sessions().values() if m.get("name") == name), "")
    if d:
        return d
    return next((e.get("domain", "") for _pane, e in live_sidecar_rows()
                 if e.get("label") == name and e.get("domain")), "")


def caller_domain():
    """The subject the session running this command owns, or "" if it owns none.

    A claude caller is read from `owners.json` by session id, NOT by name: two live sessions can
    share a display name, and the cap this feeds (a mate never spawns a mate) must not fire on the
    wrong one. A pi caller is read from its own sidecar row, keyed on the pane id, which is unique
    by construction. A call from outside any pane has neither and therefore no domain, which is
    correct - the orchestrator seat is the root and is allowed to create mates.
    """
    sid = os.environ.get("CLAUDE_CODE_SESSION_ID", "")
    if sid:
        return (owners_load().get(sid) or {}).get("domain", "")
    pane = os.environ.get("HERDR_PANE_ID", "")
    if pane:
        return (sidecar_load().get(pane) or {}).get("domain", "")
    return ""


def mate_workspace_id(name):
    """The herdr workspace id recorded for the live domain mate called `name`, or "" when it has
    none yet (never migrated with `space`) or `name` is not a mate at all.

    Looked up by IDENTITY - the row THIS mate's own spawn (or a later `space`) wrote - never by
    label match: two mates can share a `--group` label, and a label search would hand one mate's
    space to the other's crew (2026-09-23 review).
    """
    for m in sessions().values():
        if m.get("name") == name and m.get("domain"):
            return m.get("workspace", "")
    for _pane, e in live_sidecar_rows():
        if e.get("label") == name and e.get("domain"):
            return e.get("workspace", "")
    return ""


def workspace_claimed_by(ws_id):
    """The live domain mate's name that already owns workspace `ws_id`, or "" if none does.

    A brand-new workspace `ensure_workspace` just created is never claimed (nobody's spawn has run
    yet), so this only fires when a label match handed back a space some OTHER live mate already
    recorded as its own - the collision `_spawn_workspace` refuses rather than silently merging two
    mates into one space (2026-09-23 review).
    """
    if not ws_id:
        return ""
    for m in sessions().values():
        if m.get("domain") and m.get("workspace") == ws_id:
            return m["name"]
    for _pane, e in live_sidecar_rows():
        if e.get("domain") and e.get("workspace") == ws_id:
            return e.get("label", "")
    return ""


def owner_harness(owner):
    """"pi"|"codex"|"" for a live pi/codex pane named `owner` - mate or ordinary crew alike. ""
    means `owner` is a live claude session, or not live at all.

    `pi_mate()` answers a narrower question (a live non-claude DOMAIN MATE); this one exists
    because an ordinary pi/codex crew pane that itself owns further crew has the exact same
    SendMessage-less problem and was falling through to the claude-owner wording, which offers a
    SendMessage path that does not exist for it (2026-09-23 review, item 7).
    """
    if not owner:
        return ""
    for _pane, e in live_sidecar_rows():
        if e.get("label") == owner and e.get("kind") != "acp":   # an ACP pane is a claude session
            return e.get("kind") or "pi"
    return ""


def sidecar_rekey(old_pane, new_pane):
    """Move a non-claude pane's sidecar row from `old_pane` to `new_pane`.

    A pane moved across herdr workspaces is handed a NEW workspace-qualified id (see traps.md). The
    sidecar is keyed on pane id, so a row left under the dead one makes `agent_pane_for`,
    `other_panes`, and every ownership check blind to that pane from the move onward - measured
    after `space`/`show` started crossing mates between workspaces (2026-09-23 review, item 5).
    A no-op when the id did not actually change, or there was no row to move.
    """
    if not new_pane or new_pane == old_pane:
        return
    d = sidecar_load()
    row = d.pop(old_pane, None)
    if row is None:
        return
    d[new_pane] = row
    os.makedirs(BOARD_DIR, exist_ok=True)
    with open(AGENT_SIDECAR, "w", encoding="utf-8") as fh:
        json.dump(d, fh, indent=2)


def _registry():
    """`sessions()` without the owner splice - the raw registry. Used by the owner helpers so they
    can never recurse through `sessions()` back into themselves."""
    out = {}
    for f in glob.glob(os.path.join(SESS, "*.json")):
        try:
            m = json.load(open(f, encoding="utf-8"))
        except Exception:
            continue
        sid = m.get("sessionId")
        if not sid:
            continue
        m.setdefault("name", "?" + sid[:6])
        m.setdefault("status", "?")
        m["_file"] = f
        if sid in out and out[sid].get("updatedAt", 0) > m.get("updatedAt", 0):
            continue    # a resume keeps the sessionId: the newest file is the live process
        out[sid] = m
    import ghostrows
    return ghostrows.prune(out, panes_by_sid)


def sessions():
    """{sessionId: registry dict}. Never raises: a half-written file is skipped, not fatal.
    `owner` and `domain` are spliced in from `owners.json` (see OWNERS); both are "" for an
    unowned session and `domain` is "" for every ordinary crew pane."""
    out = _registry()
    own = owners_load()
    for sid, m in out.items():
        row = own.get(sid) or {}
        m["owner"] = row.get("owner", "")
        m["domain"] = row.get("domain", "")
        m["workspace"] = row.get("workspace", "")
    return out


def resolve(name):
    """Find one session by display name, session-id prefix, or pane id."""
    live = sessions()
    hits = [m for m in live.values() if m.get("name") == name]
    if not hits:
        hits = [m for m in live.values() if m["sessionId"].startswith(name)]
    if not hits:
        p = next((p for p in panes() if p.get("pane_id") == name), None)
        if p:
            sid = (p.get("agent_session") or {}).get("value")
            hits = [live[sid]] if sid in live else []
    if len(hits) > 1:
        by_sid = panes_by_sid()
        # A `--resume`/`--fork-session` copy keeps the name but has no pane. The one with a
        # pane is the live one, so prefer it over an "ambiguous" exit (2026-10-02 ghost row).
        paned = [m for m in hits if m["sessionId"] in by_sid]
        if len(paned) == 1:
            return paned[0]
        lines = "\n".join(
            f"  {m['name']}  sid={m['sessionId'][:8]}  pane={by_sid.get(m['sessionId'], {}).get('pane_id', '-')}"
            for m in hits)
        sys.exit(f"{name} matches {len(hits)} live sessions; pass a session-id prefix or pane id:\n{lines}")
    return hits[0] if hits else None


# ---------------------------------------------------------------- logs

def find_log(prefix, soft=False):
    """Path of the one .jsonl whose name starts with `prefix`, searched across every per-cwd log
    folder (a --cwd pane's log is not under LOGS). Exits with the candidates if ambiguous, unless
    `soft`, in which case a miss returns None and an ambiguous match returns the first hit - callers
    that must never raise (log_path, called on every `ls`) pass soft=True."""
    # Must match `.jsonl`, not the same-named subagent directory sitting beside it.
    hits = sorted(glob.glob(os.path.join(PROJECTS_DIR, "*", prefix + "*.jsonl")))
    if not hits:
        if soft:
            return None
        sys.exit(f"no log starting with {prefix!r} under {PROJECTS_DIR}")
    if len(hits) > 1 and len({os.path.basename(h) for h in hits}) > 1:
        if soft:
            return hits[0]
        sys.exit(f"{prefix!r} matches {len(hits)} logs; give more of the id:\n  " + "\n  ".join(hits))
    return hits[0]


_CWD_CACHE = {}


def log_path(sid):
    """The session's .jsonl. Claude Code files logs under a per-cwd folder, so a session spawned
    with --cwd elsewhere (the WebApp builders) is NOT under LOGS - read its cwd from the
    registry. Before this, `ls` showed ctx=0k and no model for every cross-cwd pane (2026-09-03).

    A registry row with no `cwd` (or one whose log isn't where that cwd predicts, e.g. an older
    Claude Code that wrote the field differently) falls through to `find_log`'s glob search across
    every per-cwd folder, so a session in another cwd is still found instead of reading as ctx=0k
    with a blank model - which `resume` was then reporting as DEAD for a perfectly live pane."""
    cwd = _CWD_CACHE.get(sid)
    if cwd is None:
        for m in sessions().values():
            if m.get("cwd"):
                _CWD_CACHE[m["sessionId"]] = m["cwd"]
        cwd = _CWD_CACHE.get(sid)
    if cwd:
        p = os.path.join(PROJECTS_DIR, project_slug(cwd), sid + ".jsonl")
        if os.path.exists(p):
            return p
    default = os.path.join(LOGS, sid + ".jsonl")
    if os.path.exists(default):
        return default
    return find_log(sid, soft=True) or default


def log_mtime(sid):
    try:
        return os.path.getmtime(log_path(sid))
    except OSError:
        return 0


def last_assistant_ts(sid, entries=None):
    """Epoch seconds of the newest assistant message in the log - not the file mtime, which every
    incoming peer message and idle-notice subscription also touches. Skips system/queue-operation
    rows by only matching type=="assistant"."""
    lines = tail_entries(sid) if entries is None else entries
    for l in reversed(lines):
        if '"assistant"' not in l:
            continue
        e = _load(l)
        if not e or e.get("type") != "assistant":
            continue
        ts = e.get("timestamp")
        if not ts:
            continue
        try:
            return datetime.datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
        except ValueError:
            continue
    return 0


TAIL_BYTES = 200000
# Read this much when the ordinary window turns out to hold no assistant record at all. 10x, and
# still bounded - the point is to cross one oversized block of `system` records, not to parse a
# whole transcript on a poll.
WIDE_TAIL_BYTES = 2000000


def _read_tail(path, nbytes):
    try:
        with open(path, "rb") as fh:
            fh.seek(0, 2)
            fh.seek(max(0, fh.tell() - nbytes))
            data = fh.read().decode("utf-8", "replace")
    except OSError:
        return []
    return [l for l in data.splitlines() if l.startswith("{")]


def tail_entries(sid, nbytes=TAIL_BYTES):
    """Raw log lines, newest last. Deliberately NOT parsed: the readers below scan backwards and
    json.loads only the handful of lines they care about. Parsing a 200KB window per session per
    poll made watch_sessions.py too slow to emit anything inside a minute (measured 2026-09-03).

    A window holding NO assistant record is widened once. This is a TAIL, and a session can write
    far more than 200KB after its last assistant turn - a fresh pane's `system` records are huge.
    Measured 2026-09-15 on pp-crew-2: it answered "READY", and that line sat 216,000 bytes from
    the end of a 391KB log, just outside the window. Everything downstream then read the pane as
    DONE with no words: `ls` printed a bare KIND column and a stop-reason line came out as
    `<name> DONE | ` with nothing behind the bar. The widen costs a second read only in that case,
    because the scan short-circuits on the first assistant line in the normal one.
    """
    path = log_path(sid)
    lines = _read_tail(path, nbytes)
    if any('"assistant"' in l for l in lines):
        return lines
    # Nothing classifiable in this window. Widen once - but only when the file really is bigger
    # than the window just read, or we already hold the whole log and a second read would return
    # exactly the same thing. NOT `if lines`: one oversized trailing record can fill the entire
    # window, leaving a single partial line that gets filtered out and no lines at all, which is
    # the same bug in its worst form rather than a different case.
    try:
        if os.path.getsize(path) <= nbytes:
            return lines
    except (OSError, TypeError):
        return lines
    wide = _read_tail(path, WIDE_TAIL_BYTES)
    return wide if len(wide) > len(lines) else lines


def _load(l):
    try:
        return json.loads(l)
    except Exception:
        return None


def last_usage(sid, entries=None):
    """(ctx in k, short model name) from the newest assistant usage block."""
    lines = tail_entries(sid) if entries is None else entries
    for l in reversed(lines):
        if '"usage"' not in l or '"assistant"' not in l:
            continue
        e = _load(l)
        if not e or e.get("type") != "assistant":
            continue
        u = e.get("message", {}).get("usage")
        if not u:
            continue
        ctx = (u.get("input_tokens", 0) + u.get("cache_read_input_tokens", 0)
               + u.get("cache_creation_input_tokens", 0)) // 1000
        return ctx, e["message"].get("model", "").replace("claude-", "")
    return 0, ""


def last_assistant_text(lines):
    for l in reversed(lines):
        if '"assistant"' not in l:
            continue
        e = _load(l)
        if not e or e.get("type") != "assistant":
            continue
        c = e.get("message", {}).get("content")
        if isinstance(c, list):
            t = " ".join(b.get("text", "") for b in c
                         if isinstance(b, dict) and b.get("type") == "text").strip()
        elif isinstance(c, str):
            t = c.strip()
        else:
            t = ""
        if t:
            return " ".join(t.split())
    return ""


def last_tool_error(lines):
    """True if the newest tool_result block in the log carried is_error."""
    for l in reversed(lines):
        if '"tool_result"' not in l:
            continue
        e = _load(l)
        m = e.get("message") if e else None
        c = m.get("content") if isinstance(m, dict) else None
        if not isinstance(c, list):
            continue
        for b in reversed(c):
            if isinstance(b, dict) and b.get("type") == "tool_result":
                return bool(b.get("is_error"))
    return False


def classify_words(text, tool_error=False):
    """The word rules alone: last words -> QUESTION|BLOCKED|OFFER|ERROR|DONE.

    Split out of `classify` so a pane-text agent (Codex, Gemini) is judged by exactly the same
    rules as a Claude session read from its log - the two can never drift apart.

    Order matters: BLOCKED outranks everything because it names an exact command a human must
    paste, and a question outranks an error mention, because the agent is waiting on a human
    either way and the question is what to read.
    """
    tail = text.lower()[-600:]      # how it ENDED; scanning the whole report calls every long
                                    # write-up an ERROR because it mentions the word once
    # A clean report SAYS the word: "lint 0 errors", "0 failed", "mutation: breaking it fails its
    # test". On 2026-09-09 the health routine nudged the orchestrator four times for a lead whose
    # last line was "typecheck 0, lint 0 errors". Strip the not-an-error shapes before matching.
    error_tail = re.sub(r"\b(0|no|zero|without)\s+(errors?|failures?|failed)\b", " ", tail)
    error_tail = re.sub(r"\b(errors?|failures?|failed)\s*[:=]\s*0\b", " ", error_tail)
    error_tail = re.sub(r"\b(fails?|failed|breaks?|kills?)\s+(its|their|the|both|that|each|only)\b"
                        r"[^.\n]{0,40}\btests?\b", " ", error_tail)
    # "failed" as an adjective on a thing ("one failed send does not stop the rest"), not an event.
    # "failed to ..." and "the build failed" stay real. 2026-10-04: a finished report was flagged ERROR.
    error_tail = re.sub(r"\b(a|an|one|any|each|every|no|per)\s+failed\s+(?!to\b)\w+", " ", error_tail)
    # The word as a NAME, not an event: error.tsx, error boundary, error page, error handling.
    error_tail = re.sub(r"\berrors?(\.[a-z]{1,4}|\s+(boundar(y|ies)|pages?|cards?|states?|handling|"
                        r"handlers?|messages?|codes?|types?|logs?))\b", " ", error_tail)
    if any(k in tail for k in BLOCKED_MARKS):
        return "BLOCKED"
    if text.rstrip().endswith("?") or any(k in tail for k in QUESTION_MARKS):
        return "QUESTION"
    if any(k in tail for k in OFFER_MARKS):
        return "OFFER"
    if tool_error or any(k in error_tail for k in ERROR_MARKS):
        return "ERROR"
    return "DONE"


def classify(sid, entries=None):
    """Why a session stopped -> (QUESTION|BLOCKED|OFFER|ERROR|DONE, last 300 chars it said).

    Only DONE is safe to auto-close.
    """
    entries = tail_entries(sid) if entries is None else entries
    text = last_assistant_text(entries)
    return classify_words(text, last_tool_error(entries)), text[-300:]


def blocked_command(text):
    """The one-line command a BLOCKED session needs a human to run, or ''.

    Prefers a fenced code block, then a backticked span that looks like a command. It gets pasted
    with the `!` prefix, so it has to survive verbatim."""
    import re
    fence = re.findall(r"```(?:\w+)?\s*\n(.+?)\n?```", text, re.S)
    for body in fence:
        line = body.strip().splitlines()[0].strip() if body.strip() else ""
        if line:
            return line
    for span in re.findall(r"`([^`\n]{4,200})`", text):
        s = span.strip()
        if s.split()[0] in ("gh", "git", "py", "python", "npm", "npx", "pwsh", "powershell",
                            "herdr", "vercel", "node", "curl", "code"):
            return s
    return ""


# ---------------------------------------------------------------- other agent kinds

# `herdr agent start --kind` accepts many agents; these are the ones this skill knows how to
# spawn and read. Anything other than `claude` has no `~/.claude/sessions` entry and no `.jsonl`,
# so its whole board row is built from herdr plus the text on its screen.
KINDS = ("claude", "codex", "pi", "grok", "agy", "cursor")

# Codex's own model tier. `~/.codex/models_cache.json` lists the newest as slug `gpt-6-astra`,
# display name "GPT-6-Astra", priority 1 - that is the "Astra" Ryan asks for. Override in
# config.json (`codex_model`) or with ORCH_CODEX_MODEL.
CODEX_MODEL = cfg("codex_model", "gpt-6-astra")

# pi (`@earendil-works/pi-coding-agent`) is the second harness: one CLI in front of every provider
# that is not Anthropic. Its default here is the same Astra tier, reached through Ryan's ChatGPT
# subscription rather than codex-cli - pi 0.85.1 added `gpt-6-astra` to the openai-codex provider.
# `pi --list-models` prints the live catalog; override in config.json (`pi_model`).
PI_MODEL = cfg("pi_model", "gpt-6-astra")

# Grok Build (xAI's own CLI) is the third harness. FULL PATH on purpose: Ryan's PowerShell profile
# defines `function grok { pi --model grok-4.6 @args }`, so the bare name in a pane is pi.
GROK_EXE = os.path.join(os.path.expanduser("~"), ".grok", "bin", "grok.exe")
GROK_MODEL = cfg("grok_model", "grok-4.7")

# Cursor Agent CLI is the fifth. NEVER the bare name `agent`: on this box that is Grok's agent.exe
# (C:\Users\YOURNAME\.grok\bin comes first in PATH). Effort lives inside the model id; there is no flag.
CURSOR_EXE = os.path.join(os.environ.get("LOCALAPPDATA", ""), "cursor-agent", "agent.cmd")
CURSOR_MODEL = cfg("cursor_model", "auto")

# One-shot harnesses: the pane runs ONE headless command that tees an NDJSON/JSON stream to
# `<report><suffix>`; done = a `"type":"result"` line in that file. No second prompt, no mate, no lead.
ONESHOT_SUFFIX = {"grok": ".ndjson", "cursor": ".json"}

# Which pi provider serves a model id, by prefix. pi takes `--provider X --model Y`, and getting
# the pair wrong is a silent fall-through to its own default provider, not an error.
PI_PROVIDERS = (("gpt-", "openai-codex"), ("o1", "openai"), ("o3", "openai"),
                ("claude-", "anthropic"), ("sonnet", "anthropic"), ("opus", "anthropic"),
                ("haiku", "anthropic"), ("kimi", "kimi-coding"), ("grok", "xai"),
                ("moonshot", "kimi-coding"), ("gemini", "google"))


def pi_provider(model):
    """The pi provider for a model id, or "" when nothing matches and pi's default should stand."""
    m = (model or "").lower()
    if "/" in m:                       # already `provider/id`, pi resolves it itself
        return m.split("/", 1)[0]
    return next((p for pre, p in PI_PROVIDERS if m.startswith(pre)), "")

# Where the report file and label for a non-claude pane are remembered. herdr already reports the
# agent kind on the pane itself, so this holds only what herdr cannot know - and, since 2026-09-06,
# everything needed to rebuild the pane after herdr dies (see codex_thread_id / sidecar_orphans).
AGENT_SIDECAR = os.path.join(BOARD_DIR, "agent-panes.json")

# codex-cli writes one `rollout-<stamp>-<uuid>.jsonl` per thread under here. Its first line is a
# `session_meta` record carrying the thread id, the cwd and where the thread came from.
CODEX_SESSIONS = os.path.join(os.path.expanduser("~"), ".codex", "sessions")


def agent_panes():
    """{pane_id: pane} for every herdr pane running a non-claude agent."""
    return {p["pane_id"]: p for p in panes()
            if p.get("agent") and p.get("agent") != "claude"}


# pi paints a spinner line while a turn is running: `── ⠸ Working ───...`. It is the only
# "this pane is busy" signal pi gives from outside, since herdr never tracks a pi pane's state
# here (see PI_NO_HERDR_AGENT in orch.py).
PI_WORKING_MARK = "working"

# The orch-pane extension (pi-extensions/orch-pane.ts in this skill, 2026-09-15) gives a pi pane a
# real door: it drops a state file here and drains an inbox of prompt files, so a live pane is
# prompted by file (multi-line, never flattened) and read from its state (never the screen).
# Both paths below fall back to the screen/typing when the extension is not running.
PI_ORCH_DIR = os.path.join(os.path.expanduser("~"), ".pi", "agent", "orch")
_PI_PIDS_CACHE = {"ts": 0.0, "pids": set()}


def pi_key(pane):
    """The extension's key for a pane: HERDR_PANE_ID with ':' -> '-' (w3:pK3 -> w3-pK3)."""
    return re.sub(r"[^A-Za-z0-9_-]", "-", pane)


def pi_state(pane, max_age=5.0):
    """The orch-pane state dict for a live pi pane, or None when the extension is not there.
    "Live" means the pid it recorded is a running pi TUI and it has not written `stopped`."""
    path = os.path.join(PI_ORCH_DIR, "state", f"{pi_key(pane)}.json")
    try:
        with open(path, encoding="utf-8") as fh:
            st = json.load(fh)
    except (OSError, ValueError):
        return None
    if st.get("state") == "stopped":
        return None
    now = time.time()
    if now - _PI_PIDS_CACHE["ts"] > max_age:
        _PI_PIDS_CACHE.update(ts=now, pids=pi_tui_pids())
    return st if st.get("pid") in _PI_PIDS_CACHE["pids"] else None


def pi_inbox_put(pane, text):
    """Queue one prompt for the extension to deliver as a real user message. -> file path."""
    d = os.path.join(PI_ORCH_DIR, "inbox", pi_key(pane))
    os.makedirs(d, exist_ok=True)
    name = f"{time.strftime('%Y%m%d-%H%M%S')}-{int((time.time() % 1) * 1000):03d}.md"
    path = os.path.join(d, name)
    # Write beside, then rename: the extension polls every 1.5 s and delivers whatever `.md` it
    # sees, so a file that is still being written would go out truncated and be filed as handled.
    # A `.tmp` name is invisible to it (review finding, 2026-09-15).
    with open(path + ".tmp", "w", encoding="utf-8") as fh:
        fh.write(text)
    os.replace(path + ".tmp", path)
    return path


def pi_inbox_depth(pane):
    """(queued, consumed) for a live pi pane, from the orch-pane state file. (0, 0) when the
    extension is not there.

    `delivered` alone could never tell the two apart: every inbox message goes out as a pi
    follow-up, so a successful send only proves pi ACCEPTED it - if a turn is running it waits in
    pi's queue until that turn ends. A mate reading `delivered` could not distinguish a crew
    report it had already answered from one still sitting behind the running turn, which is the
    difference between "my crew told me" and "my crew is about to tell me". `consumed` is counted
    by the extension on the next `agent_start`, the one moment pi has provably taken the input.
    (2026-09-15.)
    """
    st = pi_state(pane) or {}
    try:
        return int(st.get("queued") or 0), int(st.get("consumed") or 0)
    except (TypeError, ValueError):
        return 0, 0


def pi_status(pane):
    """"working", "idle", or "dead" for a pi pane.

    The extension's state file when it is live, else pi's own spinner line off the screen.

    "dead" matters: a pi TUI that has exited leaves a bare shell prompt in the pane, which has no
    spinner, so the screen fallback read it as "idle" - and an idle mate is a HEALTHY mate that
    `reap` deliberately never closes. On 2026-09-16 `pi-mate` sat on the board as a healthy domain
    mate with no pi process on the box at all. Same shape as every other bug this week: the
    fallback worked, so the dead thing behind it looked fine. No state file AND no pi TUI running
    is not idle - there is nothing there to be idle.
    """
    st = pi_state(pane)
    if st:
        return "working" if st.get("state") == "working" else "idle"
    if not pi_tui_pids():
        return "dead"
    tail = pane_read(pane, 8).lower()
    return "working" if PI_WORKING_MARK in tail else "idle"


# ---------------------------------------------------------------- ACP panes (A1, 2026-10-09)
#
# An ACP pane (`spawn --acp`) runs scripts/acp_pane.py in its herdr pane; that process owns a
# claude-agent-acp child and talks to it in JSON lines (acp/DESIGN.md, references/acp.md). It is a
# real claude session, so it has a registry row, but the sidecar row (kind "acp") is what marks it
# as ACP. The three file contracts below mirror the pi extension's (state file, inbox, log), so
# `prompt`, `ls`, `kill` and the watchdog read DATA instead of the screen.
ACP_DIR = cfg("acp_dir", os.path.join(SKILL, "state", "acp"))
ACP_OFF = os.path.join(BOARD_DIR, "acp.off")            # the kill switch: its presence refuses `--acp`
ACP_POLICY = os.path.join(BOARD_DIR, "acp-policy.json")


def acp_off():
    """The kill switch note, or "" when ACP spawns are allowed."""
    try:
        with open(ACP_OFF, encoding="utf-8") as fh:
            return fh.read().strip() or "board/acp.off exists"
    except OSError:
        return ""


def acp_key(pane):
    """File key for a pane: its id with ':' -> '-' (w3:pK3 -> w3-pK3), same rule as pi_key."""
    return re.sub(r"[^A-Za-z0-9_-]", "-", pane)


def acp_path(kind, key, ext):
    return os.path.join(ACP_DIR, kind, f"{key}{ext}")


def pid_alive(pid):
    """True while a process with that pid runs. Never `os.kill(pid, 0)`: on Windows that KILLS it."""
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if pid <= 0:
        return False
    if not WINDOWS:
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False
    import ctypes
    k = ctypes.windll.kernel32
    k.OpenProcess.restype = ctypes.c_void_p
    h = k.OpenProcess(0x1000, False, pid)           # PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        return False
    code = ctypes.c_ulong()
    ok = k.GetExitCodeProcess(ctypes.c_void_p(h), ctypes.byref(code))
    k.CloseHandle(ctypes.c_void_p(h))
    return bool(ok) and code.value == 259           # STILL_ACTIVE


def acp_state_read(key):
    """The state file dict for a key, or {}. Never raises: a half-written file is just absent."""
    path = acp_path("state", key, ".json")
    for i in range(8):
        try:
            with open(path, encoding="utf-8") as fh:
                d = json.load(fh)
            return d if isinstance(d, dict) else {}
        except PermissionError:
            # A writer's os.replace has the file for an instant; the file is there, not absent.
            if i < 7:
                time.sleep(0.01 * (i + 1))
        except (OSError, ValueError):
            return {}
    return {}


_ACP_STATE_LOCK = threading.Lock()


def replace_retry(tmp, dst, tries=8):
    """os.replace with a short retry. On Windows a file that a reader (`ls`, a test poll) has open
    cannot be replaced - WinError 5/32 - and the pane thread that hit it died without writing idle."""
    for i in range(tries):
        try:
            os.replace(tmp, dst)
            return
        except PermissionError:
            if i == tries - 1:
                raise
            time.sleep(0.05 * (i + 1))


def acp_state_put(key, **fields):
    """Merge fields into the state file, atomically (tmp + rename), and stamp `updated`.
    The pane writes from several threads (turn worker, adapter reader, permission server); the lock
    keeps read-merge-write whole, so a late write cannot put back a state a newer one replaced."""
    path = acp_path("state", key, ".json")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with _ACP_STATE_LOCK:
        d = acp_state_read(key)
        d.update(fields)
        d["updated"] = time.time()
        tmp = f"{path}.{os.getpid()}.{threading.get_ident()}.tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(d, fh, indent=1)
        replace_retry(tmp, path)
    return d


def acp_key_of(pane, side=None):
    """The file key of an ACP pane's state, inbox and log. The sidecar row's `acp_key` wins: it was
    fixed when the client started, while a herdr cross-workspace move gives the pane a NEW id
    (`sidecar_rekey` moves the row, the ACP files stay put). Deriving the key from the current pane id
    would then read and write files no client watches. `side` is a sidecar dict a caller already holds."""
    row = (sidecar_load() if side is None else side).get(pane) or {}
    return row.get("acp_key") or acp_key(pane)


def _acp_live(st):
    return bool(st) and st.get("state") != "stopped" and pid_alive(st.get("pid"))


def acp_state(pane, side=None):
    """The live state dict for an ACP pane, or None when there is no client behind it.
    "Live" means the client pid the state file recorded is still running and it has not written
    `stopped` - a client that died leaves its last words, and those must not read as healthy."""
    st = acp_state_read(acp_key_of(pane, side))
    return st if _acp_live(st) else None


def acp_inbox_put(pane, text, suffix=".md"):
    """Queue one prompt for the pane's client. -> file path. Same write-then-rename contract as
    pi_inbox_put: the client delivers every `.md` it sees, so a half-written file must be invisible."""
    d = os.path.join(ACP_DIR, "inbox", acp_key_of(pane))
    os.makedirs(d, exist_ok=True)
    name = f"{time.strftime('%Y%m%d-%H%M%S')}-{int((time.time() % 1) * 1000):03d}{suffix}"
    path = os.path.join(d, name)
    with open(path + ".tmp", "w", encoding="utf-8") as fh:
        fh.write(text)
    os.replace(path + ".tmp", path)
    return path


def acp_rows():
    """[(pane_id, sidecar entry)] for every live ACP pane."""
    return [(pane, e) for pane, e in live_sidecar_rows() if e.get("kind") == "acp"]


def acp_pane_ids():
    """Pane ids whose sidecar kind is acp. The watchers and restarters type into a pane's terminal
    (`herdr agent prompt`, `claude --resume`); on an ACP pane that would reach the client, not the agent."""
    return {pane for pane, e in sidecar_load().items() if e.get("kind") == "acp"}


def other_panes():
    """{pane_id: pane} for every live non-claude agent pane, from BOTH places one can be recorded.

    herdr's own `agent` flag covers codex. It never covers pi on Windows, so a pi pane is found
    through the sidecar row `cmd_spawn_pi` wrote, and given a synthesized `agent`/`agent_status`
    so that callers (`orch.py ls`, `watch_sessions.py`) need no special case for it. Its status is
    read from pi's spinner rather than invented, or a pane would be classified mid-answer and
    reported DONE on half a sentence.
    """
    live = {p["pane_id"]: p for p in panes()}
    # An ACP pane is judged by its client's state file, never by what herdr or the screen shows:
    # herdr may flag a codex ACP pane `codex` (the adapter runs a codex process under the client),
    # and the watchers would then read the client's tee as if it were a codex TUI.
    acp = acp_pane_ids()
    out = {pid: p for pid, p in live.items()
           if p.get("agent") and p.get("agent") != "claude" and pid not in acp}
    for pid, e in sidecar_load().items():
        if pid in live and e.get("kind") == "pi" and pid not in out:
            out[pid] = {**live[pid], "agent": "pi", "agent_status": pi_status(pid)}
        elif pid in live and e.get("kind") in ONESHOT_SUFFIX and pid not in out:
            out[pid] = {**live[pid], "agent": e["kind"], "agent_status": oneshot_status(e)}
    return out


FINISHED_STATUS = ("idle", "done", "failed")   # a non-claude pane that stopped: `failed` is a one-shot's is_error


def oneshot_result(entry):
    """The `"type":"result"` JSON object a one-shot pane's stream file holds, or None while it runs.

    Tee-Object writes UTF-16LE with a BOM under Windows PowerShell 5.1 and UTF-8 under pwsh 7, so
    the file is decoded by its BOM. Only grok and cursor panes (ONESHOT_SUFFIX) have a stream file;
    for any other kind `report` is the agent's own report, which must never be read as one."""
    if entry.get("kind") not in ONESHOT_SUFFIX:
        return None
    path = (entry.get("report") or "") + ONESHOT_SUFFIX[entry["kind"]]
    try:
        with open(path, "rb") as fh:
            raw = fh.read()
    except OSError:
        return None
    text = raw.decode("utf-16" if raw[:2] in (b"\xff\xfe", b"\xfe\xff") else "utf-8", "replace")
    for line in reversed(text.splitlines()):
        if '"type"' in line and '"result"' in line:
            try:
                obj = json.loads(line.strip())
            except ValueError:
                continue
            if obj.get("type") == "result":
                return obj
    return None


def oneshot_status(entry):
    """"done" once the stream file holds a good result line, "failed" when that line says
    is_error (a usage limit, a crash), else "working" (the pane is still live)."""
    res = oneshot_result(entry)
    if not res:
        return "working"
    return "failed" if res.get("is_error") else "done"


def sidecar_load():
    try:
        with open(AGENT_SIDECAR, encoding="utf-8") as fh:
            d = json.load(fh)
    except (OSError, ValueError):
        return {}
    return d if isinstance(d, dict) else {}


def sidecar_put(pane, **fields):
    d = sidecar_load()
    d.setdefault(pane, {}).update(fields)
    os.makedirs(BOARD_DIR, exist_ok=True)
    with open(AGENT_SIDECAR, "w", encoding="utf-8") as fh:
        json.dump(d, fh, indent=2)
    return d[pane]


def sidecar_drop(pane):
    d = sidecar_load()
    if d.pop(pane, None) is None:
        return
    with open(AGENT_SIDECAR, "w", encoding="utf-8") as fh:
        json.dump(d, fh, indent=2)


def sidecar_orphans(kind="codex"):
    """[(pane_id, entry)] for sidecar rows whose herdr pane is gone - what `resume` reopens.

    herdr recycles pane ids, so "gone" is decided against the live pane list, not the agent list:
    a pane id that came back as somebody else's pane is not this agent's pane any more either.
    """
    live = {p["pane_id"] for p in panes()}
    return [(pane, e) for pane, e in sorted(sidecar_load().items())
            if pane not in live and (not kind or e.get("kind") == kind)]


# On Windows `codex` on PATH is an npm shim, so a TUI is a node.exe running @openai/codex. Matching
# on `codex.exe` instead would also hit the native helper the Desktop app and the MCP runtime spawn,
# and killing one of those takes out an unrelated tool.
_CODEX_PROC_PS = (r"Get-CimInstance Win32_Process | "
                  r"Where-Object { $_.CommandLine -match 'node_modules[\\/]@openai[\\/]codex' } | "
                  r"ForEach-Object { $_.ProcessId }")


def codex_tui_pids():
    """The pids of every codex TUI running on this box."""
    if not WINDOWS:
        r = subprocess.run(["pgrep", "-f", "@openai/codex"], capture_output=True, text=True)
    else:
        r = subprocess.run(["powershell", "-NoProfile", "-Command", _CODEX_PROC_PS],
                           capture_output=True, text=True)
    return {int(x) for x in (r.stdout or "").split() if x.strip().isdigit()}


# pi is an npm package too, so a pi TUI is a node.exe running its dist/cli.js. Matched on the
# package dir rather than the bare word "pi", which would hit half the node processes on the box.
# `-match 'pi-coding-agent'` also matches THIS powershell, whose own command line contains the
# pattern. `_codex_open` never noticed the same flaw in _CODEX_PROC_PS because it only ever uses
# `after - before`, and the self-match cancels; a bare call does not have that luxury, and killing
# what comes back would kill the query. So the name filter is on the executable, not just the
# command line. (Measured 2026-09-14: three "kill them all" rounds each reported one fresh pid.)
_PI_PROC_PS = (r"Get-CimInstance Win32_Process | "
               r"Where-Object { $_.Name -eq 'node.exe' -and "
               r"$_.CommandLine -match 'pi-coding-agent' } | "
               r"ForEach-Object { $_.ProcessId }")


def pi_tui_pids():
    """The pids of every pi TUI running on this box."""
    if not WINDOWS:
        r = subprocess.run(["pgrep", "-f", "pi-coding-agent"], capture_output=True, text=True)
    else:
        r = subprocess.run(["powershell", "-NoProfile", "-Command", _PI_PROC_PS],
                           capture_output=True, text=True)
    return {int(x) for x in (r.stdout or "").split() if x.strip().isdigit()}


def kill_tree(pid):
    """Kill a process and its children. A codex TUI spawns the native `codex.exe` as a child and
    THAT is what holds the thread's writer lock, so killing the parent alone leaves the lock held.
    """
    if WINDOWS:
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True)
    else:
        subprocess.run(["pkill", "-9", "-P", str(pid)], capture_output=True)
        kill_pid(pid)


def _codex_meta(path):
    """The `session_meta` payload on a rollout file's first line, or {}."""
    try:
        with open(path, encoding="utf-8") as fh:
            d = json.loads(fh.readline())
    except (OSError, ValueError):
        return {}
    return d.get("payload") or {} if d.get("type") == "session_meta" else {}


def codex_threads(since=0, cwd=None, exclude=()):
    """Top-level codex TUI threads as [(thread_id, started_epoch, cwd)], newest first.

    Measured 2026-09-06: a rollout's `session_meta` marks `thread_source` "user" for a thread a
    human started, "subagent" for one codex spawned itself and "automation" for the Desktop app.
    Only the first is resumable as the pane's own thread, so the other two are filtered out - a
    spawn that grabbed the newest file blind would have recorded a subagent's id instead.
    """
    out = []
    want = os.path.normcase(os.path.abspath(cwd)) if cwd else None
    for f in glob.glob(os.path.join(CODEX_SESSIONS, "**", "rollout-*.jsonl"), recursive=True):
        try:
            mtime = os.path.getmtime(f)
        except OSError:
            continue
        if mtime < since:
            continue
        p = _codex_meta(f)
        tid = p.get("id")
        if not tid or tid in exclude or p.get("thread_source") != "user":
            continue
        if not str(p.get("originator", "")).startswith("codex-tui"):
            continue
        if want and os.path.normcase(os.path.abspath(p.get("cwd") or "")) != want:
            continue
        out.append((tid, mtime, p.get("cwd")))
    return sorted(out, key=lambda r: r[1], reverse=True)


# Frame and furniture a TUI paints around whatever the agent actually said.
# The last five are pi's: its update banner (three lines, permanent once a release is out) and the
# hints in its composer. Without them a pi pane's "last words" are the banner, not its answer.
TUI_CHROME = ("ask codex to do anything", "esc to interrupt", "press enter to continue",
              "/model to change", "ctrl+c to quit", "send with enter", "tip: ",
              "usage limit resets available", "to enable our fastest inference",
              "update available", "is available. run pi update", "pi.dev/changelog",
              "shift+enter for newline", "? for shortcuts")

# pi's two-line status footer, which sits BELOW the composer and so is the literal last thing on
# screen: `↑14k ↓10 $0.070 (sub) 5.1%/272k (auto)   (openai-codex) gpt-5.6-sol • medium` over
# `~\Vault (main)`. Codex's footer is caught by the " · " rule below; pi's uses " • " and a
# `NN%/NNNk` context gauge, so it needs its own match or every pi pane reports the footer as its
# answer and `DONE` is never seen (measured 2026-09-14).
PI_FOOTER = re.compile(r"\d+(\.\d+)?%/\d+k|\s•\s|^~[\\/].*\((?:main|master|[\w./-]+)\)$")
# Same gauge, captured: `57.0%/500k` -> 285 (k-tokens). `ls` used to print ctx=- for every pi
# row because "ctx is unknowable from outside". It is knowable: it is on the footer.
PI_CTX = re.compile(r"(\d+(?:\.\d+)?)%/(\d+)k")


def pi_ctx_k(pane, txt=None):
    """k-tokens from a pi pane's footer gauge, or None if the footer is not on screen."""
    raw = pane_read(pane, 8) if txt is None else txt
    m = PI_CTX.search(raw or "")
    if not m:
        return None
    pct, cap = float(m.group(1)), int(m.group(2))
    return max(0, int(round(cap * pct / 100.0)))
TUI_BOX = set("-_=|/\\ ") | set("─│┌┐└┘├┤┬┴"
                                "┼═║╔╗╚╝╱╲╴"
                                "╵╶╷▀▄█▌▐░▒"
                                "▓●╭╮╯╰━┃╌")
BULLETS = ("•", "›", ">", "*")

# Codex paints a shimmer under its last message out of single-dot Braille cells that move every
# frame. Measured 2026-09-16 (pi-orch-audit, G5): a codex crew pane's screen read
# `• READY` and then lines like `⠁             ⠄             ⠈   ⠁ ⢀   ...` made of exactly
# U+2801 U+2802 U+2804 U+2808 U+2810 U+2820 U+2840 U+2880 and spaces. Those lines are not the
# agent's words, but they survived every filter above, and because the dots moved between scans
# the same READY fingerprinted as new text six times in five minutes and was delivered to its
# mate six times. A line whose only non-space characters are Braille cells is screen chrome.
BRAILLE = (0x2800, 0x28FF)


def spinner_line(s):
    """True for a line made only of Braille cells and spaces - a spinner or shimmer frame."""
    chars = [c for c in s if not c.isspace()]
    return bool(chars) and all(BRAILLE[0] <= ord(c) <= BRAILLE[1] for c in chars)


def pane_last_text(txt):
    """What the agent itself last printed in a TUI pane, with the frame stripped.

    Keeps only real content lines, then cuts back to the last bullet the agent printed - in
    Codex that is the `•` in front of each of its own messages, so the result is its last say
    rather than the whole visible scrollback."""
    keep = []
    for raw in txt.splitlines():
        s = raw.strip().strip("│┃|").strip()
        if not s or set(s) <= TUI_BOX or spinner_line(s):
            continue
        low = s.lower()
        if any(c in low for c in TUI_CHROME):
            continue
        keep.append(s)
    # the status footer under the input box ("gpt-5.6-sol xhigh - ~\.claude") is the last thing
    # on screen and would otherwise become the agent's "last words". Codex's carries " · ";
    # pi's is PI_FOOTER (see above). Both can be two lines deep, so this pops, not slices.
    while keep and (" · " in keep[-1] or PI_FOOTER.search(keep[-1])):
        keep.pop()
    for i in range(len(keep) - 1, -1, -1):
        if keep[i][:1] in BULLETS:
            keep = keep[i:]
            break
    return "\n".join(keep).strip()


def _classify_text(text):
    """(kind, last 300 chars) for an agent's last words, wherever they were read from. A last
    line that is the bare word DONE is taken at its word: that is the contract `orch.py task`
    writes into a codex or pi task file, because neither has SendMessage to report with."""
    text = (text or "").strip()
    if not text:
        return "DONE", ""
    last = text.splitlines()[-1].strip().strip("*_`.• ").upper()
    if last == "DONE":
        return "DONE", text[-300:]
    return classify_words(text), text[-300:]


def classify_pane(pane, txt=None, lines=40):
    """Why a non-claude pane stopped -> (kind, last 300 chars it printed).

    Same word rules as `classify`. The words come from the pane's orch-pane STATE FILE when one
    is live (`last_text`, the assistant's own last message, written by the extension on every
    `agent_end`), and only otherwise off the screen.

    The screen used to win even when the state file was there. Measured 2026-09-16 (pi-orch-audit,
    G4): a pi crew pane had answered the bare word DONE and its state file said so, but its screen
    still showed the spawn footer, READY, the `[from <mate>] finish now ...` prompt, DONE and then
    pi's cwd line - so the scrape returned that whole slab, the word rules found a question in it,
    and the pane's mate was told `[crew] ps-pi QUESTION | ther session's browser tabs, ...`. The
    orchestrator's watcher recorded the same wrong verdict. The state file is the thing we care
    about; the screen is the fallback for a pane whose extension is not running.
    """
    st = pi_state(pane)
    if st is not None and st.get("last_text"):
        return _classify_text(st["last_text"])
    if txt is None:
        # A one-shot grok/cursor pane's screen is raw JSON (it holds the word "error" in
        # `"is_error":false`), so its verdict comes from the stream file's result line instead.
        res = oneshot_result(sidecar_load().get(pane) or {})
        if res:
            if res.get("is_error"):
                return "ERROR", " ".join(res.get("errors") or [res.get("subtype") or "error"])[-300:]
            return _classify_text(res.get("result") or "")
    txt = pane_read(pane, lines) if txt is None else txt
    return _classify_text(pane_last_text(txt))


# ---------------------------------------------------------------- pane mode

# "Would you like to proceed" / "ready to execute" is the plan-mode approval dialog. It shows no
# "Esc to cancel" line, so four builders sat on it for eight hours on 2026-09-04 while
# auto_accept.py and watch_sessions.py both saw nothing.
PROMPT_MARKS = ("Do you want to proceed", "Esc to cancel", "Do you want to make this edit",
                "Would you like to proceed", "ready to execute")
# The updater can flip settings.json's defaultMode; a pane that came up in auto instead of bypass
# stalls on the first classifier hit, and the teach dialog swallows keystrokes until dismissed.
AUTO_MODE_MARK = "auto mode on"
BYPASS_MARK = "bypass permissions on"
TEACH_DIALOG_MARK = "teach auto mode about your environment"


def pending_prompt(txt):
    """The permission question showing in a pane, or ''. Bypass panes still prompt on a deny rule."""
    # A real dialog always shows the numbered choice; quoted text in a message does not.
    if not any(m in txt for m in PROMPT_MARKS) or "1. Yes" not in txt:
        return ""
    for l in txt.splitlines():
        if ("Do you want" in l or "Would you like" in l) and "Message from" not in l:
            return l.strip().strip("│ ").strip()
    return "permission prompt"


def pane_mode(txt):
    """'bypass' | 'auto' | 'teach-dialog' | '' from a pane's visible text."""
    low = txt.lower()
    if TEACH_DIALOG_MARK in low:
        return "teach-dialog"
    if BYPASS_MARK in low:
        return "bypass"
    if AUTO_MODE_MARK in low:
        return "auto"
    return ""


def ensure_bypass(pane, timeout=10):
    """Accept any pending prompt, then shift+tab until the pane says bypass. -> True if confirmed."""
    deadline = time.time() + timeout
    txt = pane_read(pane, 40)
    if pane_mode(txt) == "teach-dialog":
        send_keys(pane, "escape")
        time.sleep(1.0)
    if pending_prompt(txt):
        send_keys(pane, "enter")
        time.sleep(1.5)
    while time.time() < deadline:
        if BYPASS_MARK in pane_read(pane, 40).lower():
            return True
        send_keys(pane, "shift+tab")
        time.sleep(1.2)
    return BYPASS_MARK in pane_read(pane, 40).lower()


# ---------------------------------------------------------------- the Chrome lock

CHROME_LOCK = os.path.join(BOARD_DIR, "chrome-lock.json")


def chrome_holder():
    """(name, iso timestamp) of whoever holds the browser, or (None, None)."""
    try:
        with open(CHROME_LOCK, encoding="utf-8") as fh:
            d = json.load(fh)
    except (OSError, ValueError):
        return None, None
    return d.get("holder"), d.get("since")


def chrome_take(name):
    """-> (True, message). ADVISORY since 2026-10-01: nothing refuses on it any more. Ryan wants
    several agents working in :9333 at once, so this only records who last said they were in it.
    The two real rules are in chrome_line(): work in your own tab, and never close or bring to
    front a tab you did not open."""
    holder, since = chrome_holder()
    os.makedirs(BOARD_DIR, exist_ok=True)
    with open(CHROME_LOCK, "w", encoding="utf-8") as fh:
        json.dump({"holder": name, "since": datetime.datetime.now().isoformat(timespec="seconds")},
                  fh, indent=2)
    if holder and holder != name:
        return True, (f"ok: noted. {holder} is also in the browser (since {since}). Use your own "
                      f"tab; never close or bring to front a tab you did not open.")
    return True, f"ok: noted. {name} is in the browser. Use your own tab."


def chrome_free(name=None):
    """Clear your own advisory note. Advisory since 2026-10-01: freeing a name that is not the one
    on file is a harmless no-op, never an error."""
    holder, _ = chrome_holder()
    if not holder:
        return True, "already free"
    if name not in (holder, "force"):
        return True, f"nothing to free for {name!r} ({holder} is the one on file; left alone)"
    try:
        os.remove(CHROME_LOCK)
    except OSError:
        pass
    return True, f"freed (was {holder})"


# ---------------------------------------------------------------- the hardware go

# A hardware action - lights, monitor power - is the one class of tool call whose cost lands on
# Ryan's body rather than on a file. On 2026-09-11 a review pane ran `lights_hotkey.py off` while he
# was working and blanked all three monitors. So hardware is opt-in per pane and per half-hour: the
# orchestrator runs `orch.py go <pane>` after Ryan says go, the pane gets 30 minutes, and
# block-known-traps.py refuses every hardware command from a pane without a fresh entry.
#
# Both the pane NAME and its session id are recorded, because the hook sees only a session_id on
# stdin and the registry row it would need to resolve a name from can be missing for a pane started
# outside orch.py.
GO_FILE = os.path.join(BOARD_DIR, "go.json")
GO_TTL_SECONDS = 30 * 60


def go_load():
    """{key: {"since": iso, "ts": epoch, "by": who}} - keys are pane names AND session ids."""
    try:
        with open(GO_FILE, encoding="utf-8") as fh:
            d = json.load(fh)
    except (OSError, ValueError):
        return {}
    return d if isinstance(d, dict) else {}


def go_fresh(key, now=None):
    """True while `key` holds a go less than GO_TTL_SECONDS old. An unparseable or absent entry is
    not fresh - the gate is opt-in, so "cannot tell" means no."""
    if not key:
        return False
    ent = go_load().get(key)
    if not isinstance(ent, dict):
        return False
    try:
        ts = float(ent.get("ts"))
    except (TypeError, ValueError):
        return False
    return 0 <= ((time.time() if now is None else now) - ts) <= GO_TTL_SECONDS


def go_grant(name, sid=None, by=None):
    """-> (ok, message). Records the go under the pane name, and under its session id too so the
    hook can match without a registry lookup."""
    if not name:
        return False, "usage: orch.py go <pane-name>"
    if not sid:
        for s, m in _registry().items():
            if m.get("name") == name:
                sid = s
                break
    gos = go_load()
    ent = {"since": datetime.datetime.now().isoformat(timespec="seconds"),
           "ts": time.time(), "by": by or caller_name()}
    gos[name] = ent
    if sid:
        gos[sid] = dict(ent, name=name)
    os.makedirs(BOARD_DIR, exist_ok=True)
    with open(GO_FILE, "w", encoding="utf-8") as fh:
        json.dump(gos, fh, indent=1)
    mins = GO_TTL_SECONDS // 60
    return True, f"go: {name} may touch hardware for {mins} min (granted by {ent['by']})"


def go_revoke(name):
    """-> (ok, message). Drops the name and any session id recorded alongside it."""
    if not name:
        return False, "usage: orch.py ungo <pane-name>"
    gos = go_load()
    dropped = gos.pop(name, None) is not None
    for key in [k for k, v in gos.items() if isinstance(v, dict) and v.get("name") == name]:
        gos.pop(key, None)
        dropped = True
    os.makedirs(BOARD_DIR, exist_ok=True)
    with open(GO_FILE, "w", encoding="utf-8") as fh:
        json.dump(gos, fh, indent=1)
    return True, (f"ungo {name}" if dropped else f"{name} had no go")


# ---------------------------------------------------------------- fleet ledger

class ledger:
    """One JSON line per fleet event in board/fleet-ledger.jsonl, so a dashboard or the AFK digest
    reads one file instead of five. Record shape and events: board/schema.md ("Fleet ledger").

        L.ledger.write("task.dispatched", "g7-watcher", kind="claude", harness="claude",
                       model="sonnet", project="claude-config")
        L.ledger.write("task.status", {"task": "g7-watcher", "state": "done", "text": "..."})

    Idea from Kun's First Mate (fm-fleet-ledger.sh and docs/fleet-ledger.md, MIT); the record shape
    is the same, the code is ours. Append-only. Never raises: a ledger that cannot be written must
    not fail a spawn or a kill, so a problem returns False and prints one stderr line.
    """
    VERSION = 1
    EVENTS = ("task.dispatched", "task.status", "task.pr_ready", "task.merged", "task.cleaned_up")
    TEXT_MAX = 2000

    @staticmethod
    def path():
        return os.path.join(BOARD_DIR, "fleet-ledger.jsonl")

    @staticmethod
    @contextlib.contextmanager
    def _locked(fd, timeout=5.0):
        """Exclusive cross-process lock for one append. Windows O_APPEND is not atomic (the C
        runtime seeks to the end, then writes), so 8 writers lost 73 of 400 lines in a test. The
        lock sits on one byte far past the end of the file, so a reader never trips over it."""
        spot = 1 << 40
        deadline = time.time() + timeout
        if WINDOWS:
            import msvcrt
            while True:
                os.lseek(fd, spot, os.SEEK_SET)
                try:
                    msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                    break
                except OSError:
                    if time.time() > deadline:
                        raise
                    time.sleep(0.003)
            try:
                yield
            finally:
                os.lseek(fd, spot, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(fd, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(fd, fcntl.LOCK_UN)

    @staticmethod
    def write(event, task, **fields):
        """-> True when the line was appended. `task` is the task id (the pane label), or a dict
        carrying it as "task" (else "name") plus any extra members."""
        try:
            if isinstance(task, dict):
                fields = {**{k: v for k, v in task.items() if k not in ("task", "name")}, **fields}
                task = task.get("task") or task.get("name")
            if event not in ledger.EVENTS:
                raise ValueError(f"unknown ledger event {event!r}")
            if not task:
                raise ValueError("no task id")
            rec = {"v": ledger.VERSION, "ts": int(time.time()), "event": event, "task": str(task)}
            rec.update({k: v for k, v in fields.items() if k not in rec})
            if isinstance(rec.get("text"), str):
                rec["text"] = rec["text"][:ledger.TEXT_MAX]
            line = (json.dumps(rec, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
            os.makedirs(BOARD_DIR, exist_ok=True)
            fd = os.open(ledger.path(), os.O_WRONLY | os.O_CREAT | getattr(os, "O_BINARY", 0), 0o644)
            try:
                with ledger._locked(fd):
                    os.lseek(fd, 0, os.SEEK_END)
                    os.write(fd, line)
            finally:
                os.close(fd)
            return True
        except Exception as e:
            print(f"ledger.write {event}: {type(e).__name__}: {e}", file=sys.stderr)
            return False

    @staticmethod
    def read(path=None):
        """Every well-formed record, oldest first. A torn or foreign line is skipped."""
        out = []
        try:
            with open(path or ledger.path(), encoding="utf-8") as fh:
                for l in fh:
                    try:
                        r = json.loads(l)
                    except ValueError:
                        continue
                    if isinstance(r, dict) and r.get("event"):
                        out.append(r)
        except OSError:
            pass
        return out


# ---------------------------------------------------------------- misc

def kill_pid(pid):
    """Stop a process by pid on either platform. In the Bash tool `taskkill /PID` gets
    path-mangled, so Windows goes through PowerShell instead."""
    if WINDOWS:
        subprocess.run(["powershell", "-NoProfile", "-Command",
                        f"Stop-Process -Id {pid} -Force"], capture_output=True)
    else:
        subprocess.run(["kill", "-9", str(pid)], capture_output=True)
