"""py test_restart_idle.py - `orch.py restart-idle`: restart idle claude panes with --resume <sid>.

The two things that must never go wrong: resuming the wrong session (`claude -c` picks the newest
one in a cwd), and restarting a pane that is busy, prompting, or has Ryan's half-typed text in it.
The draft fixtures below are verbatim pane reads from 2026-10-02 (learn-voice-tutor, sc-reaction-lines).
"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import restart_idle as R  # noqa: E402

RULE = "\u2500" * 60
DRAFT = f"""✻ Sautéed for 9m 11s · done 3:32 PM
{RULE} learn-voice-tutor ─
❯ run ensure now to swap to v5
{RULE}
  learn-voice-tutor ·  main *621 · Sonnet 5.5 high · 19% ctx
  ⏵⏵ bypass permissions on (shift+tab to cycle) · ← 1 agent
"""
EMPTY = f"""✻ Cooked for 4m 44s · done 1:30 PM

{RULE} sc-reaction-lines ─
❯
{RULE}
  sc-reaction-lines · +138 -38 · Sonnet 5.5 · 12% ctx
  ⏵⏵ bypass permissions on (shift+tab to cycle) · ← 1 agent
"""
SID = "4eaabe13-5063-4b7b-a27e-6993a4c1bf70"
ARGV = [r"C:\Users\YOURNAME\.local\bin\claude.exe", "--dangerously-skip-permissions", "--model",
        "claude-sonnet-5-5", "--name", "hx-restart-idle"]


def agent(pane="w1:p1", status="idle", sid=SID):
    return {"pane_id": pane, "agent": "claude", "agent_status": status, "name": "n",
            "agent_session": {"value": sid} if sid else {}}


class LaunchLine(unittest.TestCase):
    def test_keeps_every_flag_and_adds_the_exact_resume(self):
        self.assertEqual(
            R.launch_line(ARGV, SID),
            f"claude --dangerously-skip-permissions --model claude-sonnet-5-5 "
            f"--name hx-restart-idle --resume {SID}")

    def test_never_uses_continue(self):
        self.assertNotIn(" -c", R.launch_line(ARGV, SID))
        self.assertNotIn("--continue", R.launch_line(ARGV + ["--continue"], SID))

    def test_an_old_resume_is_replaced_not_doubled(self):
        for old in (["--resume", "oldid"], ["-r", "oldid"], ["--resume=oldid"],
                    ["--session-id", "oldid"], ["--resume"]):
            line = R.launch_line(ARGV + old, SID)
            self.assertEqual(line.count("--resume"), 1, line)
            self.assertNotIn("oldid", line)
            self.assertTrue(line.endswith(f"--resume {SID}"), line)

    def test_effort_and_a_spaced_name_survive_quoted(self):
        line = R.launch_line(ARGV[:-1] + ["my pane", "--effort", "high"], SID)
        self.assertIn('--name "my pane"', line)
        self.assertIn("--effort high", line)

    def test_a_launch_prompt_is_not_replayed_on_resume(self):
        # Verbatim from pane w28:p2 (Bridge open live test), 2026-10-02: the resume re-ran it.
        argv = [r"C:\Users\YOURNAME\.local\bin\claude.exe", "--allow-dangerously-skip-permissions",
                "--model", "haiku", "--permission-mode", "acceptEdits",
                "Use the Write tool to write the file x.html. Then stop."]
        line = R.launch_line(argv, SID)
        self.assertNotIn("Use the Write", line)
        self.assertIn("--permission-mode acceptEdits", line)

    def test_not_a_claude_launch_is_refused(self):
        self.assertIsNone(R.launch_line(["node", "cli.js"], SID))
        self.assertIsNone(R.launch_line(None, SID))


class Draft(unittest.TestCase):
    def test_typed_text_is_a_draft(self):
        self.assertTrue(R.has_draft(DRAFT))

    def test_empty_box_is_not(self):
        self.assertFalse(R.has_draft(EMPTY))

    def test_no_box_is_not(self):
        self.assertFalse(R.has_draft("just a shell prompt\n$ "))


class Judge(unittest.TestCase):
    def judge(self, a, txt=EMPTY, seat="w9:p9", include_seat=False, me="w0:p0"):
        with mock.patch.object(R.L, "pane_read", return_value=txt):
            return R.judge(a, seat, include_seat, me)

    def test_idle_and_done_restart(self):
        for st in ("idle", "done"):
            self.assertEqual(self.judge(agent(status=st))[0], "restart")

    def test_acp_pane_is_skipped(self):
        with mock.patch.object(R.L, "acp_pane_ids", return_value={"w1:p1"}):
            verdict, why = self.judge(agent(pane="w1:p1"))
        self.assertEqual(verdict, "skip")
        self.assertIn("ACP", why)

    def test_working_is_queued_not_restarted(self):
        self.assertEqual(self.judge(agent(status="working"))[0], "queue")

    def test_blocked_and_unknown_are_skipped(self):
        for st in ("blocked", "unknown"):
            self.assertEqual(self.judge(agent(status=st))[0], "skip")

    def test_open_permission_prompt_is_skipped(self):
        txt = ("Do you want to proceed?\n 1. Yes\n 2. No\nEsc to cancel\n" + EMPTY)
        self.assertEqual(self.judge(agent(), txt)[0], "skip")

    def test_a_draft_is_skipped(self):
        v, why = self.judge(agent(), DRAFT)
        self.assertEqual(v, "skip")
        self.assertIn("typed", why)

    def test_seat_skipped_unless_asked(self):
        self.assertEqual(self.judge(agent(pane="w9:p9"))[0], "skip")
        self.assertEqual(self.judge(agent(pane="w9:p9"), include_seat=True)[0], "restart")

    def test_the_pane_running_the_command_is_never_restarted(self):
        self.assertEqual(self.judge(agent(pane="w0:p0"), include_seat=True)[0], "skip")

    def test_no_session_id_is_skipped(self):
        self.assertEqual(self.judge(agent(sid=None))[0], "skip")


class SeatPanes(unittest.TestCase):
    def test_a_pane_named_like_the_seat_is_protected_even_with_no_seat_file(self):
        # 2026-10-02: run from a worktree, the seat file was not found and the live seat restarted.
        seat = dict(agent(pane="w5:p26"), name="orchestrator")
        with mock.patch.object(R.L, "orchestrator_pane", return_value=None), \
                mock.patch.object(R, "claude_agents", return_value=[seat, agent("w1:p1")]), \
                mock.patch.object(R.os.path, "join", side_effect=lambda *p: "/nonexistent/x"):
            self.assertEqual(R.seat_panes(), {"w5:p26"})


class RestartPane(unittest.TestCase):
    def run_it(self, argv_seq, agents):
        sent = []

        def herdr(*args, **kw):
            sent.append(args)
            return {}
        with mock.patch.object(R.L, "herdr", side_effect=herdr), \
                mock.patch.object(R.L, "send_keys"), \
                mock.patch.object(R.L, "pane_read", return_value=""), \
                mock.patch.object(R, "foreground_argv", side_effect=argv_seq), \
                mock.patch.object(R, "agent_for", side_effect=agents), \
                mock.patch.object(R.time, "sleep"):
            ok, detail = R.restart_pane(agent())
        return ok, detail, sent

    def test_exits_then_types_the_resume_line_in_the_same_pane(self):
        shell = ["pwsh.exe"]
        ok, detail, sent = self.run_it([ARGV, shell, shell, shell],
                                       [agent(), agent(), agent()])
        self.assertTrue(ok, detail)
        run = [s for s in sent if s[:2] == ("pane", "run")][0]
        self.assertEqual(run[2], "w1:p1")
        self.assertIn(f"--resume {SID}", run[3])
        self.assertIn(("pane", "send-text", "w1:p1", "/exit"), sent)

    def test_a_pane_that_went_busy_is_left_alone(self):
        ok, detail, sent = self.run_it([ARGV], [agent(status="working")])
        self.assertFalse(ok)
        self.assertFalse([s for s in sent if s[:2] == ("pane", "send-text")])

    def run_bg(self, ignore_bg):
        keys = []
        shell = ["pwsh.exe"]
        idle_bg = dict(agent(), tokens={"bgn": "1", "bg": "1 bg: monitor"})
        with mock.patch.object(R.L, "herdr", return_value={}), \
                mock.patch.object(R.L, "send_keys", side_effect=lambda *k: keys.append(k)), \
                mock.patch.object(R.L, "pane_read", return_value="Background work is running\n The following will stop when you exit:"), \
                mock.patch.object(R, "foreground_argv", side_effect=[ARGV, shell, shell, shell]), \
                mock.patch.object(R, "agent_for", side_effect=[idle_bg, agent(), agent()]), \
                mock.patch.object(R.time, "sleep"):
            ok, detail = R.restart_pane(idle_bg, ignore_bg=ignore_bg)
        return ok, detail, keys

    def test_button_restarts_an_idle_pane_with_a_monitor_and_answers_the_dialog(self):
        ok, detail, keys = self.run_bg(True)
        self.assertTrue(ok, detail)
        self.assertIn(("w1:p1", "1"), keys)

    def test_bulk_restart_still_treats_a_monitor_as_busy(self):
        ok, detail, keys = self.run_bg(False)
        self.assertFalse(ok)
        self.assertEqual(keys, [])


if __name__ == "__main__":
    unittest.main()
