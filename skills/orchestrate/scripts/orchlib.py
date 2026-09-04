"""Shared helpers for the orchestrate skill: config, registry, herdr, log reading, stop-reason
classifier, the Chrome lock.

Imported by orch.py, watch_sessions.py, board.py, fix_mode.py. Keep every rule that both the board
and the watcher need in here so the two can never disagree about what DONE means.

Nothing personal is hardcoded. `config.json` next to this skill supplies the machine-specific
values; every one of them has a default derived from `~/.claude` and the current directory, so the
skill runs on a fresh clone with no config file at all. See `config.example.json`.
"""
import datetime, glob, json, os, platform, subprocess, sys, time

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
ROTATE_AT = int(cfg("rotate_at_k", 400))       # k tokens

MODELS = {
    "fable": "claude-fable-5-1",
    "opus": "claude-opus-5",
    "sonnet": "claude-sonnet-5",
    "haiku": "claude-haiku-4-5-20251001",
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

def herdr(*args, timeout=90, soft=False):
    """Run herdr and return parsed JSON. soft=True returns None instead of exiting."""
    try:
        r = subprocess.run(["herdr", *args], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=timeout)
    except Exception as e:
        if soft:
            return None
        sys.exit(f"herdr {' '.join(args)} crashed: {e}")
    out = (r.stdout or "").strip()
    try:
        return json.loads(out)
    except ValueError:
        if soft:
            return None
        sys.exit(f"herdr {' '.join(args)} failed: {out or r.stderr}")


def pane_read(pane, lines=30, source="visible"):
    r = subprocess.run(["herdr", "pane", "read", pane, "--lines", str(lines), "--source", source],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    return r.stdout or ""


def send_keys(pane, *k):
    subprocess.run(["herdr", "pane", "send-keys", pane, *k], capture_output=True)


def panes():
    d = herdr("pane", "list", soft=True) or {}
    return d.get("result", {}).get("panes", [])


def tabs():
    d = herdr("tab", "list", soft=True) or {}
    return d.get("result", {}).get("tabs", [])


def panes_by_sid():
    out = {}
    for p in panes():
        s = p.get("agent_session") or {}
        if s.get("value"):
            out[s["value"]] = p
    return out


def tab_label(tab_id):
    return next((t.get("label", "") for t in tabs() if t.get("tab_id") == tab_id), "")


def orchestrator_sid():
    """Session id of the pane sitting in the tab labelled with `orchestrator_tab`."""
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
    # During a rotation two sessions share the tab and often the label; the live orchestrator is
    # the one whose session title is the tab label too (rotate-self names it so), and rotate-self
    # relabels the retiring pane `orch-retiring`, which is skipped above. Taking the first pane
    # named the retiring seat in four task files (2026-09-03).
    found.sort(key=lambda t: (not t[0], not t[1]))
    return found[0][2]


def orchestrator_name():
    """Display name of the orchestrator session; falls back to the bare session prefix."""
    sid = orchestrator_sid()
    if sid:
        for m in sessions().values():
            if m.get("sessionId") == sid:
                return m.get("name") or SESSION_PREFIX
    return SESSION_PREFIX


# ---------------------------------------------------------------- registry

def sessions():
    """{sessionId: registry dict}. Never raises: a half-written file is skipped, not fatal."""
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
        out[sid] = m
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
    with --cwd elsewhere (the CourseGrid builders) is NOT under LOGS - read its cwd from the
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


def tail_entries(sid, nbytes=200000):
    """Raw log lines, newest last. Deliberately NOT parsed: the readers below scan backwards and
    json.loads only the handful of lines they care about. Parsing a 200KB window per session per
    poll made watch_sessions.py too slow to emit anything inside a minute (measured 2026-09-03)."""
    try:
        with open(log_path(sid), "rb") as fh:
            fh.seek(0, 2)
            fh.seek(max(0, fh.tell() - nbytes))
            data = fh.read().decode("utf-8", "replace")
    except OSError:
        return []
    return [l for l in data.splitlines() if l.startswith("{")]


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
    if any(k in tail for k in BLOCKED_MARKS):
        return "BLOCKED"
    if text.rstrip().endswith("?") or any(k in tail for k in QUESTION_MARKS):
        return "QUESTION"
    if any(k in tail for k in OFFER_MARKS):
        return "OFFER"
    if tool_error or any(k in tail for k in ERROR_MARKS):
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
KINDS = ("claude", "codex")

# Where the report file and label for a non-claude pane are remembered. herdr already reports the
# agent kind on the pane itself, so this holds only what herdr cannot know.
AGENT_SIDECAR = os.path.join(BOARD_DIR, "agent-panes.json")


def agent_panes():
    """{pane_id: pane} for every herdr pane running a non-claude agent."""
    return {p["pane_id"]: p for p in panes()
            if p.get("agent") and p.get("agent") != "claude"}


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


# Frame and furniture a TUI paints around whatever the agent actually said.
TUI_CHROME = ("ask codex to do anything", "esc to interrupt", "press enter to continue",
              "/model to change", "ctrl+c to quit", "send with enter", "tip: ",
              "usage limit resets available", "to enable our fastest inference")
TUI_BOX = set("-_=|/\\ ") | set("─│┌┐└┘├┤┬┴"
                                "┼═║╔╗╚╝╱╲╴"
                                "╵╶╷▀▄█▌▐░▒"
                                "▓●╭╮╯╰━┃╌")
BULLETS = ("•", "›", ">", "*")


def pane_last_text(txt):
    """What the agent itself last printed in a TUI pane, with the frame stripped.

    Keeps only real content lines, then cuts back to the last bullet the agent printed - in
    Codex that is the `•` in front of each of its own messages, so the result is its last say
    rather than the whole visible scrollback."""
    keep = []
    for raw in txt.splitlines():
        s = raw.strip().strip("│┃|").strip()
        if not s or set(s) <= TUI_BOX:
            continue
        low = s.lower()
        if any(c in low for c in TUI_CHROME):
            continue
        keep.append(s)
    # the status footer under the input box ("gpt-5.6-sol xhigh - ~\.claude") is the last thing
    # on screen and would otherwise become the agent's "last words"
    while keep and " · " in keep[-1]:
        keep.pop()
    for i in range(len(keep) - 1, -1, -1):
        if keep[i][:1] in BULLETS:
            keep = keep[i:]
            break
    return "\n".join(keep).strip()


def classify_pane(pane, txt=None, lines=40):
    """Why a non-claude pane stopped -> (kind, last 300 chars it printed).

    Same word rules as `classify`, read off the screen instead of a log. A pane whose last line
    is the bare word DONE is taken at its word: that is the contract `orch.py task --kind codex`
    writes into the task file, because Codex has no SendMessage to report with.
    """
    txt = pane_read(pane, lines) if txt is None else txt
    text = pane_last_text(txt)
    if not text:
        return "DONE", ""
    last = text.splitlines()[-1].strip().strip("*_`.• ").upper()
    if last == "DONE":
        return "DONE", text[-300:]
    return classify_words(text), text[-300:]


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
    """-> (ok, message). One session drives the real browser at a time; two at once typed over each
    other's draft on 2026-09-03. Re-taking your own lock is fine and refreshes it."""
    holder, since = chrome_holder()
    if holder and holder != name:
        return False, f"busy: {holder} has held it since {since}"
    os.makedirs(BOARD_DIR, exist_ok=True)
    with open(CHROME_LOCK, "w", encoding="utf-8") as fh:
        json.dump({"holder": name, "since": datetime.datetime.now().isoformat(timespec="seconds")},
                  fh, indent=2)
    return True, f"ok: {name} holds the browser"


def chrome_free(name=None):
    """Release the lock. A name that does not match the holder is refused unless it is 'force'.

    `name` must actually match - a caller that passes nothing used to skip this check entirely
    (`if name and ...` is False for name=None) and silently release whoever held it. A bare `chrome
    free` really did drop another session's live lock this way (2026-09-04)."""
    holder, _ = chrome_holder()
    if not holder:
        return True, "already free"
    if name not in (holder, "force"):
        return False, f"{holder} holds it, not {name!r} (use `chrome free force`)"
    try:
        os.remove(CHROME_LOCK)
    except OSError:
        pass
    return True, f"freed (was {holder})"


# ---------------------------------------------------------------- misc

def kill_pid(pid):
    """Stop a process by pid on either platform. In the Bash tool `taskkill /PID` gets
    path-mangled, so Windows goes through PowerShell instead."""
    if WINDOWS:
        subprocess.run(["powershell", "-NoProfile", "-Command",
                        f"Stop-Process -Id {pid} -Force"], capture_output=True)
    else:
        subprocess.run(["kill", "-9", str(pid)], capture_output=True)
