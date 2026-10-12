"""py test_fleet_ledger_emit.py - the merge and cleanup ledger writers. Every test writes to a temp board dir."""
import io
import os
import sys
import tempfile
import threading
import unittest
from contextlib import redirect_stderr, redirect_stdout

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import fleet_ledger_emit as E  # noqa: E402
import orchlib as L  # noqa: E402


class EmitCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.old, L.BOARD_DIR = L.BOARD_DIR, self._tmp.name
        self.addCleanup(self._tmp.cleanup)
        self.addCleanup(lambda: setattr(L, "BOARD_DIR", self.old))
        self.path = os.path.join(self._tmp.name, "fleet-ledger.jsonl")

    def raw_lines(self):
        with open(self.path, encoding="utf-8") as fh:
            return fh.read().splitlines()


class Merged(EmitCase):
    def test_fake_merge_writes_one_line_that_ledger_reads_back(self):
        self.assertTrue(E.merged("kun-k8", via="local", sha="1a2b3c4d", branch="main", repo="claude-config"))
        self.assertEqual(len(self.raw_lines()), 1)
        (r,) = L.ledger.read()
        self.assertEqual((r["v"], r["event"], r["task"]), (1, "task.merged", "kun-k8"))
        self.assertEqual((r["via"], r["sha"], r["branch"], r["repo"]), ("local", "1a2b3c4d", "main", "claude-config"))
        self.assertNotIn("pr", r)

    def test_pr_merge_keeps_the_url(self):
        E.merged("kun-k8", via="pr", pr="https://github.com/acme/repo/pull/7")
        (r,) = L.ledger.read()
        self.assertEqual((r["via"], r["pr"]), ("pr", "https://github.com/acme/repo/pull/7"))

    def test_local_merge_drops_a_stray_pr(self):
        E.merged("kun-k8", via="local", pr="https://x/pull/1")
        self.assertNotIn("pr", L.ledger.read()[0])

    def test_empty_optional_members_are_left_out(self):
        E.merged("kun-k8")
        (r,) = L.ledger.read()
        self.assertEqual(set(r), {"v", "ts", "event", "task", "via"})

    def test_bad_via_writes_nothing_and_does_not_raise(self):
        with redirect_stderr(io.StringIO()) as err:
            self.assertFalse(E.merged("kun-k8", via="squash"))
        self.assertIn("via must be", err.getvalue())
        self.assertFalse(os.path.exists(self.path))

    def test_pr_merge_without_a_url_writes_nothing(self):
        for pr in ("", None):
            with redirect_stderr(io.StringIO()) as err:
                self.assertFalse(E.merged("kun-k8", via="pr", pr=pr))
            self.assertIn("needs a nonempty pr", err.getvalue())
        self.assertFalse(os.path.exists(self.path))

    def test_no_task_returns_false(self):
        with redirect_stderr(io.StringIO()):
            self.assertFalse(E.merged(""))
        self.assertFalse(os.path.exists(self.path))


class Cleaned(EmitCase):
    def test_cleaned_writes_the_ledger_event_name(self):
        self.assertTrue(E.cleaned("kun-k8", via="kill"))
        (r,) = L.ledger.read()
        self.assertEqual((r["event"], r["task"], r["via"]), ("task.cleaned_up", "kun-k8", "kill"))

    def test_bad_via_writes_nothing_and_does_not_raise(self):
        for via in ("nonsense", "merge", ""):
            with redirect_stderr(io.StringIO()) as err:
                self.assertFalse(E.cleaned("kun-k8", via=via))
            self.assertIn("via must be", err.getvalue())
        self.assertFalse(os.path.exists(self.path))

    def test_default_via_is_reap(self):
        E.cleaned("kun-k8")
        self.assertEqual(L.ledger.read()[0]["via"], "reap")


class Lock(EmitCase):
    def test_parallel_writers_lose_no_line(self):
        def work(i):
            for j in range(25):
                (E.merged if j % 2 else E.cleaned)(f"t{i}")
        threads = [threading.Thread(target=work, args=(i,)) for i in range(6)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(len(L.ledger.read()), 150)
        self.assertEqual(len(self.raw_lines()), 150)

    def test_a_merge_line_sits_beside_a_dispatch_line(self):
        L.ledger.write("task.dispatched", "kun-k8", kind="crew", harness="claude")
        E.merged("kun-k8", via="local")
        self.assertEqual([r["event"] for r in L.ledger.read()], ["task.dispatched", "task.merged"])


class Cli(EmitCase):
    def run_cli(self, *argv):
        out = io.StringIO()
        with redirect_stdout(out):
            rc = E.main(list(argv))
        return rc, out.getvalue()

    def test_merged_command(self):
        rc, out = self.run_cli("merged", "--task", "kun-k8", "--via", "pr", "--pr", "https://x/pull/2")
        self.assertEqual(rc, 0)
        self.assertIn("wrote merged kun-k8", out)
        self.assertEqual(L.ledger.read()[0]["pr"], "https://x/pull/2")

    def test_cleaned_command(self):
        rc, _ = self.run_cli("cleaned", "--task", "kun-k8", "--via", "kill")
        self.assertEqual(rc, 0)
        self.assertEqual(L.ledger.read()[0]["via"], "kill")

    def refused(self, *argv):
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as cm:
            self.run_cli(*argv)
        self.assertEqual(cm.exception.code, 2)
        self.assertFalse(os.path.exists(self.path))

    def test_cleaned_with_a_bad_via_is_refused(self):
        self.refused("cleaned", "--task", "kun-k8", "--via", "nonsense")
        self.refused("cleaned", "--task", "kun-k8", "--via", "merge")

    def test_merged_via_pr_without_a_url_is_refused(self):
        self.refused("merged", "--task", "kun-k8", "--via", "pr")
        self.refused("merged", "--task", "kun-k8", "--via", "pr", "--pr", "")

    def test_a_ledger_failure_still_exits_zero(self):
        L.BOARD_DIR = os.path.join(self._tmp.name, "a-file")
        open(L.BOARD_DIR, "w").close()                      # a file where the board dir should be
        with redirect_stderr(io.StringIO()):
            rc, out = self.run_cli("merged", "--task", "kun-k8")
        self.assertEqual(rc, 0)
        self.assertIn("skipped merged", out)


if __name__ == "__main__":
    unittest.main()
