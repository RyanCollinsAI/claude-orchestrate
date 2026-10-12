"""py test_declared_wait.py - the declared-wait rules, plus how watch_sessions uses them.

No herdr, no disk beyond a temp dir, no network.
"""
import datetime
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import declared_wait as DW  # noqa: E402
import watch_sessions as WS  # noqa: E402

NOW = datetime.datetime(2026, 10, 9, 1, 0, tzinfo=datetime.timezone.utc).timestamp()


def iso(epoch):
    return DW.iso_utc(epoch)


def msg(text, ts=NOW, tool_only=False):
    """One raw transcript line, as Claude Code writes an assistant message."""
    content = ([{"type": "tool_use", "name": "Bash", "input": {}}] if tool_only
               else [{"type": "text", "text": text}])
    return json.dumps({"type": "assistant", "timestamp": iso_full(ts),
                       "message": {"role": "assistant", "content": content}})


def iso_full(epoch):
    return datetime.datetime.fromtimestamp(epoch, datetime.timezone.utc).isoformat().replace("+00:00", "Z")


class ParseLine(unittest.TestCase):
    def test_a_valid_line(self):
        d = DW.parse_line("paused: render of ep 14 until 2026-10-09T03:40Z")
        self.assertEqual(d["reason"], "render of ep 14")
        self.assertEqual(iso(d["until"]), "2026-10-09T03:40Z")

    def test_seconds_case_bullet_and_bold_are_fine(self):
        for line in ("Paused: x until 2026-10-09T03:40:15Z", "- paused: x until 2026-10-09T03:40Z",
                     "**paused:** x until 2026-10-09T03:40Z", "> paused: x UNTIL 2026-10-09T03:40Z"):
            self.assertIsNotNone(DW.parse_line(line), line)

    def test_no_time_is_not_a_declaration(self):
        self.assertIsNone(DW.parse_line("paused: waiting on the render"))

    def test_a_bad_time_is_not_a_declaration(self):
        self.assertIsNone(DW.parse_line("paused: x until 2026-13-45T99:99Z"))
        self.assertIsNone(DW.parse_line("paused: x until tomorrow"))
        self.assertIsNone(DW.parse_line("paused: x until 2026-10-09T03:40"))     # no Z: not UTC

    def test_the_word_in_prose_is_not_a_declaration(self):
        self.assertIsNone(DW.parse_line("I said paused: x until 2026-10-09T03:40Z"))
        self.assertIsNone(DW.parse_line("render paused until 2026-10-09T03:40Z"))


class State(unittest.TestCase):
    def decl(self, until_in, written_ago=0):
        return {"kind": "paused", "reason": "r", "until": NOW + until_in, "written": NOW - written_ago}

    def test_none_active_expired(self):
        self.assertEqual(DW.state(None, NOW), "none")
        self.assertEqual(DW.state(self.decl(600), NOW), "active")
        self.assertEqual(DW.state(self.decl(-1), NOW), "expired")
        self.assertEqual(DW.state(self.decl(0), NOW), "expired")

    def test_valid_but_old_is_stale_not_silent_forever(self):
        d = self.decl(3600, written_ago=4 * 3600 + 1)
        self.assertEqual(DW.state(d, NOW), "stale")
        self.assertEqual(DW.state(self.decl(3600, written_ago=4 * 3600 - 60), NOW), "active")

    def test_resurface_slot_advances_every_4_hours(self):
        self.assertEqual(DW.resurface_slot(self.decl(99999, 4 * 3600 + 5), NOW), 1)
        self.assertEqual(DW.resurface_slot(self.decl(99999, 8 * 3600 + 5), NOW), 2)

    def test_a_year_long_wait_cannot_mute_forever(self):
        d = self.decl(365 * 86400, written_ago=5 * 3600)
        self.assertEqual(DW.state(d, NOW), "stale")


class FindInEntries(unittest.TestCase):
    def test_finds_the_declaration_and_its_write_time(self):
        until = iso(NOW + 600)
        d = DW.find_in_entries([msg("starting"), msg(f"ok\npaused: render until {until}", NOW - 120)])
        self.assertEqual(d["reason"], "render")
        self.assertAlmostEqual(d["written"], NOW - 120, delta=1)

    def test_tool_call_only_messages_do_not_end_it(self):
        until = iso(NOW + 600)
        d = DW.find_in_entries([msg(f"paused: render until {until}"), msg("", tool_only=True)])
        self.assertIsNotNone(d)

    def test_a_newer_reply_without_the_line_ends_it(self):
        until = iso(NOW + 600)
        d = DW.find_in_entries([msg(f"paused: render until {until}"), msg("render finished, continuing")])
        self.assertIsNone(d)

    def test_no_declaration_means_untouched(self):
        self.assertIsNone(DW.find_in_entries([msg("working on it")]))
        self.assertIsNone(DW.find_in_entries([]))
        self.assertIsNone(DW.find_in_entries(["not json {", ""]))

    def test_user_messages_are_ignored(self):
        user = json.dumps({"type": "user", "message": {"content": "paused: x until " + iso(NOW + 600)}})
        self.assertIsNone(DW.find_in_entries([user]))


class Holds(unittest.TestCase):
    def test_old_holds_never_expire(self):
        holds = {"a": {"reason": "waiting on Ryan", "ts": NOW - 99 * 86400}}
        self.assertEqual(DW.active_holds(holds, NOW), holds)

    def test_hold_with_until_expires(self):
        holds = {"live": {"reason": "r", "ts": NOW, "until": iso(NOW + 60)},
                 "dead": {"reason": "r", "ts": NOW - 7200, "until": iso(NOW - 60)},
                 "old": {"reason": "r", "ts": NOW - 7200}}
        self.assertEqual(set(DW.active_holds(holds, NOW)), {"live", "old"})

    def test_a_garbled_until_does_not_drop_a_hold(self):
        holds = {"a": {"reason": "r", "ts": NOW, "until": "soon"}}
        self.assertEqual(DW.active_holds(holds, NOW), holds)

    def test_hold_until_wins_over_the_panes_own_line(self):
        holds = {"p": {"reason": "Ryan is at the gate", "ts": NOW, "until": iso(NOW + 600)}}
        st, d = DW.waiting("p", [msg("hello")], holds, NOW)
        self.assertEqual((st, d["kind"]), ("active", "hold"))

    def test_an_expired_hold_falls_back_to_the_panes_line(self):
        holds = {"p": {"reason": "r", "ts": NOW - 9000, "until": iso(NOW - 60)}}
        st, d = DW.waiting("p", [msg("paused: x until " + iso(NOW + 600))], holds, NOW)
        self.assertEqual((st, d["kind"]), ("active", "paused"))

    def test_describe_names_which_human_it_blocks_on(self):
        self.assertIn("held by Ryan", DW.describe({"kind": "hold", "reason": "r", "until": NOW}))
        self.assertIn("external wait", DW.describe({"kind": "paused", "reason": "r", "until": NOW}))


class UntilArg(unittest.TestCase):
    def test_relative_and_absolute(self):
        self.assertEqual(DW.parse_until_arg("45m", NOW), iso(NOW + 45 * 60))
        self.assertEqual(DW.parse_until_arg("+2h", NOW), iso(NOW + 7200))
        self.assertEqual(DW.parse_until_arg("2026-10-09T05:00Z", NOW), "2026-10-09T05:00Z")

    def test_garbage_is_refused(self):
        for bad in ("", "soon", "2026-10-09T05:00", "0x", "12"):
            self.assertIsNone(DW.parse_until_arg(bad, NOW), bad)


class WatchSessionsUsesIt(unittest.TestCase):
    """The two nag sites in watch_sessions.main (STALE ping, idle-nudge) call under_declared_wait."""

    def test_a_valid_wait_blocks_the_stale_ping_and_the_nudge(self):
        ents = [msg("paused: render until " + iso(NOW + 600))]
        self.assertTrue(WS.under_declared_wait("p", ents, {}, NOW))
        self.assertFalse(WS.idle_nudge_check("1. do a thing", "idle", "p", {"p": {}}))   # old hold, still held

    def test_an_expired_wait_stops_counting(self):
        ents = [msg("paused: render until " + iso(NOW - 1))]
        self.assertFalse(WS.under_declared_wait("p", ents, {}, NOW))

    def test_a_pane_with_no_declaration_is_untouched(self):
        self.assertFalse(WS.under_declared_wait("p", [msg("working")], {}, NOW))

    def test_stale_wait_is_still_not_pinged_but_is_resurfaced(self):
        ents = [msg("paused: render until " + iso(NOW + 9 * 3600), NOW - 5 * 3600)]
        self.assertTrue(WS.under_declared_wait("p", ents, {}, NOW))
        st, d = DW.waiting("p", ents, {}, NOW)
        self.assertEqual((st, DW.resurface_slot(d, NOW)), ("stale", 1))

    def test_expired_hold_until_lets_the_nudge_run_again(self):
        holds = {"p": {"reason": "r", "ts": NOW - 9000, "until": iso(NOW - 60)}}
        live = DW.active_holds(holds, NOW)
        self.assertTrue(WS.idle_nudge_check("1. do a thing", "idle", "p", live))


if __name__ == "__main__":
    unittest.main()
