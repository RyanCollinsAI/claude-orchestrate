import os, sys, unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import status_page as S


def row(name, state, space="School", domain="", **kw):
    base = {"name": name, "pane": "w1:p1", "owner": "", "domain": domain, "task": "t", "state": state,
            "why": "", "age": "1m", "age_s": 60, "last": "x", "model": "m", "mb": 10, "bg": "",
            "space": space}
    base.update(kw)
    return base


class PickState(unittest.TestCase):
    def test_waiting_reason_wins_over_working(self):
        self.assertEqual(S.pick_state("working", "DONE", "blocked", 5, False, False), ("needs", "blocked"))

    def test_idle_pane_that_asked_a_question_needs_ryan(self):
        self.assertEqual(S.pick_state("idle", "QUESTION", "", 5, False, False)[0], "needs")

    def test_working_pane_with_a_question_label_is_still_working(self):
        self.assertEqual(S.pick_state("working", "QUESTION", "", 5, False, False)[0], "working")

    def test_working_with_no_output_for_20_minutes_is_stuck(self):
        self.assertEqual(S.pick_state("working", "DONE", "", 20 * 60, False, False)[0], "stuck")

    def test_background_task_keeps_a_quiet_pane_working(self):
        self.assertEqual(S.pick_state("working", "DONE", "", 20 * 60, True, False)[0], "working")

    def test_finished_pane_is_done(self):
        self.assertEqual(S.pick_state("done", "DONE", "", 9999, False, False)[0], "done")


class Page(unittest.TestCase):
    def test_groups_by_space_and_sorts_needs_first(self):
        g = S.group_rows([row("a", "done"), row("b", "needs"), row("c", "working", space="Slack")])
        self.assertEqual([x["name"] for x in g], ["School", "Slack"])
        self.assertEqual([r["name"] for r in g[0]["rows"]], ["b", "a"])

    def test_bar_line_counts_stuck_as_needing_ryan(self):
        self.assertEqual(S.bar_line({"working": 3, "needs": 1, "stuck": 1, "done": 9}), "3 working, 2 need you")

    def test_total_only_on_first_space_and_zero_spaces_clear(self):
        c = lambda w, n, s=0: {"working": w, "needs": n, "stuck": s, "done": 0}
        snap = {"counts": c(3, 1), "by_ws": {"w1": c(1, 0), "w2": c(2, 1), "w3": c(0, 0)}}
        t = S.bar_tokens(snap, [{"workspace_id": "w2", "number": 2}, {"workspace_id": "w1", "number": 1},
                                {"workspace_id": "w3", "number": 3}])
        self.assertEqual(t["w1"], {"fleet": "all: 3 working, 1 need you", "fleetws": None})
        self.assertEqual(t["w2"], {"fleet": None, "fleetws": "2 working, 1 need you"})
        self.assertEqual(t["w3"], {"fleet": None, "fleetws": None})

    def test_render_escapes_pane_text_and_lists_needs(self):
        rows = [row("a<b>", "needs", why="blocked", last="<script>x</script>")]
        snap = {"at": 0, "free_gb": 20.0, "accounts": [{"name": "primary", "five": None, "week": 16,
                                                        "active": False}],
                "counts": S.counts(rows), "groups": S.group_rows(rows), "needs": rows}
        out = S.render(snap)
        self.assertNotIn("<script>x", out)
        self.assertIn("Needs Ryan (1)", out)
        self.assertIn("? / 16%", out)


class Chrome(unittest.TestCase):
    """Shapes from 2026-10-05 23:16: 41 tabs on Ryan's :9333 (personal), the headless agent on
    :9334, and a portless Playwright window (channela)."""
    MAINS = [{"pid": 1, "profile": "personal", "port": "9333"},
             {"pid": 2, "profile": "agent-chrome", "port": "9334"},
             {"pid": 3, "profile": "channela", "port": ""}]

    def rows(self):
        return S.chrome_rows(self.MAINS, {1: 2, 3: 1}, {"9333": 41, "9334": 3}.get)

    def test_counts_windows_and_tabs_per_profile(self):
        r = {x["profile"]: x for x in self.rows()}
        self.assertEqual((r["personal"]["windows"], r["personal"]["tabs"]), (2, 41))
        self.assertEqual((r["agent-chrome"]["windows"], r["agent-chrome"]["tabs"]), (0, 3))
        self.assertEqual((r["channela"]["windows"], r["channela"]["tabs"]), (1, None))

    def test_warns_at_30_tabs_in_one_profile(self):
        r = {x["profile"]: x for x in self.rows()}
        self.assertTrue(r["personal"]["warn"])
        self.assertFalse(r["agent-chrome"]["warn"])
        at = S.chrome_rows([{"pid": 1, "profile": "p", "port": "1"}], {}, lambda _: 30)
        self.assertTrue(at[0]["warn"])
        under = S.chrome_rows([{"pid": 1, "profile": "p", "port": "1"}], {}, lambda _: 29)
        self.assertFalse(under[0]["warn"])

    def test_render_shows_the_table_and_the_warning(self):
        snap = {"at": 0, "free_gb": 20.0, "accounts": [], "chrome": self.rows(),
                "counts": S.counts([]), "groups": [], "needs": []}
        out = S.render(snap)
        self.assertIn("Chrome: personal has 41 tabs", out)
        self.assertIn("44 / 3</b><span>Chrome tabs / windows", out)
        self.assertIn("<td><b>channela</b></td><td>none</td>", out)

    def test_render_without_chrome_data(self):
        snap = {"at": 0, "free_gb": 20.0, "accounts": [], "counts": S.counts([]), "groups": [],
                "needs": []}
        self.assertNotIn("Chrome tabs", S.render(snap))

    def test_reuses_its_own_status_tab_only(self):
        url = "http://127.0.0.1:8791/"
        targets = [{"id": "A", "type": "page", "url": url}, {"id": "B", "type": "page", "url": url}]
        self.assertEqual(S.own_tab(url, targets, {"B": {}}), "B")
        self.assertIsNone(S.own_tab(url, targets, {}))      # someone else's tab: open a new one

    def test_live_counts_run_on_this_box(self):
        rows = S.chrome_rows(S.chrome_mains(), S.chrome_windows(), S.chrome_tabs)
        for r in rows:
            self.assertGreaterEqual(r["instances"], 1)


if __name__ == "__main__":
    unittest.main()
