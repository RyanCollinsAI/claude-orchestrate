"""Monitor script: one line whenever a session needs the orchestrator.

  Monitor(command='py ".../watch_sessions.py"', persistent=true)

Emits:
  NEW session <name> status=<s>
  GONE session <name> (exited)
  NEW manual <name> pane=<id> cwd=<dir> | ...                   the human opened a pane by hand;
                                                                 it is labelled and managed now
                                                                 (prints even in quiet mode)
  NEW codex <name> pane=<id>                                    a non-claude agent pane appeared
  GONE codex <name> (pane closed)
  <name> REPORT | <path> (<n> bytes)                            a codex task's report file landed
  <name> QUESTION|BLOCKED|OFFER|ERROR|DONE | <last 300 chars it said>  when it has genuinely stopped
  <name> PROMPT | <the permission question>                     pane stuck on a prompt
  <name> MODE auto | <pane>                                     pane came up in auto, not bypass
  <name> TEACH-DIALOG dismissed | <pane>                        the "Teach auto mode..." dialog
  USAGE 5h <n>% left, 7d <n>% - pause: <name> [<sid8>/<cwd>] (+<n>k/h), ...   two fastest-growing
  USAGE recovered - 5h <n>% left, 7d <n>%
  <name> STALE 30m                                              once, busy but no new assistant
                                                                 message in 30 min - pings the pane
                                                                 itself for a status line
  <name> BLOCKED <dialog text>                                  a STALE ping was refused because a
                                                                 dialog has the pane (herdr agent_blocked)
  <name> SILENT 35m (pinged, no reply)                          the STALE ping got no new output
                                                                 5 min after it was sent
  <name> WAIT-RECHECK <n>h | <who>: <reason> until <UTC>        a declared wait ("paused: ... until T",
                                                                 or `hold --until`) still valid but over
                                                                 4 h old; one line per 4 h. While it is
                                                                 younger, or until T, the pane gets no
                                                                 STALE ping and no idle-nudge.
  REAP: <n> candidates                                          at most hourly
  ` -> board q7`                                                a line that also landed on the board

Domain mates (2026-09-14). A pane spawned with `--domain` owns a subject and supervises its own
crew, so two things change here:
  - its CREW is dropped before any line is considered - the whole crew goes silent in one place
    rather than at nine print sites. A crew pane whose mate is NOT live is never silenced: it is
    orphaned, nobody else is watching it, and that is when this seat has to hear about it.
  - the MATE keeps its stop-reason line (what got accomplished) and its stuck-state lines (PROMPT,
    MODE auto, TEACH-DIALOG - a mate stuck on a dialog hangs forever), but loses STALE and
    SILENT. Ryan: "the orchestrator just has to know what it is that got accomplished."
Measured 2026-09-14 on the live board: a busy crew pane under a live mate produced zero lines over
75 s while the mate's own DONE line came through.

A stop-reason line only fires once a session has genuinely stopped: registry status is idle/waiting
AND the herdr pane's agent_status is idle/done AND that has held for READY_HOLD seconds AND the text
differs from the last line emitted for that session id. Without this, a builder mid-task fires
DONE/QUESTION/OFFER on every intermediate assistant sentence (observed: "Let me start CG4-08..."
reported DONE three times) because the registry status flickers idle between tool calls.

"The last line emitted" now survives a restart: it is written to `board/watcher-seen.json` as a
fingerprint of the text plus the assistant timestamp it came from. Before that, every restart of
the Monitor re-fired every pane's standing QUESTION and DONE lines (app-lead's e2e line fired three
times on 2026-09-09).

Every print flushes or Monitor never sees it. Poll is 8 s; panes are read every 4th pass because
`herdr pane read` costs a subprocess per pane (`herdr pane list`, used for agent_status, is one call
for all panes and is cheap enough to run every poll).
"""
import argparse, glob, hashlib, json, os, re, sys, threading, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import orchlib as L
import board as B
import declared_wait as DW
from orch import reap_candidates

HERE = os.path.dirname(os.path.abspath(__file__))
ORCH_PY = os.path.join(HERE, "orch.py")
SEEN_PATH = os.path.join(L.BOARD_DIR, "watcher-seen.json")
USAGE_DIR = os.path.join(L.CLAUDE_HOME, "accounts", "usage")

HEARTBEAT_PATH = os.path.join(L.BOARD_DIR, "heartbeat-watch_sessions.json")
HEARTBEAT_EVERY = 30


def start_heartbeat(path, every=HEARTBEAT_EVERY, extra=None):
    """Write {"ts": <epoch>, "pid": ...} to `path` every `every` seconds, from a daemon thread.

    Nothing else on this box can tell a live Monitor from a dead one. Measured 2026-09-09 by
    killing both watcher pids with taskkill /F: `orch.py doctor` printed a BYTE-IDENTICAL
    all-green report afterwards, and `orch.py resume` printed its two Monitor commands the same
    way it always does, running or not. The orchestrator went completely deaf and the only
    detector was a human noticing the stream had gone quiet - which is what a healthy night also
    looks like. routines\\orchestrator-health reads this file and calls stale > 3 min dead.

    A THREAD, not an inline call at the top of the loop: this loop legitimately blocks for
    minutes on a slow herdr or usage read, and an inline write would then
    look dead while the watcher was working fine. The thread only stops when the process does,
    which is exactly the signal wanted. `extra` is called each tick for extra fields, and any
    write error is swallowed - reporting liveness must never be the thing that kills the watcher.
    """
    def tick():
        while True:
            data = {"ts": time.time(), "pid": os.getpid()}
            try:
                if extra:
                    data.update(extra())
            except Exception:
                pass
            try:
                os.makedirs(os.path.dirname(path), exist_ok=True)
                tmp = path + ".tmp"
                with open(tmp, "w", encoding="utf-8") as fh:
                    json.dump(data, fh)
                os.replace(tmp, path)
            except Exception:
                pass
            time.sleep(every)

    t = threading.Thread(target=tick, daemon=True)
    t.start()
    return t


def short_title(said, fallback):
    """First plain-text line of `said`, trimmed - Bridge now shows only the title by default
    (2026-09-04, the question card puts everything else behind a Details toggle), so a static
    "X is waiting on an answer" says nothing until the orchestrator edits it in. This gives the
    card a real short title from the start."""
    line = next((l.strip() for l in said.splitlines() if l.strip()), "")
    line = re.sub(r"[`*_#>]+", "", line).strip()
    if not line:
        return fallback
    return line[:77] + "..." if len(line) > 80 else line


# Panes answer in Ryan's ELI5 style, so a plain status line often ends "What do you do now?
# Nothing." - a question mark with no ask in it. Those landed on the board as q102 (a reviewer's
# status) on 2026-09-12. A review pane reports to its lead, never to Ryan, so it never posts.
NOOP_PHRASES = (
    "nothing for you", "no action needed", "you don't need to do anything",
    "you do not need to do anything", "nothing needed from you", "nothing to do right now",
    "what do you do now? nothing", "no action from you", "nothing from you",
)


def is_noop_status(name, text):
    """True when a QUESTION-shaped line is really a status report: from a review pane, or
    carrying one of the 'nothing for you' phrases. Pure, for tests."""
    if name.startswith("review-"):
        return True
    low = " ".join((text or "").lower().split())
    return any(p in low for p in NOOP_PHRASES)

def to_board(kind, name, said):
    """A builder's QUESTION becomes a board question; a BLOCKED one becomes a question carrying the
    exact command a human must paste; an ERROR becomes a Show block.

    Free text, no options and no pick: the orchestrator reads it, then edits the options and the
    pick in before the human ever sees it. `said` is the session's own last message, so the question
    carries the real thing rather than a summary of it.

    Never lets a board problem kill the watcher: this runs inside the Monitor loop.
    """
    try:
        key = " ".join(said.split())[:80]
        if name.startswith("review-"):
            return          # a reviewer reports to its lead, never to Ryan (s3 on 2026-09-12)
        if kind == "QUESTION" and is_noop_status(name, said):
            return
        s = B.load()
        if kind in ("QUESTION", "BLOCKED"):
            # dedup on pane + the first 80 chars, so a session that re-states the same question
            # (or a watcher restart) does not stack duplicates on the board
            if any(q.get("from") == name and not q.get("answered")
                   and " ".join(q.get("context_md", "").split())[:80] == key
                   for q in s["questions"]):
                return
            if kind == "BLOCKED":
                cmd = L.blocked_command(said)
                ctx = said + (
                    f"\n\n**Run this yourself with the `!` prefix:**\n\n```\n{cmd}\n```"
                    if cmd else
                    "\n\n(No single command found in its message - read it and paste the right one.)")
                title = short_title(said, f"{name} is blocked by the permission classifier")
            else:
                ctx, title = said, short_title(said, f"{name} is waiting on an answer")
            qid = B.add_question_dict({"title": title, "from": name, "context_md": ctx})
            print(f"  -> board {qid}", flush=True)
        else:
            cap = f"{name} hit an error"
            if any(b.get("caption") == cap
                   and " ".join(b.get("body_md", "").split())[:80] == key for b in s["show"]):
                return
            sid_ = B.add_show(cap, said, group=B.group_for_who(name))
            print(f"  -> board {sid_}", flush=True)
    except Exception as e:
        print(f"  -> board write failed ({type(e).__name__}: {e})", flush=True)


def board_update(name, kind, text):
    """One line into Bridge's live Updates feed - every stop-reason and report, not only the ones
    that also become a question. Never lets a board write kill the watcher."""
    try:
        B.sync_board(new_updates=[{"ts": B.now_iso(), "pane": name, "kind": kind,
                                    "text": " ".join(text.split())[:200],
                                    "group": B.group_for_who(name)}])
    except Exception as e:
        print(f"  -> board update failed ({type(e).__name__}: {e})", flush=True)


def orchestrator_ref():
    """Bare display name of the orchestrator seat for SendMessage. SendMessage resolves the bare
    name on its own whenever exactly one live session has it, which is always true on this box -
    no bracket ref needed, and a stale one (from a restart) was actively wrong."""
    return L.orchestrator_name()


def ping_stale(name, pane, dry_run=False):
    """Ping a STALE pane for a one-line status. Returns True if the ping was sent (or would have
    been, under --dry-run) so the caller can start the 5-minute SILENT watch; False if herdr
    refused it outright (agent_blocked - a dialog has the pane), in which case the caller prints
    BLOCKED instead and there is nothing to watch for."""
    if pane in L.acp_pane_ids():
        return False        # ACP pane: `herdr agent prompt` would type into the client's terminal
    msg = (f"Status in one line to {orchestrator_ref()}: what you are doing, what you wait on, "
           f"or 'idle'. Then continue.")
    if dry_run:
        print(f"  (dry-run) would ping {pane}: {msg}", flush=True)
        return True
    r = L.herdr("agent", "prompt", pane, msg, soft=True) or {}
    code = (r.get("error") or {}).get("code")
    if code == "agent_blocked":
        first_line = ((r.get("error") or {}).get("message") or "blocked").splitlines()[0][:200]
        print(f"{name} BLOCKED {first_line}", flush=True)
        return False
    return True


# ------------------------------------------------------------------ memory across restarts

def fingerprint(text):
    """A stable short hash of what a pane said, whitespace-normalised. Stored rather than the text
    itself so `watcher-seen.json` stays small and is never mistaken for a transcript."""
    return hashlib.sha1(" ".join((text or "").split()).encode("utf-8")).hexdigest()[:16]


def seen_load(path=SEEN_PATH):
    try:
        with open(path, encoding="utf-8") as fh:
            d = json.load(fh)
    except (OSError, ValueError):
        return {}
    return d if isinstance(d, dict) else {}


def seen_save(seen, path=SEEN_PATH, live_keys=None):
    """Write the seen map, dropping rows for panes that are gone so it never outgrows the board.
    A failure here is printed, never raised - the watcher must survive a locked or full disk."""
    if live_keys is not None:
        seen = {k: v for k, v in seen.items() if k in live_keys}
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(seen, fh, indent=2)
        os.replace(tmp, path)
    except OSError as e:
        print(f"  -> watcher-seen write failed ({type(e).__name__}: {e})", flush=True)
    return seen


def already_seen(seen, key, said, ts=0):
    """True when this exact stop-reason line has already been emitted, in this run or an earlier
    one. Two guards, because either alone leaks a duplicate after a restart:

      - the text fingerprint catches the same message re-classified;
      - the assistant timestamp catches a message whose text the classifier trimmed differently
        (a longer tail window after a restart) but which has not actually moved.
    """
    e = seen.get(key)
    if not e:
        return False
    if e.get("fp") == fingerprint(said):
        return True
    return bool(ts) and float(e.get("ts") or 0) >= float(ts)


def mark_seen(seen, key, said, ts=0, kind=""):
    seen[key] = {"fp": fingerprint(said), "ts": float(ts or 0), "kind": kind}
    return seen


# ------------------------------------------------------------------ idle-nudge

# ci-routines finished step 3 at 19:12 and sat idle 40 min until the health routine flagged it
# ERROR (2026-09-10) - a human had to re-prompt it by hand. A pane the orchestrator owns that goes
# idle with steps left in its own brief gets nudged once instead.
NUDGE_IDLE_SECS = 10 * 60
NUDGE_COOLDOWN = 60 * 60
NUDGE_STATE_PATH = os.path.join(L.BOARD_DIR, "nudged.json")
NUDGE_MSG = ("Your turn ended with steps left in your brief. Continue with the next step; if you "
             "are actually done, report DONE to the orchestrator per the brief.")


def nudge_load(path=NUDGE_STATE_PATH):
    try:
        with open(path, encoding="utf-8") as fh:
            d = json.load(fh)
    except (OSError, ValueError):
        return {}
    return d if isinstance(d, dict) else {}


def nudge_save(d, path=NUDGE_STATE_PATH):
    """Never lets a state-file problem kill the watcher - same contract as seen_save."""
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(d, fh, indent=2)
        os.replace(tmp, path)
    except OSError as e:
        print(f"  -> nudge state write failed ({type(e).__name__}: {e})", flush=True)


def latest_brief(name, handoffs_dir=None):
    """Path of the newest `task-<name>-*.md` under handoffs, or None."""
    hits = glob.glob(os.path.join(handoffs_dir or L.HANDOFFS, f"task-{name}-*.md"))
    return max(hits, key=os.path.getmtime) if hits else None


def brief_has_steps(text):
    """True when the brief contains a numbered step list - '1. ' or '1) ' at the start of a line."""
    return bool(re.search(r"(?m)^\s*\d+[.)]\s+\S", text or ""))


def already_reported_done(pane_text):
    """True when the pane's own recent output already contains a DONE report - it finished and is
    just waiting on the orchestrator to notice, not stuck with steps left."""
    return bool(re.search(r"\bDONE\b", pane_text or ""))


# A pane parked on Ryan (a gated hardware test, a check he has to answer) is idle on purpose;
# nudging it burns an opus turn an hour and can push it into the gated step. `orch.py hold
# <name> [reason]` writes it here, `orch.py unhold <name>` clears it. lights-fix, 2026-09-11.
HOLDS_PATH = os.path.join(L.BOARD_DIR, "holds.json")


def holds_load(path=HOLDS_PATH):
    try:
        with open(path, encoding="utf-8") as fh:
            d = json.load(fh)
    except (OSError, ValueError):
        return {}
    return d if isinstance(d, dict) else {}


def under_declared_wait(name, entries, holds, now):
    """True when a pane must not be pinged or nudged: it declared `paused: ... until T` (or has a
    `hold --until`) and T has not passed. Includes a wait over 4 h old, which is resurfaced by
    its own WAIT-RECHECK line instead of by a ping. Pure, for tests."""
    return DW.waiting(name, entries, holds, now)[0] in ("active", "stale")


def idle_nudge_check(brief_text, pane_text, name="", holds=None):
    """True when this idle pane should be nudged: its brief has numbered steps, it has not
    already reported DONE, and it is not on hold. Pure (no I/O) so it can be unit-tested."""
    if name and name in (holds or {}):
        return False
    return brief_has_steps(brief_text) and not already_reported_done(pane_text)


# ------------------------------------------------------------------ usage guard

def usage_snapshot(usage_dir=USAGE_DIR):
    """(5h % LEFT, 7d % LEFT, epoch of the reading) from the newest file in the account usage feed
    - the same feed `orch.py doctor` reads. The feed stores used%, not left%; reading one for the
    other burned a whole day once, so the conversion happens here and nowhere else.

    (None, None, 0) when nothing has been written yet."""
    files = [os.path.join(usage_dir, "ambient.json")] + \
        glob.glob(os.path.join(usage_dir, "session", "*.json"))
    best = None
    for f in files:
        try:
            with open(f, encoding="utf-8") as fh:
                o = json.load(fh)
        except (OSError, ValueError):
            continue
        if not isinstance(o, dict):
            continue
        ts = float(o.get("ts") or 0)
        if best is None or ts > best[0]:
            best = (ts, o)
    if not best:
        return None, None, 0
    ts, o = best

    def left(v):
        return None if v is None else max(0, round(100 - float(v)))

    return (left((o.get("five_hour") or {}).get("used_percentage")),
            left((o.get("seven_day") or {}).get("used_percentage")), ts)


def busiest(hist, now, window=3600, n=2):
    """The `n` panes whose context grew most over the last `window` seconds, as [(key, k), ...].
    A pane with one reading has no growth to report and is left out.

    `hist` is keyed by SESSION ID, not display name. Keying it by name merged every session that
    shares one - the box routinely runs several auto-named `vault-xx` panes at once - so the
    growth figure was the sum of two or three unrelated sessions and named only one of them
    (2026-09-14). A rename also silently started a second history under the old code.
    """
    out = []
    for name, pts in hist.items():
        recent = [(t, c) for t, c in pts if t >= now - window]
        if len(recent) < 2:
            continue
        grew = recent[-1][1] - recent[0][1]
        if grew > 0:
            out.append((name, grew))
    out.sort(key=lambda x: -x[1])
    return out[:n]


def stale_worthy(status, agent_status):
    """True only when a pane is genuinely mid-work: the registry says busy AND herdr says the agent
    is working. A pane that is idle, done, blocked or sitting at a shell has stopped on purpose -
    pinging it for a status line asks a parked session to spend a turn saying "still parked"."""
    return status == "busy" and agent_status in ("working", "busy")


def usage_line(five, week, hist, now, label_of=None):
    """`label_of(sid)` turns a session id into something Ryan can actually act on - the pane name
    plus the session prefix and cwd basename, because the name alone can point at three panes."""
    label_of = label_of or (lambda sid: sid[:8])
    pause = (", ".join(f"{label_of(k)} (+{g}k/h)" for k, g in busiest(hist, now))
             or "nothing growing fast")
    return f"USAGE 5h {five}% left, 7d {week}% - pause: {pause}"


def owner_of(sid):
    return ((L.owners_load().get(sid) or {}).get("owner") or "").strip()


POLL = 8
CTX_EVERY = 2           # passes between context scans (~16 s)
PANE_EVERY = 4          # passes between pane-content scans (~32 s)
READY_HOLD = 30         # seconds idle/waiting + pane idle/done must hold before it counts as stopped
STALE_WAIT_SECS = 5 * 60      # how long to give a STALE ping before calling it SILENT
STALE_PING_COOLDOWN = 30 * 60 # never ping the same pane more than once in this window
USAGE_EVERY = 10 * 60         # how often the usage feed is read
USAGE_REPEAT = 30 * 60        # and how often it is allowed to say the same thing
REAP_HOURS = float(os.environ.get("WATCH_REAP_HOURS", 6))   # set 0 to see today's candidates
REAP_EVERY = 3600
# NEW/GONE lines are noise to the orchestrator once projects have leads (Ryan, 2026-09-06:
# "so many things going on at once"). Default quiet; WATCH_QUIET=0 brings them back.
QUIET = os.environ.get("WATCH_QUIET", "1") == "1"


def _flag(key, default=True):
    v = L.cfg(key, default)
    return str(v).strip().lower() not in ("0", "false", "no", "off")


def parse_args(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--stale-min", type=int, default=None,
                    help="override WATCH_STALE_MIN for a throwaway test pane")
    ap.add_argument("--dry-run", action="store_true",
                    help="print the STALE ping instead of sending it")
    ap.add_argument("--usage-5h", type=int, default=None,
                    help="warn under this many %% left in the 5-hour window")
    ap.add_argument("--usage-7d", type=int, default=None,
                    help="warn under this many %% left in the 7-day window")
    return ap.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    start_heartbeat(HEARTBEAT_PATH)
    stale_secs = (args.stale_min or int(os.environ.get("WATCH_STALE_MIN", 30))) * 60
    warn_5h = args.usage_5h if args.usage_5h is not None else int(L.cfg("usage_warn_5h_pct", 15))
    warn_7d = args.usage_7d if args.usage_7d is not None else int(L.cfg("usage_warn_7d_pct", 20))

    prev = {}                # sid -> (status, name), previous poll
    said_stale = set()
    pinged_at = {}           # sid -> time last STALE-pinged (30-min cooldown, across episodes)
    pending_ping = {}        # sid -> (ping_time, last_assistant_ts at ping) awaiting the 5-min check
    said_prompt = set()
    said_mode = set()        # sids already warned that the pane is in auto, not bypass
    ready_since = {}         # sid -> time it first became idle/waiting + pane idle/done
    announced = set()        # sids that got a NEW line (only sessions with a herdr pane qualify)
    manual_seen = set()      # sids of hand-started panes already labelled and announced
    prev_agents = {}         # pane_id -> (label, kind) for non-claude panes, previous poll
    agent_ready = {}         # pane_id -> time that pane first went idle/done
    said_report = set()      # pane_ids whose report file has already been announced
    ctx_hist = {}            # name -> [(epoch, ctx k)], trimmed to the last hour
    wait_slot = {}           # sid -> 4 h slot of a stale declared wait already resurfaced
    seen = seen_load()      # the cross-restart memory: key -> {fp, ts, kind}
    nudged = nudge_load()    # name -> time last idle-nudged (60-min cooldown, across restarts)
    usage_low = False
    last_usage_line = None
    last_usage_warn = 0
    # first usage check a minute in, not on the first pass: the "pause: <panes>" half of the line
    # needs two context readings per pane to have any growth to report.
    last_usage_check = time.time() - USAGE_EVERY + 60
    first = True
    last_reap = 0
    loop = 0

    while True:
        loop += 1
        seen_dirty = False
        try:
            me = L.orchestrator_sid()
            cur = {}
            orch_name = ""
            sess = L.sessions()
            # Ryan 2026-09-14: "our agent isnt reading chats and getting context taken up by their
            # chats themselves. The orchestrator just has to know what it is that got accomplished."
            # A DOMAIN MATE owns a subject and supervises its own crew, so:
            #   - a mate's crew is dropped from `cur` entirely - every line below iterates `cur`,
            #     so the whole crew goes silent here in one place instead of at nine print sites;
            #   - the mate itself stays, but `mates` gags its pane chatter (STALE, CTX, PROMPT,
            #     MODE, dialogs) further down, leaving only the stop-reason line that says what
            #     got accomplished.
            # A crew pane whose mate is NOT live is never silenced: it is orphaned, nobody else is
            # watching it, and that is exactly when the orchestrator has to hear about it.
            mate_names = {m["name"] for m in sess.values() if m.get("domain")}
            mates = set()
            watched = {}      # every live pane, for upkeep (auto-compact) - never for reporting
            for sid, m in sess.items():
                if sid == me:
                    orch_name = m["name"]
                    continue
                watched[sid] = (m["status"], m["name"])
                if m.get("owner") in mate_names and L.owner_is_live(m.get("owner", "")):
                    continue                      # its mate supervises it, not this seat
                if m.get("domain"):
                    mates.add(sid)
                cur[sid] = (m["status"], m["name"])
            by_sid = L.panes_by_sid()
            now = time.time()
            holds_now = DW.active_holds(holds_load(), now)   # an expired `hold --until` is gone

            if not first:
                # Only sessions with a herdr pane get NEW/GONE lines. Headless `claude -p` runs (a
                # builder proving a hook, a routine) register for seconds and have no pane;
                # announcing each one flooded the orchestrator with 8 NEW/GONE pairs in ten minutes
                # on 2026-09-04.
                for sid, (st, name) in cur.items():
                    if sid not in announced and sid in by_sid:
                        announced.add(sid)
                        if not QUIET: print(f"NEW session {name} status={st}", flush=True)

                for sid, (st, name) in prev.items():
                    if sid not in cur:
                        if sid in announced:
                            announced.discard(sid)
                            if not QUIET: print(f"GONE session {name} (exited)", flush=True)
                        ready_since.pop(sid, None)
            if first:
                announced.update(sid for sid in cur if sid in by_sid)

            # A pane the human opened by hand (typed `claude` into a shell) has a session and a
            # herdr pane but no herdr agent name and a bare pane label. Ryan (2026-09-07): "if I
            # manually spin up a new pane, you should automatically see that I spun it up ... and
            # you still manage that pane." Label it with its session name so ls/board/show can find
            # it, and say so even in quiet mode - this line is the orchestrator's only notice.
            names = L.agent_names()
            acp_panes = L.acp_pane_ids()
            for sid, (st, name) in cur.items():
                p = by_sid.get(sid)
                if not p or sid in manual_seen or p["pane_id"] in names or p["pane_id"] in acp_panes:
                    continue
                manual_seen.add(sid)
                if (p.get("label") or "") != name:
                    L.herdr("pane", "rename", p["pane_id"], name, soft=True)
                if not first:
                    print(f"NEW manual {name} pane={p['pane_id']} cwd={p.get('cwd', '?')} | "
                          f"started by hand - prompt it with `herdr agent prompt {p['pane_id']} ...`",
                          flush=True)

            if not first:
                # stop-reason lines: (a) registry idle/waiting, (b) pane agent_status idle/done,
                # (c) held for READY_HOLD seconds straight, (d) the text has not been emitted
                # before - in this run OR in any earlier one (watcher-seen.json).
                for sid, (st, name) in cur.items():
                    p = by_sid.get(sid, {})
                    ast = p.get("agent_status")
                    ready = st in ("idle", "waiting") and ast in ("idle", "done")
                    if not ready:
                        ready_since.pop(sid, None)
                        continue
                    since = ready_since.setdefault(sid, now)
                    if now - since < READY_HOLD:
                        continue
                    ents = L.tail_entries(sid)
                    kind, said = L.classify(sid, ents)
                    ts = L.last_assistant_ts(sid, ents)
                    if already_seen(seen, "sid:" + sid, said, ts):
                        continue
                    print(f"{name} {kind} | {said}", flush=True)
                    L.ledger.write("task.status", name, state=kind.lower(), text=said)
                    # a routine status update ("nothing to do", "holding for go") classifies
                    # QUESTION on the wide ls-facing word list; only a real question mark or an
                    # unambiguous ask for a decision is worth interrupting a human for
                    board_kind = (None if kind == "QUESTION" and not L.looks_like_a_real_question(said)
                                  else kind)
                    if board_kind in ("QUESTION", "BLOCKED", "ERROR"):
                        to_board(board_kind, name, said)
                    board_update(name, kind, said)
                    mark_seen(seen, "sid:" + sid, said, ts, kind)
                    seen_dirty = True
                    said_stale.discard(sid)
                    said_prompt.discard(sid)

                # idle-nudge: an orchestrator-owned pane idle for NUDGE_IDLE_SECS with steps left in
                # its own task brief gets prompted once, instead of sitting until a human notices.
                # Never a review pane (its footer is a fixed gate, not a step list) and never the
                # orchestrator itself (excluded from `cur` above).
                if orch_name:
                    for sid, (st, name) in cur.items():
                        if name.startswith("review-") or owner_of(sid) != orch_name:
                            continue
                        since = ready_since.get(sid)
                        if not since or now - since < NUDGE_IDLE_SECS:
                            continue
                        if now - nudged.get(name, 0) < NUDGE_COOLDOWN:
                            continue
                        p = by_sid.get(sid)
                        if not p or p["pane_id"] in L.acp_pane_ids():
                            continue
                        brief = latest_brief(name)
                        if not brief:
                            continue
                        try:
                            with open(brief, encoding="utf-8") as fh:
                                brief_text = fh.read()
                        except OSError:
                            continue
                        # a declared wait ("paused: ... until T") is idle on purpose, same as a hold
                        if under_declared_wait(name, L.tail_entries(sid), holds_now, now):
                            continue
                        pane_text = L.pane_read(p["pane_id"], 40)
                        if not idle_nudge_check(brief_text, pane_text, name, holds_now):
                            continue
                        L.herdr("agent", "prompt", p["pane_id"], NUDGE_MSG, soft=True)
                        print(f"NUDGE {name}: idle 10m, brief has steps, no DONE", flush=True)
                        nudged[name] = now
                        # drop rows for panes that are gone, so the file never outgrows the board
                        live_names = {n for _, n in cur.values()}
                        nudged = {k: v for k, v in nudged.items() if k in live_names}
                        nudge_save(nudged)

                # context pressure. At or over the compact line the watcher now runs
                # `orch.py compact <name>` itself rather than printing a line and waiting for the
                # orchestrator to notice it - but only on an idle pane, never mid-turn, and never
                # on the orchestrator seat (its own plugin compacts that one).
                if loop % CTX_EVERY == 0:
                    for sid, (st, name) in watched.items():
                        ctx, _ = L.last_usage(sid)
                        pts = ctx_hist.setdefault(sid, [])
                        pts.append((now, ctx))
                        while len(pts) > 2 and pts[1][0] < now - 3600:
                            pts.pop(0)

                # the account usage feed: one line when a window is nearly spent, naming the panes
                # burning it fastest, and one when it clears.
                if now - last_usage_check >= USAGE_EVERY:
                    last_usage_check = now
                    five, week, _uts = usage_snapshot()
                    low = ((five is not None and five < warn_5h)
                           or (week is not None and week < warn_7d))
                    if low:
                        # Repeat only when the line would say something new: a week parked at
                        # 14% with nothing growing printed the same line every tick (2026-09-11).
                        # Key on the two percentages only: the "pause:" pane list shifts every
                        # tick as rates wobble, which made the line repeat anyway.
                        # A blank 5h reading (the feed returns null now and then) is not a change.
                        prev_five = last_usage_line[0] if last_usage_line else None
                        key = (five if five is not None else prev_five, week)
                        if not usage_low or (now - last_usage_warn >= USAGE_REPEAT
                                             and key != last_usage_line):
                            def _label(sid, _w=watched, _p=by_sid):
                                nm = (_w.get(sid) or ("", ""))[1] or "?"
                                cwd = (_p.get(sid) or {}).get("cwd") or ""
                                base = os.path.basename(cwd.rstrip("\\/")) if cwd else ""
                                return f"{nm} [{sid[:8]}{'/' + base if base else ''}]"
                            print(usage_line(five, week, ctx_hist, now, _label), flush=True)
                            last_usage_warn = now
                            last_usage_line = key
                        usage_low = True
                    elif usage_low:
                        print(f"USAGE recovered - 5h {five}% left, 7d {week}%", flush=True)
                        usage_low = False

                # a working session whose last assistant message stopped moving - not file mtime,
                # which every incoming peer message and idle-notice subscription also touches.
                #
                # BOTH sides must say it is working. The registry alone is not enough: it reads
                # `busy` for a pane that has finished and is parked on a human, so STALE fired and
                # pinged learn-python, app-lead-5-3-2 and learn-linreg on 2026-09-09 while herdr
                # showed them idle, shell and done. A pane that has stopped is not stale, it is
                # waiting, and it already has a stop-reason line of its own.
                for sid, (st, name) in cur.items():
                    p = by_sid.get(sid)
                    if st != "busy" or sid in said_stale or sid in mates:
                        continue
                    if not stale_worthy(st, (p or {}).get("agent_status")):
                        continue
                    ts = L.last_assistant_ts(sid)
                    if ts and now - ts > stale_secs:
                        # a pane that declared "paused: ... until T" is not stale until T passes
                        if under_declared_wait(name, L.tail_entries(sid), holds_now, now):
                            continue
                        print(f"{name} STALE {stale_secs // 60}m", flush=True)
                        said_stale.add(sid)
                        if p and now - pinged_at.get(sid, 0) >= STALE_PING_COOLDOWN:
                            if ping_stale(name, p["pane_id"], dry_run=args.dry_run):
                                pinged_at[sid] = now
                                pending_ping[sid] = (now, ts)

                # a declared wait older than 4 h gets one line per 4 h, so a forgotten wait cannot
                # rot unseen. A wait that is still young says nothing; an expired one is just a
                # normal pane again (STALE / nudge / watchdog pick it up).
                if loop % PANE_EVERY == 0:
                    for sid, (st, name) in cur.items():
                        if sid in mates:
                            continue
                        wst, decl = DW.waiting(name, L.tail_entries(sid), holds_now, now)
                        if wst != "stale":
                            wait_slot.pop(sid, None)
                            continue
                        slot = DW.resurface_slot(decl, now)
                        if wait_slot.get(sid) == slot:
                            continue
                        wait_slot[sid] = slot
                        line = f"{DW.describe(decl)} (declared {slot * 4}h+ ago)"
                        print(f"{name} WAIT-RECHECK {slot * 4}h | {line}", flush=True)
                        board_update(name, "WAIT", line)

                # 5 minutes after a STALE ping: silence (no new assistant message) is the only thing
                # worth a line - a reply already reached the orchestrator through SendMessage, and a
                # pane that kept working produced one anyway, so both cases print nothing here.
                for sid in list(pending_ping):
                    if sid not in cur:
                        pending_ping.pop(sid, None)
                        continue
                    ping_time, ts_at_ping = pending_ping[sid]
                    if now - ping_time < STALE_WAIT_SECS:
                        continue
                    pending_ping.pop(sid)
                    if L.last_assistant_ts(sid) > ts_at_ping:
                        said_stale.discard(sid)   # it spoke - a later silence is a fresh episode
                        continue
                    print(f"{cur[sid][1]} SILENT {(stale_secs + STALE_WAIT_SECS) // 60}m "
                          f"(pinged, no reply)", flush=True)

                # pane state: a permission prompt, or the wrong permission mode. The auto-updater
                # flipped settings.json's defaultMode to `auto` twice on 2026-09-03, and every pane
                # spawned after that came up in auto and stalled on its first classifier hit. The
                # "Teach auto mode about your environment?" dialog swallows keystrokes until
                # dismissed, so that one is escaped here and only then reported.
                if loop % PANE_EVERY == 0:
                    for sid, (st, name) in cur.items():
                        p = by_sid.get(sid)
                        if not p or p["pane_id"] in acp_panes:
                            continue        # an ACP pane's terminal is the client's: never type Escape into it
                        txt = L.pane_read(p["pane_id"], 30)
                        mode = L.pane_mode(txt)
                        if mode == "teach-dialog":
                            L.send_keys(p["pane_id"], "escape")
                            print(f"{name} TEACH-DIALOG dismissed | {p['pane_id']}", flush=True)
                            said_mode.discard(sid)
                            continue
                        if mode == "auto" and sid not in said_mode:
                            print(f"{name} MODE auto | {p['pane_id']} - expected bypass; "
                                  f"run fix_mode.py {p['pane_id']}", flush=True)
                            said_mode.add(sid)
                        elif mode == "bypass":
                            said_mode.discard(sid)
                        q = L.pending_prompt(txt)
                        if q and sid not in said_prompt:
                            print(f"{name} PROMPT | {q[:200]}", flush=True)
                            said_prompt.add(sid)

                # non-claude panes (codex and pi): no registry entry and no .jsonl, so everything
                # below comes from herdr plus the text on the pane. Same word rules, same
                # READY_HOLD, same cross-restart memory, keyed on the pane id instead of a session
                # id. `other_panes` (not `agent_panes`) because herdr never flags a pi pane as an
                # agent on Windows - a pi pane is found through the sidecar and its idle/working
                # state read off pi's own spinner.
                side = L.sidecar_load()
                cur_agents = {}
                for pane_id, p in L.other_panes().items():
                    label = side.get(pane_id, {}).get("label") or p.get("label") or pane_id
                    cur_agents[pane_id] = (label, p.get("agent", "?"), p.get("agent_status"))
                for pane_id, (name, akind, _) in cur_agents.items():
                    if pane_id not in prev_agents:
                        if not QUIET: print(f"NEW {akind} {name} pane={pane_id}", flush=True)
                for pane_id, (name, akind, _) in prev_agents.items():
                    if pane_id not in cur_agents:
                        if not QUIET: print(f"GONE {akind} {name} (pane closed)", flush=True)
                        agent_ready.pop(pane_id, None)
                        said_report.discard(pane_id)

                for pane_id, (name, akind, ast) in cur_agents.items():
                    rep = side.get(pane_id, {}).get("report")
                    if rep and pane_id not in said_report and os.path.exists(rep):
                        print(f"{name} REPORT | {rep} ({os.path.getsize(rep)} bytes)", flush=True)
                        board_update(name, "REPORT", f"report written: {rep}")
                        said_report.add(pane_id)
                    if ast not in L.FINISHED_STATUS:
                        agent_ready.pop(pane_id, None)
                        continue
                    since = agent_ready.setdefault(pane_id, now)
                    # reading a pane costs a subprocess each, so only classify on a scan pass
                    if now - since < READY_HOLD or loop % PANE_EVERY:
                        continue
                    kind, said = L.classify_pane(pane_id)
                    if not said or already_seen(seen, "pane:" + pane_id, said):
                        continue
                    print(f"{name} {kind} | {said}", flush=True)
                    L.ledger.write("task.status", name, state=kind.lower(), text=said)
                    board_kind = (None if kind == "QUESTION" and not L.looks_like_a_real_question(said)
                                  else kind)
                    if board_kind in ("QUESTION", "BLOCKED", "ERROR"):
                        to_board(board_kind, name, said)
                    board_update(name, kind, said)
                    mark_seen(seen, "pane:" + pane_id, said, 0, kind)
                    seen_dirty = True
                prev_agents = cur_agents

                if now - last_reap > REAP_EVERY:
                    n = len(reap_candidates(REAP_HOURS))
                    if n:
                        # Housekeeping: one REAP line is never something Ryan acts on, but the
                        # same one hour after hour means panes are piling up unclosed (screen
                        # audit 0913, answer E).
                        say, hits = L.noise_gate("reap")
                        if say:
                            print(f"REAP: {n} candidates ({hits} sweeps running)", flush=True)
                    last_reap = now

                if seen_dirty:
                    live = {"sid:" + s for s in cur} | {"pane:" + p for p in cur_agents}
                    seen = seen_save(seen, live_keys=live)

                # Bridge's Sessions table, kept live off the same 8 s poll instead of waiting for
                # someone to run `board sessions --from-ls` by hand.
                try:
                    B.sync_board(session_rows=B.live_sessions())
                except Exception as e:
                    print(f"  -> board session sync failed ({type(e).__name__}: {e})", flush=True)

            prev = cur
            first = False
        except Exception as e:
            print(f"watch error (continuing): {type(e).__name__}: {e}", flush=True)
        time.sleep(POLL)


if __name__ == "__main__":
    main()
