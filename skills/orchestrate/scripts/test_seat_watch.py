"""py test_seat_watch.py - the non-claude orchestrator seat's wake channel.

The one that matters is the feedback loop: it happened live on 2026-09-21, in the first minute the
channel was armed, and it spends the seat's model quota on nothing until someone notices.
"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import orchlib as L  # noqa: E402
import seat_watch as S  # noqa: E402


class SeatNeverHearsAboutItself(unittest.TestCase):
    """watch_sessions drops the orchestrator using orchestrator_sid(), which is None for a pi seat,
    so the seat's own lines DO reach this delivery step. They must die here."""

    def test_the_seats_own_done_line_is_dropped(self):
        self.assertTrue(S.about_the_seat("orch-grok DONE | Fleet note only: seat-probe DONE.",
                                         "orch-grok"))

    def test_the_exact_line_that_looped(self):
        # Verbatim from board/seat-watch.log, 2026-09-21 14:06:48.
        self.assertTrue(S.about_the_seat(
            "orch-grok DONE | Fleet note only: `seat-probe` DONE. Not my pane; no a", "orch-grok"))

    def test_every_line_shape_the_watcher_emits_about_the_seat(self):
        for line in ("orch-grok DONE | x",
                     "orch-grok QUESTION | x",
                     "NEW manual orch-grok pane=w5:p8 cwd=C:/x",
                     "NEW session orch-grok status=idle",
                     "GONE session orch-grok (exited)",
                     "NUDGE orch-grok: idle 10m, brief has steps, no DONE",
                     "orch-grok STALE 12m",
                     "orch-grok BLOCKED something"):
            self.assertTrue(S.about_the_seat(line, "orch-grok"), line)

    def test_another_panes_line_still_gets_through(self):
        for line in ("seat-probe DONE | PROBE DONE.",
                     "vault-b4 QUESTION | which option",
                     "NEW manual vault-2a pane=w5:p6 cwd=C:/x"):
            self.assertFalse(S.about_the_seat(line, "orch-grok"), line)

    def test_a_substring_of_the_label_is_not_the_seat(self):
        # `orch-grok-2` is a different pane; a naive `in` test would have swallowed its lines.
        self.assertFalse(S.about_the_seat("orch-grok-2 DONE | x", "orch-grok"))

    def test_no_label_drops_nothing(self):
        self.assertFalse(S.about_the_seat("anything DONE | x", ""))


class OnlyANonClaudeSeatIsFed(unittest.TestCase):
    """A claude seat consumes the watcher through its own Monitor. Feeding it by inbox as well
    would show it every line twice."""

    def test_a_claude_seat_is_not_fed(self):
        with mock.patch.object(L, "orchestrator_seat",
                               return_value={"pane": "w5:p1", "label": "vault-c6",
                                             "kind": "claude", "sid": "abc"}):
            self.assertIsNone(S.seat_pane())

    def test_a_claude_seat_with_no_monitor_is_fed_on_request(self):
        with mock.patch.object(L, "orchestrator_seat",
                               return_value={"pane": "w5:p1", "label": "vault-c6",
                                             "kind": "claude", "sid": "abc"}):
            self.assertEqual(S.seat_pane(claude_ok=True), "w5:p1")

    def test_a_burst_goes_to_the_claude_seat_as_one_prompt(self):
        with mock.patch.object(S.subprocess, "run") as run:
            run.return_value.returncode = 0
            batch = ["a DONE | x", "b ERROR | y"]
            S.flush_batch(batch)
        self.assertEqual(run.call_count, 1)
        argv = run.call_args[0][0]
        self.assertEqual(argv[2:4], ["prompt", "orchestrator"])
        self.assertEqual(argv[4], "a DONE | x\nb ERROR | y")
        self.assertEqual(batch, [])

    def test_an_empty_batch_sends_nothing(self):
        with mock.patch.object(S.subprocess, "run") as run:
            S.flush_batch([])
        run.assert_not_called()

    def test_no_seat_at_all_is_not_fed(self):
        with mock.patch.object(L, "orchestrator_seat", return_value={}):
            self.assertIsNone(S.seat_pane())

    def test_a_pi_seat_is_fed(self):
        with mock.patch.object(L, "orchestrator_seat",
                               return_value={"pane": "w5:p8", "label": "orch-grok",
                                             "kind": "pi", "sid": ""}):
            self.assertEqual(S.seat_pane(), "w5:p8")


class WatcherChatterIsNotDelivered(unittest.TestCase):
    def test_lifecycle_lines_are_skipped(self):
        for line in ("watch_sessions: started (pid 1)",
                     "  -> board q1",
                     "seat_watch: started (pid 2)"):
            self.assertTrue(line.startswith(S.SKIP_PREFIXES), line)

    def test_a_real_line_is_not_skipped(self):
        self.assertFalse("seat-probe DONE | PROBE DONE.".startswith(S.SKIP_PREFIXES))


if __name__ == "__main__":
    unittest.main(verbosity=1)
