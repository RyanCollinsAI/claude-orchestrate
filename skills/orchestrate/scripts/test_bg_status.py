"""py test_bg_status.py - a pane busy only with background work counts as working.

The bg-visible plugin writes herdr pane tokens `bgn` and `bg`. orchlib.bg_tasks / effective_status
read them; `orch.py ls`, fleet_json (fleet board + band) and restart_idle use the result.
The pane fixture below is the shape `herdr pane list` returned for a pane after
`herdr pane report-metadata w2Z:p0 --source bg-visible-test --token bg="2 bg: pytest, render" --token bgn=2`
(captured 2026-10-04).
"""
import contextlib
import io
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import orchlib as L  # noqa: E402
import orch  # noqa: E402
import restart_idle as R  # noqa: E402

PANE = {"agent": "claude", "agent_status": "idle", "label": "bg-visible", "pane_id": "w2Z:p0",
        "agent_session": {"agent": "claude", "kind": "id", "source": "herdr:claude", "value": "sid-1"},
        "cwd": "C:\\Users\\YOURNAME\\wt\\.claude\\bg-visible", "workspace_id": "w2Z",
        "state_labels": {"idle": "working in bg: pytest"},
        "tokens": {"bg": "2 bg: pytest, render", "bgn": "2"}}


def with_status(status, **extra):
    return {**PANE, "agent_status": status, **extra}


class TestEffectiveStatus(unittest.TestCase):
    def test_idle_with_bg_tasks_is_working(self):
        self.assertEqual(L.effective_status(with_status("idle")), "working")

    def test_done_with_bg_tasks_is_working(self):
        self.assertEqual(L.effective_status(with_status("done")), "working")

    def test_blocked_stays_blocked(self):
        self.assertEqual(L.effective_status(with_status("blocked")), "blocked")

    def test_no_tokens_keeps_the_real_status(self):
        self.assertEqual(L.effective_status({"agent_status": "idle"}), "idle")

    def test_zero_or_garbage_count_is_not_working(self):
        for bgn in ("0", "", "x"):
            p = with_status("idle", tokens={"bgn": bgn})
            self.assertEqual(L.effective_status(p), "idle", bgn)

    def test_missing_pane_is_dash(self):
        self.assertEqual(L.effective_status({}), "-")

    def test_bg_tasks_text(self):
        self.assertEqual(L.bg_tasks(PANE), (2, "2 bg: pytest, render"))
        self.assertEqual(L.bg_tasks({"tokens": {"bgn": "3"}}), (3, "3 bg"))
        self.assertEqual(L.bg_tasks({}), (0, ""))


class TestLs(unittest.TestCase):
    def ls(self, pane):
        sess = {"sid-1": {"name": "bg-visible", "status": "idle", "owner": "orchestrator"}}
        with mock.patch.object(L, "panes_by_sid", return_value={"sid-1": pane}), \
             mock.patch.object(L, "sessions", return_value=sess), \
             mock.patch.object(L, "sidecar_load", return_value={}), \
             mock.patch.object(L, "other_panes", return_value={}), \
             mock.patch.object(L, "tail_entries", return_value=[]), \
             mock.patch.object(L, "last_usage", return_value=(10, "opus-5")), \
             mock.patch.object(L, "classify", return_value=("DONE", "done")), \
             mock.patch.object(L, "workspaces", return_value=[]):
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                orch.cmd_ls()
        return buf.getvalue().splitlines()[1]

    def test_a_bg_pane_shows_working_and_the_task_names(self):
        line = self.ls(with_status("idle"))
        self.assertIn(" working ", line)
        self.assertIn("[2 bg: pytest, render]", line)

    def test_a_plain_idle_pane_still_shows_idle(self):
        line = self.ls({**PANE, "tokens": {}})
        self.assertIn(" idle ", line)
        self.assertNotIn("bg:", line)


class TestRestartIdleSkipsBgPanes(unittest.TestCase):
    def test_a_pane_with_live_bg_tasks_is_queued_not_restarted(self):
        a = {**with_status("idle"), "pane_id": "w1:p1"}
        with mock.patch.object(L, "pane_read", return_value="") as read:
            verdict = R.judge(a, "w9:p9", False, "w0:p0")
        self.assertEqual(verdict[0], "queue")
        read.assert_not_called()


if __name__ == "__main__":
    unittest.main()
