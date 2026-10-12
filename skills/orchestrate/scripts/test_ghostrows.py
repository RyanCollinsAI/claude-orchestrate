import json
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(__file__))
import ghostrows as G
import orchlib as L


def row(sid, name, pid, started=1):
    return {"sessionId": sid, "name": name, "pid": pid, "startedAt": started, "status": "idle"}


def reg(*rows):
    return {r["sessionId"]: r for r in rows}


def alive_set(*pids):
    return lambda pid: pid in pids


class Prune(unittest.TestCase):
    def test_resume_new_sid_takes_over_the_name(self):
        # old process still has a file and is even alive; the new one owns the pane
        rows = reg(row("old", "orchestrator", 1), row("new", "orchestrator", 2, 5))
        out = G.prune(rows, lambda: {"new": {"pane_id": "w5:p26"}}, alive=alive_set(1, 2))
        self.assertEqual(list(out), ["new"])

    def test_resume_same_sid_one_row(self):
        rows = reg(row("same", "orchestrator", 2))
        out = G.prune(rows, lambda: self.fail("no name clash, no herdr call"), alive=alive_set(2))
        self.assertEqual(list(out), ["same"])

    def test_restart_idle_old_process_gone(self):
        rows = reg(row("old", "hx-x", 1), row("new", "hx-x", 2))
        out = G.prune(rows, lambda: {"new": {}}, alive=alive_set(2))
        self.assertEqual(list(out), ["new"])

    def test_crashed_process_dropped_even_without_a_clash(self):
        rows = reg(row("dead", "crashed", 1), row("ok", "fine", 2))
        out = G.prune(rows, lambda: {}, alive=alive_set(2))
        self.assertEqual(list(out), ["ok"])

    def test_two_live_panes_same_name_both_kept(self):
        rows = reg(row("a", "twin", 1), row("b", "twin", 2))
        out = G.prune(rows, lambda: {"a": {}, "b": {}}, alive=alive_set(1, 2))
        self.assertEqual(sorted(out), ["a", "b"])

    def test_clash_with_no_pane_keeps_newest(self):
        rows = reg(row("a", "twin", 1, 1), row("b", "twin", 2, 9))
        out = G.prune(rows, lambda: {}, alive=alive_set(1, 2))
        self.assertEqual(list(out), ["b"])

    def test_row_without_pid_is_kept(self):
        r = row("x", "n", 1)
        del r["pid"]
        self.assertEqual(list(G.prune(reg(r), lambda: {})), ["x"])

    def test_pid_alive_self_and_garbage(self):
        self.assertTrue(G.pid_alive(os.getpid()))
        self.assertFalse(G.pid_alive(None))
        self.assertFalse(G.pid_alive(0))


class RegistryFiles(unittest.TestCase):
    def test_ls_registry_has_one_orchestrator_after_a_resume(self):
        with tempfile.TemporaryDirectory() as d:
            for pid, sid, upd in ((os.getpid(), "new", 9), (2 ** 22 + 7, "old", 1)):
                with open(os.path.join(d, f"{pid}.json"), "w") as fh:
                    json.dump({**row(sid, "orchestrator", pid), "updatedAt": upd}, fh)
            with mock.patch.object(L, "SESS", d), \
                 mock.patch.object(L, "panes_by_sid", lambda: {"new": {}}):
                names = [m["name"] for m in L._registry().values()]
        self.assertEqual(names, ["orchestrator"])

    def test_same_sid_in_two_files_newest_wins(self):
        with tempfile.TemporaryDirectory() as d:
            for pid, upd in ((os.getpid(), 9), (2 ** 22 + 7, 1)):
                with open(os.path.join(d, f"{pid}.json"), "w") as fh:
                    json.dump({**row("s", "o", pid), "updatedAt": upd}, fh)
            with mock.patch.object(L, "SESS", d):
                out = L._registry()
        self.assertEqual(out["s"]["pid"], os.getpid())


if __name__ == "__main__":
    unittest.main()
