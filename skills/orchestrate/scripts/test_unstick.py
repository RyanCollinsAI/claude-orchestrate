"""py test_unstick.py - unstick-mate's guard rails: what it refuses, what it escalates, what it logs."""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import orch_watchdog as W  # noqa: E402
import unstick as U  # noqa: E402

NOW = 1_000_000.0

# Verbatim text a pane shows after a hook refuses `cd X && ...` (captured 2026-10-02 in this build).
HOOK_PANE = """\
● Bash(cd C:/Users/YOURNAME/.claude/hooks && grep -n "x" block-known-traps.py)
  ⎿  PreToolUse:Bash hook error: Blocked: `cd X && ...` trips a deny rule and parks the pane on a prompt.
     Split into two calls instead: run `cd C:/Users/YOURNAME/.claude/hooks` alone first.

────────────────────────────────────────────
❯
  ⏵⏵ bypass permissions on (shift+tab to cycle)
"""
QUESTION_PANE = """\
● The task file says to write the log to board/x.log, but board/ has two x.log files.
  Which one should I append to - the one in orchestrate/board or the one in the repo root?

❯
  ⏵⏵ bypass permissions on (shift+tab to cycle)
"""
SUBMIT_PANE = """\
● The homework is finished and the PDF is ready.
  Do you want me to submit it to Canvas now?

❯
  ⏵⏵ bypass permissions on (shift+tab to cycle)
"""
PERMISSION_PANE = """\
 Do you want to make this edit to foo.py?
 ❯ 1. Yes
   2. Yes, allow all edits during this session
   3. No, and tell Claude what to do differently (esc)
 Esc to cancel
"""
STALL_PANE = """\
● I ran the tests. 14 passed.

❯
  ⏵⏵ bypass permissions on (shift+tab to cycle)
"""


class Classify(unittest.TestCase):
    def test_a_hook_denied_command(self):
        self.assertEqual(U.classify(HOOK_PANE)[0], "HOOK_DENIED")

    def test_a_question_the_rules_can_answer(self):
        self.assertEqual(U.classify(QUESTION_PANE)[0], "QUESTION")

    def test_a_submit_question_is_escalated_not_answered(self):
        self.assertEqual(U.classify(SUBMIT_PANE)[0], "ESCALATE")

    def test_a_permission_prompt_is_never_answered_here(self):
        self.assertEqual(U.classify(PERMISSION_PANE)[0], "PERMISSION")

    def test_no_question_is_a_plain_stall(self):
        self.assertEqual(U.classify(STALL_PANE)[0], "STALL")

    def test_every_human_only_action_escalates(self):
        for said in ("Should I pay the quote?", "Want me to send an email to the professor?",
                     "Ready to publish the video - go?", "Delete the old worktree?",
                     "Should I force-push?", "Which option do you prefer, A or B?", "Which approach do you prefer?",
                     "Merge to main now?"):
            self.assertEqual(U.classify("● " + said + "\n\n❯\n")[0], "ESCALATE", said)


# Verbatim screen of stage-c (captured 2026-10-02): the spawn prompt says "never message ... pay",
# and a Stop hook error is showing. Neither may change the verdict.
STAGE_C_SCREEN = """\
❯ STAGED TEST, do nothing else. Pretend you finished a homework PDF.
  questions and your final report by SendMessage. Only the orchestrator talks to your human: never SendUserFile, never message or notify them directly
● Do you want me to submit it to Canvas now?
● Ran 6 stop hooks
  ⎿  Stop hook error: You ended the turn by offering instead of acting.
✢ Sprouting… (8s · ↓ 338 tokens · thinking)
  ⎿  Tip: Run /ultrareview for a cloud-based multi-agent review
hide off · voice on · dict fix on · keep off · fleet · needs me · handoff
  stage-c ·  main *91 ↑6 ↓2 · Haiku 4.5 · 23% ctx · limit 5h 30% ·29m 7d 29% ·1d · 16s
  ⏵⏵ bypass permissions on (shift+tab to cycle) · ← 1 agent
"""


class Screen(unittest.TestCase):
    def test_clean_screen_drops_footer_and_tips(self):
        out = U.clean_screen(STAGE_C_SCREEN)
        self.assertIn("Do you want me to submit it to Canvas now?", out)
        self.assertNotIn("Tip:", out)
        self.assertNotIn("bypass permissions", out)

    def test_a_stop_hook_error_is_not_a_hook_denial(self):
        self.assertEqual(U.classify("Fine.", U.clean_screen(STAGE_C_SCREEN))[0], "STALL")

    def test_the_spawn_prompt_on_screen_does_not_escalate(self):
        self.assertEqual(U.classify("All tests pass.", STAGE_C_SCREEN)[0], "STALL")

    def test_the_submit_message_escalates(self):
        self.assertEqual(U.classify("Do you want me to submit it to Canvas now?",
                                    U.clean_screen(STAGE_C_SCREEN))[0], "ESCALATE")

    def test_a_real_hook_denial_on_screen(self):
        self.assertEqual(U.classify("Running it.", U.clean_screen(HOOK_PANE))[0], "HOOK_DENIED")


class Gate(unittest.TestCase):
    def test_never_the_seat_or_itself(self):
        self.assertFalse(U.gate("orchestrator", NOW, {}, {"orchestrator"})[0])
        self.assertFalse(U.gate("unstick-mate", NOW, {}, {"orchestrator"})[0])

    def test_never_a_domain_mate(self):
        self.assertFalse(U.gate("sc-mate", NOW, {}, set(), domain_mate=True)[0])

    def test_one_action_per_pane_per_ten_minutes(self):
        st = {"acted": {"sc-crop": NOW}}
        self.assertFalse(U.gate("sc-crop", NOW + 599, st, set())[0])
        self.assertTrue(U.gate("sc-crop", NOW + 601, st, set())[0])
        self.assertTrue(U.gate("other", NOW + 1, st, set())[0])

    def test_record_logs_and_starts_the_clock(self):
        with tempfile.TemporaryDirectory() as d:
            old = (U.STATE_PATH, U.LOG_PATH, U.board_update)
            U.STATE_PATH = os.path.join(d, "s.json")
            U.LOG_PATH = os.path.join(d, "unstick.log")
            U.board_update = lambda *a: True
            try:
                line = U.record("sc-crop", "STALLED", "answered the log-path question", "task file", NOW)
                self.assertIn("pane=sc-crop", line)
                self.assertIn("flag=STALLED", line)
                self.assertFalse(U.gate("sc-crop", NOW + 5, U.state_load(), set())[0])
            finally:
                U.STATE_PATH, U.LOG_PATH, U.board_update = old


class WatchdogRouting(unittest.TestCase):
    def flag(self, name, cond):
        return {"name": name, "cond": cond, "detail": ["x"], "owner": "sc-mate"}

    def test_stall_error_prompt_vanish_wake_the_mate(self):
        for c in ("STALLED", "ERROR", "PROMPT", "VANISHED"):
            self.assertEqual(W.unstick_target(self.flag("sc-crop", [c]), "orchestrator", "sc-mate"),
                             "unstick-mate")

    def test_never_for_the_seat_the_mate_or_a_ghost(self):
        self.assertIsNone(W.unstick_target(self.flag("orchestrator", ["STALLED"]), "orchestrator", "x"))
        self.assertIsNone(W.unstick_target(self.flag("unstick-mate", ["ERROR"]), "orchestrator", "x"))
        self.assertIsNone(W.unstick_target(self.flag("dup", ["GHOST"]), "orchestrator", "x"))

    def test_not_told_twice_when_it_is_the_owner(self):
        self.assertIsNone(W.unstick_target(self.flag("p", ["STALLED"]), "orchestrator", "unstick-mate"))

    def test_the_wake_line_names_the_pane_and_the_check_command(self):
        t = W.unstick_text(self.flag("sc-crop", ["PROMPT"]))
        self.assertIn("sc-crop", t)
        self.assertIn("unstick.py", t)
        self.assertIn("check sc-crop --flag PROMPT", t)

    def test_respawn_named_refuses_a_pane_that_is_not_vanished(self):
        with tempfile.TemporaryDirectory() as d:
            old = W.STATE_PATH
            W.STATE_PATH = os.path.join(d, "w.json")
            try:
                W.state_save({"tasked": {"k": {"name": "live-one", "status": "busy"}}})
                self.assertIn("not a vanished pane", W.respawn_named("live-one"))
            finally:
                W.STATE_PATH = old


if __name__ == "__main__":
    unittest.main()
