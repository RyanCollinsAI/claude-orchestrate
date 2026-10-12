"""podium_watch against lavish-axi 0.1.84's one-poller-per-session rule.

    py test_podium_watch_listener.py
"""
import os
import sys
import threading
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import podium_watch as PW

failures = []


def check(label, cond):
    print(("ok   " if cond else "FAIL ") + label)
    if not cond:
        failures.append(label)


ACTIVE = ('error: "Lavish Editor already has an active poll listener (current listener: {who}; '
          'active for 4001ms)"\ncode: LISTENER_ACTIVE\n'
          'help[1]: Use --takeover only when you intend to displace the current listener\n')
REPLACED = "error: Lavish Editor poll listener was replaced by a takeover\ncode: LISTENER_REPLACED\n"
EMPTY = "session:\n  file: x\n  status: waiting\n"

check("active names the holder", PW.listener_conflict(ACTIVE.format(who="learn-linreg")) == ("active", "learn-linreg"))
check("active without a label", PW.listener_conflict("code: LISTENER_ACTIVE\n") == ("active", ""))
check("replaced", PW.listener_conflict(REPLACED)[0] == "replaced")
check("a normal poll is no conflict", PW.listener_conflict(EMPTY) == ("", ""))

path = r"C:\x\page.html"


def run_watch(outputs):
    calls = []

    def fake_poll(p, takeover=False):
        calls.append(takeover)
        return outputs[len(calls) - 1] if len(calls) <= len(outputs) else EMPTY

    stop = threading.Event()
    with mock.patch.object(PW, "owner_is_working", return_value=False), \
            mock.patch.object(PW, "poll_once", side_effect=fake_poll), \
            mock.patch.object(PW.BW, "parse", side_effect=lambda out: ([], 0, "status: ended" in out)):
        PW.watch(path, stop)
    return calls


calls = run_watch([ACTIVE.format(who="learn-linreg"), "status: ended\n"])
check("stale poller: first poll plain, retry takes over", calls == [False, True])

calls = run_watch([ACTIVE.format(who=PW.OWNER_LABEL)])
check("another podium_watch holds it: stand down, no takeover", calls == [False])

calls = run_watch([REPLACED])
check("displaced by the page owner: stand down", calls == [False])

if failures:
    print(f"\n{len(failures)} check(s) failed")
    sys.exit(1)
print("\nall checks passed")
