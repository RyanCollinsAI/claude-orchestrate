import io, json, os, sys, tempfile, unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import quota
import orch


def row(provider, five=None, week=None, active=False, seen=1000, note="",
        five_reset=None, week_reset=None):
    return {"provider": provider, "plan": "max", "five_hour_left": five,
            "weekly_left": week, "five_hour_resets": five_reset,
            "weekly_resets": week_reset,
            "active": active, "seen": seen, "billing": "sub", "source": "fixture",
            "note": note}


PAYLOAD = {
    "generatedAt": "1970-01-01T00:16:40Z",
    "providers": [
        {"provider": "codex", "state": {"status": "fresh", "stale": False}},
        {"provider": "grok", "state": {"status": "auth_required", "stale": False}},
        {"provider": "cursor", "state": {"status": "error", "stale": False}},
    ],
}


class TestQuotaSources(unittest.TestCase):
    def test_ledger_used_percent_becomes_left_once_and_marks_active(self):
        ledger = {
            "partial": {"five_hour": {"used_percentage": 43},
                        "seven_day": {"used_percentage": 25}, "ts": 4},
            "unused": {"five_hour": {"used_percentage": 0},
                       "seven_day": {"used_percentage": 0}, "ts": 1},
            "spent": {"five_hour": {"used_percentage": 100},
                      "seven_day": {"used_percentage": 100}, "ts": 2},
            "unknown": {"five_hour": {"used_percentage": None},
                        "seven_day": {"used_percentage": None}, "ts": 3},
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "ledger.json")
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(ledger, fh)
            status = "  unused unused@example.com\n* spent spent@example.com\n  unknown unknown@example.com\n"
            with mock.patch.object(quota, "LEDGER", path), \
                 mock.patch.object(quota, "_run", return_value=status):
                rows = {r["provider"]: r for r in quota.claude_rows()}
        self.assertEqual((rows["claude/partial"]["five_hour_left"],
                          rows["claude/partial"]["weekly_left"]), (57, 75))
        self.assertEqual((rows["claude/unused"]["five_hour_left"],
                          rows["claude/unused"]["weekly_left"]), (100, 100))
        self.assertEqual((rows["claude/spent"]["five_hour_left"],
                          rows["claude/spent"]["weekly_left"]), (0, 0))
        self.assertEqual((rows["claude/unknown"]["five_hour_left"],
                          rows["claude/unknown"]["weekly_left"]), (None, None))
        self.assertTrue(rows["claude/spent"]["active"])
        self.assertFalse(rows["claude/unused"]["active"])

    def test_ambient_used_percent_becomes_left_from_json(self):
        data = {"five_hour": {"used_percentage": 0, "resets_at": 11},
                "seven_day": {"used_percentage": 100, "resets_at": 22}, "ts": 7}
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "ambient.json")
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(data, fh)
            with mock.patch.object(quota, "AMBIENT", path):
                result = quota.ambient_row()
        self.assertEqual((result["five_hour_left"], result["weekly_left"]), (100, 0))
        self.assertEqual((result["five_hour_resets"], result["weekly_resets"]), (11, 22))

    def test_axi_ignores_claude_and_preserves_remaining_boundaries(self):
        payload = {"providers": [
            {"provider": "claude", "windows": [
                {"kind": "session", "percentRemaining": 55}]},
            {"provider": "codex", "windows": [
                {"kind": "session", "percentRemaining": 0},
                {"kind": "weekly", "percentRemaining": 100}]},
        ]}
        rows = quota.axi_rows(payload, watched=())
        self.assertEqual([r["provider"] for r in rows], ["codex"])
        self.assertEqual((rows[0]["five_hour_left"], rows[0]["weekly_left"]), (0, 100))

    def test_absent_windows_and_providers_stay_unknown(self):
        payload = {"providers": [
            {"provider": "codex"},
            {"provider": "grok", "windows": []},
            {"provider": "kimi", "windows": [{"kind": "monthly", "percentRemaining": 0}]},
        ]}
        rows = quota.axi_rows(payload)
        self.assertEqual(len(rows), 3)
        for result in rows:
            self.assertIsNone(result["five_hour_left"])
            self.assertIsNone(result["weekly_left"])
        self.assertEqual(quota.axi_rows({}), [])
        self.assertEqual(quota.axi_rows({"providers": []}), [])

    def test_collect_headroom_uses_active_local_claude_not_ambient(self):
        local = [row("claude/inactive", 10, 90),
                 row("claude/active", 20, 12, active=True)]
        ambient = row("claude/ambient", 100, 100)
        with mock.patch.object(quota, "axi_payload", return_value={"providers": []}), \
             mock.patch.object(quota, "claude_rows", return_value=local), \
             mock.patch.object(quota, "ambient_row", return_value=ambient):
            rows, _payload = quota.collect()
        headroom = orch.quota_headroom(rows)
        self.assertEqual(headroom["claude"], 12)
        self.assertIsNone(headroom["pi"])
        self.assertIsNone(headroom["codex"])


class TestTuiRender(unittest.TestCase):
    def test_accounts_ambient_and_inactive_are_distinct_and_private(self):
        rows = [row("claude/primary@example.com", -12, 150, True, note="ryan@example.com"),
                row("codex", 35, 83), row("claude/ambient", 44, 55)]
        out = quota.render_tui(rows, PAYLOAD, width=40, colour=False, now=1065)
        self.assertIn("[account]", out)
        self.assertIn("██████████", out)
        self.assertIn("░░░░░░░░░░", out)
        self.assertIn("ACTIVE", out)
        self.assertIn("Shared Claude feed", out)
        self.assertIn("account unverified", out)
        self.assertIn("grok", out)
        self.assertIn("auth required", out)
        self.assertNotIn("cursor", out)
        self.assertNotIn("example.com", out)
        self.assertTrue(all(len(line) == 40 for line in out.splitlines()))
        wide = quota.render_tui(rows, PAYLOAD, width=80, colour=False, now=1065)
        self.assertIn("codex  max plan  age unknown", wide)
        self.assertIn("cached observed 1m ago", wide)
        self.assertEqual(wide.count("Shared Claude feed (account unverified)"), 1)
        self.assertIn("5-hour 44% remaining", wide)
        self.assertIn("Weekly 55% remaining", wide)

    def test_expired_epoch_window_is_unknown_at_exact_boundary(self):
        out = quota.render_tui([
            row("claude/main", 25, 0, five_reset=2001, week_reset=2000),
        ], PAYLOAD, width=98, colour=False, now=2000)
        self.assertIn("5-hour", out)
        self.assertIn("25% remaining", out)
        self.assertIn("Weekly", out)
        self.assertIn("unknown", out)
        self.assertIn("reset passed", out)
        self.assertNotIn("  0% remaining", out)

    def test_expired_iso_window_does_not_hide_fresh_other_window(self):
        out = quota.render_tui([
            row("claude/main", 72, 18,
                five_reset="1970-01-01T00:33:21Z",
                week_reset="1970-01-01T00:33:20Z"),
        ], PAYLOAD, width=98, colour=False, now=2000)
        self.assertIn("72% remaining", out)
        self.assertIn("reset passed", out)
        self.assertNotIn("18% remaining", out)

    def test_missing_and_invalid_resets_leave_values_readable(self):
        out = quota.render_tui([
            row("claude/main", 61, 42, five_reset={"bad": "value"},
                week_reset="not-a-date"),
        ], PAYLOAD, width=98, colour=False, now=2000)
        self.assertIn("61% remaining", out)
        self.assertIn("42% remaining", out)
        self.assertNotIn("reset passed", out)

    def test_axi_stale_state_is_explicit(self):
        payload = {"generatedAt": "1970-01-01T00:16:40Z", "providers": [
            {"provider": "codex", "state": {"status": "fresh", "stale": True,
                                             "refreshedAt": 1000}}]}
        out = quota.render_tui([row("codex", 50, 60)], payload,
                               width=80, colour=False, now=1065)
        self.assertIn("STALE - observed 1m ago", out)
        self.assertIn("░░░░░░░░░░░░░░░░", out)
        self.assertNotIn(" 50%", out)
        self.assertNotIn(" 60%", out)

    def test_external_labels_cannot_write_terminal_controls(self):
        attack = "codex\x1b]52;c;c2VjcmV0\x07\r\nnext\x1b[2J"
        rows = [row(attack, 10, 20)]
        plain = quota.render_tui(rows, None, width=80, colour=False)
        coloured = quota.render_tui(rows, None, width=80, colour=True)
        for out in (plain, coloured):
            self.assertNotIn("52;c", out)
            self.assertNotIn("c2VjcmV0", out)
            self.assertNotIn("[2J", out)
            self.assertIn("codex next", out)

    def test_interactive_frame_is_height_bounded_with_footer(self):
        rows = [row(f"provider-{n}", 10 + n, 50 + n) for n in range(8)]
        payload = {"providers": [
            {"provider": f"provider-{n}",
             "state": {"status": "fresh", "stale": False, "refreshedAt": 1000}}
            for n in range(8)]}
        out = quota.render_tui(rows, payload, width=60, height=25,
                               page=1, colour=False, now=1065)
        self.assertLessEqual(len(out.splitlines()), 25)
        self.assertIn("page 2/", out)
        self.assertIn("q close", out)

    def test_three_claude_accounts_and_shared_feed_fit_98_by_22(self):
        rows = [row(f"claude/account-{n}", 50, 60) for n in range(3)]
        rows.append(row("claude/ambient", 99, 100, seen=1065))
        out = quota.render_tui(rows, PAYLOAD, width=98, height=22,
                               colour=False, now=1065)
        self.assertLessEqual(len(out.splitlines()), 22)
        self.assertTrue(all(len(line) == 98 for line in out.splitlines()))
        self.assertIn("Shared Claude feed (account unverified)", out)
        self.assertIn("Unavailable", out)
        self.assertNotIn("j/k page", out)

    def test_single_page_footer_omits_paging_controls(self):
        out = quota.render_tui([row("codex", 50, 60)], PAYLOAD,
                               width=100, height=30, colour=False, now=1065)
        self.assertIn("r reload | q close | reload 60s", out)
        self.assertNotIn("j/k page", out)

    def test_coloured_lines_fit_by_visible_width(self):
        out = quota.render_tui([row("claude/main", 26, 25, active=True)], PAYLOAD,
                               width=100, height=30, colour=True, now=1065)
        for line in out.splitlines():
            self.assertEqual(len(quota.TERMINAL_ESCAPE.sub("", line)), 100)
        self.assertIn(quota.CYAN + "█", out)
        self.assertIn(quota.YELLOW + "█", out)


class TestTuiLoop(unittest.TestCase):
    def test_q_restores_cursor_and_primary_screen(self):
        output = io.StringIO()
        keys = iter(["q"])
        with mock.patch.object(quota, "collect", return_value=([], None)) as collect:
            quota.run_tui(refresh=False, stream=output,
                          key_reader=lambda _timeout: next(keys), clock=lambda: 0)
        self.assertEqual(collect.call_args.args, (False, ()))
        self.assertTrue(output.getvalue().startswith("\x1b[?1049h\x1b[?25l"))
        self.assertTrue(output.getvalue().endswith("\x1b[0m\x1b[?25h\x1b[?1049l"))

    def test_once_is_capturable_without_terminal_takeover(self):
        output = io.StringIO()
        attack = row("codex\x1b]52;c;c2VjcmV0\x07", 1, 2)
        with mock.patch.object(quota, "collect", return_value=([attack], PAYLOAD)):
            quota.run_tui(refresh=True, once=True, stream=output)
        self.assertIn("HERDR USAGE", output.getvalue())
        self.assertNotIn("c2VjcmV0", output.getvalue())
        self.assertNotIn("?1049", output.getvalue())
        self.assertNotIn("\x1b[", output.getvalue())

    def test_once_falls_back_to_ascii_for_cp1252_stream(self):
        class Cp1252Stream:
            encoding = "cp1252"

            def __init__(self):
                self.value = ""

            def write(self, value):
                value.encode(self.encoding)
                self.value += value

            def flush(self):
                pass

            def isatty(self):
                return False

        output = Cp1252Stream()
        with mock.patch.object(quota, "collect", return_value=([row("codex", 50, 60)], PAYLOAD)):
            quota.run_tui(refresh=True, once=True, stream=output)
        self.assertIn("############", output.value)
        self.assertNotIn("█", output.value)
        self.assertNotIn("\x1b[", output.value)


if __name__ == "__main__":
    unittest.main()
