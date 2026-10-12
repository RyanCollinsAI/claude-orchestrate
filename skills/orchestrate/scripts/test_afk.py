"""py -m pytest test_afk.py - the AFK and quiet modes, branch by branch. No real board, no phone, no pane."""
import contextlib
import datetime
import io
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import afk as A  # noqa: E402
import asks as K  # noqa: E402
import orch_watchdog as W  # noqa: E402

NOW = datetime.datetime(2026, 10, 9, 1, 0, 0).astimezone()


def at(minutes):
    return NOW + datetime.timedelta(minutes=minutes)


def iso(dt):
    return dt.isoformat()


def question(qid="q1", title="Which colour for the badge?", pick="A", created=None, **kw):
    q = {"id": qid, "title": title, "from": "ui-pane", "context_md": "", "group": "ui",
         "options": [{"value": "A", "label": "Teal"}, {"value": "B", "label": "Navy"}],
         "pick": pick, "pick_why": "", "inputs": [], "multi": False,
         "auto": False, "auto_after": "", "created": iso(created or NOW), "answered": None, "answer": None}
    q.update(kw)
    return q


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        j = lambda n: os.path.join(self.tmp, n)  # noqa: E731
        for obj, attr, val in ((A, "AFK_PATH", j("afk.json")), (A, "DIGEST_PATH", j("afk-digest.md")),
                               (A, "ARCHIVE_DIR", j("afk-archive")), (A, "HOLDS_PATH", j("holds.json")),
                               (A.L, "BOARD_DIR", self.tmp), (K, "ASKS_FILE", j("asks.json"))):
            p = mock.patch.object(obj, attr, val)
            p.start()
            self.addCleanup(p.stop)
        self.board = {"questions": [], "done": []}
        self.saves = 0
        self.told = []
        for attr, fn in (("_board_load", lambda: self.board), ("_board_save", self._save),
                         ("_board_slack", lambda line, group: None)):
            p = mock.patch.object(A, attr, fn)
            p.start()
            self.addCleanup(p.stop)

    def _save(self, s):
        self.saves += 1

    def send(self, name, text):
        self.told.append((name, text))
        return True

    def on(self, *args, all=True):
        """`afk on` for the test. --auto-all by default; the default-scope tests pass all=False."""
        with contextlib.redirect_stdout(io.StringIO()) as out:
            d = A.cmd_on(list(args) + (["--auto-all"] if all else []), now=NOW)
        return d, out.getvalue()

    def tick(self, minutes=0):
        return A.tick(now=at(minutes), send=self.send)

    def ledger(self, *rows):
        with open(os.path.join(self.tmp, "fleet-ledger.jsonl"), "w", encoding="utf-8") as fh:
            for r in rows:
                fh.write(json.dumps({"v": 1, **r}) + "\n")


class TestOn(Base):
    def test_writes_contract_with_words_verbatim_and_g7_fields(self):
        words = "Auto-gate everything free.\n  Don't touch money - wait for me.  "
        d, out = self.on("--words", words, "--until", "07:00")
        got = A.load()
        self.assertEqual(got["words"], words)
        for key in ("afk", "since", "loud", "wedge_minutes"):
            self.assertIn(key, got)
        self.assertTrue(got["afk"])
        self.assertEqual((got["loud"], got["wedge_minutes"], got["auto_minutes"]), (False, 30, 20))
        self.assertEqual(datetime.datetime.fromisoformat(got["return_at"]).hour, 7)
        self.assertIn("Auto-gate everything free.", out)

    def test_the_g7_watchdog_reads_it(self):
        self.on("--words", "away")
        loaded = W.afk_load(A.AFK_PATH)
        self.assertTrue(loaded["afk"])
        self.assertEqual(W._epoch(loaded["since"]), NOW.timestamp())

    def test_refresh_without_words_changes_nothing(self):
        self.on("--words", "away", "--until", "07:00")
        before = A.load()
        with contextlib.redirect_stdout(io.StringIO()) as out:
            A.cmd_on([], now=at(30))
        self.assertEqual(A.load(), before)
        self.assertIn("already on", out.getvalue())

    def test_new_words_replace_and_archive_the_old(self):
        self.on("--words", "first")
        with contextlib.redirect_stdout(io.StringIO()):
            A.cmd_on(["--words", "second"], now=at(30))
        got = A.load()
        self.assertEqual((got["words"], got["since"]), ("second", iso(NOW)))
        self.assertEqual(got["superseded"][0]["words"], "first")

    def test_bad_numbers_are_refused(self):
        with self.assertRaises(SystemExit):
            A.cmd_on(["--auto-minutes", "soon"], now=NOW)
        with self.assertRaises(SystemExit):
            A.cmd_on(["--wedge-minutes", "-5"], now=NOW)

    def test_until_forms(self):
        self.assertEqual(A.parse_until("6h", NOW), at(360))
        self.assertEqual(A.parse_until("+90m", NOW), at(90))
        self.assertEqual(A.parse_until("7am", NOW).hour, 7)
        self.assertEqual(A.parse_until("12am", NOW).hour, 0)
        self.assertGreater(A.parse_until("00:30", NOW), NOW)                 # next occurrence, not the past
        self.assertEqual(A.parse_until("00:30", NOW).day, 10)
        self.assertEqual(A.parse_until("2026-10-09T07:00", NOW).hour, 7)
        with self.assertRaises(SystemExit):
            A.parse_until("whenever", NOW)

    def test_loud_and_wedge_minutes_pass_through_to_g7(self):
        d, _ = self.on("--words", "x", "--loud", "--wedge-minutes", "45")
        self.assertEqual((d["loud"], d["wedge_minutes"]), (True, 45.0))


class TestRoutingWhileAfk(Base):
    def test_a_free_question_with_a_pick_gets_an_auto_clock(self):
        self.on("--words", "away")
        self.board["questions"].append(question())
        acts = self.tick(5)
        q = self.board["questions"][0]
        self.assertTrue(q["auto"])
        self.assertTrue(q["afk_auto"])
        self.assertEqual(datetime.datetime.fromisoformat(q["auto_after"]), at(20))
        self.assertEqual(self.saves, 1)
        self.assertTrue(any(a.startswith("auto q1") for a in acts))

    def test_clock_starts_at_the_later_of_asked_and_afk_began(self):
        self.on("--words", "away")
        self.board["questions"].append(question(created=at(-300)))          # asked long before AFK
        self.tick(1)
        self.assertEqual(datetime.datetime.fromisoformat(self.board["questions"][0]["auto_after"]), at(20))

    def test_the_clock_uses_auto_minutes_from_the_contract(self):
        self.on("--words", "away", "--auto-minutes", "7")
        self.board["questions"].append(question())
        self.tick(1)
        self.assertEqual(datetime.datetime.fromisoformat(self.board["questions"][0]["auto_after"]), at(7))

    def test_money_delete_send_publish_security_plans_never_auto(self):
        self.on("--words", "away")
        cases = {"money": "Pay the $40 invoice now?", "delete": "Delete the old worktrees?",
                 "send": "Send the draft email to the TA?", "publish": "Upload the Short to YouTube?",
                 "security": "Rotate the API key?", "plans": "Reschedule the dentist visit?"}
        for i, text in enumerate(cases.values()):
            self.board["questions"].append(question(f"q{i}", text))
        self.tick(1)
        self.tick(500)                                                     # far past any clock
        for q in self.board["questions"]:
            self.assertFalse(q["auto"], q["title"])
            self.assertTrue(q["afk_hold"].startswith("Ryan-only"), q["title"])
            self.assertIsNone(q["answered"], q["title"])

    def test_option_text_counts_too(self):
        self.on("--words", "away")
        q = question()
        q["options"][1]["label"] = "Delete the branch"
        self.board["questions"].append(q)
        self.tick(1)
        self.assertEqual(self.board["questions"][0]["afk_hold"], "Ryan-only (delete)")

    def test_no_pick_typed_input_multi_and_bad_pick_wait(self):
        self.on("--words", "away")
        self.board["questions"] += [
            question("q1", pick=""), question("q2", inputs=[{"name": "a", "label": "a ="}]),
            question("q3", multi=True), question("q4", pick="Z"), question("q5", options=[])]
        self.tick(1)
        self.assertEqual([q["afk_hold"] for q in self.board["questions"]],
                         ["no recommended option", "needs typed input", "multi-select",
                          "the pick is not one of the options", "no recommended option"])
        self.assertFalse(any(q["auto"] for q in self.board["questions"]))

    def test_auto_minutes_zero_turns_auto_pick_off(self):
        self.on("--words", "away", "--auto-minutes", "0")
        self.board["questions"].append(question())
        self.tick(1)
        self.assertEqual(self.board["questions"][0]["afk_hold"], "auto-pick is off for this window")

    def test_the_asker_s_own_auto_keeps_its_rule_but_gets_the_short_clock(self):
        self.on("--words", "away")
        self.board["questions"].append(question(auto=True, auto_after=iso(at(24 * 60))))
        self.tick(1)
        q = self.board["questions"][0]
        self.assertEqual(datetime.datetime.fromisoformat(q["auto_after"]), at(20))

    def test_ryan_only_beats_the_askers_own_auto(self):
        self.on("--words", "away")
        self.board["questions"].append(question(title="Pay the invoice?", auto=True, auto_after=iso(at(60))))
        self.tick(1)
        q = self.board["questions"][0]
        self.assertFalse(q["auto"])
        self.assertEqual(q["auto_after"], "")
        self.assertIn("Ryan-only", q["afk_hold"])

    def test_extra_ryan_only_rules_from_the_contract(self):
        d, _ = self.on("--words", "away")
        d["ryan_only_extra"] = [r"\bbadge\b", "(unclosed"]
        A.save(d)
        self.board["questions"].append(question())
        self.tick(1)
        self.assertEqual(self.board["questions"][0]["afk_hold"], "Ryan-only (your extra rule)")

    def test_a_question_is_routed_once(self):
        self.on("--words", "away")
        self.board["questions"].append(question())
        self.tick(1)
        self.saves = 0
        self.assertEqual(self.tick(2), [])
        self.assertEqual(self.saves, 0)


PHRASINGS = (
    "Wire the money to the vendor?", "Spend the extra budget on more GPU credits?", "Upgrade to the Pro tier?",
    "This costs forty dollars, ok?", "Renew the domain?", "Order the part?", "Enter the card number?",
    "Merge PR 12 into main?", "Push the branch to origin?", "Post the summary in the channel?",
    "Message the TA about the extension?", "Notify the team?", "Share the doc with ci?",
    "Make the repo public?", "Ship the release to users?", "Tell ci about it?", "Kill the running render?",
    "Clean up the old worktrees?", "Overwrite the existing config?", "Revert the last commit?",
    "Discard local changes?", "Close the old PRs?", "Rotate the token?", "Transfer ownership of the repo?",
    "Apply to the internship?", "Register for the class?", "Sign the form?", "do_delete or keep?",
    "rm_rf the cache?", "Delete the old branches?", "Email the professor?",
)


class TestDefaultScope(Base):
    """Without --auto-all only a question its asker marked --auto gets a clock."""

    def test_a_plain_question_waits_by_default(self):
        self.on("--words", "away", all=False)
        self.board["questions"].append(question())
        self.tick(1)
        self.tick(500)
        q = self.board["questions"][0]
        self.assertFalse(q["auto"])
        self.assertIsNone(q["answered"])
        self.assertIn("--auto", q["afk_hold"])

    def test_an_asker_marked_question_still_gets_the_short_clock(self):
        self.on("--words", "away", all=False)
        self.board["questions"].append(question(auto=True, auto_after=iso(at(24 * 60))))
        self.tick(1)
        self.tick(21)
        self.assertEqual(self.board["questions"][0]["answered_by"], "auto")

    def test_auto_all_does_not_carry_into_the_next_window(self):
        self.on("--words", "one")
        with contextlib.redirect_stdout(io.StringIO()):
            A.cmd_off(now=at(5))
            A.cmd_on(["--words", "two"], now=at(10))
        self.assertFalse(A.load()["auto_all"])

    def test_the_real_phrasings_a_reviewer_found_all_wait_even_with_auto_all(self):
        self.on("--words", "away")
        for i, text in enumerate(PHRASINGS):
            self.board["questions"].append(question(f"q{i}", text))
        self.tick(1)
        self.tick(500)
        for q in self.board["questions"]:
            self.assertIsNone(q["answered"], q["title"])
            self.assertTrue(q["afk_hold"].startswith("Ryan-only"), (q["title"], q["afk_hold"]))

    def test_ordinary_design_questions_still_auto_pick_with_auto_all(self):
        self.on("--words", "away")
        for i, text in enumerate(("Which colour for the badge?", "Use tabs or spaces in the table?",
                                  "Name the helper parse_row or read_row?")):
            self.board["questions"].append(question(f"q{i}", text))
        self.tick(1)
        self.assertTrue(all(q["auto"] for q in self.board["questions"]))


class TestRules(Base):
    def test_a_clocked_question_is_rechecked_before_it_fires(self):
        self.on("--words", "away")
        self.board["questions"].append(question())
        self.tick(1)
        d = A.load()
        d["auto_minutes"] = 0
        A.save(d)
        self.tick(30)
        q = self.board["questions"][0]
        self.assertIsNone(q["answered"])
        self.assertFalse(q["auto"])
        self.assertEqual(self.told, [])

    def test_a_new_extra_rule_stops_an_already_clocked_question(self):
        self.on("--words", "away")
        self.board["questions"].append(question())
        self.tick(1)
        d = A.load()
        d["ryan_only_extra"] = [r"badge"]
        A.save(d)
        self.tick(30)
        self.assertIsNone(self.board["questions"][0]["answered"])
        self.assertEqual(self.board["questions"][0]["afk_hold"], "Ryan-only (your extra rule)")

    def test_a_demoted_asker_question_gets_its_own_clock_back_at_off(self):
        self.on("--words", "away")
        self.board["questions"].append(question(auto=True, auto_after=iso(at(900))))
        self.tick(1)
        d = A.load()
        d["auto_minutes"] = 0
        A.save(d)
        self.tick(2)
        with contextlib.redirect_stdout(io.StringIO()):
            A.cmd_off(now=at(5))
        q = self.board["questions"][0]
        self.assertEqual((q["auto"], q["auto_after"]), (True, iso(at(900))))

    def test_a_stale_stamp_from_an_old_window_is_looked_at_again(self):
        self.on("--words", "away")
        self.board["questions"].append(question())
        self.tick(1)                                  # stamped, clocked at 20 min
        stale = self.board["questions"][0]
        A.save({**A.load(), "afk": False})            # window ended without releasing the board
        with contextlib.redirect_stdout(io.StringIO()):
            A.cmd_on(["--words", "next night", "--auto-minutes", "0", "--auto-all"], now=at(600))
        self.tick(601)
        self.assertEqual(stale["afk_hold"], "auto-pick is off for this window")
        self.assertIsNone(stale["answered"])


class TestAutoPick(Base):
    def setUp(self):
        super().setUp()
        self.on("--words", "away")
        self.board["questions"].append(question())

    def test_fires_after_n_minutes_and_tells_the_asker(self):
        self.tick(1)
        self.assertEqual(self.tick(19), [])                                 # not yet
        acts = self.tick(21)
        q = self.board["questions"][0]
        self.assertEqual((q["answer"], q["answered_by"]), ("A", "auto"))
        self.assertTrue(q["answered"])
        self.assertIn("auto-picked q1: A", self.board["done"][0]["text"])
        self.assertEqual(self.board["done"][0]["group"], "ui")
        self.assertIn("auto-picked q1: A (AFK, recommended option)", acts)
        self.assertEqual(self.told[0][0], "ui-pane")
        self.assertIn("Ryan is AFK", self.told[0][1])
        self.assertIn('"A" (Teal)', self.told[0][1])

    def test_writes_the_digest_line(self):
        self.tick(1)
        self.tick(21)
        kinds = [k for _, k, _ in A.digest_lines()]
        self.assertEqual(kinds, ["AUTO-PICK"])
        self.assertIn("Q1 Which colour for the badge?: picked A (Teal)", A.digest_lines()[0][2])

    def test_fires_only_once(self):
        self.tick(1)
        self.tick(21)
        self.tick(30)
        self.assertEqual(len(self.board["done"]), 1)
        self.assertEqual(len(self.told), 1)

    def test_a_human_answer_in_time_wins(self):
        self.tick(1)
        q = self.board["questions"][0]
        q["answered"], q["answer"] = iso(at(10)), "B"
        self.tick(30)
        self.assertEqual((q["answer"], q.get("answered_by")), ("B", None))
        self.assertEqual(self.told, [])

    def test_a_dead_asker_pane_does_not_break_the_pass(self):
        self.tick(1)

        def boom(name, text):
            raise OSError("no such pane")
        A.tick(now=at(21), send=boom)
        self.assertEqual(self.board["questions"][0]["answered_by"], "auto")

    def test_a_failed_save_changes_nothing_and_tells_nobody(self):
        self.tick(1)

        def boom(s):
            raise OSError("disk full")
        with mock.patch.object(A, "_board_save", boom):
            self.assertEqual(A.tick(now=at(21), send=self.send), [])
        self.assertEqual(self.told, [])
        self.assertEqual([k for _, k, _ in A.digest_lines()], [])

    def test_an_unreadable_board_is_a_quiet_no_op(self):
        def bad():
            raise SystemExit("state.json is not readable JSON")
        with mock.patch.object(A, "_board_load", bad):
            self.assertEqual(A.tick(now=at(1), send=self.send), [])


class TestWedgeHandOff(Base):
    """Held questions are what G7's alarm sees; auto ones are not."""

    def test_held_waits_for_the_alarm_and_auto_does_not(self):
        self.on("--words", "away")
        self.board["questions"] += [question("q1"), question("q2", title="Pay the invoice?")]
        self.tick(1)
        afk = W.afk_load(A.AFK_PATH)
        due, _ = W.wedge_due(self.board["questions"], afk, {}, (at(1 + 31)).timestamp())
        self.assertEqual([d["id"] for d in due], ["q2"])

    def test_the_alarm_event_goes_through_a_mocked_phone(self):
        self.on("--words", "away", "--loud")
        self.board["questions"].append(question("q2", title="Pay the invoice?"))
        self.tick(1)
        fake = mock.Mock()
        fake.handle.return_value = "sent"
        with mock.patch.dict(sys.modules, {"phone_alerts": fake}), contextlib.redirect_stdout(io.StringIO()):
            ev, alerted = W.run_wedge(at(40).timestamp(), {}, questions=self.board["questions"],
                                      afk=W.afk_load(A.AFK_PATH))
        self.assertEqual(ev["kind"], "WEDGE")
        self.assertTrue(ev["loud"])
        fake.handle.assert_called_once()
        self.assertIn("q2", alerted)


class TestDigest(Base):
    def test_afk_parks_the_watchdog_line(self):
        self.on("--words", "away")
        self.assertTrue(A.digest_flag("ci-upgrade", "STALLED", "no output 20 min", NOW))
        self.assertEqual(A.digest_lines()[0][1:], ("WATCHDOG", "ci-upgrade: STALLED - no output 20 min"))
        self.assertTrue(open(A.DIGEST_PATH, encoding="utf-8").read().startswith("# AFK digest"))

    def test_quiet_parks_it_too(self):
        with contextlib.redirect_stdout(io.StringIO()):
            A.cmd_quiet(True, now=NOW)
        self.assertTrue(A.digest_flag("p", "ERROR", "boom", NOW))

    def test_off_means_send_as_before(self):
        self.assertFalse(A.digest_flag("p", "ERROR", "boom", NOW))
        self.assertFalse(os.path.exists(A.DIGEST_PATH))

    def test_a_corrupt_contract_means_not_away(self):
        with open(A.AFK_PATH, "w", encoding="utf-8") as fh:
            fh.write("{not json")
        self.assertFalse(A.digest_flag("p", "ERROR", "boom", NOW))
        self.assertEqual(A.tick(now=NOW, send=self.send), [])

    def test_newlines_and_length_are_squashed_to_one_line(self):
        self.on("--words", "away")
        A.digest_flag("p", "ERROR", "a\nb\r\n" + "x" * 500, NOW)
        text = open(A.DIGEST_PATH, encoding="utf-8").read()
        self.assertEqual(len([ln for ln in text.splitlines() if ln.startswith("- ")]), 1)
        self.assertLessEqual(len(A.digest_lines()[0][2]), 300)


class TestQuiet(Base):
    def test_quiet_never_touches_questions(self):
        with contextlib.redirect_stdout(io.StringIO()):
            A.cmd_quiet(True, now=NOW)
        self.board["questions"].append(question())
        self.assertEqual(self.tick(60), [])
        self.assertFalse(self.board["questions"][0]["auto"])
        self.assertNotIn("afk_seen", self.board["questions"][0])

    def test_quiet_on_leaves_the_g7_alarm_off(self):
        with contextlib.redirect_stdout(io.StringIO()):
            A.cmd_quiet(True, now=NOW)
        got = A.load()
        self.assertFalse(got["afk"])
        self.assertTrue(got["quiet"])
        self.assertEqual(W.wedge_due([question()], W.afk_load(A.AFK_PATH), {}, at(600).timestamp()), ([], {}))

    def test_quiet_off_prints_the_digest_and_archives_it(self):
        with contextlib.redirect_stdout(io.StringIO()):
            A.cmd_quiet(True, now=NOW)
        A.digest_flag("pane-x", "STALLED", "stuck", at(5))
        with contextlib.redirect_stdout(io.StringIO()) as out:
            A.cmd_quiet(False, now=at(60))
        self.assertIn("Quiet digest", out.getvalue())
        self.assertIn("pane-x: STALLED - stuck", out.getvalue())
        self.assertFalse(A.load()["quiet"])
        self.assertFalse(os.path.exists(A.DIGEST_PATH))
        self.assertTrue(any(f.endswith("-digest.md") for f in os.listdir(A.ARCHIVE_DIR)))

    def test_quiet_off_while_afk_keeps_the_digest_for_the_return(self):
        self.on("--words", "away")
        with contextlib.redirect_stdout(io.StringIO()):
            A.cmd_quiet(True, now=NOW)
        A.digest_flag("p", "ERROR", "boom", at(5))
        with contextlib.redirect_stdout(io.StringIO()) as out:
            A.cmd_quiet(False, now=at(10))
        self.assertIn("afk is still on", out.getvalue())
        self.assertTrue(os.path.exists(A.DIGEST_PATH))
        self.assertTrue(A.load()["afk"])

    def test_double_on_and_double_off_are_harmless(self):
        with contextlib.redirect_stdout(io.StringIO()) as out:
            A.cmd_quiet(False, now=NOW)
            A.cmd_quiet(True, now=NOW)
            A.cmd_quiet(True, now=at(5))
        self.assertIn("not on", out.getvalue())
        self.assertIn("already on", out.getvalue())
        self.assertEqual(A.load()["quiet_since"], iso(NOW))


class TestReturnBrief(Base):
    def run_night(self):
        words = "Auto-gate the free stuff.\nDo not touch money."
        self.on("--words", words, "--until", "2026-10-09T07:00")
        self.board["questions"] += [question("q1"), question("q2", title="Pay the $40 invoice?")]
        self.tick(1)
        self.tick(25)                                                      # q1 auto-picks
        A.digest_flag("ci-upgrade", "STALLED", "no output 20 min", at(60))
        A.digest_flag("g9", "VANISHED", "pane gone", at(90))
        self.ledger({"ts": at(40).timestamp(), "event": "task.status", "task": "g7", "state": "done", "text": "ok"},
                    {"ts": at(41).timestamp(), "event": "task.pr_ready", "task": "g7", "pr": "http://x/1"},
                    {"ts": at(42).timestamp(), "event": "task.status", "task": "g5", "state": "question", "text": "?"},
                    {"ts": at(-500).timestamp(), "event": "task.status", "task": "old", "state": "done", "text": "before"})
        with open(A.HOLDS_PATH, "w", encoding="utf-8") as fh:
            json.dump({"school-mate": {"reason": "needs Canvas login"}}, fh)
        K.add("Ship it?", ["Yes", "No"], now=at(50).timestamp())
        with contextlib.redirect_stdout(io.StringIO()) as out:
            brief = A.cmd_off(now=at(7 * 60))
        return words, brief, out.getvalue()

    def test_brief_has_words_decisions_waits_fleet_and_digest(self):
        words, brief, printed = self.run_night()
        self.assertEqual(brief, printed.rstrip("\n"))
        self.assertIn("=== Return brief: 2026-10-09 01:00 -> 2026-10-09 08:00 (7h00m) ===", brief)
        for line in words.splitlines():
            self.assertIn("  " + line, brief)
        self.assertIn("Expected back 2026-10-09 07:00; you are 1h00m past that.", brief)
        self.assertIn("Decided for you (1):\n  - 2026-10-09 01:25 Q1 Which colour for the badge?: picked A (Teal), "
                      "asked by ui-pane", brief)
        self.assertIn("Waiting on you (3):", brief)
        self.assertIn("Q2 Pay the $40 invoice? [Ryan-only (money)]", brief)
        self.assertIn("school-mate is held: needs Canvas login", brief)
        self.assertIn("Ship it?", brief)
        self.assertIn("Fleet while you were away: 1 done, 1 pr ready, 0 merged, 0 cleaned up, 1 attention", brief)
        self.assertNotIn("old", brief.split("Fleet while")[1].split("Watchdog")[0])
        self.assertIn("Watchdog notes parked (2):", brief)
        self.assertIn("ci-upgrade: STALLED - no output 20 min", brief)

    def test_the_decisions_survive_the_hourly_prune(self):
        self.on("--words", "away")
        self.board["questions"].append(question())
        self.tick(1)
        self.tick(25)
        self.board["questions"].clear()               # board.py prune deletes answered questions
        with contextlib.redirect_stdout(io.StringIO()) as out:
            A.cmd_off(now=at(300))
        self.assertIn("Decided for you (1):", out.getvalue())
        self.assertIn("Q1 Which colour for the badge?: picked A (Teal)", out.getvalue())

    def test_off_ends_the_window_and_clears_the_digest(self):
        self.run_night()
        got = A.load()
        self.assertFalse(got["afk"])
        self.assertEqual(got["ack_ts"], iso(at(7 * 60)))
        self.assertEqual(got["words"], "Auto-gate the free stuff.\nDo not touch money.")
        self.assertFalse(os.path.exists(A.DIGEST_PATH))
        names = os.listdir(A.ARCHIVE_DIR)
        self.assertEqual(sorted(n.split("-", 1)[1] for n in names), ["afk.json", "brief.md", "digest.md"])

    def test_off_takes_the_afk_clock_off_what_it_did_not_fire(self):
        self.on("--words", "away")
        self.board["questions"] += [
            question("q1"), question("q2", title="Pay the invoice?", auto=True, auto_after=iso(at(900))),
            question("q3", auto=True, auto_after=iso(at(900)))]
        self.tick(1)
        self.assertEqual(datetime.datetime.fromisoformat(self.board["questions"][2]["auto_after"]), at(20))
        with contextlib.redirect_stdout(io.StringIO()):
            A.cmd_off(now=at(5))
        q1, q2, q3 = self.board["questions"]
        self.assertEqual((q1["auto"], q1["auto_after"]), (False, ""))
        self.assertEqual((q2["auto"], q2["auto_after"]), (True, iso(at(900))))
        self.assertEqual((q3["auto"], q3["auto_after"]), (True, iso(at(900))))
        for q in self.board["questions"]:
            self.assertFalse([k for k in q if k.startswith("afk_")], q["id"])

    def test_off_keeps_the_digest_while_quiet_is_still_on(self):
        self.on("--words", "away")
        with contextlib.redirect_stdout(io.StringIO()):
            A.cmd_quiet(True, now=NOW)
        A.digest_flag("p", "ERROR", "boom", at(5))
        with contextlib.redirect_stdout(io.StringIO()):
            A.cmd_off(now=at(10))
        self.assertTrue(os.path.exists(A.DIGEST_PATH))
        self.assertTrue(A.load()["quiet"])

    def test_off_when_not_afk_says_so(self):
        with contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertIsNone(A.cmd_off(now=NOW))
        self.assertIn("not on", out.getvalue())

    def test_an_empty_night_says_nothing_instead_of_inventing(self):
        self.on("--words", "")
        with contextlib.redirect_stdout(io.StringIO()) as out:
            A.cmd_off(now=at(30))
        text = out.getvalue()
        self.assertIn("Your words: none recorded.", text)
        self.assertEqual(text.count("(nothing)"), 3)

    def test_a_long_digest_shows_the_last_eight(self):
        self.on("--words", "away")
        for i in range(12):
            A.digest_flag(f"p{i}", "ERROR", "boom", at(i + 1))
        with contextlib.redirect_stdout(io.StringIO()) as out:
            A.cmd_off(now=at(60))
        self.assertIn("Watchdog notes parked (12):", out.getvalue())
        self.assertIn("(+4 older ones in board/afk-digest.md)", out.getvalue())
        self.assertIn("p11:", out.getvalue())
        self.assertNotIn("p3:", out.getvalue())


class TestReviewFixes(Base):
    """The five FIX-FIRST items from the 2026-10-09 Rule 6 review (wrap-p1-orch, range a)."""

    def next_window(self, *args):
        with contextlib.redirect_stdout(io.StringIO()):
            A.cmd_off(now=at(5))
            return A.cmd_on(["--words", "two"] + list(args), now=at(10))

    def test_loud_and_wedge_minutes_do_not_carry_into_the_next_window(self):
        self.on("--words", "one", "--loud", "--wedge-minutes", "5")
        d = self.next_window()
        self.assertEqual((d["loud"], d["wedge_minutes"]), (False, float(A.WEDGE_MINUTES)))

    def test_a_held_question_is_looked_at_again_when_auto_all_is_added(self):
        self.on("--words", "away", all=False)
        self.board["questions"].append(question(title="Pick the layout"))
        self.tick(1)
        self.assertIn("--auto-all", self.board["questions"][0]["afk_hold"])
        self.on("--auto-all", all=False)
        self.tick(2)
        q = self.board["questions"][0]
        self.assertTrue(q["auto"] and q["afk_auto"])
        self.assertNotIn("afk_hold", q)
        self.tick(30)
        self.assertEqual(self.board["questions"][0]["answered_by"], "auto")

    def test_a_ryan_only_hold_is_not_lifted_by_auto_all(self):
        self.on("--words", "away", all=False)
        self.board["questions"].append(question(title="Delete the old branch?"))
        self.tick(1)
        self.on("--auto-all", all=False)
        self.tick(2)
        self.assertTrue(self.board["questions"][0]["afk_hold"].startswith("Ryan-only"))

    def test_turning_auto_pick_back_on_releases_the_off_hold(self):
        self.on("--words", "away", "--auto-minutes", "0")
        self.board["questions"].append(question())
        self.tick(1)
        self.assertEqual(self.board["questions"][0]["afk_hold"], A.HOLD_OFF)
        self.on("--auto-minutes", "10")
        self.tick(2)
        self.assertTrue(self.board["questions"][0]["afk_auto"])

    def test_the_destructive_words_the_review_found_all_wait(self):
        self.on("--words", "away")
        words = ("Run git clean -fdx on the repo?", "Use del /q on the temp files?", "Use rd /s on the folder?",
                 "Move the old folder to the trash?", "Move it to the recycle bin?", "Format the drive?",
                 "Unlink the old file?", "Restart the server now?", "Reboot the box?", "Shut down the box?",
                 "Shutdown the box?")
        for i, text in enumerate(words):
            self.board["questions"].append(question(f"q{i}", text))
        self.tick(1)
        self.tick(500)
        for q in self.board["questions"]:
            self.assertIsNone(q["answered"], q["title"])
            self.assertEqual(q["afk_hold"], "Ryan-only (delete)", q["title"])

    def test_a_failed_send_to_the_asker_is_noticed(self):
        self.on("--words", "away")
        self.board["questions"].append(question())
        self.tick(1)
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            A.tick(now=at(21), send=lambda name, text: False)
        self.assertIn("could not tell ui-pane", err.getvalue())
        waits = [t for _, k, t in A.digest_lines() if k == "WAITS"]
        self.assertEqual(len(waits), 1)
        self.assertIn("ui-pane was not told", waits[0])

    def test_a_raising_send_is_noticed_in_the_digest_too(self):
        self.on("--words", "away")
        self.board["questions"].append(question())
        self.tick(1)

        def boom(name, text):
            raise OSError("no such pane")
        with contextlib.redirect_stderr(io.StringIO()):
            A.tick(now=at(21), send=boom)
        self.assertEqual(sum(k == "WAITS" for _, k, _ in A.digest_lines()), 1)

    def test_a_good_send_adds_no_waits_line(self):
        self.on("--words", "away")
        self.board["questions"].append(question())
        self.tick(1)
        self.tick(21)
        self.assertEqual([k for _, k, _ in A.digest_lines()], ["AUTO-PICK"])

    def test_the_quiet_digest_does_not_claim_nothing_is_waiting(self):
        with contextlib.redirect_stdout(io.StringIO()):
            A.cmd_quiet(True, now=NOW)
        A.digest_flag("pane-x", "STALLED", "stuck", at(5))
        with contextlib.redirect_stdout(io.StringIO()) as out:
            A.cmd_quiet(False, now=at(60))
        text = out.getvalue()
        self.assertIn("pane-x: STALLED - stuck", text)
        for false_claim in ("Waiting on you", "Your words", "Fleet while", "Decided for you"):
            self.assertNotIn(false_claim, text)


class TestCli(Base):
    def run_main(self, *argv):
        with contextlib.redirect_stdout(io.StringIO()) as out:
            A.main(list(argv))
        return out.getvalue()

    def test_on_status_off_round_trip(self):
        self.assertIn("afk is ON", self.run_main("afk", "on", "--words", "gone to sleep"))
        status = self.run_main("afk", "status")
        self.assertIn("afk: ON", status)
        self.assertIn("words: gone to sleep", status)
        self.assertIn("Return brief", self.run_main("afk", "off"))
        self.assertEqual(self.run_main("afk", "status").strip(), "afk: off. quiet: off.")

    def test_quiet_commands(self):
        self.assertIn("quiet is ON", self.run_main("quiet", "on"))
        self.assertIn("quiet: ON", self.run_main("afk", "status"))
        self.assertIn("Quiet digest", self.run_main("quiet", "off"))

    def test_usage_errors_exit(self):
        with self.assertRaises(SystemExit):
            A.main(["quiet", "maybe"])
        with self.assertRaises(SystemExit):
            A.main(["afk", "sideways"])

    def test_tick_command_when_nothing_to_do(self):
        self.assertIn("nothing to do", self.run_main("afk", "tick"))


if __name__ == "__main__":
    unittest.main()
