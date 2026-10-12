import os
import time
"""Monitor script: one line whenever your human touches the board.

  Monitor(command='py ".../scripts/board_watch.py"', persistent=true)

`lavish-axi poll` long-polls and then dumps a whole YAML-ish page including a dom_snapshot that is
several thousand characters of noise. This wraps it in a loop, throws the noise away, and prints
one short line per thing they actually did:

  ANSWER q4 | A: Ask box, who gets to use it?      an answered question, already applied to the board
  NOTE div#questions > form:nth-of-type(1) | ...   an annotation on one element
  MESSAGE | ...                                    freeform text they typed to the agent
  LAYOUT 2 warnings                                the browser reported overflow - fix it first
  SESSION ended                                    he closed the Bridge session; the loop exits

On an ANSWER it also runs `board answer`, so the page moves the question into Done by itself and
they are not asked twice.

  py board_watch.py [--agent-reply "text"] [--once]

`--agent-reply` is passed to the FIRST poll only; that is how a reply shows up in the Bridge
conversation pane. Every print flushes or Monitor never sees it.
"""
import datetime, json, re, subprocess, sys, threading

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import board as B
import orchlib as L                 # noise_gate only
import watch_sessions as W          # start_heartbeat only; one copy, not two
import podium_home as PH

NOISE = ("Still waiting", "Long-polling", "[lavish-axi]", "dom_snapshot:", "next_step:")
CTX_RE = re.compile(r"\n\s*Context data:\s*(\{.*)\Z", re.S)
# "Q5 Some title: the answer" - the shape board.py's queuePrompt always builds
SHAPE_RE = re.compile(r"^(?P<id>[Qq]\S+)\s+(?P<title>.*?):\s*(?P<answer>.*)$", re.S)


def wake_orchestrator(msg):
    """A pi orchestrator has no Monitor, so watcher stdout never reaches it. Drop the same
    line through orch.py prompt, which inboxes a pi seat and herdr-prompts a claude one."""
    orch = os.path.join(os.path.dirname(os.path.abspath(__file__)), "orch.py")
    try:
        subprocess.run([sys.executable, orch, "prompt", "orchestrator", msg[:1500]],
                       capture_output=True, text=True, encoding="utf-8", errors="replace",
                       timeout=30)
    except Exception:
        pass


def run_poll(agent_reply=None):
    args = ["poll", B.HTML, "--owner", "board_watch"] + (["--agent-reply", agent_reply] if agent_reply else [])
    r = subprocess.run(B.lavish_cmd(*args), capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    return (r.stdout or "") + (r.stderr or "")


CALM_OWNER = "board_watch_calm"
# seconds to wait before the calm page is looked at again, by what the last cycle found
CALM_DELAY = {"none": 30, "busy": 60, "empty": 15, "ended": 30, "ok": 1}
_calm_stop = threading.Event()       # Event.wait, not time.sleep: tests stub time.sleep process-wide


def calm_session_open():
    """True when Bridge has an open, recently touched session for board-calm.html (the Calm inbox
    page). No session means nobody is looking at that page, so there is nothing to poll."""
    want = os.path.normcase(os.path.abspath(B.calm_path()))
    cutoff = time.time() - PH.RECENT_DAYS * 86400
    for v in PH._raw_sessions().values():
        if v.get("status") != "open" or not v.get("file"):
            continue
        if os.path.normcase(os.path.abspath(v["file"])) == want and PH._epoch(v.get("updated_at")) >= cutoff:
            return True
    return False


def run_calm_poll():
    args = ["poll", B.calm_path(), "--owner", CALM_OWNER]
    r = subprocess.run(B.lavish_cmd(*args), capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    return (r.stdout or "") + (r.stderr or "")


def calm_cycle(poll=None):
    """One look at the Calm page: poll it and hand every prompt to handle(), the same ANSWER parsing
    path board.html uses (the page's send() is identical, so a calm answer reads the same).
    -> "none" (no session), "busy" (another poller holds it), "ended", "empty" or "ok"."""
    if not calm_session_open():
        return "none"
    out = (poll or run_calm_poll)()
    if "LISTENER_ACTIVE" in out:
        print("calm poll: another poller holds the calm page, checking again in 60s", flush=True)
        return "busy"
    prompts, warns, ended = parse(out)
    for p in prompts:
        handle(p)
    if ended:
        return "ended"
    return "ok" if prompts or warns else "empty"


def calm_loop():
    while not _calm_stop.is_set():
        try:
            res = calm_cycle()
        except Exception as e:
            print("calm poll failed: " + str(e).replace("\n", " ")[:160], flush=True)
            res = "empty"
        _calm_stop.wait(CALM_DELAY.get(res, 30))


def split_row(line):
    """One `"a","b",c,"d"` row -> list of fields. json.decoder handles the \\" and \\n escapes that
    lavish-axi uses inside quoted fields; an unquoted field runs to the next comma."""
    out, i, n = [], 0, len(line)
    dec = json.JSONDecoder()
    while i < n:
        while i < n and line[i] in " \t":
            i += 1
        if i < n and line[i] == '"':
            try:
                val, i = dec.raw_decode(line, i)
            except ValueError:
                out.append(line[i:].strip('"'))
                break
            out.append(val)
        else:
            j = line.find(",", i)
            j = n if j < 0 else j
            out.append(line[i:j].strip())
            i = j
        while i < n and line[i] in " \t":
            i += 1
        if i < n and line[i] == ",":
            i += 1
    return out


def parse(out):
    """-> (prompts as dicts, layout_warning count, session ended?)"""
    prompts, warns, ended = [], 0, False
    fields, in_prompts = [], False
    for line in out.splitlines():
        if any(m in line for m in NOISE):
            continue
        head = re.match(r"^(\w+)\[(\d+)\](?:\{([^}]*)\})?:", line.strip())
        if head:
            key, count = head.group(1), int(head.group(2))
            in_prompts = key == "prompts"
            fields = (head.group(3) or "").split(",") if in_prompts else []
            if key == "layout_warnings":
                warns = count
            continue
        if re.match(r"^\s*status:\s*ended", line):
            ended = True
        if line.strip().startswith("layout_warnings:") and "0" not in line:
            warns = max(warns, 1)
        if in_prompts and line.startswith(("  ", "\t")) and line.strip():
            if not line.strip()[0] in '"0123456789':
                in_prompts = False
                continue
            vals = split_row(line.strip())
            prompts.append(dict(zip(fields, vals)))
    return prompts, warns, ended


def read_answer(p):
    """(question id, answer) from one prompt, or (None, None).

    The `data:` object queuePrompt sends comes back appended to the prompt as `Context data: {...}`
    and is the reliable path; the `Q<id> <title>: <answer>` text shape is the fallback for a prompt
    queued by hand or by an older page."""
    text = p.get("prompt") or p.get("text") or ""
    m = CTX_RE.search(text)
    if m:
        try:
            d = json.loads(m.group(1))
            if d.get("q"):
                return str(d["q"]), str(d.get("answer", ""))
        except ValueError:
            pass
    m = SHAPE_RE.match(text.split("\n\nContext data:")[0].strip())
    if m:
        qid = m.group("id").lower()
        if not qid.startswith("q"):
            qid = "q" + qid
        qid = "q" + qid.lstrip("q")          # Q5 -> q5, and Qq5 -> q5
        return qid, m.group("answer").strip()
    return None, None


def title_of(qid):
    return next((q.get("title", "") for q in B.load()["questions"] if q.get("id") == qid), "")


def render_home():
    """home.html carries the board's open-question and show-block counts; call this any time an
    answer or an auto-pick changes them, so it is never more than one poll-cycle stale. Never
    fatal - a broken home page must not take the board watcher down."""
    try:
        PH.render()
    except Exception as e:
        print(f"HOME render failed: {e}", flush=True)


def handle(p):
    qid, answer = read_answer(p)
    if qid and answer:
        known = any(q.get("id") == qid for q in B.load()["questions"])
        line = f"ANSWER {qid} | {answer}: {title_of(qid)}".rstrip(": ")
        print(line, flush=True)
        wake_orchestrator("[bridge] " + line)
        if known:
            B.cmd_answer(type("A", (), {"qid": qid, "answer": answer})())
            render_home()
        else:
            print(f"  (no {qid} on the board; nothing applied)", flush=True)
        return
    # A freeform chat message arrives with the words in `prompt` and the literal label
    # "Freeform message" in `text` (lavish-axi chrome-client.js sendQueued). Reading `text`
    # first printed the label and dropped everything Ryan typed, 2026-09-07.
    raw = p.get("text") or ""
    if p.get("tag") == "message" or raw.strip() == "Freeform message":
        raw = p.get("prompt") or raw
    full = " ".join((raw or p.get("prompt") or "").split())
    # A dictated message runs past 300 chars and the cut dropped the second half of Ryan's
    # LinkedIn edits on 2026-09-13; nothing on disk had the rest. The full text now lands in
    # board/messages.jsonl and the event line says so.
    text = full[:300]
    if len(full) > 300:
        try:
            with open(MESSAGES_PATH, "a", encoding="utf-8") as fh:
                fh.write(json.dumps({"at": time.time(), "selector": p.get("selector") or "",
                                     "text": full}) + "\n")
            text += f" ... [{len(full)} chars; full text in {MESSAGES_PATH}]"
        except OSError:
            text = full
    sel = p.get("selector") or ""
    if sel:
        line = f"NOTE {sel} | {text}"
    else:
        line = f"MESSAGE | {text}"
    print(line, flush=True)
    wake_orchestrator("[bridge] " + line)


MESSAGES_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "board", "messages.jsonl")
AUTOPICK_EVERY = 3600
_last_autopick = 0.0
_autopick_lock = threading.Lock()


def maybe_autopick():
    """Run the auto-pick clock at most once an hour, from the loop that is already up for the board.

    This is the cheapest home for it: no scheduled task, no second process, and a question whose
    24h ran out is applied within an hour of that. The loop long-polls, so the real gap between two
    checks is "an hour, or the next time your human touches the page" - fine for a 24h clock.
    Never fatal: a bad state file or a dead Slack token must not take the watcher down.

    Called from two threads - the main poll loop and the heartbeat's `_beat_extra` tick every 30s
    (main()) - and `contextlib.redirect_stdout` swaps the process-global `sys.stdout`, so without a
    lock a heartbeat tick landing mid-redirect can restore the real stdout while the other thread's
    print() is still in flight, leaking "autopick: nothing due" straight to the Monitor stream (five
    in one batch, 2026-09-10, right after 27172f7 supposedly swallowed it). The lock also makes the
    rate-limit check-then-set atomic, so two threads can no longer both slip past it at once."""
    global _last_autopick
    with _autopick_lock:
        if time.time() - _last_autopick < AUTOPICK_EVERY:
            return
        _last_autopick = time.time()
        if not B.autopick_enabled():
            return
        try:
            # cmd_autopick prints its own "autopick: nothing due" for the CLI; on this stream every
            # line is a Monitor event, so swallow its stdout and emit only the picks.
            import contextlib
            import io
            with contextlib.redirect_stdout(io.StringIO()):
                picks = B.cmd_autopick(type("A", (), {"dry_run": False})()) or []
            for line in picks:
                print("AUTOPICK " + line, flush=True)
            if picks:
                render_home()
        except SystemExit as e:
            print(f"AUTOPICK skipped: {e}", flush=True)
        except Exception as e:
            print("AUTOPICK failed: " + str(e).replace("\n", " ")[:160], flush=True)


_last_withdraw_aged = 0.0
_withdraw_aged_lock = threading.Lock()

ORCH_PY = os.path.join(os.path.dirname(os.path.abspath(__file__)), "orch.py")
AUTO_CLOSE_EVERY = 1800          # 30 min - matches the 30-min idle+DONE gate auto-close itself uses
_last_auto_close = 0.0
_auto_close_lock = threading.Lock()
_auto_close_since_summary = []   # names closed since the last daily summary line
_auto_close_summary_date = None  # date() the summary was last posted, so it fires once a day


def maybe_auto_close():
    """Run `orch.py auto-close` at most every AUTO_CLOSE_EVERY seconds, same home and reasoning as
    maybe_autopick()/maybe_withdraw_aged() (rate limit, lock, never fatal). Gated behind
    `auto_close_enabled` (default off) - a live run only fires once that has been turned on, and
    the CLI's own `--dry-run` still works with the gate off for a manual check.

    Runs `orch.py` as a subprocess rather than calling `cmd_auto_close` in-process, same as
    `wake_orchestrator` above: it is a short-lived, separate concern from the poll loop, and a bug
    in it must not be able to take the board watcher down with it."""
    global _last_auto_close
    with _auto_close_lock:
        if time.time() - _last_auto_close < AUTO_CLOSE_EVERY:
            return
        _last_auto_close = time.time()
        if not B.auto_close_enabled():
            return
        try:
            r = subprocess.run([sys.executable, ORCH_PY, "auto-close"], capture_output=True,
                               text=True, encoding="utf-8", errors="replace", timeout=120)
        except Exception as e:
            print("AUTO-CLOSE failed: " + str(e).replace("\n", " ")[:160], flush=True)
            return
        closed = [line.split()[1] for line in (r.stdout or "").splitlines()
                  if line.startswith("CLOSED ")]
        for name in closed:
            print(f"AUTO-CLOSE closed {name}", flush=True)
        # Always report a nonzero exit, even when some panes closed before it happened - a crash
        # partway through the subprocess's own candidate loop (cmd_kill raising) must never read as
        # quiet just because the earlier candidates in the same run already printed CLOSED lines
        # (code review 2026-09-24).
        if r.returncode != 0:
            print("AUTO-CLOSE errored: " + (r.stderr or r.stdout or "").replace("\n", " ")[:160],
                 flush=True)
        _auto_close_since_summary.extend(closed)
        maybe_post_auto_close_summary()


REAP_DONE_EVERY = 900            # 15 min
_last_reap_done = 0.0


def maybe_reap_done():
    """Run `orch.py reap --done` every 15 min. Its gates are strict (finished, report delivered,
    not a seat or mate, clean worktree), and every close is logged to reap-done.log with the pane's
    log path. A subprocess, like maybe_auto_close, so a bug in it cannot stop the watcher. Move this
    behind orch-watchdog's pass if that lands."""
    global _last_reap_done
    if time.time() - _last_reap_done < REAP_DONE_EVERY:
        return
    _last_reap_done = time.time()
    try:
        r = subprocess.run([sys.executable, ORCH_PY, "reap", "--done"], capture_output=True,
                           text=True, encoding="utf-8", errors="replace", timeout=180)
    except Exception as e:
        print("REAP-DONE failed: " + str(e).replace("\n", " ")[:160], flush=True)
        return
    for line in (r.stdout or "").splitlines():
        if line.startswith("CLOSED "):
            print("REAP-DONE closed " + line.split()[1], flush=True)
    if r.returncode != 0:
        print("REAP-DONE errored: " + (r.stderr or r.stdout or "").replace("\n", " ")[:160],
              flush=True)


def maybe_post_auto_close_summary():
    """One Done line a day naming everything auto-close closed since the last summary - never one
    per close, so a busy fleet does not turn a 30-minute tick into a running feed on Bridge/Slack.

    The date is stamped, and the pending names cleared, ONLY after `B.cmd_done` actually succeeds -
    two bugs review caught 2026-09-24: stamping the date on a quiet tick (nothing closed yet) made
    the NEXT close that same day silently skip its summary until tomorrow, and clearing the names
    before the post landed lost them for good on a failure. `SystemExit` is caught explicitly
    alongside `Exception` because `B.cmd_done` -> `board.save()`/`load()` can raise it on a corrupt
    state.json, and `except Exception` alone would let that escape and kill the calling thread -
    the same shape `maybe_autopick` already uses for exactly this reason."""
    global _auto_close_summary_date
    if not _auto_close_since_summary:
        return
    today = datetime.date.today()
    if _auto_close_summary_date == today:
        return
    names = list(_auto_close_since_summary)
    text = f"auto-close: closed {len(names)} finished pane(s) - {', '.join(names)}"
    try:
        B.cmd_done(type("A", (), {"text": text, "group": ""})())
    except SystemExit as e:
        print(f"AUTO-CLOSE summary skipped: {e}", flush=True)
        return
    except Exception as e:
        print("AUTO-CLOSE summary failed: " + str(e).replace("\n", " ")[:160], flush=True)
        return
    _auto_close_since_summary.clear()
    _auto_close_summary_date = today
    print("AUTO-CLOSE " + text, flush=True)


def maybe_withdraw_aged():
    """Past 72h: withdraw only what has no asker left and no action-item shape, resurface (once a
    day, AGED-STILL-OPEN, moved to the top) everything else - at most once an hour, same home and
    reasoning as maybe_autopick() (rate limit, lock, never fatal). Gated behind
    withdraw_aged_enabled (default off): the first cut had no gate and withdrew four live asks on
    its very first heartbeat tick (2026-09-13) - see board.py's comment above cmd_withdraw_aged."""
    global _last_withdraw_aged
    with _withdraw_aged_lock:
        if time.time() - _last_withdraw_aged < AUTOPICK_EVERY:
            return
        _last_withdraw_aged = time.time()
        if not B.withdraw_aged_enabled():
            return
        try:
            import contextlib
            import io
            with contextlib.redirect_stdout(io.StringIO()):
                lines = B.cmd_withdraw_aged(type("A", (), {"dry_run": False, "hours": None})()) or []
            for line in lines:
                print(line, flush=True)
            if lines:
                render_home()
        except Exception as e:
            print("WITHDRAW-AGED failed: " + str(e).replace("\n", " ")[:160], flush=True)


HEARTBEAT_PATH = os.path.join(B.BOARD, "heartbeat-board_watch.json")

# An empty poll (no prompts, no warnings, not ended) used to mean "the server went away, stop" -
# but the lavish-axi server also comes back within seconds of a deliberate restart (a patch
# reload), and treating that the same as truly dead cost every open page its poller at once
# (2026-09-10: patching cli.mjs killed board_watch and all three learn/classes pollers together).
# A handful of short, backed-off retries rides out a restart; only a poll that stays empty through
# all of them means the server is actually down.
EMPTY_POLL_RETRIES = 6
EMPTY_POLL_BACKOFF = (2, 4, 8, 16, 32, 60)   # seconds, one per retry attempt; caps at the last


def empty_poll_delay(attempt):
    """Seconds to wait before retry `attempt` (1-indexed). Never raises on attempt <= 0 or one
    past the table's end - clamped to the first/last entry either way."""
    idx = min(max(attempt, 1), len(EMPTY_POLL_BACKOFF)) - 1
    return EMPTY_POLL_BACKOFF[idx]


def main():
    a = sys.argv[1:]
    reply = a[a.index("--agent-reply") + 1] if "--agent-reply" in a else None
    once = "--once" in a
    empty_streak = 0
    # Liveness for routines\orchestrator-health, same reason and same shape as the watcher's:
    # nothing else can tell this loop apart from a dead one, and this loop blocks inside a
    # lavish-axi long poll for minutes at a time, so the heartbeat has to come from its own
    # thread. `last_autopick` rides along because it lives only in this process's memory -
    # there is no on-disk record of when auto-pick last ran (2026-09-09).
    # Auto-pick runs from the heartbeat thread, not the poll loop: run_poll() blocks inside the
    # long poll until the human touches the page, which on a quiet evening is hours, so a loop-side
    # check went 2.5 h without firing and the health routine nudged the orchestrator (2026-09-09).
    # The heartbeat ticks every 30 s regardless; maybe_autopick() rate-limits itself to hourly.
    def _beat_extra():
        maybe_autopick()
        maybe_withdraw_aged()
        maybe_auto_close()
        maybe_reap_done()
        return {"last_autopick": _last_autopick, "last_withdraw_aged": _last_withdraw_aged,
                "last_auto_close": _last_auto_close}
    if not once:
        W.start_heartbeat(HEARTBEAT_PATH, extra=_beat_extra)
        threading.Thread(target=calm_loop, name="calm-poll", daemon=True).start()
    while True:
        maybe_autopick()
        maybe_withdraw_aged()
        maybe_auto_close()
        maybe_reap_done()
        out = run_poll(reply)
        if "LISTENER_ACTIVE" in out:
            # lavish-axi 0.1.84 allows one poller per session: another board_watch (the worktree
            # twin) already holds the board. That is not an empty poll and not a dead server.
            print("board poll: another poller holds the board, checking again in 60s", flush=True)
            if once:
                return
            time.sleep(60)
            continue
        reply = None                         # --agent-reply belongs to the first poll only
        prompts, warns, ended = parse(out)
        if warns:
            # One LAYOUT report is routine and self-correcting; the same one repeating means the
            # page is genuinely broken and nobody fixed it (screen audit 0913, answer E).
            say, hits = L.noise_gate(f"layout:{warns}")
            if say:
                print(f"LAYOUT {warns} warnings (reported {hits}x)", flush=True)
        for p in prompts:
            handle(p)
        if ended:
            print("SESSION ended", flush=True)
            return
        if once:
            return
        if not prompts and not warns:
            empty_streak += 1
            if empty_streak > EMPTY_POLL_RETRIES:
                print("MESSAGE | poll returned empty; is lavish-axi still up?", flush=True)
                return
            delay = empty_poll_delay(empty_streak)
            print(f"board poll empty, retrying in {delay}s", flush=True)
            time.sleep(delay)
        else:
            empty_streak = 0


if __name__ == "__main__":
    main()
