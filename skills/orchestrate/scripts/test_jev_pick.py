"""Tests for jev_pick.py with mocked HTTP (no network, no key), run with `py -m pytest test_jev_pick.py`.

  body      each rule's `when` is one option, `default` is last, only the task text is the state
  key       a vault `NAME=` prefix is dropped; a missing key fails before any call
  pick      the choice and confidence come back; non-200 and odd shapes raise JevError without the key
  backend   direct uses jev-latest, gateway uses typesafe-ai/jev
  replay    refused and review rows are skipped, task rows are scored against the seat's rule
"""
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import jev_pick as J

TABLE = {"rules": [{"when": "hook  work"}, {"when": "genuinely hard"}, {"when": "hard but ordinary"}],
         "default": []}
KEY = "sekrit-key-123"


def ok(choice="2", conf=0.9, tokens=100):
    return 200, {"answers": {"rule": {"type": "choice", "choice": choice, "confidence": conf}},
                 "usage": {"input_tokens": tokens, "output_tokens": 5}}


class TestBody(unittest.TestCase):

    def test_options_are_the_when_texts_plus_default(self):
        b = J.build_body(TABLE, "fix a race", "jev-latest")
        crit = b["questions"]["rule"]["criteria"]
        self.assertEqual(list(crit), ["1", "2", "3", "default"])
        self.assertEqual(crit["1"], "hook work")
        self.assertEqual(b["state"], "fix a race")
        self.assertEqual(b["questions"]["rule"]["type"], "choice")


    def test_jev_hint_beats_when_text(self):
        table = {"rules": [{"when": "long human text", "jev": "tight hint"}, {"when": "plain"}]}
        crit = J.build_body(table, "t", "jev-latest")["questions"]["rule"]["criteria"]
        self.assertEqual((crit["1"], crit["2"]), ("tight hint", "plain"))


class TestKey(unittest.TestCase):

    def test_prefix_dropped(self):
        self.assertEqual(J.clean_key("TYPESAFE_API_KEY=abc_123"), "abc_123")
        self.assertEqual(J.clean_key("  abc_123 \n"), "abc_123")

    def test_missing_key_fails_before_a_call(self):
        with mock.patch.object(J, "_post") as post:
            with self.assertRaises(J.JevError):
                J.pick(TABLE, "t", env={})
            post.assert_not_called()


class TestPick(unittest.TestCase):

    def test_direct_pick(self):
        with mock.patch.object(J, "_post", return_value=ok("3", 0.77)) as post:
            rule, conf, usage = J.pick(TABLE, "task", env={"TYPESAFE_API_KEY": "TYPESAFE_API_KEY=" + KEY})
        self.assertEqual((rule, conf, usage["input_tokens"]), ("3", 0.77, 100))
        url, key, body, _ = post.call_args[0]
        self.assertEqual(url, "https://api.typesafe.ai/v1/systemone")
        self.assertEqual(key, KEY)
        self.assertEqual(body["model"], "jev-latest")

    def test_gateway_backend(self):
        with mock.patch.object(J, "_post", return_value=ok("default", 1.0)) as post:
            rule, _, _ = J.pick(TABLE, "task", env={"AI_GATEWAY_API_KEY": KEY})
        self.assertEqual(rule, "default")
        url, _, body, _ = post.call_args[0]
        self.assertTrue(url.startswith("https://ai-gateway.vercel.sh/typesafe/"))
        self.assertEqual(body["model"], "typesafe-ai/jev")

    def test_401_raises_without_the_key(self):
        bad = (401, {"detail": {"error_type": "authentication_error", "message": "no"}})
        with mock.patch.object(J, "_post", return_value=bad):
            with self.assertRaises(J.JevError) as cm:
                J.pick(TABLE, "task", env={"TYPESAFE_API_KEY": KEY})
        self.assertIn("401", str(cm.exception))
        self.assertIn("authentication_error", str(cm.exception))
        self.assertNotIn(KEY, str(cm.exception))

    def test_odd_shape_raises(self):
        with mock.patch.object(J, "_post", return_value=(200, {"answers": {}})):
            with self.assertRaises(J.JevError):
                J.pick(TABLE, "task", env={"TYPESAFE_API_KEY": KEY})


class TestDefaultEnv(unittest.TestCase):

    def _file(self, text):
        fh = tempfile.NamedTemporaryFile("w", suffix=".env", delete=False, encoding="utf-8")
        fh.write(text)
        fh.close()
        self.addCleanup(os.unlink, fh.name)
        return fh.name

    def test_key_file_fills_a_missing_key(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            env = J.default_env(self._file("TYPESAFE_API_KEY=" + KEY + "\n"))
        self.assertEqual(J.enabled(env), "direct")
        self.assertEqual(J.clean_key(env["TYPESAFE_API_KEY"]), KEY)

    def test_process_env_wins_over_the_file(self):
        with mock.patch.dict(os.environ, {"TYPESAFE_API_KEY": "fromenv"}, clear=True):
            env = J.default_env(self._file("TYPESAFE_API_KEY=" + KEY + "\n"))
        self.assertEqual(env["TYPESAFE_API_KEY"], "fromenv")

    def test_no_file_means_off(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertIsNone(J.enabled(J.default_env("no-such-file.env")))


class TestEnabled(unittest.TestCase):

    def test_off_without_a_key(self):
        self.assertIsNone(J.enabled({}))
        self.assertIsNone(J.enabled({"TYPESAFE_API_KEY": "  "}))

    def test_direct_key_turns_it_on_even_with_a_name_prefix(self):
        self.assertEqual(J.enabled({"TYPESAFE_API_KEY": "TYPESAFE_API_KEY=" + KEY}), "direct")

    def test_vercel_key_needs_typesafe_base(self):
        self.assertIsNone(J.enabled({"AI_GATEWAY_API_KEY": KEY}))
        self.assertEqual(J.enabled({"AI_GATEWAY_API_KEY": KEY, "TYPESAFE_BASE": "https://x.test/typesafe"}),
                         "gateway")

    def test_gateway_url_comes_from_typesafe_base(self):
        env = {"AI_GATEWAY_API_KEY": KEY, "TYPESAFE_BASE": "https://x.test/typesafe/"}
        with mock.patch.object(J, "_post", return_value=ok("1", 0.9)) as post:
            J.pick(TABLE, "task", "gateway", env)
        self.assertEqual(post.call_args[0][0], "https://x.test/typesafe/v1/systemone")


class TestDecide(unittest.TestCase):
    ENV = {"TYPESAFE_API_KEY": KEY}

    def test_off_makes_no_network_call(self):
        with mock.patch.object(J, "_post") as post:
            res = J.decide(TABLE, "task", env={})
        post.assert_not_called()
        self.assertEqual(res["status"], "off")

    def test_clear_at_or_over_the_gate(self):
        with mock.patch.object(J, "_post", return_value=ok("2", 0.6)):
            res = J.decide(TABLE, "task", env=self.ENV)
        self.assertEqual((res["status"], res["rule"], res["confidence"]), ("clear", "2", 0.6))

    def test_under_the_gate_is_ambiguous_but_keeps_the_lean(self):
        with mock.patch.object(J, "_post", return_value=ok("2", 0.59)):
            res = J.decide(TABLE, "task", env=self.ENV)
        self.assertEqual((res["status"], res["rule"]), ("ambiguous", "2"))

    def test_default_can_be_the_clear_pick(self):
        with mock.patch.object(J, "_post", return_value=ok("default", 0.9)):
            self.assertEqual(J.decide(TABLE, "task", env=self.ENV)["rule"], "default")

    def test_timeout_is_5_seconds_and_a_failure_is_ambiguous(self):
        with mock.patch.object(J, "_post", side_effect=J.JevError("request failed: TimeoutError")) as post:
            res = J.decide(TABLE, "task", env=self.ENV)
        self.assertEqual(post.call_args[0][3], 5)
        self.assertEqual((res["status"], res["confidence"]), ("ambiguous", None))
        self.assertNotIn(KEY, json.dumps(res))

    def test_post_turns_a_socket_timeout_into_jeverror(self):
        with mock.patch.object(J.urllib.request, "urlopen", side_effect=TimeoutError("timed out")):
            with self.assertRaises(J.JevError):
                J._post("https://x.test", KEY, {}, 5)


class TestLogAndPrint(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.log = os.path.join(self.tmp.name, "state", "dispatch-log.jsonl")

    def rows(self):
        with open(self.log, encoding="utf-8") as fh:
            return [json.loads(ln) for ln in fh]

    def test_row_shape_has_picker_jev_and_no_task_text_or_key(self):
        res = {"status": "clear", "rule": "3", "confidence": 0.82, "backend": "direct", "tokens": 90}
        J.log_pick(res, "secret task words", "axi-lead", self.log)
        (row,) = self.rows()
        self.assertEqual((row["picker"], row["decision"], row["sub"]), ("jev", "pick", "pick"))
        self.assertEqual((row["rule"], row["confidence"], row["status"], row["caller"]),
                         ("3", 0.82, "clear", "axi-lead"))
        self.assertEqual(row["task_chars"], len("secret task words"))
        self.assertNotIn("secret task words", json.dumps(row))

    def test_off_writes_no_row(self):
        J.log_pick({"status": "off", "rule": "", "confidence": None, "note": "x"}, "t", "", self.log)
        self.assertFalse(os.path.exists(self.log))

    def test_cmd_pick_prints_the_three_shapes(self):
        rules = os.path.join(self.tmp.name, "rules.json")
        with open(rules, "w", encoding="utf-8") as fh:
            json.dump(TABLE, fh)
        out = []
        env = {"ORCH_DISPATCH_LOG": self.log}
        with mock.patch.dict(os.environ, env), mock.patch("builtins.print", side_effect=out.append):
            with mock.patch.object(J, "_post", return_value=ok("3", 0.9)):
                J.cmd_pick("t", rules, {"TYPESAFE_API_KEY": KEY})
            with mock.patch.object(J, "_post", return_value=ok("3", 0.4)):
                J.cmd_pick("t", rules, {"TYPESAFE_API_KEY": KEY})
            J.cmd_pick("t", rules, {})
        self.assertEqual(out[0], "rule=3 confidence=0.90")
        self.assertTrue(out[1].startswith("ambiguous (Jev leaned 3 at 0.40)"))
        self.assertTrue(out[2].startswith("jev off:"))
        self.assertEqual([r["status"] for r in self.rows()], ["clear", "ambiguous"])


class TestReplay(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        rows = [
            {"ts": "t1", "decision": "deny", "sub": "task", "label": "a"},
            {"ts": "t2", "decision": "allow", "sub": "review", "label": "b", "rule": "6"},
            {"ts": "t3", "decision": "allow", "sub": "task", "label": "c", "rule": "2"},
            {"ts": "t4", "decision": "allow", "sub": "task", "label": "d", "rule": "1"},
            {"ts": "t5", "decision": "allow", "sub": "task", "label": "nohand", "rule": "1"},
        ]
        self.log = os.path.join(self.tmp.name, "log.jsonl")
        with open(self.log, "w", encoding="utf-8") as fh:
            fh.write("\n".join(json.dumps(r) for r in rows) + "\n")
        for label, goal in (("c", "hard thing"), ("d", "CHROME RULE: skip me\n\nsmall thing")):
            with open(os.path.join(self.tmp.name, f"task-{label}-2026-10-08-0001.md"), "w",
                      encoding="utf-8") as fh:
                fh.write(f"# Task: {label}\n\n## Goal\n\n{goal}\n\n## Why\n\nx\n")

    def test_goal_strips_chrome_rule(self):
        self.assertEqual(J.goal_of("d", self.tmp.name), "small thing")
        self.assertIsNone(J.goal_of("nohand", self.tmp.name))

    def test_scores_only_task_rows_with_text(self):
        picks = {"hard thing": ("2", 0.9, {"input_tokens": 50}),
                 "small thing": ("3", 0.6, {"input_tokens": 70})}
        with mock.patch.object(J, "pick", side_effect=lambda t, text, *a, **k: picks[text]):
            res = J.replay(TABLE, self.log, 20, self.tmp.name)
        self.assertEqual([r["status"] for r in res][2:4], ["match", "differ"])
        self.assertTrue(res[0]["status"].startswith("skipped"))
        self.assertTrue(res[1]["status"].startswith("skipped"))
        self.assertTrue(res[4]["status"].startswith("skipped"))
        out = J.format_replay(res)
        self.assertIn("scored 2 of 5 rows, matched 1, input tokens 120", out)

    def test_short_keeps_the_first_goal_line_only(self):
        path = os.path.join(self.tmp.name, "task-e-2026-10-08-0001.md")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("# T\n\n## Goal\n\nCHROME RULE: x\nfirst line\nsecond line\n\n## Why\n\nx\n")
        self.assertEqual(J.goal_of("e", self.tmp.name), "first line second line")
        self.assertEqual(J.goal_of("e", self.tmp.name, short=True), "first line")

    def test_skip_and_pick_rows_are_left_out(self):
        with open(self.log, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"ts": "t6", "decision": "pick", "picker": "jev", "sub": "pick"}) + "\n")
        with mock.patch.object(J, "pick", return_value=("2", 1.0, {})):
            res = J.replay(TABLE, self.log, 20, self.tmp.name, skip=("c",))
        self.assertNotIn("t6", [r["ts"] for r in res])
        self.assertTrue(res[2]["status"].startswith("skipped: label on the skip list"))

    def test_n_takes_the_last_rows(self):
        with mock.patch.object(J, "pick", return_value=("2", 1.0, {})):
            res = J.replay(TABLE, self.log, 2, self.tmp.name)
        self.assertEqual([r["ts"] for r in res], ["t4", "t5"])


if __name__ == "__main__":
    unittest.main()
