"""py test_ledger.py - orchlib.ledger, the fleet ledger writer. Every test writes to a temp board dir."""
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import orchlib as L  # noqa: E402


class LedgerCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.old, L.BOARD_DIR = L.BOARD_DIR, self._tmp.name
        self.addCleanup(self._tmp.cleanup)
        self.addCleanup(lambda: setattr(L, "BOARD_DIR", self.old))
        self.path = os.path.join(self._tmp.name, "fleet-ledger.jsonl")

    def lines(self):
        with open(self.path, encoding="utf-8") as fh:
            return [json.loads(l) for l in fh]


class Write(LedgerCase):
    def test_one_line_per_event_with_the_common_members(self):
        self.assertTrue(L.ledger.write("task.dispatched", "g7", kind="claude", harness="claude",
                                       model="sonnet", project="claude-config"))
        (r,) = self.lines()
        self.assertEqual((r["v"], r["event"], r["task"]), (1, "task.dispatched", "g7"))
        self.assertEqual((r["kind"], r["harness"], r["model"], r["project"]),
                         ("claude", "claude", "sonnet", "claude-config"))
        self.assertIsInstance(r["ts"], int)

    def test_all_five_events_are_accepted(self):
        for ev in ("task.dispatched", "task.status", "task.pr_ready", "task.merged", "task.cleaned_up"):
            self.assertTrue(L.ledger.write(ev, "t"), ev)
        self.assertEqual([r["event"] for r in self.lines()],
                         ["task.dispatched", "task.status", "task.pr_ready", "task.merged",
                          "task.cleaned_up"])

    def test_task_may_be_a_dict_carrying_its_own_members(self):
        L.ledger.write("task.merged", {"task": "g7", "via": "pr", "pr": "https://x/pull/1"})
        (r,) = self.lines()
        self.assertEqual((r["task"], r["via"], r["pr"]), ("g7", "pr", "https://x/pull/1"))

    def test_a_dict_may_use_name_for_the_task(self):
        L.ledger.write("task.cleaned_up", {"name": "g7"})
        self.assertEqual(self.lines()[0]["task"], "g7")

    def test_a_field_cannot_overwrite_a_common_member(self):
        L.ledger.write("task.status", {"task": "g7", "event": "task.merged", "v": 9, "ts": 1,
                                       "state": "done"})
        (r,) = self.lines()
        self.assertEqual((r["event"], r["v"], r["state"]), ("task.status", 1, "done"))
        self.assertGreater(r["ts"], 1)

    def test_text_is_capped(self):
        L.ledger.write("task.status", "g7", state="done", text="x" * 5000)
        self.assertEqual(len(self.lines()[0]["text"]), L.ledger.TEXT_MAX)

    def test_non_ascii_survives(self):
        L.ledger.write("task.status", "g7", text="café ✓")
        self.assertEqual(self.lines()[0]["text"], "café ✓")

    def test_appends_never_truncate(self):
        for i in range(3):
            L.ledger.write("task.status", "g7", text=str(i))
        self.assertEqual([r["text"] for r in self.lines()], ["0", "1", "2"])


class NeverRaises(LedgerCase):
    def test_unknown_event_and_missing_task_return_false(self):
        self.assertFalse(L.ledger.write("task.exploded", "g7"))
        self.assertFalse(L.ledger.write("task.status", ""))
        self.assertFalse(L.ledger.write("task.status", {}))
        self.assertFalse(os.path.exists(self.path))

    def test_an_unwritable_board_dir_returns_false(self):
        blocker = os.path.join(self._tmp.name, "file")
        open(blocker, "w").close()
        L.BOARD_DIR = os.path.join(blocker, "sub")          # a path under a FILE: makedirs fails
        self.assertFalse(L.ledger.write("task.status", "g7"))


class Read(LedgerCase):
    def test_read_skips_torn_and_foreign_lines(self):
        L.ledger.write("task.status", "a")
        with open(self.path, "a", encoding="utf-8") as fh:
            fh.write('{"v":1,"ts":1,"event":"task.stat\n[1,2]\n{"no":"event"}\n')
        L.ledger.write("task.status", "b")
        self.assertEqual([r["task"] for r in L.ledger.read()], ["a", "b"])

    def test_read_of_no_file_is_empty(self):
        self.assertEqual(L.ledger.read(), [])


class Concurrent(LedgerCase):
    def test_threads_never_interleave_a_line(self):
        def work(n):
            for i in range(50):
                L.ledger.write("task.status", f"t{n}", text="y" * 300 + str(i))
        ts = [threading.Thread(target=work, args=(n,)) for n in range(8)]
        [t.start() for t in ts]
        [t.join() for t in ts]
        self.assertEqual(len(self.lines()), 400)                 # json.loads on every line = none torn

    def test_separate_processes_never_interleave_a_line(self):
        code = ("import sys,orchlib as L\n"
                "for i in range(40): L.ledger.write('task.status', sys.argv[1], text='z'*300+str(i))\n")
        env = dict(os.environ, ORCH_BOARD_DIR=self._tmp.name)
        procs = [subprocess.Popen([sys.executable, "-I", "-c", f"import sys; sys.path.insert(0, {HERE!r})\n" + code,
                                   f"p{n}"], env=env) for n in range(5)]
        [p.wait(timeout=60) for p in procs]
        self.assertEqual(len(self.lines()), 200)


class Producers(LedgerCase):
    def test_board_done_record_writes_status_and_pr_ready(self):
        env = dict(os.environ, ORCH_BOARD_DIR=self._tmp.name, PODIUM_SLACK="0")
        out = subprocess.run([sys.executable, os.path.join(HERE, "board_done.py"), "record", "--session",
                              "pane-a", "--outcome", "shipped, PR https://github.com/acme/repo/pull/7",
                              "--task", "task-pane-a-1", "--group", "g"],
                             capture_output=True, text=True, env=env, timeout=60)
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        got = [(r["event"], r["task"], r.get("state"), r.get("pr")) for r in self.lines()]
        self.assertEqual(got, [("task.status", "pane-a", "done", None),
                               ("task.pr_ready", "pane-a", None, "https://github.com/acme/repo/pull/7")])

    def test_board_done_without_a_pr_writes_only_status(self):
        env = dict(os.environ, ORCH_BOARD_DIR=self._tmp.name, PODIUM_SLACK="0")
        subprocess.run([sys.executable, os.path.join(HERE, "board_done.py"), "record", "--session",
                        "pane-b", "--outcome", "fixed it", "--task", "t", "--group", "g"],
                       capture_output=True, text=True, env=env, timeout=60, check=True)
        self.assertEqual([r["event"] for r in self.lines()], ["task.status"])


if __name__ == "__main__":
    unittest.main()
