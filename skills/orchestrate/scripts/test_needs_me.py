"""Tests for needs_me.py: which panes wait on Ryan, oldest first, and how --next walks them."""
import os, sys, unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import needs_me as N


def pane(pid, label, status="idle", focused=False):
    return {"pane_id": pid, "label": label, "agent_status": status, "focused": focused}


class TestWaiting(unittest.TestCase):
    def test_blocked_question_and_hold_all_count_oldest_first(self):
        panes = [pane("w1:p1", "a", "blocked"), pane("w1:p2", "b"), pane("w1:p3", "c"),
                 pane("w1:p4", "d", "working")]
        qs = [{"id": "q1", "from": "b", "created": "2026-10-02T10:00:00-07:00"},
              {"id": "q2", "from": "d", "answered": "2026-10-02T11:00:00-07:00"}]
        holds = {"c": {"reason": "needs a pick", "ts": 1000.0}}
        rows, seen = N.waiting(panes, qs, holds, {"w1:p1": 5000.0}, now=9e9)
        self.assertEqual([r[2] for r in rows], ["c", "a", "b"])
        self.assertNotIn("w1:p4", [r[1] for r in rows])
        self.assertEqual(seen, {"w1:p1": 5000.0})

    def test_new_blocked_pane_is_stamped_now_and_idle_panes_never_count(self):
        rows, seen = N.waiting([pane("w1:p1", "a", "blocked"), pane("w1:p2", "b", "done")],
                               [], {}, {}, now=123.0)
        self.assertEqual([r[1] for r in rows], ["w1:p1"])
        self.assertEqual(seen, {"w1:p1": 123.0})

    def test_one_row_per_pane_oldest_reason_wins(self):
        rows, _ = N.waiting([pane("w1:p1", "a", "blocked")],
                            [{"id": "q9", "from": "a", "created": "2026-10-02T10:00:00-07:00"}],
                            {}, {"w1:p1": 9e12}, now=9e12)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][3], "question q9")

    def test_question_from_a_dead_pane_is_ignored(self):
        rows, _ = N.waiting([pane("w1:p1", "a")], [{"id": "q1", "from": "gone"}], {}, {})
        self.assertEqual(rows, [])


class TestPickNext(unittest.TestCase):
    ROWS = [(1.0, "p1", "a", "blocked"), (2.0, "p2", "b", "blocked"), (3.0, "p3", "c", "blocked")]

    def test_walks_oldest_first_then_wraps(self):
        v, seq = [], []
        for _ in range(4):
            row, v = N.pick_next(self.ROWS, v)
            seq.append(row[1])
        self.assertEqual(seq, ["p1", "p2", "p3", "p1"])

    def test_pane_already_focused_is_skipped(self):
        row, v = N.pick_next(self.ROWS, [], focused_pane="p1")
        self.assertEqual(row[1], "p2")

    def test_visited_pane_that_stopped_waiting_is_forgotten(self):
        row, v = N.pick_next(self.ROWS[1:], ["p1", "p2"])
        self.assertEqual(row[1], "p3")
        self.assertNotIn("p1", v)

    def test_nothing_waiting_returns_none(self):
        self.assertEqual(N.pick_next([], ["p1"]), (None, []))


class TestCmd(unittest.TestCase):
    def test_next_focuses_oldest_and_reports_how_many_are_left(self):
        panes = [pane("w1:p1", "a", "blocked"), pane("w1:p2", "b", "blocked", focused=True),
                 pane("w1:p3", "c", "blocked")]
        out = []
        with mock.patch.object(N.L, "panes", return_value=panes), \
             mock.patch.object(N, "_load", side_effect=lambda p, d: {"seen": {"w1:p1": 1.0, "w1:p2": 2.0, "w1:p3": 3.0}}
                               if p == N.STATE else d), \
             mock.patch.object(N, "_save") as save, \
             mock.patch.object(N, "_focus", return_value=True) as focus, \
             mock.patch.object(N, "_toast"):
            N.cmd_needs_me(True, out.append)
        self.assertEqual(focus.call_args[0][0], "w1:p1")
        self.assertIn("a (blocked", out[0])
        self.assertIn("1 more waiting", out[0])      # p2 is where he is, p3 is the one left
        self.assertEqual(save.call_args[0][1]["visited"], ["w1:p2", "w1:p1"])

    def test_listing_never_focuses(self):
        with mock.patch.object(N.L, "panes", return_value=[pane("w1:p1", "a", "blocked")]), \
             mock.patch.object(N, "_load", side_effect=lambda p, d: d), \
             mock.patch.object(N, "_save"), \
             mock.patch.object(N, "_focus") as focus:
            N.cmd_needs_me(False, lambda s: None)
        focus.assert_not_called()


if __name__ == "__main__":
    unittest.main()
