"""Stand down only while the Bridge page's owner is working.

    py test_podium_watch_standdown.py
"""
import os
import sys
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import podium_watch as PW

failures = []


def check(label, cond):
    print(("ok   " if cond else "FAIL ") + label)
    if not cond:
        failures.append(label)


path = r"C:\x\meditations-podium.html"

with mock.patch.object(PW, "live_owner", return_value="learn"):
    with mock.patch.object(PW.L, "panes", return_value=[
            {"label": "learn", "agent_status": "idle"}]):
        check("idle owner is not working", PW.owner_is_working(path) is False)
    with mock.patch.object(PW.L, "panes", return_value=[
            {"label": "learn", "agent_status": "done"}]):
        check("DONE owner is not working", PW.owner_is_working(path) is False)
    with mock.patch.object(PW.L, "panes", return_value=[
            {"label": "learn", "agent_status": "working"}]):
        check("working owner is working", PW.owner_is_working(path) is True)
    with mock.patch.object(PW.L, "panes", return_value=[
            {"label": "other", "agent_status": "working"}]):
        check("a different working pane does not count", PW.owner_is_working(path) is False)

with mock.patch.object(PW, "live_owner", return_value=""):
    with mock.patch.object(PW.L, "panes", return_value=[
            {"label": "learn", "agent_status": "working"}]):
        check("no owner is not working", PW.owner_is_working(path) is False)

if failures:
    print(f"\n{len(failures)} check(s) failed")
    sys.exit(1)
print("\nall checks passed")
