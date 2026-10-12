"""pane close must kill the pane's process tree first, so herdr's server does not stall waiting for it."""
import json
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import orchlib as L  # noqa: E402

L._HERDR_EXE = "herdr"  # the tests match argv[0] == "herdr"; herdr_exe() resolves a full path


class HerdrExeLookup(unittest.TestCase):
    """Over SSH `herdr` is not on PATH (the Programs\\Herdr\\bin junction is invisible), so fall back to releases."""

    def test_falls_back_to_newest_release_when_not_on_path(self):
        old = L._HERDR_EXE
        L._HERDR_EXE = None
        try:
            with mock.patch("shutil.which", return_value=None), \
                 mock.patch("glob.glob", return_value=["a/herdr.exe", "b/herdr.exe"]), \
                 mock.patch("os.path.getmtime", side_effect=lambda p: {"a/herdr.exe": 1, "b/herdr.exe": 2}[p]):
                self.assertEqual(L.herdr_exe(), "b/herdr.exe")
        finally:
            L._HERDR_EXE = old

    def test_prefers_path(self):
        old = L._HERDR_EXE
        L._HERDR_EXE = None
        try:
            with mock.patch("shutil.which", return_value="X/herdr.exe"):
                self.assertEqual(L.herdr_exe(), "X/herdr.exe")
        finally:
            L._HERDR_EXE = old


class CloseKillsTreeFirst(unittest.TestCase):
    def test_pane_close_taskkills_the_shell_tree_before_closing(self):
        calls = []

        def fake_run(argv, **kw):
            calls.append(list(argv))
            out = ""
            if argv[:3] == ["herdr", "pane", "process-info"]:
                out = '{"result":{"process_info":{"shell_pid":4242}}}'
            elif argv[:3] == ["herdr", "pane", "close"]:
                out = '{"result":{"type":"ok"}}'
            return subprocess.CompletedProcess(argv, 0, stdout=out, stderr="")

        with mock.patch.object(L.subprocess, "run", fake_run):
            L.herdr("pane", "close", "w1:p9")

        calls = [c for c in calls if c[:3] != ["herdr", "pane", "list"]]
        kinds = [c[:3] if c[0] == "herdr" else c[:1] + c[3:5] for c in calls]
        self.assertEqual(kinds[0], ["herdr", "pane", "process-info"])
        self.assertEqual(calls[1], ["taskkill", "/F", "/T", "/PID", "4242"])
        self.assertEqual(calls[2], ["herdr", "pane", "close", "w1:p9"])

    def test_a_failed_lookup_still_closes(self):
        def fake_run(argv, **kw):
            if argv[:3] == ["herdr", "pane", "process-info"]:
                raise OSError("boom")
            return subprocess.CompletedProcess(argv, 0, stdout='{"result":{"type":"ok"}}', stderr="")

        with mock.patch.object(L.subprocess, "run", fake_run):
            self.assertEqual(L.herdr("pane", "close", "w1:p9")["result"]["type"], "ok")

    def test_other_commands_are_untouched(self):
        calls = []

        def fake_run(argv, **kw):
            calls.append(list(argv))
            return subprocess.CompletedProcess(argv, 0, stdout='{"result":{}}', stderr="")

        with mock.patch.object(L.subprocess, "run", fake_run):
            L.herdr("pane", "list")
        self.assertEqual(calls, [["herdr", "pane", "list"]])


class CloseKeepsTheHumansView(unittest.TestCase):
    """A close/move that makes herdr jump the client to another workspace must be undone."""

    def _run(self, args, focus_after):
        calls, lists = [], iter([("w5", "w5:t9"), focus_after])

        def fake_run(argv, **kw):
            calls.append(list(argv))
            out = '{"result":{"type":"ok"}}'
            if argv[:3] == ["herdr", "pane", "list"]:
                ws, tab = next(lists)
                out = json.dumps({"result": {"panes": [
                    {"pane_id": "x", "focused": False, "workspace_id": "w0", "tab_id": "w0:t1"},
                    {"pane_id": "y", "focused": True, "workspace_id": ws, "tab_id": tab}]}})
            elif argv[:3] == ["herdr", "pane", "process-info"]:
                out = '{"result":{"process_info":{}}}'
            return subprocess.CompletedProcess(argv, 0, stdout=out, stderr="")

        with mock.patch.object(L.subprocess, "run", fake_run):
            L.herdr(*args)
        return calls

    def test_view_moved_by_a_close_is_put_back(self):
        calls = self._run(("pane", "close", "wS:p1"), ("wS", "wS:t1"))
        self.assertIn(["herdr", "workspace", "focus", "w5"], calls)
        self.assertIn(["herdr", "tab", "focus", "w5:t9"], calls)

    def test_unmoved_view_is_left_alone(self):
        calls = self._run(("pane", "close", "wS:p1"), ("w5", "w5:t9"))
        self.assertFalse([c for c in calls if c[1] == "workspace"])

    def test_show_keeps_its_deliberate_focus(self):
        calls = self._run(("pane", "move", "w1:p1", "--tab", "w5:t9", "--focus"), ("w5", "w5:t9"))
        self.assertFalse([c for c in calls if c[:3] == ["herdr", "pane", "list"]])


if __name__ == "__main__":
    unittest.main()
