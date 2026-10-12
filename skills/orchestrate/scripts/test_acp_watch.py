"""py test_acp_watch.py - watch_sessions.main() must never type into an ACP pane's terminal.

`panes_by_sid()` now maps an ACP child's session to its pane, so the generic claude loops reach it.
Its terminal is the client's, not an agent's: Escape, a rename or `herdr agent prompt` would land in
acp_pane.py. A plain claude pane in the same fake fleet is the control: the loop does type into it.
"""
import contextlib
import io
import os
import sys
import tempfile
import unittest
from unittest import mock

os.environ["PODIUM_SLACK"] = "0"
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import watch_sessions as W  # noqa: E402

L = W.L


class Stop(BaseException):
    pass


class AcpWatch(unittest.TestCase):
    def run_loop(self, acp):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        sess = {"sid-orch": {"sessionId": "sid-orch", "name": "orchestrator", "status": "idle"},
                "sid-1": {"sessionId": "sid-1", "name": "child", "status": "busy", "owner": "orchestrator"}}
        by_sid = {"sid-1": {"pane_id": "w1:p1", "agent_status": "working", "cwd": "C:/x", "label": "pane-label"}}
        side = {"w1:p1": {"kind": "acp", "label": "child", "acp_key": "w1-p1"}} if acp else {}
        calls = {"n": 0}
        herdr_calls, keys = [], []

        def sleep(_):
            calls["n"] += 1
            if calls["n"] >= 6:
                raise Stop

        patches = [
            mock.patch.object(L, "BOARD_DIR", tmp.name),
            mock.patch.object(W, "seen_load", lambda *a, **k: {}),
            mock.patch.object(W, "seen_save", lambda seen, *a, **k: seen),
            mock.patch.object(W, "nudge_load", lambda *a, **k: {}),
            mock.patch.object(W, "nudge_save", lambda *a, **k: None),
            mock.patch.object(W, "holds_load", lambda *a, **k: {}),
            mock.patch.object(W, "start_heartbeat"),
            mock.patch.object(W, "POLL", 0),
            mock.patch.object(W, "reap_candidates", return_value=[]),
            mock.patch.object(W, "board_update"),
            mock.patch.object(W, "to_board"),
            mock.patch.object(W.B, "sync_board"),
            mock.patch.object(W.B, "live_sessions", return_value=[]),
            mock.patch.object(L, "orchestrator_sid", return_value="sid-orch"),
            mock.patch.object(L, "orchestrator_name", return_value="orchestrator"),
            mock.patch.object(L, "sessions", return_value=sess),
            mock.patch.object(L, "panes_by_sid", return_value=by_sid),
            mock.patch.object(L, "agent_names", return_value={}),
            mock.patch.object(L, "owners_load", return_value={s: {"owner": "orchestrator"} for s in sess}),
            mock.patch.object(L, "owner_is_live", return_value=False),
            mock.patch.object(L, "tail_entries", return_value=[]),
            mock.patch.object(L, "last_usage", return_value=(100, "m")),
            mock.patch.object(L, "classify", return_value=("DONE", "")),
            mock.patch.object(L, "pane_read", return_value="Teach auto mode about your environment?"),
            mock.patch.object(L, "pane_mode", return_value="teach-dialog"),
            mock.patch.object(L, "pending_prompt", return_value="Allow this? (y/n)"),
            mock.patch.object(L, "other_panes", return_value={}),
            mock.patch.object(L, "sidecar_load", return_value=side),
            mock.patch.object(L, "send_keys", lambda pane, *k: keys.append((pane, k))),
            mock.patch.object(L, "herdr", lambda *a, **k: herdr_calls.append(a) or {}),
            mock.patch.object(W.time, "sleep", sleep),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf), self.assertRaises(Stop):
            W.main(["--stale-min", "1", "--dry-run"])
        return buf.getvalue(), keys, herdr_calls

    def test_the_loop_types_nothing_into_an_acp_pane(self):
        out, keys, herdr_calls = self.run_loop(acp=True)
        self.assertEqual(keys, [], out)
        self.assertEqual([c for c in herdr_calls if c[:2] in (("pane", "rename"), ("agent", "prompt"))], [], out)
        self.assertNotIn("TEACH-DIALOG", out)
        self.assertNotIn("NEW manual", out)

    def test_control_the_same_loop_does_type_into_a_plain_claude_pane(self):
        out, keys, herdr_calls = self.run_loop(acp=False)
        self.assertEqual(keys, [("w1:p1", ("escape",))], out)
        self.assertIn("TEACH-DIALOG dismissed", out)


if __name__ == "__main__":
    unittest.main()
