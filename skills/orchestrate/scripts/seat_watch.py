"""The orchestrator seat's wake channel when the seat is NOT a claude pane.

  py reloader.py seat_watch.py

A claude seat consumes `watch_sessions.py` through the Monitor tool: it arms
`Monitor(command='... watch_sessions.py', persistent=true)` and every line the watcher prints
arrives as a notification. **pi has no Monitor tool.** So a pi seat was told (SKILL.md 3) to sweep
`orch.py ls` on its own beat instead - which spends the seat's own turns on nothing, and fails the
same way a Monitor that never fires does: silence is indistinguishable from a quiet fleet.

This closes that. It runs the REAL watcher as a child process and drops each line it prints into
the seat's orch-pane inbox, where `orch-pane.ts` delivers it as a real user message. The seat gets
woken by its fleet exactly the way a claude seat does.

**It re-implements nothing.** `mate_watch.py` had to import `watch_sessions`' classification
because it filters to one mate's crew; this watches everything the seat watches, which is what
`watch_sessions.py` already prints, so the only thing here is delivery. A second copy of the
READY_HOLD rule or the word lists would drift within a week - there is no second copy.

Exits when the seat is gone or stops being a pi pane, so it cannot fill an inbox nothing drains.
Under `reloader.py` a clean child exit ends the supervisor too.
"""
import argparse
import os
import queue
import subprocess
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import orchlib as L
import watch_sessions as W

SCRIPTS = os.path.dirname(os.path.abspath(__file__))
HEARTBEAT = os.path.join(L.BOARD_DIR, "heartbeat-seat.json")
BATCH_WAIT = 3      # seconds of quiet that close one burst of lines into one prompt to a claude seat
GRACE = 60         # a seat set moments ago may not be visible in herdr's pane list yet

# Lines the seat does not need woken for. The watcher prints its own lifecycle chatter to the same
# stream, and waking a model to tell it a watcher started is pure noise.
SKIP_PREFIXES = ("watch_sessions:", "  -> ", "  (dry-run)", "seat_watch")


def about_the_seat(line, label):
    """True when this line is ABOUT the seat itself.

    THE FEEDBACK LOOP THIS EXISTS TO STOP (measured live 2026-09-21, caught in its first minute):
    `watch_sessions.py` drops the orchestrator from its own report via `orchestrator_sid()`, which
    is None for a pi seat - a pi pane has no session id and arrives through `other_panes()` like
    any other pane. So the seat's OWN reply was classified DONE, delivered into the seat's OWN
    inbox, woke it, and its answer to that was classified DONE again. Three round trips in under
    two minutes, each one spending the seat's model quota on nothing.

    Matching the label as a whole token is deliberately broad: the seat never needs a line about
    itself from any source, and the watcher's line shapes put the subject in three different
    positions (`<name> <kind> |`, `NEW manual <name>`, `NUDGE <name>:`).
    """
    if not label:
        return False
    return label in line.replace(":", " ").replace("|", " ").split()


def seat_pane(claude_ok=False):
    """The live pane holding the seat. By default only a pi pane: a claude seat with a Monitor
    armed must not also be fed, or it would see every line twice. `claude_ok` (--claude) is for a
    claude seat that armed NO Monitor, because Monitor dies at 30 minutes and this does not."""
    seat = L.orchestrator_seat()
    if not seat or ((seat.get("kind") or "claude") == "claude" and not claude_ok):
        return None
    return seat["pane"]


def deliver(pane, line):
    return L.pi_inbox_put(pane, f"[fleet] {line}")


def deliver_claude(lines):
    """One `orch.py prompt orchestrator` for a burst of lines - the same path board_watch uses.
    A claude seat takes each prompt as a turn, so a burst goes in as one message."""
    env = dict(os.environ, ORCH_SENDER="fleet")
    r = subprocess.run([sys.executable, os.path.join(SCRIPTS, "orch.py"), "prompt", "orchestrator",
                        "\n".join(lines)[:1500]], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=60, env=env)
    return r.returncode == 0


def flush_batch(batch):
    if batch and not deliver_claude(batch):
        print(f"seat_watch: prompt to the seat failed; dropped {len(batch)} line(s)", flush=True)
    batch.clear()


def main(argv=None):
    ap = argparse.ArgumentParser(description="wake a non-claude orchestrator seat by inbox")
    ap.add_argument("--grace", type=float, default=GRACE)
    ap.add_argument("--once", action="store_true", help="drain what is buffered, then exit (tests)")
    ap.add_argument("--claude", action="store_true",
                    help="feed a claude seat by prompt (no Monitor armed on the seat)")
    a = ap.parse_args(argv)

    W.start_heartbeat(HEARTBEAT, extra=lambda: {"seat": L.orchestrator_name(),
                                                "pane": seat_pane(a.claude)})
    print(f"seat_watch: started (pid {os.getpid()})", flush=True)

    started = time.time()
    warned_no_extension = False
    child = subprocess.Popen(
        [sys.executable, os.path.join(SCRIPTS, "watch_sessions.py")],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="replace", bufsize=1,
    )
    lines_q = queue.Queue()

    def pump():
        for raw in child.stdout:
            lines_q.put(raw)
        lines_q.put(None)
    threading.Thread(target=pump, daemon=True).start()
    batch = []
    try:
        while True:
            try:
                line = lines_q.get(timeout=BATCH_WAIT)
            except queue.Empty:
                flush_batch(batch)
                continue
            if line is None:
                flush_batch(batch)
                break
            line = line.rstrip()
            if not line or line.startswith(SKIP_PREFIXES):
                continue

            pane = seat_pane(a.claude)
            if not pane:
                if time.time() - started > a.grace:
                    print("seat_watch: the seat is gone or is a claude pane; exiting", flush=True)
                    return 0
                continue
            started = time.time()   # the seat is alive; the grace window is for a MISSING seat

            if about_the_seat(line, (L.orchestrator_seat() or {}).get("label", "")):
                print(f"   (self) {line[:70]}", flush=True)
                continue

            if a.claude:
                batch.append(line)
                print(f"-> seat prompt: {line[:70]}", flush=True)
                if a.once:
                    flush_batch(batch)
                    break
                continue

            # An inbox is only drained while orch-pane.ts is live in that pane. Say so once: an
            # inbox filling with files nobody reads looks exactly like a silent fleet.
            if not warned_no_extension and not L.pi_state(pane):
                warned_no_extension = True
                print(f"seat_watch: pane {pane} has no live orch-pane extension, so inbox files "
                      f"will queue until it comes back", flush=True)

            path = deliver(pane, line)
            print(f"-> seat inbox: {line[:70]} ({os.path.basename(path)})", flush=True)
            if a.once:
                break
    finally:
        try:
            child.terminate()
            child.wait(timeout=10)
        except Exception:
            try:
                child.kill()
            except Exception:
                pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
