"""Tests for crew-dispatch.json wiring and the --effort flag, run with `py test_dispatch.py`.

  effort    one level, three spellings: claude --effort, pi --thinking, codex -c override;
            an unknown level is refused before it reaches a pane
  dispatch  the shipped table is valid; a profile list resolves to the harness with the most
            weekly quota left; `dispatch` prints every rule with one pick
  review    --harness pi spawns a pi reviewer that reports by file, never by SendMessage
"""
import contextlib, io, json, os, sys, tempfile, unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import orchlib as L
import orch

HERE = os.path.dirname(os.path.abspath(__file__))


class TestEffortFlags(unittest.TestCase):

    def test_claude_flag_pair_only_when_asked(self):
        self.assertEqual(orch.effort_args("xhigh"), ["--effort", "xhigh"])
        self.assertEqual(orch.effort_args(None), [])

    def test_pi_uses_thinking(self):
        cmd = orch.pi_cmd("gpt-6-astra", "medium")
        self.assertIn("--model gpt-6-astra", cmd)
        self.assertTrue(cmd.endswith("--thinking medium"), cmd)
        self.assertNotIn("--thinking", orch.pi_cmd("gpt-6-astra"))

    def test_codex_uses_config_override(self):
        cmd = orch.codex_cmd(model="gpt-6-astra", effort="high")
        self.assertIn('-c model_reasoning_effort="high"', cmd)
        self.assertNotIn("model_reasoning_effort", orch.codex_cmd(model="gpt-6-astra"))

    def test_unknown_level_is_refused_and_max_is_not_a_level(self):
        self.assertEqual(orch.check_effort("high"), "high")
        self.assertIsNone(orch.check_effort(None))
        with self.assertRaises(SystemExit):
            orch.check_effort("silly")
        with self.assertRaises(SystemExit):
            orch.check_effort("max")


ROWS = [
    {"provider": "claude/primary", "weekly_left": 41, "active": True},
    {"provider": "claude/secondary", "weekly_left": 0, "active": False},
    {"provider": "claude/ambient", "weekly_left": 94, "active": False},
    {"provider": "codex", "weekly_left": 78, "active": False},
]


class TestDispatchResolution(unittest.TestCase):

    def test_shipped_table_is_valid(self):
        d = orch.load_dispatch(os.path.join(os.path.dirname(HERE), "crew-dispatch.json"))
        self.assertTrue(d["rules"])
        for r in d["rules"]:
            self.assertTrue(r["when"].strip())
            for p in orch._profiles(r["use"]):
                self.assertIn(p["harness"], L.KINDS)
                if p.get("effort"):
                    self.assertIn(p["effort"], orch.EFFORTS)
                if p["harness"] == "pi" and p.get("model"):
                    self.assertNotIn(p["model"], L.MODELS, "an Anthropic tier on pi is extra usage")
        self.assertTrue(orch._profiles(d["default"]))

    def test_headroom_uses_active_claude_account_and_codex_for_pi(self):
        h = orch.quota_headroom(ROWS)
        self.assertEqual(h["claude"], 41)   # the active login, not the 94% ambient feed
        self.assertEqual(h["pi"], 78)

    def test_list_resolves_to_most_weekly_headroom(self):
        profs = [{"harness": "claude", "model": "sonnet"}, {"harness": "pi", "model": "gpt-6-astra"}]
        self.assertEqual(orch.resolve_profile(profs, {"claude": 41, "pi": 78})["harness"], "pi")
        self.assertEqual(orch.resolve_profile(profs, {"claude": 80, "pi": 78})["harness"], "claude")
        # a tie keeps the author's order; an unreadable harness never wins
        self.assertEqual(orch.resolve_profile(profs, {"claude": 50, "pi": 50})["harness"], "claude")
        self.assertEqual(orch.resolve_profile(profs, {"claude": None, "pi": 3})["harness"], "pi")
        self.assertEqual(orch.resolve_profile([profs[0]], {})["harness"], "claude")

    def test_dispatch_prints_one_pick_per_rule(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = os.path.join(tmp.name, "crew-dispatch.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({
                "rules": [
                    {"when": "a review", "use": [{"harness": "pi", "model": "gpt-6-astra", "effort": "medium"},
                                                 {"harness": "claude", "model": "sonnet", "effort": "high"}],
                     "why": "because"},
                    {"when": "hardware", "use": {"harness": "claude", "model": "sonnet"}},
                ],
                "default": [{"harness": "claude", "model": "sonnet"}],
            }, fh)
        import quota
        buf = io.StringIO()
        with mock.patch.object(quota, "collect", return_value=(ROWS, None)), \
             contextlib.redirect_stdout(buf):
            orch.cmd_dispatch(path)
        out = buf.getvalue()
        self.assertIn("weekly headroom: claude 41%, pi 78%", out)
        self.assertIn("1. WHEN a review", out)
        self.assertIn("USE  --harness pi --model gpt-6-astra --effort medium", out)
        self.assertIn("else --harness claude --model sonnet --effort high", out)
        self.assertIn("WHY  because", out)
        self.assertIn("2. WHEN hardware", out)
        self.assertIn("DEFAULT --harness claude --model sonnet", out)

    def test_missing_table_says_so_and_bad_json_exits(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            orch.cmd_dispatch(os.path.join(HERE, "no-such-dispatch.json"))
        self.assertIn("no dispatch table", buf.getvalue())
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        bad = os.path.join(tmp.name, "crew-dispatch.json")
        with open(bad, "w", encoding="utf-8") as fh:
            fh.write("{ not json")
        with self.assertRaises(SystemExit):
            orch.load_dispatch(bad)


class TestDispatchSchema(unittest.TestCase):

    GOOD = {"rules": [{"when": "x", "use": [{"harness": "claude", "model": "sonnet", "effort": "high"}]}],
            "default": [{"harness": "pi", "model": "gpt-6-astra"}], "runwayFloor": 10}

    def test_shipped_table_and_good_table_have_no_problems(self):
        self.assertEqual(orch.dispatch_problems(self.GOOD), [])
        with open(os.path.join(os.path.dirname(HERE), "crew-dispatch.json"), encoding="utf-8") as fh:
            self.assertEqual(orch.dispatch_problems(json.load(fh)), [])

    def test_each_fault_is_named(self):
        def bad(**over):
            d = json.loads(json.dumps(self.GOOD))
            d["rules"][0].update(over)
            return orch.dispatch_problems(d)
        self.assertIn("rule 1: missing \"use\"", orch.dispatch_problems({"rules": [{"when": "x"}]}))
        self.assertTrue(any("harness" in p for p in bad(use=[{"harness": "gemini"}])))
        self.assertTrue(any("effort" in p for p in bad(use=[{"harness": "pi", "effort": "max"}])))
        self.assertTrue(any("empty list" in p for p in bad(use=[])))
        self.assertTrue(any("\"when\"" in p for p in bad(when="")))
        self.assertTrue(any("runwayFloor" in p for p in orch.dispatch_problems(dict(self.GOOD, runwayFloor="10"))))
        self.assertTrue(any("runwayFloor" in p for p in orch.dispatch_problems(dict(self.GOOD, runwayFloor=150))))
        self.assertTrue(any("rules" in p for p in orch.dispatch_problems({})))

    def test_loader_exits_on_a_bad_table(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = os.path.join(tmp.name, "crew-dispatch.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"rules": [{"when": "x", "use": [{"harness": "pi", "effort": "max"}]}]}, fh)
        with self.assertRaises(SystemExit) as cm:
            orch.load_dispatch(path)
        self.assertIn("effort", str(cm.exception))


class TestReviewOnPi(unittest.TestCase):

    def _review(self, harness):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        calls = {}

        def fake_spawn(label, prompt, **kw):
            calls.update(label=label, prompt=prompt, **kw)
            return "p9", "sid00000"

        with mock.patch.object(orch, "_diff_check", return_value=(True, "HEAD~1..HEAD")), \
             mock.patch.object(orch, "cmd_spawn", side_effect=fake_spawn), \
             mock.patch.object(L, "HANDOFFS", tmp.name), \
             mock.patch.object(L, "orchestrator_name", return_value="orch"), \
             mock.patch.object(L, "caller_name", return_value="orch"), \
             mock.patch.object(L, "owner_is_live", return_value=True), \
             mock.patch.object(L, "pi_mate", return_value=None), \
             contextlib.redirect_stdout(io.StringIO()):
            orch.cmd_review("lead-x", diff="HEAD~1..HEAD", model="gpt-6-astra", cwd=tmp.name,
                            harness=harness, effort="medium")
        return calls

    def test_pi_reviewer_reports_by_file_and_ends_with_done(self):
        c = self._review("pi")
        self.assertEqual(c["kind"], "pi")
        self.assertEqual(c["effort"], "medium")
        self.assertTrue(c["report"].endswith(".md"))
        self.assertNotIn("SendMessage", c["prompt"])
        self.assertIn("End with DONE", c["prompt"])
        self.assertIn(c["report"], c["prompt"])

    def test_claude_reviewer_still_messages_both(self):
        c = self._review("claude")
        self.assertEqual(c["kind"], "claude")
        self.assertEqual(c["report"], "")
        self.assertIn("SendMessage it to both lead-x and orch", c["prompt"])

    def test_other_harness_refused(self):
        with self.assertRaises(SystemExit):
            orch.cmd_review("lead-x", diff="x..y", harness="codex")


if __name__ == "__main__":
    unittest.main(verbosity=1)
