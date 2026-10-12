"""Regression test for board_watch.py's empty-poll handling (2026-09-10): a restart of the
lavish-axi server (a patch reload) makes one or two polls come back empty for a few seconds, and
the loop used to treat that exactly like the server being permanently dead and exit outright -
which is what silently dropped board_watch's Monitor the moment a patch touched cli.mjs.

Isolated: no real lavish-axi, no real sleep, no real board (ORCH_BOARD_DIR into a temp dir).

    py test_board_watch_empty_poll.py
"""
import os, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

failures = []


def check(label, cond):
    print(("ok   " if cond else "FAIL ") + label)
    if not cond:
        failures.append(label)


# ignore_cleanup_errors: board_watch writes a board into this directory, and on Windows the
# rmtree at the end of the block intermittently hits "the directory is not empty" on a file whose
# delete has not landed yet - a teardown race in the harness, never the code under test. It failed
# about one run in five, on this test and on HEAD alike, until this flag went in (2026-09-14).
with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
    os.environ["ORCH_BOARD_DIR"] = tmp
    os.environ["PODIUM_SLACK"] = "0"
    import board_watch as BW

    check("delay grows with each attempt",
          [BW.empty_poll_delay(n) for n in range(1, 4)] == list(BW.EMPTY_POLL_BACKOFF[:3]))
    check("delay caps at the table's last entry past EMPTY_POLL_RETRIES",
          BW.empty_poll_delay(BW.EMPTY_POLL_RETRIES + 5) == BW.EMPTY_POLL_BACKOFF[-1])
    check("delay never raises on attempt 0", BW.empty_poll_delay(0) == BW.EMPTY_POLL_BACKOFF[0])

    # Full loop: run_poll always empty, sleep stubbed out so the test doesn't wait for real.
    slept = []
    BW.run_poll = lambda reply=None: ""
    BW.maybe_autopick = lambda: None
    BW.time.sleep = lambda s: slept.append(s)
    printed = []
    real_print = print
    import builtins
    builtins.print = lambda *a, **k: printed.append(" ".join(str(x) for x in a))
    try:
        sys.argv = ["board_watch.py"]
        BW.main()
    finally:
        builtins.print = real_print

    retry_lines = [l for l in printed if l.startswith("board poll empty, retrying in")]
    check("it retries EMPTY_POLL_RETRIES times before giving up",
          len(retry_lines) == BW.EMPTY_POLL_RETRIES)
    check("it sleeps once per retry, matching the backoff table",
          slept == list(BW.EMPTY_POLL_BACKOFF))
    check("it only gives up after exhausting every retry",
          any("is lavish-axi still up" in l for l in printed))
    check("giving up is the last line, not a mid-loop one",
          "is lavish-axi still up" in printed[-1])

    # lavish-axi 0.1.84: a second poller on the board gets LISTENER_ACTIVE. That is neither an
    # empty poll nor a dead server, so it must not eat the retry budget or print the giving-up line.
    outs = iter(["code: LISTENER_ACTIVE\n"] * 3 + [""] * 20)
    BW.run_poll = lambda reply=None: next(outs)
    slept.clear()
    printed.clear()
    builtins.print = lambda *a, **k: printed.append(" ".join(str(x) for x in a))
    try:
        BW.main()
    finally:
        builtins.print = real_print
    check("LISTENER_ACTIVE waits 60s and does not count as an empty poll",
          slept[:3] == [60, 60, 60] and slept[3:] == list(BW.EMPTY_POLL_BACKOFF))
    check("LISTENER_ACTIVE says who holds the board",
          sum("another poller holds the board" in l for l in printed) == 3)

if failures:
    print(f"\n{len(failures)} check(s) failed")
    sys.exit(1)
print("\nall checks passed")
