"""Tests for dispatch_rule.py (the --rule gate), run with `py test_dispatch_rule.py`.

  parse     a command is read like orch.py reads it: quotes keep a prompt whole, `$ORCH` counts
  decide    a combo inside the rule's USE list (else-fallbacks included) is allowed, any other is
            refused with the exact allowed combos; no --rule prints the rule list
  override  `--rule override` needs --reason and is logged as an override
  off       ORCH_DISPATCH_HOOK=off, in the env or as a command prefix, lets everything through
  log       every gate decision is one JSON row; a hook-side allow is not logged (the CLI does it)
  cli       orch.py spawn/task/review refuse without --rule and pass with a matching one
"""
import contextlib, io, json, os, sys, tempfile, unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import dispatch_rule as DR

TABLE = {
    "rules": [
        {"when": "hook-guarded work", "use": [{"harness": "claude", "model": "sonnet", "effort": "high"}]},
        {"when": "genuinely hard", "use": [{"harness": "claude", "model": "fable", "effort": "xhigh"}]},
        {"when": "hard but ordinary", "use": [
            {"harness": "claude", "model": "sonnet", "effort": "high"},
            {"harness": "pi", "model": "gpt-6-astra", "effort": "xhigh"}]},
        {"when": "review", "use": [{"harness": "pi", "model": "gpt-5.6-sol", "effort": "medium"},
                                   {"harness": "claude", "model": "sonnet", "effort": "high"}]},
    ],
    "default": [{"harness": "claude", "model": "sonnet", "effort": "high"}],
}
OK_ENV = {}


def run(command, env=OK_ENV):
    """decide() for the first gated call in `command`."""
    sub, a = DR.find_calls(command)[0]
    return DR.decide(sub, a, TABLE, env, command)


class TestParse(unittest.TestCase):

    def test_finds_each_spelling_of_the_cli(self):
        for cli in ('py "$ORCH"', 'py orch.py', r'py "C:\Users\YOURNAME\.claude\skills\orchestrate\scripts\orch.py"',
                    "py ${ORCH}", "py %ORCH%"):
            calls = DR.find_calls(f'{cli} task lbl --goal g')
            self.assertEqual([c[0] for c in calls], ["task"], cli)

    def test_not_a_gated_verb(self):
        for cmd in ('py "$ORCH" ls', 'py "$ORCH" dispatch', 'py "$ORCH" prompt p "orch.py task x"',
                    'echo orch.py task x'):
            self.assertEqual(DR.find_calls(cmd), [], cmd)

    def test_quoted_prompt_text_cannot_fake_a_flag(self):
        # `--rule 3` inside the goal text is one quoted token, not a flag.
        sub, a = DR.find_calls('py "$ORCH" task t --goal "use --rule 3 and --model opus" --rule 1')[0]
        self.assertEqual(DR.opt(a, "--rule"), "1")
        self.assertIsNone(DR.opt(a, "--model"))

    def test_second_call_after_a_separator(self):
        calls = DR.find_calls('py "$ORCH" ls; py "$ORCH" spawn a "p" --rule 1')
        self.assertEqual([c[0] for c in calls], ["spawn"])

    def test_trailing_flag_with_no_value_is_not_given(self):
        sub, a = DR.find_calls('py "$ORCH" spawn a p --rule')[0]
        self.assertIsNone(DR.opt(a, "--rule"))

    def test_bash_line_continuation_keeps_the_flags(self):
        sub, a = DR.find_calls('py "$ORCH" spawn a p \\\n  --rule 1 --model sonnet')[0]
        self.assertEqual((DR.opt(a, "--rule"), DR.opt(a, "--model")), ("1", "sonnet"))

    def test_powershell_line_continuation_keeps_the_flags(self):
        sub, a = DR.find_calls('py "$ORCH" spawn a p `\r\n  --rule 1')[0]
        self.assertEqual(DR.opt(a, "--rule"), "1")


class TestDecide(unittest.TestCase):

    def base(self, rule, h="claude", m="sonnet", e="high", sub="task"):
        return (f'py "$ORCH" {sub} lbl --goal g --done d --rule {rule} --harness {h} --model {m} '
                f'--effort {e} --cwd C:/x')

    def test_matching_combo_is_allowed(self):
        self.assertEqual(run(self.base(1))[0], "allow")

    def test_hard_rule_denies_sonnet_and_names_fable(self):
        d, msg, rec = run(self.base(2))
        self.assertEqual(d, "deny")
        self.assertIn("rule 2 does not allow --harness claude --model sonnet --effort high", msg)
        self.assertIn("--harness claude --model fable --effort xhigh", msg)

    def test_else_fallback_entries_are_allowed(self):
        self.assertEqual(run(self.base(3, "pi", "gpt-6-astra", "xhigh"))[0], "allow")
        self.assertEqual(run(self.base(4, "claude", "sonnet", "high", "review"))[0], "allow")

    def test_wrong_effort_is_denied(self):
        d, msg, _ = run(self.base(1, e="medium"))
        self.assertEqual(d, "deny")
        self.assertIn("--effort high", msg)

    def test_missing_effort_is_denied(self):
        d, msg, _ = run('py "$ORCH" spawn a p --rule 1 --model sonnet --cwd C:/x')
        self.assertEqual(d, "deny")
        self.assertIn("(no --effort)", msg)

    def test_harness_defaults_to_claude_and_kind_is_an_alias(self):
        self.assertEqual(run('py "$ORCH" spawn a p --rule 1 --model sonnet --effort high')[0], "allow")
        self.assertEqual(run('py "$ORCH" spawn a p --rule 3 --kind pi --model gpt-6-astra --effort xhigh')[0],
                         "allow")

    def test_model_wrong_harness_is_denied(self):
        # sonnet on pi is the extra-usage trap crew-dispatch.json warns about
        self.assertEqual(run(self.base(1, "pi", "sonnet", "high"))[0], "deny")

    def test_full_model_id_matches_its_tier(self):
        self.assertEqual(run(self.base(1, "claude", "claude-sonnet-5-5", "high"))[0], "allow")

    def test_review_defaults_model_to_sonnet(self):
        self.assertEqual(run('py "$ORCH" review lead --diff A..B --rule 4 --effort high')[0], "allow")

    def test_missing_rule_prints_the_rule_list(self):
        d, msg, rec = run('py "$ORCH" spawn a p --model sonnet --effort high')
        self.assertEqual(d, "deny")
        self.assertIn("needs --rule", msg)
        self.assertIn("1. hook-guarded work", msg)
        self.assertIn("allows: --harness claude --model fable --effort xhigh", msg)
        self.assertIn("override.", msg)

    def test_unknown_rule_is_denied(self):
        for r in ("9", "0", "x", "-1"):
            self.assertEqual(run(self.base(r))[0], "deny", r)

    def test_default_rule_uses_the_default_list(self):
        self.assertEqual(run(self.base("default"))[0], "allow")
        self.assertEqual(run(self.base("default", "claude", "fable", "xhigh"))[0], "deny")


class TestOverrideAndOff(unittest.TestCase):

    def test_override_needs_a_reason(self):
        d, msg, _ = run('py "$ORCH" spawn a p --rule override --model opus --effort high')
        self.assertEqual(d, "deny")
        self.assertIn("--reason", msg)

    def test_override_with_a_reason_passes_any_combo(self):
        d, _, rec = run('py "$ORCH" spawn a p --rule override --reason "Ryan asked for opus" '
                        '--model opus --effort high')
        self.assertEqual(d, "override")
        self.assertEqual(rec["reason"], "Ryan asked for opus")

    def test_kill_switch_env(self):
        d, _, _ = run('py "$ORCH" spawn a p --model opus', env={"ORCH_DISPATCH_HOOK": "off"})
        self.assertEqual(d, "off")
        self.assertEqual(run('py "$ORCH" spawn a p --model opus', env={"ORCH_DISPATCH_HOOK": "ON"})[0], "deny")

    def test_kill_switch_command_prefix(self):
        self.assertEqual(run('ORCH_DISPATCH_HOOK=off py "$ORCH" spawn a p --model opus')[0], "off")

    def test_kill_switch_in_prompt_text_is_not_a_prefix_but_still_matches_word(self):
        # The token is a literal word match on purpose (same shape as G-4's one-shot token): the
        # seat must type it. A goal that merely quotes it would also match, which is accepted.
        self.assertTrue(DR.is_off('x ORCH_DISPATCH_HOOK=off y', {}))


class TestLog(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.log = os.path.join(self.tmp, "dispatch-log.jsonl")
        self.table = os.path.join(self.tmp, "table.json")
        with open(self.table, "w", encoding="utf-8") as fh:
            json.dump(TABLE, fh)
        p = mock.patch.dict(os.environ, {"ORCH_DISPATCH_LOG": self.log})
        p.start()
        self.addCleanup(p.stop)

    def rows(self):
        if not os.path.exists(self.log):
            return []
        with open(self.log, encoding="utf-8") as fh:
            return [json.loads(x) for x in fh if x.strip()]

    def gate(self, command, source="cli", env=None):
        sub, a = DR.find_calls(command)[0]
        return DR.gate(sub, a, source=source, caller="seat", env=env or {}, command=command,
                       table_path=self.table)

    def test_cli_allow_writes_rule_model_label(self):
        self.assertIsNone(self.gate('py "$ORCH" task my-lbl --rule 3 --model sonnet --effort high'))
        (row,) = self.rows()
        self.assertEqual((row["decision"], row["rule"], row["model"], row["label"], row["source"]),
                         ("allow", "3", "sonnet", "my-lbl", "cli"))
        self.assertEqual(row["caller"], "seat")

    def test_hook_allow_is_not_logged_but_hook_deny_is(self):
        self.assertIsNone(self.gate('py "$ORCH" task a --rule 3 --model sonnet --effort high', "hook"))
        self.assertEqual(self.rows(), [])
        self.assertTrue(self.gate('py "$ORCH" task a --rule 2 --model sonnet --effort high', "hook"))
        (row,) = self.rows()
        self.assertEqual((row["decision"], row["why"]), ("deny", "combo not in rule 2"))

    def test_override_and_off_are_logged(self):
        self.gate('py "$ORCH" task a --rule override --reason "because" --model opus')
        self.gate('py "$ORCH" task b --model opus', env={"ORCH_DISPATCH_HOOK": "off"})
        self.assertEqual([r["decision"] for r in self.rows()], ["override", "off"])

    def test_a_missing_table_lets_the_call_through(self):
        sub, a = DR.find_calls('py "$ORCH" task a --model opus')[0]
        self.assertIsNone(DR.gate(sub, a, table_path=os.path.join(self.tmp, "nope.json"), env={}))

    def test_hook_entry_point_prints_the_refusal_and_exits_3(self):
        cmd = 'py "$ORCH" task a --rule 2 --model sonnet --effort high --harness claude'
        buf = io.StringIO()
        with mock.patch.object(DR, "DISPATCH_FILE", self.table), contextlib.redirect_stdout(buf), \
             mock.patch.dict(os.environ, {"ORCH_DISPATCH_HOOK": ""}):   # test_orch sets it to off
            code = DR.main(["x", "check", "--command", cmd, "--caller", "seat"])
        self.assertEqual(code, 3)
        self.assertIn("rule 2 does not allow", buf.getvalue())
        with mock.patch.object(DR, "DISPATCH_FILE", self.table), contextlib.redirect_stdout(io.StringIO()) as ok:
            code = DR.main(["x", "check", "--command", 'py "$ORCH" ls'])
        self.assertEqual((code, ok.getvalue()), (0, ""))


CLAUDE = {"harness": "claude", "model": "sonnet", "effort": "high"}
ASTRA = {"harness": "pi", "model": "gpt-6-astra", "effort": "xhigh"}
SOL = {"harness": "pi", "model": "gpt-5.6-sol", "effort": "xhigh"}


def pick(profiles, head, floor=None):
    return DR.resolve_profile(profiles, head, floor)


class TestRunwayFloor(unittest.TestCase):

    def test_no_floor_is_the_old_most_headroom_rule(self):
        self.assertEqual(pick([CLAUDE, ASTRA], {"claude": 41, "pi": 78})["harness"], "pi")
        self.assertEqual(pick([CLAUDE, ASTRA], {"claude": 80, "pi": 78})["harness"], "claude")
        self.assertEqual(pick([CLAUDE, ASTRA], {"claude": 50, "pi": 50})["harness"], "claude")
        self.assertEqual(pick([CLAUDE, ASTRA], {"claude": None, "pi": 3})["harness"], "pi")

    def test_floor_skips_a_harness_under_it_even_with_top_priority(self):
        hot = dict(ASTRA, spendPriority=9)
        self.assertEqual(pick([CLAUDE, hot], {"claude": 30, "pi": 4}, floor=10)["harness"], "claude")
        self.assertEqual(pick([CLAUDE, hot], {"claude": 30, "pi": 40}, floor=10)["harness"], "pi")

    def test_chatgpt_window_at_zero_is_skipped(self):
        # the 2026-10-08 night: pi at 0%, every pick had to fall back by hand
        self.assertEqual(pick([ASTRA, CLAUDE, SOL], {"claude": 5, "pi": 0}, floor=3)["harness"], "claude")

    def test_floor_is_inclusive_of_the_boundary(self):
        self.assertEqual(pick([ASTRA, CLAUDE], {"claude": 5, "pi": 10}, floor=10)["harness"], "pi")

    def test_all_under_the_floor_lifts_it_and_most_headroom_wins(self):
        hot = dict(ASTRA, spendPriority=9)
        self.assertEqual(pick([hot, CLAUDE], {"claude": 8, "pi": 2}, floor=10)["harness"], "claude")

    def test_unreadable_harness_is_not_under_the_floor(self):
        self.assertEqual(pick([ASTRA, CLAUDE], {"claude": None, "pi": 2}, floor=10)["harness"], "claude")

    def test_floor_reads_the_top_level_field(self):
        self.assertEqual(DR.runway_floor({"runwayFloor": 15}), 15)
        self.assertIsNone(DR.runway_floor({}))
        self.assertIsNone(DR.runway_floor({"runwayFloor": "x"}))


class TestSpendPriority(unittest.TestCase):

    def test_higher_spend_priority_beats_more_headroom(self):
        eager = dict(CLAUDE, spendPriority=2)
        self.assertEqual(pick([ASTRA, eager], {"claude": 20, "pi": 90})["harness"], "claude")

    def test_equal_priority_falls_back_to_headroom_then_order(self):
        a, b = dict(CLAUDE, spendPriority=1), dict(ASTRA, spendPriority=1)
        self.assertEqual(pick([a, b], {"claude": 20, "pi": 90})["harness"], "pi")
        self.assertEqual(pick([a, b], {"claude": 50, "pi": 50})["harness"], "claude")

    def test_negative_priority_ranks_below_the_default_zero(self):
        spare = dict(CLAUDE, spendPriority=-1)
        self.assertEqual(pick([spare, ASTRA], {"claude": 90, "pi": 10})["harness"], "pi")

    def test_missing_or_non_numeric_priority_counts_as_zero(self):
        self.assertEqual(pick([dict(CLAUDE, spendPriority="high"), ASTRA], {"claude": 20, "pi": 90})["harness"], "pi")
        self.assertEqual(pick([dict(CLAUDE, spendPriority=True), ASTRA], {"claude": 20, "pi": 90})["harness"], "pi")

    def test_combo_gate_ignores_the_new_fields(self):
        table = {"rules": [{"when": "w", "use": [dict(CLAUDE, spendPriority=5)]}], "runwayFloor": 10}
        sub, a = DR.find_calls('py "$ORCH" spawn a p --rule 1 --model sonnet --effort high')[0]
        self.assertEqual(DR.decide(sub, a, table, {}, "")[0], "allow")


class TestSchema(unittest.TestCase):

    def test_shipped_table_passes(self):
        self.assertEqual(DR.schema_errors(DR.load_table()), [])

    def test_bad_floor_and_bad_priority_are_named(self):
        bad = {"runwayFloor": 140, "rules": [{"when": "w", "use": [dict(CLAUDE, spendPriority="x")]}],
               "default": [dict(CLAUDE, spendPriority=True)]}
        errs = DR.schema_errors(bad)
        self.assertEqual(len(errs), 3, errs)
        self.assertIn("runwayFloor", errs[0])
        self.assertIn("rule 1", errs[1])
        self.assertIn("default", errs[2])
        self.assertEqual(DR.schema_errors({"runwayFloor": True}), ["runwayFloor must be a number from 0 to 100"])

    def test_valid_optional_fields_pass(self):
        ok = {"runwayFloor": 0, "rules": [{"when": "w", "use": CLAUDE | {"spendPriority": 2.5}}], "default": CLAUDE}
        self.assertEqual(DR.schema_errors(ok), [])


class TestExplain(unittest.TestCase):

    TBL = {"runwayFloor": 10,
           "rules": [{"when": "hard but ordinary", "use": [CLAUDE, dict(ASTRA, spendPriority=3), SOL]}],
           "default": [CLAUDE]}

    def text(self, head, rule="1", table=None):
        return DR.explain(rule, table or self.TBL, head)

    def test_every_entry_is_accounted_for_with_a_reason(self):
        out = self.text({"claude": 40, "pi": 80})
        self.assertIn("rule 1 - hard but ordinary", out)
        self.assertIn("runway floor: 10%   weekly headroom: claude 40%, pi 80%", out)
        self.assertEqual(out.count("WON"), 1)
        self.assertEqual(out.count("LOST"), 2)
        self.assertIn("WON   --harness pi --model gpt-6-astra --effort xhigh   left 80%  spendPriority 3", out)
        self.assertIn("highest spendPriority (3) among entries over the floor", out)
        self.assertIn("lower spendPriority (0 vs 3)", out)
        self.assertTrue(out.rstrip().endswith("pick: --harness pi --model gpt-6-astra --effort xhigh"))

    def test_floor_loss_is_named(self):
        out = self.text({"claude": 40, "pi": 0})
        self.assertIn("under the runway floor (0% < 10%) while another entry has room", out)
        self.assertIn("pick: --harness claude --model sonnet --effort high", out)

    def test_all_under_floor_says_the_floor_lifts(self):
        out = self.text({"claude": 4, "pi": 2})
        self.assertIn("every entry is under the 10% floor, so the floor lifts", out)
        self.assertIn("pick: --harness claude", out)

    def test_tie_goes_to_first_written(self):
        t = {"rules": [{"when": "w", "use": [CLAUDE, ASTRA]}]}
        out = DR.explain("1", t, {"claude": 50, "pi": 50})
        self.assertIn("ties on spendPriority and headroom: first written wins", out)
        self.assertIn("ties on spendPriority and headroom, written later", out)
        self.assertIn("runway floor: none", out)

    def test_unreadable_harness_prints_no_reading(self):
        out = self.text({"claude": None, "pi": 20}, table={"rules": self.TBL["rules"]})
        self.assertIn("left no reading", out)
        self.assertIn("claude -", out)

    def test_default_and_unknown_rule(self):
        self.assertIn("default - nothing else fits", self.text({"claude": 9}, "default"))
        with self.assertRaises(ValueError):
            self.text({}, "9")

    def test_explain_names_the_same_pick_as_resolve(self):
        for head in ({"claude": 40, "pi": 80}, {"claude": 40, "pi": 0}, {"claude": 1, "pi": 2}, {}):
            use = self.TBL["rules"][0]["use"]
            want = DR.flags_of(pick(use, head, 10))
            self.assertTrue(self.text(head).rstrip().endswith("pick: " + want), head)


class TestExplainCli(unittest.TestCase):

    def run_cli(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = DR.main(["dispatch_rule.py", *args])
        return code, out.getvalue(), err.getvalue()

    def test_explain_with_given_headroom_prints_and_exits_0(self):
        code, out, _ = self.run_cli("--explain", "2", "--headroom", "claude=67")
        self.assertEqual(code, 0)
        self.assertIn("rule 2 - Hard but ordinary", out)
        self.assertIn("pick: --harness claude --model sonnet --effort high", out)

    def test_explain_unknown_rule_lists_rules_and_exits_2(self):
        code, out, err = self.run_cli("--explain", "99", "--headroom", "claude=1")
        self.assertEqual((code, out), (2, ""))
        self.assertIn("crew-dispatch.json rules", err)

    def test_explain_without_a_rule_is_usage(self):
        code, _, err = self.run_cli("--explain")
        self.assertEqual(code, 2)
        self.assertIn("usage", err)

    def test_check_still_works_beside_explain(self):
        code, out, _ = self.run_cli("check", "--command", 'py "$ORCH" ls')
        self.assertEqual((code, out), (0, ""))

    def test_parse_headroom(self):
        self.assertEqual(DR._parse_headroom("claude=40, pi=0%"), {"claude": 40.0, "pi": 0.0})
        self.assertEqual(DR._parse_headroom("pi=?"), {"pi": None})


class TestOrchCli(unittest.TestCase):
    """The same rule, reached through orch.py main() the way a bare CLI call reaches it."""

    def setUp(self):
        import orch
        self.orch = orch
        self.tmp = tempfile.mkdtemp()
        self.log = os.path.join(self.tmp, "log.jsonl")
        env = mock.patch.dict(os.environ, {"ORCH_DISPATCH_LOG": self.log})
        env.start()
        self.addCleanup(env.stop)

    def call(self, *argv, off=False):
        err = io.StringIO()
        with mock.patch.dict(os.environ, {}), \
             mock.patch.object(self.orch, "cmd_spawn", return_value=("w3:pX", "sid")) as spawn, \
             mock.patch.object(self.orch, "cmd_review") as review, \
             mock.patch.object(self.orch, "_require_worktree", side_effect=lambda a, c: c), \
             mock.patch.object(sys, "argv", ["orch.py", *argv]), \
             contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
            if off:
                os.environ["ORCH_DISPATCH_HOOK"] = "off"
            else:
                os.environ.pop("ORCH_DISPATCH_HOOK", None)
            try:
                self.orch.main()
                code = 0
            except SystemExit as e:
                code = e.code
        return code, err.getvalue(), spawn, review

    def test_spawn_without_rule_is_refused_with_the_rule_list(self):
        code, err, spawn, _ = self.call("spawn", "x", "hi", "--model", "sonnet", "--cwd", "C:/w",
                                        "--effort", "high")
        self.assertEqual(code, 2)
        self.assertIn("needs --rule", err)
        spawn.assert_not_called()

    def test_spawn_with_a_matching_rule_runs_and_logs(self):
        d = DR.load_table()
        n = next(i for i, r in enumerate(d["rules"], 1)
                 if {"harness": "claude", "model": "sonnet", "effort": "high"} in DR._profiles(r["use"]))
        code, err, spawn, _ = self.call("spawn", "x", "hi", "--model", "sonnet", "--cwd", "C:/w",
                                        "--effort", "high", "--rule", str(n))
        self.assertEqual(code, 0, err)
        spawn.assert_called_once()
        with open(self.log, encoding="utf-8") as fh:
            row = json.loads(fh.readline())
        self.assertEqual((row["decision"], row["label"], row["rule"]), ("allow", "x", str(n)))

    def test_spawn_with_the_wrong_combo_for_the_rule_is_refused(self):
        d = DR.load_table()
        n = next(i for i, r in enumerate(d["rules"], 1)
                 if _only(r["use"], "opus"))
        code, err, spawn, _ = self.call("spawn", "x", "hi", "--model", "sonnet", "--cwd", "C:/w",
                                        "--effort", "high", "--rule", str(n))
        self.assertEqual(code, 2)
        self.assertIn("--model opus --effort xhigh", err)
        spawn.assert_not_called()

    def test_review_without_rule_is_refused_and_kill_switch_passes(self):
        code, err, _, review = self.call("review", "lead", "--diff", "A..B")
        self.assertEqual(code, 2)
        review.assert_not_called()
        code, err, _, review = self.call("review", "lead", "--diff", "A..B", off=True)
        self.assertEqual(code, 0, err)
        review.assert_called_once()


def _only(use, model):
    p = DR._profiles(use)
    return len(p) == 1 and p[0].get("model") == model


if __name__ == "__main__":
    unittest.main()
