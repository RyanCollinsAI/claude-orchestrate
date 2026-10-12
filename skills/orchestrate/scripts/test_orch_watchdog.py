"""py test_orch_watchdog.py - the fleet watchdog's rules, on the row shapes a real pass produces."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import orch_watchdog as W  # noqa: E402

NOW = 1_000_000.0


def row(name="ci-upgrade", **kw):
    r = {"key": "sid-" + name, "sid": "sid-" + name, "name": name, "owner": "orchestrator",
         "harness": "claude", "pane": "w1:p" + name[:2], "status": "busy", "idle_secs": 0,
         "kind": "DONE", "synthetic": False, "reported": False, "prompt": "", "held": False,
         "task": "t.md", "cwd": "C:/x", "model": "sonnet-5-5"}
    r.update(kw)
    return r


def run(rows, state=None, now=NOW, seats=()):
    return W.evaluate(rows, state or {}, now, seats)


def run_held(rows, passes=2, seats=()):
    """Run `passes` passes 3 min apart on the same rows -> (flags of the last pass, state)."""
    st, flags = {}, []
    for i in range(passes):
        flags, st = W.evaluate(rows, st, NOW + 180 * i, seats)
    return flags, st


class Vanished(unittest.TestCase):
    def test_a_busy_pane_that_disappears_is_flagged(self):
        _, st = run([row()])
        flags, st2 = run([], st, NOW + 180)
        self.assertEqual([f["cond"] for f in flags], [["VANISHED"]])
        self.assertEqual(flags[0]["name"], "ci-upgrade")
        self.assertTrue(st2["tasked"]["sid-ci-upgrade"]["gone"])

    def test_a_pane_that_finished_clean_is_not(self):
        _, st = run([row(status="idle", kind="DONE")])
        flags, st2 = run([], st, NOW + 180)
        self.assertEqual(flags, [])
        self.assertNotIn("sid-ci-upgrade", st2["tasked"])

    def test_a_pane_that_reported_is_not(self):
        _, st = run([row(reported=True, kind="ERROR")])
        flags, _ = run([], st, NOW + 180)
        self.assertEqual(flags, [])

    def test_a_vanished_pane_is_flagged_once(self):
        _, st = run([row()])
        _, st = run([], st, NOW + 180)
        flags, _ = run([], st, NOW + 360)
        self.assertEqual(flags, [])


class Killed(unittest.TestCase):
    def test_a_killed_busy_pane_is_not_a_vanish(self):
        _, st = run([row()])
        flags, st2 = W.evaluate([], st, NOW + 180, (), {"sid-ci-upgrade": NOW})
        self.assertEqual(flags, [])
        self.assertNotIn("sid-ci-upgrade", st2["tasked"])

    def test_mark_killed_round_trips_and_never_raises(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            old, W.KILLED_PATH = W.KILLED_PATH, os.path.join(d, "k.json")
            try:
                W.mark_killed("sid-a", "", "w1:p2")
                self.assertEqual(set(W.killed_load()), {"sid-a", "w1:p2"})
                W.KILLED_PATH = os.path.join(d, "no", "such\0dir", "k.json")
                W.mark_killed("x")
            finally:
                W.KILLED_PATH = old


class Ghost(unittest.TestCase):
    """2026-10-02: a `--bg --fork-session` copy db9b5ca0 shared the name `orchestrator` with the
    live a1df6cb9 (pane w5:p26). Shapes below are the two real registry rows."""
    LIVE = {"sessionId": "a1df6cb9-8f11-4858-8319-9b03279c4a38", "name": "orchestrator",
            "status": "busy", "kind": "interactive"}
    GHOST = {"sessionId": "db9b5ca0-3c62-4037-a7d4-5dd99bffb01d", "name": "orchestrator",
             "status": "idle", "kind": "bg"}

    def test_pane_less_row_sharing_a_live_name_is_a_ghost(self):
        from unittest import mock
        sess = {m["sessionId"]: m for m in (dict(self.LIVE), dict(self.GHOST))}
        with mock.patch.object(W.L, "sessions", return_value=sess):
            rows = W.ghost_rows({self.LIVE["sessionId"]: {"pane_id": "w5:p26"}})
        self.assertEqual([r["sid"] for r in rows], [self.GHOST["sessionId"]])
        flags, _ = run(rows)
        self.assertEqual(flags[0]["cond"], ["GHOST"])
        self.assertIn("claude stop db9b5ca0", flags[0]["detail"][0])

    def test_a_lone_headless_row_is_not_a_ghost(self):
        from unittest import mock
        sess = {self.GHOST["sessionId"]: dict(self.GHOST)}
        with mock.patch.object(W.L, "sessions", return_value=sess):
            self.assertEqual(W.ghost_rows({}), [])

    def test_the_seat_is_not_told_about_its_own_ghost(self):
        f = {"name": "orchestrator", "owner": ""}
        self.assertIsNone(W.notice_target(f, "orchestrator"))

    def test_resolve_prefers_the_row_with_a_pane(self):
        from unittest import mock
        L = W.L
        sess = {m["sessionId"]: m for m in (dict(self.LIVE), dict(self.GHOST))}
        with mock.patch.object(L, "sessions", return_value=sess), \
                mock.patch.object(L, "panes_by_sid",
                                  return_value={self.LIVE["sessionId"]: {"pane_id": "w5:p26"}}):
            self.assertEqual(L.resolve("orchestrator")["sessionId"], self.LIVE["sessionId"])


class ErrorRows(unittest.TestCase):
    def test_idle_error(self):
        flags, _ = run_held([row("sc-mate", status="idle", kind="ERROR", idle_secs=60)])
        self.assertEqual(flags[0]["cond"], ["ERROR"])

    def test_one_pass_of_error_is_a_blip(self):
        """292 flags in 7 days, 0 lasted 3 checks: a pane between two turns reads idle once."""
        flags, _ = run([row("sc-mate", status="idle", kind="ERROR", idle_secs=60)])
        self.assertEqual(flags, [])

    def test_error_that_clears_resets_the_count(self):
        r = row("sc-mate", status="idle", kind="ERROR", idle_secs=60)
        _, st = run([r])
        _, st = run([row("sc-mate", status="busy")], st, NOW + 180)
        flags, _ = run([r], st, NOW + 360)
        self.assertEqual(flags, [])

    def test_error_with_a_background_task_running_is_waiting_not_stuck(self):
        flags, _ = run_held([row("sc-mate", status="idle", kind="ERROR", bg=1)], passes=3)
        self.assertEqual(flags, [])

    def test_error_wording_after_a_report_is_not_flagged(self):
        """2026-10-05 reaper-upgrade: 'failed' in its old message tripped ERROR after it reported."""
        flags, _ = run([row("reaper-upgrade", status="idle", kind="ERROR", reported=True)])
        self.assertEqual(flags, [])

    def test_a_report_to_the_current_seat_counts(self):
        import json
        send = json.dumps({"message": {"content": [
            {"type": "tool_use", "name": "SendMessage", "input": {"to": "vault-6c"}}]}})
        self.assertTrue(W._reported_claude("s", "vault-ac", [send], "vault-6c"))
        self.assertFalse(W._reported_claude("s", "vault-ac", [send], "someone-else"))

    def test_busy_error_wording_is_not_yet_an_error(self):
        flags, _ = run([row("sc-mate", status="busy", kind="ERROR")])
        self.assertEqual(flags, [])

    def test_synthetic_ctx0_with_no_herdr_pane_is_dead(self):
        flags, _ = run_held([row("sc-mate", status="busy", synthetic=True, pane="")])
        self.assertEqual(flags[0]["cond"], ["ERROR"])

    def test_synthetic_ctx0_right_after_a_restart_is_not(self):
        for status in ("busy", "idle"):
            flags, _ = run([row("sc-mate", status=status, synthetic=True, idle_secs=30)])
            self.assertEqual(flags, [], status)


class Stalled(unittest.TestCase):
    def test_idle_eleven_minutes_no_report(self):
        flags, _ = run_held([row(status="idle", idle_secs=660)])
        self.assertEqual(flags[0]["cond"], ["STALLED"])

    def test_one_idle_pass_is_a_blip(self):
        self.assertEqual(run([row(status="idle", idle_secs=660)])[0], [])

    def test_a_pane_polling_for_ryan_is_not_stalled(self):
        """rcai-brand: a lavish poll in the background, idle between replies, 61 STALLED flags."""
        flags, _ = run_held([row(status="idle", idle_secs=3600, kind="QUESTION", bg=1)], passes=3)
        self.assertEqual(flags, [])

    def test_idle_nine_minutes_is_fine(self):
        self.assertEqual(run([row(status="idle", idle_secs=540)])[0], [])

    def test_reported_or_held_is_fine(self):
        self.assertEqual(run([row(status="idle", idle_secs=900, reported=True)])[0], [])
        self.assertEqual(run([row(status="idle", idle_secs=900, held=True)])[0], [])


class Prompt(unittest.TestCase):
    def test_needs_two_passes(self):
        r = row(status="idle", prompt="Do you want to proceed?")
        flags, st = run([r])
        self.assertEqual(flags, [])
        flags, _ = run([r], st, NOW + 180)
        self.assertEqual(flags[0]["cond"], ["PROMPT"])

    def test_streak_resets(self):
        r = row(status="idle", prompt="Do you want to proceed?")
        _, st = run([r])
        _, st = run([row(status="busy")], st, NOW + 180)
        flags, _ = run([r], st, NOW + 360)
        self.assertEqual(flags, [])


class NoticeRules(unittest.TestCase):
    def test_same_flag_is_told_once_until_the_pane_works(self):
        """2026-10-05: learn-e2e-test was flagged at 16:08, 16:38 and 17:11 for the same park."""
        r = row(status="idle", kind="ERROR")
        flags, st = run_held([r])
        self.assertEqual(len(flags), 1)
        self.assertEqual(run([r], st, NOW + 600)[0], [])
        self.assertEqual(run([r], st, NOW + 4 * 3600)[0], [])
        _, st = run([row(status="busy")], st, NOW + 4 * 3600)
        _, st = run([r], st, NOW + 4 * 3600 + 180)
        self.assertEqual(len(run([r], st, NOW + 4 * 3600 + 360)[0]), 1)

    def test_a_new_condition_on_the_same_pane_is_told(self):
        flags, st = run_held([row(status="idle", kind="ERROR")])
        self.assertEqual(flags[0]["cond"], ["ERROR"])
        flags, _ = run([row(status="idle", kind="ERROR", prompt="Do you want to proceed?")],
                       {**st, "prompt_seen": {"sid-ci-upgrade": 1}}, NOW + 540)
        self.assertEqual(flags[0]["cond"], ["ERROR", "PROMPT"])

    def test_old_float_clock_in_the_state_file_still_works(self):
        r = row(status="idle", kind="ERROR")
        st = {"notified": {r["pane"]: NOW - 60}, "cond_seen": {"sid-ci-upgrade|ERROR": 1}}
        self.assertEqual(run([r], st)[0], [])
        st = {"notified": {r["pane"]: NOW - 60}, "cond_seen": {"sid-ci-upgrade|ERROR": 1}}
        self.assertEqual(len(run([r], st, NOW + 1900)[0]), 1)

    def test_one_notice_when_two_conditions_hit(self):
        flags, _ = run_held([row(status="idle", idle_secs=900, synthetic=True, pane="")])
        self.assertEqual(len(flags), 1)

    def test_the_seat_is_never_a_subject(self):
        r = row("orch-grok", status="idle", kind="ERROR")
        self.assertEqual(run([r], seats={r["key"]})[0], [])

    def test_never_tells_a_pane_about_itself(self):
        f = {"name": "orch-grok", "owner": "orch-grok"}
        self.assertEqual(W.notice_target(f, "orch-grok"), None)
        self.assertEqual(W.notice_target({"name": "a", "owner": "a"}, "orch"), "orch")
        self.assertEqual(W.notice_target({"name": "a", "owner": "mate"}, "orch"), "mate")


class DeclaredWait(unittest.TestCase):
    """A pane that wrote `paused: ... until T` is idle on purpose until T (declared_wait.py)."""

    def test_a_waiting_pane_is_not_stalled(self):
        flags, _ = run_held([row(status="idle", idle_secs=3600, waiting=True)], passes=4)
        self.assertEqual(flags, [])

    def test_the_wait_ends_and_the_pane_counts_again(self):
        r = row(status="idle", idle_secs=3600, waiting=True)
        _, st = run_held([r], passes=3)
        r = row(status="idle", idle_secs=3600, waiting=False)           # T passed
        flags, st = run([r], st, NOW + 600)
        self.assertEqual(flags, [], "one pass after expiry is still a blip")
        flags, _ = run([r], st, NOW + 780)
        self.assertEqual(flags[0]["cond"], ["STALLED"])

    def test_a_wait_over_4_hours_old_is_named_in_the_flag(self):
        r = row(status="idle", idle_secs=3600, waiting=False,
                wait_note="external wait: render until 2026-10-10T03:00Z")
        flags, _ = run_held([r])
        self.assertIn("declared wait is over 4 h old", flags[0]["detail"][0])
        self.assertIn("render", flags[0]["detail"][0])

    def test_a_pane_with_no_declaration_is_untouched(self):
        flags, _ = run_held([row(status="idle", idle_secs=3600)])
        self.assertEqual(flags[0]["cond"], ["STALLED"])

    def test_waiting_does_not_hide_a_permission_prompt_or_a_dead_pane(self):
        r = row(status="idle", waiting=True, prompt="Do you want to proceed?")
        flags, _ = run_held([r])
        self.assertEqual(flags[0]["cond"], ["PROMPT"])
        flags, _ = run_held([row(status="busy", waiting=True, synthetic=True, pane="")])
        self.assertEqual(flags[0]["cond"], ["ERROR"])


class DeclaredWaitEndToEnd(unittest.TestCase):
    """The proof: a real transcript file, the real snapshot() and run_once(dry_run=True), only the
    live fleet (herdr, registry) faked. A pane writes `paused: render until <now+10min>`."""

    def setUp(self):
        import declared_wait as DW
        import json
        import tempfile
        from unittest import mock
        self.DW, self.tmp = DW, tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.log = os.path.join(self.tmp.name, "sid-x.jsonl")
        self.sid = "sid-x"
        self.until = NOW + 600

        def line(text, ts):
            return json.dumps({"type": "assistant", "timestamp": DW.iso_utc(ts).replace("Z", ":00Z"),
                               "message": {"role": "assistant", "model": "claude-sonnet-5-5",
                                           "usage": {"input_tokens": 1000},
                                           "content": [{"type": "text", "text": text}]}})
        with open(self.log, "w", encoding="utf-8") as fh:
            fh.write(line(f"Render started.\npaused: render of ep 14 until {DW.iso_utc(self.until)}",
                          NOW - 2400) + "\n")
        self.holds = os.path.join(self.tmp.name, "holds.json")
        L = W.L
        meta = {"sessionId": self.sid, "name": "render-pane", "owner": "orchestrator",
                "status": "idle", "statusUpdatedAt": (NOW - 2400) * 1000}
        patches = [
            mock.patch.object(L, "sessions", return_value={self.sid: meta}),
            mock.patch.object(L, "owners_load", return_value={self.sid: {"spawned": True}}),
            mock.patch.object(L, "panes_by_sid", return_value={self.sid: {"pane_id": "w1:p9"}}),
            mock.patch.object(L, "log_path", return_value=self.log),
            mock.patch.object(L, "pane_read", return_value=""),
            mock.patch.object(L, "bg_tasks", return_value=(0, "")),
            mock.patch.object(L, "orchestrator_name", return_value="orchestrator"),
            mock.patch.object(W, "seat_keys", return_value=set()),
            mock.patch.object(W, "killed_load", return_value={}),
            mock.patch.object(W, "OFF_PATH", os.path.join(self.tmp.name, "no.off")),
            mock.patch.object(W, "AFK_PATH", os.path.join(self.tmp.name, "no-afk.json")),
            mock.patch.object(L, "BOARD_DIR", self.tmp.name),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def dry(self, now, st):
        import contextlib
        import io
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            flags = W.run_once(dry_run=True, state=st, now=now)
        return flags, buf.getvalue()

    def test_skipped_while_waiting_then_flagged_after_it_expires(self):
        # two passes inside the wait: the pane is idle 40 min with no report, and still not flagged
        st = {}
        for t in (NOW, NOW + 180, NOW + 360):
            rows = W.snapshot(t)
            self.assertTrue(rows[0]["waiting"], "wait should hold at t+%d" % (t - NOW))
            flags, st = W.evaluate(rows, st, t, (), {})
            self.assertEqual(flags, [])
        flags, out = self.dry(NOW + 360, {})
        self.assertEqual((flags, out), ([], ""), "dry-run inside the wait prints nothing")
        # T has passed: two passes in a row, then it is flagged
        for t in (NOW + 660, NOW + 840):
            rows = W.snapshot(t)
            self.assertFalse(rows[0]["waiting"])
            flags, st = W.evaluate(rows, st, t, (), {})
        self.assertEqual([f["cond"] for f in flags], [["STALLED"]])
        seeded = {"cond_seen": {"sid-x|STALLED": 1}}
        flags, out = self.dry(NOW + 840, seeded)
        print("\n--- watchdog --once --dry-run after the wait expired ---\n" + out)
        self.assertIn("render-pane STALLED", out)
        self.assertIn("(dry-run) would tell orchestrator", out)

    def test_an_expired_hold_until_stops_holding(self):
        import json
        with open(self.holds, "w", encoding="utf-8") as fh:
            json.dump({"render-pane": {"reason": "Ryan at the gate", "ts": NOW - 3000,
                                       "until": self.DW.iso_utc(NOW + 100)}}, fh)
        self.assertTrue(W.snapshot(NOW)[0]["held"])
        self.assertFalse(W.snapshot(NOW + 200)[0]["held"])

    def test_an_old_forever_hold_still_holds(self):
        import json
        with open(self.holds, "w", encoding="utf-8") as fh:
            json.dump({"render-pane": {"reason": "waiting on Ryan", "ts": NOW - 99 * 86400}}, fh)
        self.assertTrue(W.snapshot(NOW + 5 * 86400)[0]["held"])


AFK_NOW = __import__("datetime").datetime(2026, 10, 9, 2, 0).timestamp()      # 02:00 local: quiet hours


def q(qid="q5", age_min=45, **kw):
    import datetime
    created = datetime.datetime.fromtimestamp(AFK_NOW - age_min * 60).astimezone().isoformat()
    return {"id": qid, "title": "Ship the thing?", "created": created, "answered": None, **kw}


class WedgeAlarm(unittest.TestCase):
    AFK = {"afk": True, "since": AFK_NOW - 3 * 3600, "loud": False, "wedge_minutes": 30}

    def due(self, questions, afk=None, alerted=None, now=AFK_NOW):
        return W.wedge_due(questions, self.AFK if afk is None else afk, alerted or {}, now)

    def test_off_unless_afk(self):
        for afk in ({}, {"afk": False}, {"afk": ""}, {"loud": True}):
            self.assertEqual(self.due([q()], afk), ([], {}), afk)

    def test_an_unacknowledged_question_past_the_line_is_due(self):
        due, alerted = self.due([q(age_min=45)])
        self.assertEqual([d["id"] for d in due], ["q5"])
        self.assertEqual(due[0]["age_min"], 45)

    def test_under_the_line_is_not(self):
        self.assertEqual(self.due([q(age_min=29)])[0], [])

    def test_the_line_is_wedge_minutes(self):
        afk = {**self.AFK, "wedge_minutes": 10}
        self.assertEqual(len(self.due([q(age_min=11)], afk)[0]), 1)
        self.assertEqual(len(self.due([q(age_min=11)], {**self.AFK, "wedge_minutes": "junk"})[0]), 0)

    def test_answered_acked_or_auto_questions_are_not_wedges(self):
        qs = [q("q1", answered="2026-10-09T01:00:00"), q("q2", acked=True), q("q3", auto=True)]
        self.assertEqual(self.due(qs)[0], [])

    def test_ryans_ack_ts_clears_what_was_raised_before_it(self):
        afk = {**self.AFK, "ack_ts": AFK_NOW - 60}
        self.assertEqual(self.due([q(age_min=45)], afk)[0], [])
        self.assertEqual([d["id"] for d in self.due([q(age_min=45)], {**self.AFK, "ack_ts": AFK_NOW - 3000})[0]],
                         ["q5"], "a question raised AFTER the ack still counts")

    def test_the_clock_starts_when_afk_began(self):
        afk = {**self.AFK, "since": AFK_NOW - 10 * 60}          # went AFK 10 min ago
        self.assertEqual(self.due([q(age_min=600)], afk)[0], [], "an old question must not alert at once")
        afk = {**self.AFK, "since": AFK_NOW - 40 * 60}
        self.assertEqual(self.due([q(age_min=600)], afk)[0][0]["age_min"], 40)

    def test_missing_since_falls_back_to_the_file_time(self):
        afk = {"afk": True, "_mtime": AFK_NOW - 5 * 60}
        self.assertEqual(self.due([q(age_min=600)], afk)[0], [])

    def test_once_per_question(self):
        _, alerted = self.due([q()])
        alerted = {"q5": AFK_NOW}
        self.assertEqual(self.due([q()], alerted=alerted)[0], [])
        due, _ = self.due([q(), q("q6")], alerted=alerted)
        self.assertEqual([d["id"] for d in due], ["q6"])

    def test_alerted_forgets_questions_that_are_gone_and_resets_when_afk_ends(self):
        self.assertEqual(self.due([q("q6")], alerted={"q5": 1})[1], {})
        self.assertEqual(self.due([q()], {"afk": False}, alerted={"q5": 1}), ([], {}))

    def test_event_is_quiet_by_default_and_loud_only_when_asked(self):
        due = self.due([q(age_min=45)])[0]
        ev = W.wedge_event(due, self.AFK)
        self.assertFalse(ev["loud"])
        self.assertTrue(W.wedge_event(due, {**self.AFK, "loud": True})["loud"])
        self.assertGreaterEqual(ev["blocked_secs"], 600)
        self.assertIn("Q5", ev["text"])
        self.assertEqual(ev["key"], "wedge|q5")

    def test_several_questions_make_one_event(self):
        due = self.due([q("q1"), q("q2"), q("q3"), q("q4"), q("q5")])[0]
        ev = W.wedge_event(due, self.AFK)
        self.assertEqual(ev["key"], "wedge|q1,q2,q3,q4,q5")
        self.assertIn("+2 more", ev["text"])


class WedgeSends(unittest.TestCase):
    """run_wedge with phone_alerts mocked: nothing here can reach Slack."""

    def setUp(self):
        from unittest import mock
        self.mock = mock
        self.handle = mock.MagicMock(return_value="sent")
        import phone_alerts
        p = mock.patch.object(phone_alerts, "handle", self.handle)
        p.start()
        self.addCleanup(p.stop)

    AFK = WedgeAlarm.AFK

    def test_not_afk_sends_nothing_and_never_reads_the_board(self):
        with self.mock.patch.object(W, "_board_questions", side_effect=AssertionError("read board")):
            ev, alerted = W.run_wedge(AFK_NOW, {}, afk={"afk": False})
        self.assertEqual((ev, alerted), (None, {}))
        self.handle.assert_not_called()

    def test_sends_one_alert_through_phone_alerts_quiet_by_default(self):
        ev, alerted = W.run_wedge(AFK_NOW, {}, questions=[q()], afk=self.AFK)
        self.handle.assert_called_once()
        sent = self.handle.call_args[0][0]
        self.assertEqual((sent["kind"], sent["loud"]), ("WEDGE", False))
        self.assertEqual(alerted, {"q5": AFK_NOW})

    def test_loud_true_is_passed_through(self):
        W.run_wedge(AFK_NOW, {}, questions=[q()], afk={**self.AFK, "loud": True})
        self.assertTrue(self.handle.call_args[0][0]["loud"])

    def test_the_next_pass_does_not_send_again(self):
        _, alerted = W.run_wedge(AFK_NOW, {}, questions=[q()], afk=self.AFK)
        W.run_wedge(AFK_NOW + 180, alerted, questions=[q()], afk=self.AFK)
        self.assertEqual(self.handle.call_count, 1)

    def test_a_failed_slack_post_is_retried_next_pass(self):
        self.handle.return_value = "drop:slack-failed"
        _, alerted = W.run_wedge(AFK_NOW, {}, questions=[q()], afk=self.AFK)
        self.assertEqual(alerted, {})
        self.handle.return_value = "sent"
        W.run_wedge(AFK_NOW + 180, alerted, questions=[q()], afk=self.AFK)
        self.assertEqual(self.handle.call_count, 2)

    def test_dry_run_sends_and_remembers_nothing(self):
        import contextlib
        import io
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            ev, alerted = W.run_wedge(AFK_NOW, {}, dry_run=True, questions=[q()], afk=self.AFK)
        self.handle.assert_not_called()
        self.assertEqual(alerted, {})
        self.assertIn("would send (quiet)", buf.getvalue())

    def test_a_broken_board_never_raises(self):
        with self.mock.patch.object(W, "_board_questions", side_effect=RuntimeError("boom")):
            self.assertEqual(W.run_wedge(AFK_NOW, {"q1": 1}, afk=self.AFK), (None, {"q1": 1}))

    def test_run_once_carries_wedge_state_in_the_saved_state(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d, \
                self.mock.patch.object(W, "STATE_PATH", os.path.join(d, "s.json")), \
                self.mock.patch.object(W, "OFF_PATH", os.path.join(d, "no.off")), \
                self.mock.patch.object(W, "afk_load", return_value=self.AFK), \
                self.mock.patch.object(W, "_board_questions", return_value=[q()]), \
                self.mock.patch.object(W, "seat_keys", return_value=set()), \
                self.mock.patch.object(W, "killed_load", return_value={}), \
                self.mock.patch.object(W.L, "orchestrator_name", return_value="orchestrator"), \
                self.mock.patch("phone_alerts.tick"):
            W.run_once(rows=[], state={}, now=AFK_NOW)
            self.assertEqual(W.state_load()["wedge_alerted"], {"q5": AFK_NOW})
            W.run_once(rows=[], state=W.state_load(), now=AFK_NOW + 180)
        self.assertEqual(self.handle.call_count, 1)


class WedgeThroughPhoneAlerts(unittest.TestCase):
    """The real phone_alerts.handle with a recording sender: quiet queues in quiet hours, loud sends."""

    def setUp(self):
        import tempfile
        import phone_alerts as P
        from unittest import mock
        self.P = P
        self.sent = []
        self._d = tempfile.TemporaryDirectory()
        self.addCleanup(self._d.cleanup)
        for target, value in (("STATE_PATH", os.path.join(self._d.name, "pa.json")),
                              ("OFF_PATH", os.path.join(self._d.name, "pa.off"))):
            p = mock.patch.object(P, target, value)
            p.start()
            self.addCleanup(p.stop)
        p = mock.patch.object(P, "send_slack", lambda text: self.sent.append(text) or "1.0")
        p.start()
        self.addCleanup(p.stop)

    def test_quiet_channel_waits_for_morning_and_loud_does_not(self):
        qs = [q(age_min=45)]
        _, a = W.run_wedge(AFK_NOW, {}, questions=qs, afk={**WedgeAlarm.AFK, "loud": False})
        self.assertEqual(self.sent, [], "02:00, loud false: queued, nothing sent")
        self.assertEqual(len(self.P.load()["queue"]), 1)
        W.run_wedge(AFK_NOW, {}, questions=[q("q9", age_min=45)], afk={**WedgeAlarm.AFK, "loud": True})
        self.assertEqual(len(self.sent), 1)
        self.assertIn("Wedge: 1 escalation", self.sent[0])

    def test_nothing_is_sent_when_phone_alerts_is_off(self):
        open(self.P.OFF_PATH, "w").close()
        W.run_wedge(AFK_NOW, {}, questions=[q()], afk={**WedgeAlarm.AFK, "loud": True})
        self.assertEqual(self.sent, [])


class AfkLoad(unittest.TestCase):
    def test_missing_bad_or_wrong_shape_is_not_afk(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(W.afk_load(os.path.join(d, "none.json")), {})
            for body in ("", "not json", "[1]", "null"):
                p = os.path.join(d, "a.json")
                with open(p, "w") as fh:
                    fh.write(body)
                self.assertEqual(W.afk_load(p), {}, body)
            with open(p, "w") as fh:
                fh.write('{"afk": true, "loud": true}')
            got = W.afk_load(p)
            self.assertTrue(got["afk"] and got["loud"] and got["_mtime"] > 0)


if __name__ == "__main__":
    os.environ["PODIUM_SLACK"] = "0"
    unittest.main()
