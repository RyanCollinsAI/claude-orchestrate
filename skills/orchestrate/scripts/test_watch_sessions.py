"""Tests for watcher v2 (2026-09-09), run with `py test_watch_sessions.py`.

  WV2-001  a stop-reason line already emitted before a restart is not emitted again
           (app-lead's e2e DONE line fired three times on 2026-09-09, once per Monitor restart)
  WV2-002  watcher-seen.json survives a round trip and drops rows for panes that are gone
  WV2-003  the sessions table's `doing` prefers the pane's own report to the orchestrator, then
           its last assistant message, then the terminal-title scrape - and never the tail
           fragment the old `classify()[1][:60]` produced ("ed migration. It turned out to be")
  WV2-004  usage_snapshot reads the newest file in the feed and converts used% to left%
  WV2-005  busiest() names the panes whose context grew most in the last hour

Plain unittest, no dependencies, no network, no herdr. Everything touching disk goes to a temp dir.
"""
import datetime, json, os, sys, tempfile, unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import board as B
import watch_sessions as W


def entry(kind, text, ts, tool_use=None):
    """One raw .jsonl line as Claude Code writes it - the tests feed these to the readers directly
    so nothing here depends on a live session."""
    content = [{"type": "text", "text": text}] if text else []
    if tool_use:
        content.append({"type": "tool_use", "name": "SendMessage", "input": tool_use})
    return json.dumps({"type": kind, "timestamp": ts,
                       "message": {"role": kind, "content": content, "usage": {}}})


def iso(minutes_ago):
    t = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(minutes=minutes_ago)
    return t.isoformat().replace("+00:00", "Z")


class TestSeenSurvivesARestart(unittest.TestCase):
    """WV2-001 / WV2-002: the watcher's memory of what it has already said."""

    def test_the_same_line_is_skipped_after_a_restart(self):
        said = "The e2e run is green. Ship it?"
        ts = 1788970000.0
        seen = {}
        self.assertFalse(W.already_seen(seen, "sid:abc", said, ts), "first sighting must fire")
        W.mark_seen(seen, "sid:abc", said, ts, "QUESTION")
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "watcher-seen.json")
            W.seen_save(seen, path=path)
            reloaded = W.seen_load(path)          # this is what a Monitor restart does
        self.assertTrue(W.already_seen(reloaded, "sid:abc", said, ts),
                        "a restart re-fired a line it had already emitted")

    def test_a_trimmed_retelling_of_the_same_message_is_still_skipped(self):
        """The classifier keeps the last 300 chars, so a bigger tail window after a restart can
        hand back a slightly different string for the same message. The timestamp guard covers it."""
        seen = W.mark_seen({}, "sid:abc", "...tail of the message", 1788970000.0)
        self.assertTrue(W.already_seen(seen, "sid:abc", "a longer tail of the message",
                                       1788970000.0))

    def test_a_genuinely_new_message_still_fires(self):
        seen = W.mark_seen({}, "sid:abc", "old news", 1788970000.0)
        self.assertFalse(W.already_seen(seen, "sid:abc", "new news", 1788970900.0))

    def test_rows_for_dead_panes_are_dropped_on_save(self):
        seen = W.mark_seen(W.mark_seen({}, "sid:live", "a", 1), "sid:dead", "b", 2)
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "watcher-seen.json")
            W.seen_save(seen, path=path, live_keys={"sid:live"})
            kept = W.seen_load(path)
        self.assertEqual(list(kept), ["sid:live"])

    def test_a_missing_or_corrupt_file_reads_as_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(W.seen_load(os.path.join(tmp, "nope.json")), {})
            bad = os.path.join(tmp, "bad.json")
            with open(bad, "w", encoding="utf-8") as fh:
                fh.write("{not json")
            self.assertEqual(W.seen_load(bad), {})


class TestDoingRow(unittest.TestCase):
    """WV2-003: what the Sessions table says a pane is doing."""

    def test_the_report_to_the_orchestrator_wins(self):
        ents = [
            entry("assistant", "Let me check the migration first.", iso(20)),
            entry("assistant", "", iso(5), tool_use={
                "to": "orchestrator", "message": "Rewrote the GE import so it reads the new column.\n"
                                                 "Details follow.", "summary": "GE import"}),
        ]
        self.assertEqual(B.doing_line(ents, {}),
                         "Rewrote the GE import so it reads the new column.")

    def test_the_last_assistant_message_wins_when_it_is_newer(self):
        ents = [
            entry("assistant", "", iso(30), tool_use={"to": "orchestrator", "message": "old report"}),
            entry("assistant", "Now running the e2e gate.\nThen I will report.", iso(2)),
        ]
        self.assertEqual(B.doing_line(ents, {}), "Now running the e2e gate.")

    def test_the_terminal_title_is_the_last_resort(self):
        self.assertEqual(B.doing_line([], {"terminal_title_stripped": "app-lead-5-3"}), "app-lead-5-3")

    def test_it_is_never_a_fragment_cut_open_at_the_front(self):
        """The old row was classify()[1][:60] - the first 60 chars of the LAST 300."""
        body = ("I started on the seat migration. " * 20) + "It turned out to be a column rename."
        ents = [entry("assistant", body, iso(1))]
        row = B.doing_line(ents, {})
        self.assertTrue(row.startswith("I started on the seat migration."), row)
        self.assertNotIn("ed migration", row[:20])

    def test_it_caps_at_120_and_cuts_at_a_sentence_end(self):
        line = ("The auto-edit pass finished on folder 1981 and the render is queued behind two "
                "others. I will report again when it lands. Nothing is blocked.")
        row = B.clip(line)
        self.assertLessEqual(len(row), 120)
        self.assertTrue(row.endswith("."), row)
        self.assertTrue(row.startswith("The auto-edit pass finished"), row)

    def test_a_single_long_sentence_is_truncated_rather_than_dropped(self):
        row = B.clip("x" * 400)
        self.assertLessEqual(len(row), 120)
        self.assertTrue(row.endswith("..."))

    def test_markdown_and_line_breaks_are_stripped(self):
        self.assertEqual(B.clip("**Done.**\nnext line"), "Done.")


class TestUsageGuard(unittest.TestCase):
    """WV2-004 / WV2-005."""

    def test_it_reads_the_newest_file_and_reports_percent_left(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, "session"))
            with open(os.path.join(tmp, "ambient.json"), "w", encoding="utf-8") as fh:
                json.dump({"ts": 100, "five_hour": {"used_percentage": 10},
                           "seven_day": {"used_percentage": 10}}, fh)
            with open(os.path.join(tmp, "session", "a.json"), "w", encoding="utf-8") as fh:
                json.dump({"ts": 200, "five_hour": {"used_percentage": 88},
                           "seven_day": {"used_percentage": 59.00000001}}, fh)
            five, week, ts = W.usage_snapshot(tmp)
        self.assertEqual((five, week, ts), (12, 41, 200))

    def test_an_empty_feed_is_not_an_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(W.usage_snapshot(tmp), (None, None, 0))

    def test_a_corrupt_file_is_skipped_not_fatal(self):
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "ambient.json"), "w", encoding="utf-8") as fh:
                fh.write("{oops")
            self.assertEqual(W.usage_snapshot(tmp), (None, None, 0))

    def test_busiest_names_the_two_fastest_growing_panes(self):
        now = 1_000_000.0
        hist = {"slow": [(now - 3000, 100), (now, 110)],
                "fast": [(now - 3000, 100), (now, 300)],
                "middle": [(now - 3000, 100), (now, 150)],
                "one-reading": [(now, 900)]}
        self.assertEqual(W.busiest(hist, now), [("fast", 200), ("middle", 50)])

    def test_a_pane_that_shrank_is_not_named(self):
        now = 1_000_000.0
        self.assertEqual(W.busiest({"compacted": [(now - 100, 700), (now, 90)]}, now), [])

    def test_the_line_reads_the_way_the_orchestrator_expects(self):
        now = 1_000_000.0
        hist = {"app-lead": [(now - 100, 100), (now, 180)]}
        self.assertEqual(W.usage_line(12, 41, hist, now),
                         "USAGE 5h 12% left, 7d 41% - pause: app-lead (+80k/h)")

    def test_the_line_survives_having_nothing_to_blame(self):
        self.assertIn("nothing growing fast", W.usage_line(9, 50, {}, 1.0))


class TestUsageThresholdWiring(unittest.TestCase):
    """The flags the usage burn-rate line reads."""

    def test_the_usage_thresholds_can_be_forced(self):
        a = W.parse_args(["--usage-5h", "99", "--usage-7d", "98"])
        self.assertEqual((a.usage_5h, a.usage_7d), (99, 98))


class TestIdleNudge(unittest.TestCase):
    """A pane the orchestrator owns that stops with steps left in its own brief gets nudged once
    instead of sitting until a human notices (ci-routines, idle 40 min, 2026-09-10)."""

    def test_a_brief_with_numbered_steps_is_detected(self):
        self.assertTrue(W.brief_has_steps("Build:\n\n1. do a thing\n2. do another\n"))
        self.assertTrue(W.brief_has_steps("Steps:\n1) first\n2) second\n"))

    def test_a_brief_with_no_numbered_steps_is_not(self):
        self.assertFalse(W.brief_has_steps("Just build the thing, no steps here."))
        self.assertFalse(W.brief_has_steps(""))
        self.assertFalse(W.brief_has_steps(None))

    def test_a_done_report_in_the_pane_text_is_detected(self):
        self.assertTrue(W.already_reported_done("...\nDONE, commit abc123, proof attached\n"))

    def test_no_done_report_is_not_detected(self):
        self.assertFalse(W.already_reported_done("still working on step 2\n"))
        self.assertFalse(W.already_reported_done(""))

    def test_it_nudges_when_steps_remain_and_no_done_was_reported(self):
        self.assertTrue(W.idle_nudge_check("1. step one\n2. step two\n", "finished step one"))

    def test_it_does_not_nudge_a_brief_with_no_steps(self):
        self.assertFalse(W.idle_nudge_check("no numbered steps here", "finished step one"))

    def test_it_does_not_nudge_once_done_was_already_reported(self):
        self.assertFalse(W.idle_nudge_check("1. step one\n2. step two\n", "DONE, all steps finished"))

    def test_latest_brief_picks_the_newest_matching_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            older = os.path.join(tmp, "task-ci-routines-2026-09-01.md")
            newer = os.path.join(tmp, "task-ci-routines-2026-09-10.md")
            with open(older, "w", encoding="utf-8") as fh:
                fh.write("old")
            with open(newer, "w", encoding="utf-8") as fh:
                fh.write("new")
            os.utime(older, (1000, 1000))
            os.utime(newer, (2000, 2000))
            self.assertEqual(W.latest_brief("ci-routines", tmp), newer)

    def test_latest_brief_is_none_when_nothing_matches(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(W.latest_brief("no-such-pane", tmp))

    def test_nudge_state_survives_a_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "nudged.json")
            W.nudge_save({"ci-routines": 12345.0}, path=path)
            self.assertEqual(W.nudge_load(path), {"ci-routines": 12345.0})

    def test_a_missing_or_corrupt_nudge_file_reads_as_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(W.nudge_load(os.path.join(tmp, "nope.json")), {})
            bad = os.path.join(tmp, "bad.json")
            with open(bad, "w", encoding="utf-8") as fh:
                fh.write("{not json")
            self.assertEqual(W.nudge_load(bad), {})


class TestHold(unittest.TestCase):
    def test_idle_nudge_skips_held_pane(self):
        brief = "## Steps\n1. do a thing\n2. do another\n"
        self.assertTrue(W.idle_nudge_check(brief, "working...", "lights-fix", {}))
        self.assertFalse(W.idle_nudge_check(brief, "working...", "lights-fix",
                                            {"lights-fix": {"reason": "go"}}))


class TestNoopStatus(unittest.TestCase):
    def test_review_pane_never_posts(self):
        self.assertTrue(W.is_noop_status("review-x", "Should I merge?"))

    def test_nothing_for_you_is_status(self):
        self.assertTrue(W.is_noop_status("lead", "What do you do now? Nothing. I wait."))
        self.assertTrue(W.is_noop_status("lead", "Nothing for you to do next."))

    def test_real_question_posts(self):
        self.assertFalse(W.is_noop_status("lead", "Which colour do you want, A or B?"))


class TestStaleOnlyForWorkingPanes(unittest.TestCase):
    """WV2-007: STALE pinged three parked panes on 2026-09-09 because the registry reads `busy`
    for a session that has finished and is waiting on a human. Both sides must agree."""

    def test_a_pane_both_sides_call_working_is_stale_worthy(self):
        self.assertTrue(W.stale_worthy("busy", "working"))

    def test_an_idle_done_blocked_or_shell_pane_is_never_pinged(self):
        for st, ast in (("busy", "idle"), ("busy", "done"), ("busy", "blocked"), ("busy", None),
                        ("shell", "done"), ("idle", "working"), ("waiting", "idle")):
            self.assertFalse(W.stale_worthy(st, ast), f"{st}/{ast} must not be pinged")

    def test_an_acp_pane_is_never_pinged(self):
        from unittest import mock
        with mock.patch.object(W.L, "acp_pane_ids", return_value={"w1:p3"}), \
                mock.patch.object(W.L, "herdr") as herdr:
            self.assertFalse(W.ping_stale("acp-pane", "w1:p3"))
            self.assertFalse(W.ping_stale("acp-pane", "w1:p3", dry_run=True))
        herdr.assert_not_called()


if __name__ == "__main__":
    unittest.main(verbosity=2)
