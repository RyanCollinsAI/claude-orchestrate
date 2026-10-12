"""py test_restart_pane.py - the draft + marker half of `orch.py restart-pane`."""
import json
import os
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import orchlib as L
import restart_pane as P


class MarkerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._old = L.HANDOFFS
        L.HANDOFFS = self.tmp
        P.R.agent_for = lambda pane: {"agent_session": {"value": "sid-1"}}

    def tearDown(self):
        L.HANDOFFS = self._old

    def test_prep_then_pending_round_trip(self):
        m = P.prep("w1:p2", "2.1.292", "half a thought\nsecond line")
        self.assertTrue(os.path.basename(m["draft"]).startswith("drafts-w1_p2-"))
        got = P.pending("w1:p2")
        self.assertEqual(got["text"], "half a thought\nsecond line")
        self.assertEqual((got["sid"], got["from"]), ("sid-1", "2.1.292"))
        self.assertEqual(P.pending("w1:p2"), {})

    def test_empty_draft_writes_no_file(self):
        m = P.prep("w1:p2", "2.1.292", "  \n")
        self.assertEqual(m["draft"], "")
        self.assertEqual(P.pending("w1:p2")["text"], "")

    def test_stale_marker_is_ignored(self):
        P.prep("w1:p2", "2.1.292", "old")
        with open(P.marker_path("w1:p2"), "w", encoding="utf-8") as fh:
            json.dump({"pane": "w1:p2", "at": time.time() - 3600, "draft": ""}, fh)
        self.assertEqual(P.pending("w1:p2"), {})

    def test_no_marker(self):
        self.assertEqual(P.pending("w9:p9"), {})

    def test_acp_pane_is_never_restarted(self):
        from unittest import mock
        with mock.patch.object(L, "acp_pane_ids", return_value={"w1:p2"}):
            ok, why = P.restart("w1:p2")
        self.assertFalse(ok)
        self.assertIn("ACP", why)

    def test_idle_prompt_with_background_monitor_is_restarted(self):
        from unittest import mock
        a = {"pane_id": "w1:p2", "agent_status": "idle", "agent_session": {"value": "sid-1"},
             "tokens": {"bgn": "1", "bg": "1 bg: monitor"}}
        with mock.patch.object(L, "acp_pane_ids", return_value=set()), \
                mock.patch.object(P.R, "agent_for", return_value=a), \
                mock.patch.object(P.R, "restart_pane", return_value=(True, "same session")) as rp, \
                mock.patch.object(P.time, "sleep"):
            ok, _ = P.restart("w1:p2", wait_s=2)
        self.assertTrue(ok)
        rp.assert_called_once_with(a, ignore_bg=True)

    def test_working_turn_is_still_busy(self):
        from unittest import mock
        a = {"pane_id": "w1:p2", "agent_status": "working", "agent_session": {"value": "sid-1"}}
        with mock.patch.object(L, "acp_pane_ids", return_value=set()), \
                mock.patch.object(P.R, "agent_for", return_value=a), \
                mock.patch.object(P.time, "sleep"):
            ok, why = P.restart("w1:p2", wait_s=0.01)
        self.assertEqual((ok, why), (False, "still busy after waiting"))


if __name__ == "__main__":
    unittest.main()
