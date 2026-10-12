"""Tests for seat_pair.py and its hooks: the decision log, the feeder, ask/answer, the watcher
guard, hard-question routing and answer logging."""
import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import orchlib as L
import seat_pair as SP

HOOKS = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(HERE))), "hooks")
PAIR = {"seat": {"label": "seat-haiku", "pane": "w1:p1", "sid": "s1"},
        "watcher": {"label": "seat-watcher", "pane": "w1:p2", "sid": "s2"}}


sys.path.insert(0, HOOKS)
import _hook_stdin  # noqa: E402


def load_hook(name):
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), os.path.join(HOOKS, name + ".py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def as_session(sid, pane=""):
    return mock.patch.dict(os.environ, {"CLAUDE_CODE_SESSION_ID": sid, "HERDR_PANE_ID": pane})


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.p = mock.patch.object(L, "BOARD_DIR", self.tmp.name)
        self.p.start()
        self.c = mock.patch.object(L, "caller_name", lambda: "seat-haiku")
        self.c.start()
        self.started_feeds = []
        self.f = mock.patch.object(SP, "start_feed", lambda quiet=True: self.started_feeds.append(1) or 4242)
        self.f.start()
        os.makedirs(SP.pair_dir())

    def tearDown(self):
        self.f.stop()
        self.c.stop()
        self.p.stop()
        self.tmp.cleanup()

    def pair_up(self):
        SP._write_json(SP._p("pair.json"), PAIR)

    def ledger(self, **row):
        with open(SP._ledger_path(), "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"v": 1, "ts": 1, "event": "task.status", **row}) + "\n")

    def decisions(self):
        try:
            with open(SP._p("decisions.jsonl"), encoding="utf-8") as fh:
                return [json.loads(x) for x in fh]
        except OSError:
            return []


class FeedTest(Base):
    def test_decide_reaches_the_watcher_and_its_own_echoes_do_not(self):
        cur = SP.start_cursor()
        SP.decide("spawn", "app-fix on sonnet", "multi-file fix", "5")
        SP.decide("fix-applied", "moved app-fix to haiku")
        SP.decide("answer-from-opus", "Ryan: x -> seat: y")
        items, cur = SP.collect(cur, PAIR)
        self.assertEqual(items, ["DECISION spawn | app-fix on sonnet | why: multi-file fix | rule 5"])
        self.assertEqual(SP.collect(cur, PAIR)[0], [])

    def test_ledger_done_rows_skip_the_pair_and_ryans_chats(self):
        cur = SP.start_cursor()
        self.ledger(task="app-fix", state="done", text="tests pass, commit abc")
        self.ledger(task="app-fix", state="question", text="which one?")
        self.ledger(task="seat-haiku", state="done", text="Ready.")
        self.ledger(task="seat-watcher", state="done", text="ok 3")
        self.ledger(task="vault-65", state="done", text="chat answer")
        items, _ = SP.collect(cur, PAIR)
        self.assertEqual(items, ["DONE app-fix | tests pass, commit abc"])

    def test_start_cursor_skips_history(self):
        self.ledger(task="old", state="done", text="old work")
        SP.decide("spawn", "old decision")
        self.assertEqual(SP.collect(SP.start_cursor(), PAIR)[0], [])

    def test_half_written_line_waits_for_its_newline(self):
        cur = SP.start_cursor()
        with open(SP._ledger_path(), "a", encoding="utf-8") as fh:
            fh.write('{"event": "task.status", "state": "done", "task": "x", "text": "half')
        items, cur = SP.collect(cur, PAIR)
        self.assertEqual(items, [])
        with open(SP._ledger_path(), "a", encoding="utf-8") as fh:
            fh.write(' done"}\n')
        self.assertEqual(SP.collect(cur, PAIR)[0], ["DONE x | half done"])

    def test_a_shrunk_file_is_read_from_the_start(self):
        self.ledger(task="a", state="done", text="one")
        cur = SP.start_cursor()
        with open(SP._ledger_path(), "w", encoding="utf-8") as fh:
            fh.write(json.dumps({"event": "task.status", "state": "done", "task": "b", "text": "two"}) + "\n")
        self.assertEqual(SP.collect(cur, PAIR)[0], ["DONE b | two"])

    def test_batches_drain_every_item_with_none_dropped(self):
        items = [f"DONE p{i} | " + "x" * 300 for i in range(80)]
        seen, pending = [], list(items)
        while pending:
            text, n = SP.batch_text(pending)
            self.assertGreater(n, 0)
            self.assertLessEqual(len(text), SP.BATCH_CHARS + 100)
            seen += pending[:n]
            if n < len(pending):
                self.assertIn(f"{len(pending) - n} more next", text)
            pending = pending[n:]
        self.assertEqual(seen, items)

    def test_a_repeat_done_is_dropped_unless_it_brings_a_new_commit_or_verdict(self):
        # 2026-10-11: after the seat switch about 15 panes resent the same report.
        cur = SP.start_cursor()
        self.ledger(task="app-fix", state="done", text="tests pass, commit abc1234")
        self.ledger(task="app-fix", state="done", text="I resent my report to the new seat: commit abc1234")
        self.ledger(task="app-fix", state="done", text="follow-up shipped as def5678")
        self.ledger(task="rv", state="done", text="VERDICT: FIX-FIRST")
        self.ledger(task="rv", state="done", text="resending: VERDICT: FIX-FIRST")
        self.ledger(task="rv", state="done", text="VERDICT: SHIP")
        items, cur = SP.collect(cur, PAIR)
        self.assertEqual([i.split(" | ")[1] for i in items],
                         ["tests pass, commit abc1234", "follow-up shipped as def5678",
                          "VERDICT: FIX-FIRST", "VERDICT: SHIP"])
        self.assertIn("app-fix", cur["done_seen"])      # kept in cursor.json across feeder restarts

    def test_a_pure_click_hotkey_answer_is_not_sent_for_review(self):
        self.assertIsNone(SP.decision_item({"kind": "answer", "what":
                          "Ryan: [hotkey id=k1] make the title blue | screenshot: C:/s.png -> seat: Clicking: Make the title blue"}))
        self.assertIsNotNone(SP.decision_item({"kind": "answer", "what":
                             "Ryan: [hotkey id=k2] what is this -> seat: Clicking: Save. Also, this page is the draft."}))
        self.assertIsNotNone(SP.decision_item({"kind": "answer", "what": "Ryan: whats running -> seat: Clicking: x"}))

    def test_the_batch_header_is_short(self):
        text, n = SP.batch_text(["DONE a | x", "DONE b | y"])
        self.assertEqual(text.splitlines()[0], "[review batch] 2 item(s).")

    def test_a_batch_waits_for_quiet_but_a_steady_trickle_still_goes(self):
        now = 10_000.0
        self.assertFalse(SP.batch_ready(now, now - 30, now - 60))          # new item 30 s ago
        self.assertTrue(SP.batch_ready(now, now - SP.QUIET, now - SP.QUIET))
        self.assertTrue(SP.batch_ready(now, now - 5, now - SP.MAX_AGE))     # trickle: age cap

    def test_answers_carry_more_text_than_other_decisions(self):
        long = "y" * 1000
        self.assertIn(long, SP.decision_item({"kind": "answer", "what": long}))
        self.assertNotIn(long, SP.decision_item({"kind": "spawn", "what": long}))


class ResetTest(Base):
    def run_feed_once(self, ctx, asks=()):
        """One feeder pass with a ready batch; time.sleep ends the loop."""
        self.pair_up()
        SP._write_json(SP._p("cursor.json"), {**SP.start_cursor(), "pending": ["DONE a | x"]})
        sent = []
        with mock.patch.object(SP, "pane_state", lambda lb: (True, True)), \
                mock.patch.object(SP, "watcher_ctx", lambda: ctx), \
                mock.patch.object(SP, "open_asks", lambda: list(asks)), \
                mock.patch.object(SP, "MAX_AGE", 0), \
                mock.patch.object(SP, "_prompt", lambda n, t: (sent.append(t), (True, "ok"))[1]), \
                mock.patch.object(SP.time, "sleep", side_effect=StopIteration), \
                mock.patch("sys.stdout", new=io.StringIO()):
            with self.assertRaises(StopIteration):
                SP.feed()
        return sent, SP._read_json(SP._p("cursor.json"))

    def test_a_large_watcher_gets_a_handoff_instead_of_the_batch(self):
        sent, cur = self.run_feed_once(SP.RESET_K + 10)
        self.assertTrue(sent[0].startswith("[handoff]"))
        self.assertIn("watcher-notes.md", sent[0])
        self.assertEqual(cur["handoff"]["phase"], "notes")
        self.assertEqual(cur["pending"], ["DONE a | x"])        # kept for the fresh watcher

    def test_no_handoff_while_a_hard_ask_is_open_or_context_is_small(self):
        sent, cur = self.run_feed_once(SP.RESET_K + 10, asks=["a1"])
        self.assertTrue(sent[0].startswith("[review batch]"))
        sent, cur = self.run_feed_once(SP.RESET_K - 10)
        self.assertTrue(sent[0].startswith("[review batch]"))
        self.assertNotIn("handoff", cur)

    def test_handoff_restarts_once_notes_are_written_and_finishes_on_the_new_sid(self):
        self.pair_up()
        started = []
        cur = {"handoff": {"phase": "notes", "ts": time.time() - 1}}
        with mock.patch.object(SP, "_detached", lambda argv, log: started.append(argv) or 1), \
                mock.patch.object(SP, "open_asks", lambda: []), mock.patch("sys.stdout", new=io.StringIO()):
            self.assertTrue(SP.handoff_step(cur, "seat-watcher", time.time()))
            self.assertEqual(started, [])                         # no notes yet: wait
            with open(SP._p(SP.NOTES), "w", encoding="utf-8") as fh:
                fh.write("fix 3 not applied yet")
            self.assertTrue(SP.handoff_step(cur, "seat-watcher", time.time(), idle=False))
            self.assertEqual(started, [])                         # still writing: wait for idle
            self.assertTrue(SP.handoff_step(cur, "seat-watcher", time.time()))
            self.assertIn("restart-watcher", started[0])
            self.assertEqual(cur["handoff"]["phase"], "restarting")
            pair = SP.load_pair()
            pair["watcher"]["sid"] = "s-new"
            SP._write_json(SP._p("pair.json"), pair)
            self.assertFalse(SP.handoff_step(cur, "seat-watcher", time.time()))
        self.assertNotIn("handoff", cur)

    def test_handoff_waits_for_an_open_ask_before_restarting(self):
        self.pair_up()
        with open(SP._p(SP.NOTES), "w", encoding="utf-8") as fh:
            fh.write("notes")
        cur = {"handoff": {"phase": "notes", "ts": time.time() - 5}}
        with mock.patch.object(SP, "_detached", lambda argv, log: self.fail("restarted during an ask")), \
                mock.patch.object(SP, "open_asks", lambda: ["a1"]):
            self.assertTrue(SP.handoff_step(cur, "seat-watcher", time.time()))

    def test_restart_watcher_spawns_with_the_notes_and_records_the_new_sid(self):
        self.pair_up()
        with open(SP._p(SP.NOTES), "w", encoding="utf-8") as fh:
            fh.write("fix 3 not applied yet")
        killed, spawned = [], []

        def spawn(label, prompt, model, cwd, **kw):
            spawned.append((label, prompt, model, kw.get("group")))
            return "w1:p9", "s-new"
        with mock.patch.object(SP, "pane_state", lambda lb: (True, True)), \
                mock.patch.object(L, "panes", lambda: [{"pane_id": "w1:p1", "tab_id": "w1:t1"}]), \
                mock.patch.object(L, "tabs", lambda: [{"tab_id": "w1:t1", "label": "ORCHESTRATOR"}]), \
                mock.patch.object(SP, "_gone", lambda pane, timeout=15: True), \
                mock.patch.object(SP, "_log_dispatch", lambda m, w: None), mock.patch("sys.stdout", new=io.StringIO()):
            SP.restart_watcher(spawn, lambda name, force=False: killed.append(name))
        self.assertEqual(killed, ["seat-watcher"])
        self.assertEqual(spawned[0][2], "opus")
        self.assertEqual(spawned[0][3], "ORCHESTRATOR")      # beside the seat, not a tab of its own
        with open(spawned[0][1][1:], encoding="utf-8") as fh:
            self.assertIn("fix 3 not applied yet", fh.read())
        self.assertEqual(SP.load_pair()["watcher"]["sid"], "s-new")

    def test_open_asks_skips_answered_and_failed(self):
        self.pair_up()
        a, b, c = SP.new_ask("q1"), SP.new_ask("q2"), SP.new_ask("q3")
        with mock.patch("sys.stdout", new=io.StringIO()):
            SP.answer(a, "done")
        ask = SP._read_json(SP._p(f"asks/{b}.json"))
        ask["send_failed"] = "x"
        SP._write_json(SP._p(f"asks/{b}.json"), ask)
        self.assertEqual(SP.open_asks(), [c])


class AskTest(Base):
    def test_ask_blocks_until_the_watcher_answers_and_marks_it_relayed(self):
        self.pair_up()
        sent = []

        def fake_prompt(name, text):
            sent.append((name, text))
            aid = text.split("]")[0].split()[-1]
            threading.Timer(0.5, SP.answer, (aid, "Ship the fix first.")).start()
            return True, "prompted"

        with mock.patch.object(SP, "_prompt", fake_prompt), mock.patch("sys.stdout", new=io.StringIO()) as out:
            rc = SP.ask_opus("fix or stripe first?", timeout=10)
        self.assertEqual(rc, 0)
        self.assertEqual(sent[0][0], "seat-watcher")
        self.assertIn("fix or stripe first?", sent[0][1])
        self.assertIn("Ship the fix first.", out.getvalue())
        aid = sent[0][1].split("]")[0].split()[-1]
        self.assertTrue(SP._read_json(SP._p(f"asks/{aid}.json"))["relayed"])

    def test_ask_times_out_with_a_wait_command(self):
        self.pair_up()
        with mock.patch.object(SP, "_prompt", lambda n, t: (True, "")), \
                mock.patch("sys.stdout", new=io.StringIO()) as out:
            rc = SP.ask_opus("q", timeout=0.1)
        self.assertEqual(rc, 3)
        self.assertIn("ask-opus --wait a", out.getvalue())

    def test_fix_goes_to_the_seat_and_is_logged(self):
        self.pair_up()
        sent = []
        with mock.patch.object(SP, "_prompt", lambda n, t: (sent.append((n, t)), (True, "ok"))[1]), \
                mock.patch("sys.stdout", new=io.StringIO()):
            SP.fix("use haiku for the rename")
        self.assertEqual(sent[0][0], "seat-haiku")
        self.assertTrue(sent[0][1].startswith("[fix from watcher] use haiku for the rename"))
        with open(SP._p("fixes.jsonl"), encoding="utf-8") as fh:
            self.assertEqual(json.loads(fh.readline())["text"], "use haiku for the rename")

    def test_no_pair_refuses(self):
        with self.assertRaises(SystemExit):
            SP.ask_opus("q", timeout=0.1)


class HardTest(unittest.TestCase):
    def test_hard_messages(self):
        for t in ("Should we make the watcher pending queue unlimited or keep the 50-item cap?",
                  "Can you plan out a build", "which one is better for the rename",
                  "what do you think about moving WebApp to Stripe first",
                  "is it worth running opus for this", "fix the bug or ship it?",
                  # missed live 2026-10-11: "Should the" is not "should we", and "batch, or send"
                  "Should the seat-pair feeder wait 15 seconds of quiet before sending Opus a batch, "
                  "or send each item right away? Pick one.",
                  "keep the cap, or drop it?", "pick one for me",
                  # the live tester's misses, 2026-10-11
                  "i want you to think about how we should structure next weeks video uploads",
                  "give me your honest take on the 50 item cap",
                  "between sonnet and opus for the cs450 deck lead go with whichever you think is smarter",
                  "do i need to worry about the laptop key getting refused",
                  "how would you set up the channela queue so renders dont stall",
                  "im torn on buying more qqq or just voo whats the move",
                  "thoughts on moving the watcher to sonnet",
                  "[hotkey id=k1] which should I click | screenshot: C:/x.png | controls: A; B"):
            self.assertTrue(SP.is_hard(t), t)

    def test_easy_messages(self):
        for t in ("what's running", "status of app-fix", "kill review-x", "open the dashboard",
                  "[from app-fix] should I commit?", "[fix from watcher] use haiku", "/compact", "",
                  # the live tester's false hards, 2026-10-11: lookups, not judgment
                  "is the dashboard up or down?", "why is vault-22 still working",
                  "did the render finish or not?", "which one is vault-65",
                  "kill the review pane or the fix pane?",
                  "[hotkey id=k2] make the title blue | screenshot: C:/x.png | reply: if a control does it, "
                  "which should you pick? reply exactly: Clicking: <control>"):
            self.assertFalse(SP.is_hard(t), t)


class GuardTest(Base):
    def test_watcher_reaches_only_the_seat(self):
        self.pair_up()
        with as_session("s2"):
            for cmd, args in (("prompt", ["app-fix"]), ("spawn", ["x"]), ("task", ["x"]), ("kill", ["app-fix"]),
                              ("seat", ["seat-watcher"]), ("ask", ["q"]), ("seat-pair", ["down"]),
                              ("seat-pair", ["up"]), ("hold", ["x"])):
                self.assertIsNotNone(SP.watcher_refusal(cmd, args), (cmd, args))
            for cmd, args in (("prompt", ["seat-haiku"]), ("ls", []), ("dispatch", ["--explain", "5"]),
                              ("seat-pair", ["fix", "x"]), ("seat-pair", ["answer", "a1", "x"]),
                              ("quota", [])):
                self.assertIsNone(SP.watcher_refusal(cmd, args), (cmd, args))

    def test_matched_by_pane_after_a_restart(self):
        self.pair_up()
        with as_session("new-sid", "w1:p2"):
            self.assertIsNotNone(SP.watcher_refusal("prompt", ["app-fix"]))

    def test_everyone_else_is_free(self):
        self.pair_up()
        for sid in ("s1", "someone"):
            with as_session(sid):
                self.assertIsNone(SP.watcher_refusal("prompt", ["app-fix"]))

    def test_orch_main_refuses_the_shell_variable_bypass(self):
        # The reviewer's `ORCH=x; py "$ORCH" prompt app-fix go`: whatever the shell spelling,
        # orch.py receives argv `prompt app-fix go` and must refuse it.
        import orch
        self.pair_up()
        with as_session("s2"), mock.patch.object(sys, "argv", ["orch.py", "prompt", "app-fix", "go"]), \
                mock.patch.object(orch, "cmd_prompt", side_effect=AssertionError("must not run")), \
                mock.patch("sys.stderr", new=io.StringIO()) as err:
            with self.assertRaises(SystemExit) as e:
                orch.main()
        self.assertEqual(e.exception.code, 2)
        self.assertIn("watcher may not run", err.getvalue())
        with as_session("s2"), mock.patch("sys.stderr", new=io.StringIO()):
            with self.assertRaises(SystemExit):
                orch.cmd_prompt("app-fix", "go")
            with self.assertRaises(SystemExit):
                orch.cmd_kill("app-fix")

    def test_seat_spawns_and_kills_log_themselves(self):
        self.pair_up()
        with as_session("s1"), mock.patch("sys.stdout", new=io.StringIO()):
            SP.auto_log("spawn", "app-fix on claude-sonnet-5-5")
        with as_session("someone"), mock.patch("sys.stdout", new=io.StringIO()):
            SP.auto_log("spawn", "not the seat")
        rows = self.decisions()
        self.assertEqual([(r["kind"], r["what"], r["by"]) for r in rows],
                         [("spawn", "app-fix on claude-sonnet-5-5", "auto")])

    def test_kill_of_a_missing_name_logs_no_closed_line(self):
        import orch
        self.pair_up()
        with as_session("s1"), mock.patch.object(orch, "agent_pane_for", return_value=(None, None)), \
                mock.patch.object(L, "resolve", return_value=None), \
                mock.patch("sys.stdout", new=io.StringIO()):
            with self.assertRaises(SystemExit):
                orch.cmd_kill("rename-notes")
        self.assertEqual(self.decisions(), [])

    def test_kill_of_a_real_pane_logs_closed_once(self):
        import orch
        self.pair_up()
        pane = {"agent": "claude"}
        with as_session("s1"), mock.patch.object(orch, "agent_pane_for", return_value=("w1:p9", pane)), \
                mock.patch.object(orch, "_owner_gate", return_value=True), \
                mock.patch.object(L, "herdr"), mock.patch.object(L, "sidecar_drop"), \
                mock.patch.object(L, "sidecar_load", return_value={}), \
                mock.patch.object(L.ledger, "write"), \
                mock.patch.dict(sys.modules, {"orch_watchdog": mock.Mock()}), \
                mock.patch("sys.stdout", new=io.StringIO()):
            orch.cmd_kill("app-fix")
        self.assertEqual([(r["kind"], r["what"]) for r in self.decisions()], [("kill", "closed app-fix")])

    def test_down_keeps_the_guards_while_the_watcher_lives(self):
        self.pair_up()
        killed = []
        with mock.patch.object(SP, "pane_state", return_value=(True, True)), \
                mock.patch.object(SP, "_gone", return_value=False), \
                mock.patch.object(L, "orchestrator_seat", return_value={}):
            with self.assertRaises(SystemExit):
                SP.down(None, lambda n, force: killed.append(n), lambda n: None)
        self.assertEqual(killed, ["seat-watcher"])
        self.assertTrue(os.path.exists(SP._p("pair.json")))


class HookTest(Base):
    def run_hook(self, mod, payload, env_pane=""):
        with mock.patch.object(_hook_stdin, "read_stdin", return_value=json.dumps(payload)), \
                mock.patch("sys.stdout", new=io.StringIO()) as out, \
                mock.patch.dict(os.environ, {"HERDR_PANE_ID": env_pane}):
            mod.main()
        return out.getvalue()

    def prompts(self):
        return SP.turn_prompts()[0]

    def test_route_forwards_a_hard_message_before_haiku_starts(self):
        self.pair_up()
        route = load_hook("seat-pair-route")
        started = []
        with mock.patch.object(SP, "start_send_ask", lambda aid: started.append(aid)):
            out = self.run_hook(route, {"session_id": "s1",
                                        "prompt": "Should we keep the 50-item cap or make it unlimited?"})
        ctx = json.loads(out)["hookSpecificOutput"]["additionalContext"]
        self.assertEqual(len(started), 1)
        self.assertIn(f"ask-opus --wait {started[0]}", ctx)
        self.assertEqual(SP._read_json(SP._p(f"asks/{started[0]}.json"))["route"], "hook")
        self.assertEqual(self.prompts()[-1]["ask"], started[0])

    def test_route_leaves_easy_messages_panes_and_other_sessions_alone(self):
        self.pair_up()
        route = load_hook("seat-pair-route")
        with mock.patch.object(SP, "start_send_ask", side_effect=AssertionError("forwarded")):
            self.assertEqual(self.run_hook(route, {"session_id": "s1", "prompt": "what's running"}), "")
            self.assertEqual(self.run_hook(route, {"session_id": "s1", "prompt": "[from app-fix] should I?"}), "")
            self.assertEqual(self.run_hook(route, {"session_id": "s2", "prompt": "should we?"}), "")
            self.assertEqual(self.run_hook(route, {"session_id": "zz", "prompt": "should we?"}), "")
        self.assertEqual([p["from_ryan"] for p in self.prompts()], [True, False])

    def test_route_restarts_a_dead_feeder_once(self):
        # Live 2026-10-11: killing the feeder child also ended its reloader; nothing restarted it.
        self.pair_up()
        route = load_hook("seat-pair-route")
        self.run_hook(route, {"session_id": "s1", "prompt": "what's running"})
        self.run_hook(route, {"session_id": "s1", "prompt": "status"})
        self.assertEqual(len(self.started_feeds), 1)
        self.assertEqual(SP.load_pair()["feed_pid"], 4242)

    def transcript(self, *turns, older=(), meta=()):
        """A transcript: `older` assistant replies an hour back, then for each (user text, [replies])
        a user entry and its replies, stamped now. A user text of None adds replies only. `meta`
        texts are appended as hook-feedback user entries (isMeta) with no origin."""
        import datetime
        now = datetime.datetime.now(datetime.timezone.utc)
        stamp = lambda d: (now - d).isoformat().replace("+00:00", "Z")
        path = os.path.join(self.tmp.name, "t.jsonl")
        with open(path, "w", encoding="utf-8") as fh:
            w = lambda e: fh.write(json.dumps(e) + "\n")
            for o in older:
                w({"type": "assistant", "timestamp": stamp(datetime.timedelta(hours=1)),
                   "message": {"content": [{"type": "text", "text": o}]}})
            for user, replies in turns:
                if user is not None and user.startswith("QUEUED:"):
                    # A message sent mid-turn: Claude Code records it as an attachment, not a user entry.
                    w({"type": "attachment", "timestamp": stamp(datetime.timedelta(0)),
                       "attachment": {"type": "queued_command", "prompt": user[7:]}})
                elif user is not None:
                    w({"type": "user", "timestamp": stamp(datetime.timedelta(0)), "origin": {"kind": "human"},
                       "message": {"content": user}})
                for r in replies:
                    if r.startswith("META:"):
                        w({"type": "user", "timestamp": stamp(datetime.timedelta(0)), "isMeta": True,
                           "message": {"content": r[5:]}})
                        continue
                    w({"type": "user", "timestamp": stamp(datetime.timedelta(0)),
                       "message": {"content": [{"type": "tool_result", "content": "ok"}]}})
                    w({"type": "assistant", "timestamp": stamp(datetime.timedelta(0)),
                       "message": {"content": [{"type": "tool_use", "name": "Bash"}, {"type": "text", "text": r}]}})
        return path

    def say(self, prompt, from_ryan=True, ask=None):
        SP.record_prompt(prompt, from_ryan, ask)

    def quick(self, log):
        return mock.patch.object(log, "WAIT", 0.3)

    def stop(self, log, path, **extra):
        with self.quick(log):
            return self.run_hook(log, {"session_id": "s1", "transcript_path": path, **extra})

    def test_log_never_takes_the_previous_reply_for_this_answer(self):
        # Live 2026-10-11: the Stop hook ran before the transcript had the new answer and logged
        # the reply before it, "Ready.".
        self.pair_up()
        log = load_hook("seat-pair-log")
        self.say("should we?")
        self.stop(log, self.transcript(("should we?", []), older=["Ready."]))
        self.assertEqual(self.decisions(), [])
        self.stop(log, self.transcript(("should we?", ["Wait for 15s of quiet."]), older=["Ready."]))
        self.assertIn("Wait for 15s of quiet.", self.decisions()[0]["what"])
        self.assertNotIn("Ready.", self.decisions()[0]["what"])

    def test_a_prompt_sent_while_busy_waits_for_the_turn_that_reads_it(self):
        # Live 2026-10-11: "is the dashboard up or down?" was submitted while the seat answered a
        # peer message, got recorded, and was logged against that peer reply ("Noted ...").
        self.pair_up()
        log = load_hook("seat-pair-log")
        self.say("<cross-session-message from=x>hotkey tags now</cross-session-message>", from_ryan=False)
        self.say("is the dashboard up or down?")
        self.stop(log, self.transcript(
            ("Another Claude session sent a message:\n<cross-session-message from=x>hotkey tags now</cross-session-message>",
             ["Noted. I tag hotkeys now."])))
        self.assertEqual(self.decisions(), [])
        self.assertEqual([r["prompt"] for r, _ in SP.pending_prompts()], ["is the dashboard up or down?"])
        self.stop(log, self.transcript(
            ("Another Claude session sent a message:\n<cross-session-message from=x>hotkey tags now</cross-session-message>",
             ["Noted. I tag hotkeys now."]),
            ("is the dashboard up or down?", ["Both are up."])))
        rows = self.decisions()
        self.assertEqual(len(rows), 1)
        self.assertIn("Both are up.", rows[0]["what"])
        self.assertNotIn("Noted", rows[0]["what"])
        self.assertEqual(SP.pending_prompts(), [])

    def test_the_closing_reply_from_the_payload_is_logged_in_this_turn(self):
        # Live tester r3, bug A: the closing reply reaches the transcript only after Stop hooks
        # run, so waiting for it logged every last answer one turn late.
        self.pair_up()
        log = load_hook("seat-pair-log")
        self.say("whats the best account right now")
        self.stop(log, self.transcript(("whats the best account right now", ["Checking quota."])),
                  last_assistant_message="Secondary: it resets first, drain it.")
        what = self.decisions()[0]["what"]
        self.assertIn("Checking quota.", what)
        self.assertIn("Secondary: it resets first, drain it.", what)
        self.assertEqual(SP.pending_prompts(), [])

    def test_replies_to_unrecorded_input_never_leak_into_ryans_answer(self):
        # Live tester r3, bug B: a peer message delivered mid-turn was never recorded, so its
        # reply ran on into Ryan's logged answer.
        self.pair_up()
        log = load_hook("seat-pair-log")
        self.say("plan out next weeks video uploads")
        self.stop(log, self.transcript(
            ("plan out next weeks video uploads", ["Mon-Fri, one per day."]),
            ("Another Claude session sent a message:\n<cross-session-message>status?</cross-session-message>",
             ["vault-65 status: all green."])),
            last_assistant_message="vault-65 status: all green.")
        what = self.decisions()[0]["what"]
        self.assertIn("Mon-Fri, one per day.", what)
        self.assertNotIn("all green", what)

    def test_each_message_in_a_turn_gets_its_own_answer(self):
        self.pair_up()
        log = load_hook("seat-pair-log")
        self.say("whats running")
        self.say("[from kill-log-fix] done", from_ryan=False)
        self.say("anything waiting on me")
        self.stop(log, self.transcript(("whats running", ["3 panes."]), ("[from kill-log-fix] done", ["Noted."]),
                                       ("anything waiting on me", ["Two questions."])))
        whats = [r["what"] for r in self.decisions()]
        self.assertEqual(len(whats), 2)
        self.assertTrue(whats[0].startswith("Ryan: whats running -> seat: 3 panes."))
        self.assertTrue(whats[1].startswith("Ryan: anything waiting on me -> seat: Two questions."))

    def test_a_dropped_prompt_does_not_block_the_queue(self):
        self.pair_up()
        log = load_hook("seat-pair-log")
        self.say("this one never reached the transcript")
        self.say("whats running")
        self.stop(log, self.transcript(("whats running", ["3 panes."])))
        self.assertEqual([r["what"] for r in self.decisions()], ["Ryan: whats running -> seat: 3 panes."])
        self.assertEqual(SP.pending_prompts(), [])

    def test_log_waits_for_an_unanswered_ask_once_then_logs(self):
        self.pair_up()
        log = load_hook("seat-pair-log")
        aid = SP.new_ask("should we?", route="hook")
        self.say("should we?", ask=aid)
        path = self.transcript(("should we?", ["Opus is thinking."]))
        out = self.stop(log, path)
        self.assertEqual(json.loads(out)["decision"], "block")
        self.assertIn(f"--wait {aid}", json.loads(out)["reason"])
        self.assertEqual(self.decisions(), [])
        self.assertEqual(self.stop(log, path, stop_hook_active=True), "")
        self.assertEqual([r["kind"] for r in self.decisions()], ["answer"])

    def test_an_answer_ryan_never_saw_sends_the_seat_back_with_it(self):
        # Live 2026-10-11: a message landed while Haiku waited on ask a2e69e8; it answered that
        # message only, and the render answer never reached Ryan although relayed was true.
        self.pair_up()
        log = load_hook("seat-pair-log")
        aid = SP.new_ask("render all at once or one at a time?", route="hook")
        with mock.patch("sys.stdout", new=io.StringIO()):
            SP.answer(aid, "Start all renders at once; the render gate allows two renders and one Topaz.")
        SP.mark_relayed(aid)
        self.say("render all at once or one at a time?", ask=aid)
        self.say("[from review-x] busy or idle?", from_ryan=False)
        path = self.transcript(("render all at once or one at a time?", ["Opus is thinking."]),
                               ("[from review-x] busy or idle?", ["Show busy, never idle."]))
        reason = json.loads(self.stop(log, path))["reason"]
        self.assertIn("has not seen Opus's answer", reason)
        self.assertIn("render gate allows two renders", reason)
        self.assertEqual(self.decisions(), [])
        # The seat relays after the hook feedback: that text belongs to Ryan's message, not the peer's.
        path = self.transcript(("render all at once or one at a time?", ["Opus is thinking."]),
                               ("[from review-x] busy or idle?", ["Show busy, never idle.",
                                "META:Stop hook feedback: Ryan has not seen Opus's answer",
                                "Opus says: start all renders at once; the render gate allows two renders and one Topaz."]))
        self.stop(log, path, stop_hook_active=True)
        kinds = {r["what"].split(" -> ")[0]: r["kind"] for r in self.decisions()}
        self.assertEqual(list(kinds.values()), ["answer-from-opus"])

    def test_a_shown_relay_logs_as_from_opus_and_only_once(self):
        self.pair_up()
        log = load_hook("seat-pair-log")
        aid = SP.new_ask("cap or no cap?", route="hook")
        with mock.patch("sys.stdout", new=io.StringIO()):
            SP.answer(aid, "Keep the fifty item cap because batches drain anyway.")
        self.say("cap or no cap?", ask=aid)
        path = self.transcript(("cap or no cap?", ["Opus says: keep the fifty item cap, batches drain anyway."]))
        self.stop(log, path)
        self.stop(log, path)
        rows = self.decisions()
        self.assertEqual([r["kind"] for r in rows], ["answer-from-opus"])
        self.assertIn("keep the fifty item cap", rows[0]["what"])

    def test_an_old_abandoned_ask_does_not_block_a_later_easy_turn(self):
        self.pair_up()
        log = load_hook("seat-pair-log")
        SP.new_ask("old question?", route="hook")      # never answered, not part of this turn
        self.say("whats running")
        self.assertEqual(self.stop(log, self.transcript(("whats running", ["3 panes."]))), "")
        self.assertEqual([r["kind"] for r in self.decisions()], ["answer"])

    def test_a_hotkey_sent_during_a_hard_ask_gets_its_reply_at_once(self):
        # Live tester r4: the mid-turn hotkey was a queued_command attachment, never matched, held
        # pending a whole turn, then got the fallback; its tagged reply sat in Ryan's row.
        self.pair_up()
        log = load_hook("seat-pair-log")
        aid = SP.new_ask("tell me one at a time or batch them?", route="hook")
        with mock.patch("sys.stdout", new=io.StringIO()):
            SP.answer(aid, "Batch them into one message so you decide once and switch context less.")
        hard = "tell me one at a time or batch them?"
        hk = "[hotkey id=t2] make the title blue | screenshot: C:/x.png"
        self.say(hard, ask=aid)
        self.say(hk)
        closing = ("[hotkey id=t2] Clicking: Make the title blue\n\n"
                   "Opus says batch them into one message so you decide once and switch context less.")
        self.stop(log, self.transcript((hard, ["Opus is thinking about this now."]), ("QUEUED:" + hk, [])),
                  last_assistant_message=closing)
        r = SP._read_json(SP._p("replies/t2.json"))
        self.assertEqual((r["text"], r["kind"]), ("Clicking: Make the title blue", "answer"))
        rows = {d["what"].split(" -> ")[0]: d for d in self.decisions()}
        ryan = rows[f"Ryan: {hard}"]
        self.assertEqual(ryan["kind"], "answer-from-opus")
        self.assertIn("batch them into one message", ryan["what"])
        self.assertNotIn("Clicking", ryan["what"])
        self.assertEqual(SP.pending_prompts(), [])

    def test_a_mid_turn_peer_message_does_not_take_ryans_opus_answer(self):
        # Review r3 must-fix 1, replayed from the live 00:46 transcript: a peer message queued
        # while the seat waited on Opus took the whole relay; Ryan's row kept "Opus is thinking".
        self.pair_up()
        log = load_hook("seat-pair-log")
        hard = "should the seat keep a ten item limit or show everything"
        aid = SP.new_ask(hard, route="hook")
        with mock.patch("sys.stdout", new=io.StringIO()):
            SP.answer(aid, "Keep a limit of ten, with one exception for urgent asks from panes.")
        self.say(hard, ask=aid)
        self.say("[from tester] PEERTEXT-ZEBRA ping", from_ryan=False)
        self.stop(log, self.transcript((hard, ["Opus is thinking about this now."]),
                                       ("QUEUED:[from tester] PEERTEXT-ZEBRA ping", [])),
                  last_assistant_message="Opus says keep a limit of ten, with one exception for urgent asks from panes.")
        rows = self.decisions()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["kind"], "answer-from-opus")
        self.assertIn("keep a limit of ten", rows[0]["what"])

    def test_an_easy_answer_after_a_task_notification_is_still_ryans(self):
        # Review r3: "whats running", a tool call, an unrecorded entry lands, then the answer.
        self.pair_up()
        log = load_hook("seat-pair-log")
        self.say("whats running")
        self.stop(log, self.transcript(("whats running", []), ("<task-notification>done</task-notification>", [])),
                  last_assistant_message="3 panes.")
        self.assertEqual([d["what"] for d in self.decisions()], ["Ryan: whats running -> seat: 3 panes."])

    def test_an_untagged_mid_turn_hotkey_never_gets_the_owed_answer(self):
        # Review r3 must-fix 2: every mid-turn hotkey is its own entry, so the old `alone` was
        # always true and the VOO answer went into the t9 bubble.
        self.pair_up()
        log = load_hook("seat-pair-log")
        hard = "should i buy voo or qqq"
        aid = SP.new_ask(hard, route="hook")
        with mock.patch("sys.stdout", new=io.StringIO()):
            SP.answer(aid, "Buy VOO first because it is broad.")
        hk = "[hotkey id=t9] make the title blue"
        self.say(hard, ask=aid)
        self.say(hk)
        self.stop(log, self.transcript((hard, ["Opus is thinking."]), ("QUEUED:" + hk, [])),
                  last_assistant_message="Opus says buy VOO first because it is broad.")
        r = SP._read_json(SP._p("replies/t9.json"))
        self.assertEqual((r["text"], r["kind"]), ("Answered in the seat-haiku pane.", "unread"))
        ryan = [d for d in self.decisions() if d["what"].startswith(f"Ryan: {hard}")][0]
        self.assertEqual(ryan["kind"], "answer-from-opus")

    def test_a_short_message_inside_a_longer_one_is_not_taken_as_read(self):
        # Review r3 nice-to-have 1: "status" matched inside the watcher note before it.
        self.pair_up()
        log = load_hook("seat-pair-log")
        note = "[from seat-watcher] [fix from watcher] the quota status line is wrong"
        self.say(note, from_ryan=False)
        self.say("status")
        self.stop(log, self.transcript((note, ["Fixed the quota line."])))
        self.assertEqual([r["prompt"] for r, _ in SP.pending_prompts()], ["status"])

    def test_a_reworded_repeat_is_logged_once_and_a_short_close_keeps_the_answer(self):
        # Live 2026-10-11: the seat answered, ran a tool, then said the same answer reworded.
        self.pair_up()
        log = load_hook("seat-pair-log")
        q = "can i read the openwhispr source code right now"
        first = ("Yes. OpenWhispr is public on GitHub. It uses the MIT license, so you can read, copy and "
                 "change it. Your copy is patched, and the newest release is newer than yours.")
        second = ("Yes. OpenWhispr is public on GitHub. The license is MIT, so you can read, copy and change "
                  "it. Your copy is patched, and the newest release is newer than yours.")
        self.say(q)
        self.stop(log, self.transcript((q, [first])), last_assistant_message=second)
        what = self.decisions()[0]["what"]
        self.assertEqual(what.count("OpenWhispr is public"), 1)
        self.assertIn("The license is MIT", what)
        q2 = "whats running and whats the quota"
        self.say(q2)
        self.stop(log, self.transcript((q2, ["Five panes are working: alpha, bravo, charlie. Quota: "
                                             "secondary account first, primary account second."])),
                  last_assistant_message="Quota: secondary account first.")
        self.assertIn("alpha, bravo, charlie", self.decisions()[1]["what"])

    def test_a_long_note_does_not_swallow_a_short_unread_message(self):
        # Review r4 nice-to-have 1: a note over 100 chars ending in "status" took the unread "status".
        self.pair_up()
        log = load_hook("seat-pair-log")
        note = "[from seat-watcher] [fix from watcher] " + "the quota line is off by one account " * 4 + "fix the status"
        self.say(note, from_ryan=False)
        self.say("status")
        self.stop(log, self.transcript((note, ["Fixed."])))
        self.assertEqual([r["prompt"] for r, _ in SP.pending_prompts()], ["status"])

    def test_an_untagged_hotkey_after_an_answered_question_gets_no_borrowed_text(self):
        # Review r4 nice-to-have 2.
        self.pair_up()
        log = load_hook("seat-pair-log")
        hk = "[hotkey id=t6] what is this page"
        self.say("whats running")
        self.say(hk)
        self.stop(log, self.transcript(("whats running", ["Let me check."]), ("QUEUED:" + hk, [])),
                  last_assistant_message="Five panes are working.")
        self.assertEqual(SP._read_json(SP._p("replies/t6.json"))["kind"], "unread")

    def test_a_hotkey_row_never_carries_an_unanswered_plain_message(self):
        # Review r4 nice-to-have 3.
        self.pair_up()
        log = load_hook("seat-pair-log")
        hk = "[hotkey id=t7] make it red"
        self.say("whats running")
        self.say(hk)
        self.stop(log, self.transcript(("whats running", []), ("QUEUED:" + hk, [])),
                  last_assistant_message="[hotkey id=t7] Clicking: Make it red")
        whats = [d["what"] for d in self.decisions()]
        self.assertTrue(all("whats running" not in w for w in whats if "hotkey" in w), whats)

    def test_route_reminder_skips_hotkeys_and_old_rows(self):
        # Review r4 nice-to-have 4.
        self.pair_up()
        route = load_hook("seat-pair-route")
        SP.record_prompt("an old message he cancelled", True, None)
        rows = [json.loads(l) for l in open(SP._p("seat-prompts.jsonl"), encoding="utf-8")]
        rows[-1]["ts"] -= 120
        with open(SP._p("seat-prompts.jsonl"), "w", encoding="utf-8") as fh:
            fh.write("".join(json.dumps(r) + "\n" for r in rows))
        self.assertEqual(self.run_hook(route, {"session_id": "s1", "prompt": "whats running"}), "")
        out = self.run_hook(route, {"session_id": "s1", "prompt": "[hotkey id=k20] make it red"})
        self.assertNotIn("Answer every one", json.loads(out)["hookSpecificOutput"]["additionalContext"])

    def test_a_hotkey_gets_its_bubble_while_the_turn_is_held_for_opus(self):
        self.pair_up()
        log = load_hook("seat-pair-log")
        hard = "should we cap it or not"
        aid = SP.new_ask(hard, route="hook")
        hk = "[hotkey id=t5] make the title blue"
        self.say(hard, ask=aid)
        self.say(hk)
        out = self.stop(log, self.transcript((hard, ["Opus is thinking."]), ("QUEUED:" + hk, [])),
                        last_assistant_message="[hotkey id=t5] Clicking: Make the title blue")
        self.assertEqual(json.loads(out)["decision"], "block")
        self.assertEqual(SP._read_json(SP._p("replies/t5.json"))["text"], "Clicking: Make the title blue")

    def test_a_failed_send_stops_the_wait_at_once(self):
        self.pair_up()
        aid = SP.new_ask("should we?", route="hook")
        ask = SP._read_json(SP._p(f"asks/{aid}.json"))
        ask["send_failed"] = "watcher pane gone"
        SP._write_json(SP._p(f"asks/{aid}.json"), ask)
        with mock.patch("sys.stdout", new=io.StringIO()) as out:
            self.assertEqual(SP.wait_answer(aid, timeout=30), 4)
        self.assertIn("could not be reached", out.getvalue())

    def test_back_to_back_messages_with_one_reply_log_one_row_naming_both(self):
        # Live tester r5: the second message landed mid-turn, the seat answered both in one reply,
        # and the first message had no row at all.
        self.pair_up()
        log = load_hook("seat-pair-log")
        self.say("which panes are working")
        self.say("whats the best account right now")
        self.stop(log, self.transcript(("which panes are working", []),
                                       ("QUEUED:whats the best account right now", [])),
                  last_assistant_message="Secondary first. Working panes: vault-22.")
        whats = [d["what"] for d in self.decisions()]
        self.assertEqual(whats, ["Ryan: which panes are working | whats the best account right now -> "
                                 "seat: Secondary first. Working panes: vault-22."])
        self.assertEqual(SP.pending_prompts(), [])

    def test_a_queued_message_that_later_becomes_its_own_turn_is_matched_once(self):
        self.pair_up()
        log = load_hook("seat-pair-log")
        self.say("whats running")
        self.say("anything waiting on me")
        self.stop(log, self.transcript(("whats running", ["3 panes."]), ("QUEUED:anything waiting on me", []),
                                       ("anything waiting on me", ["Two questions."])))
        whats = [d["what"] for d in self.decisions()]
        self.assertEqual(whats, ["Ryan: whats running -> seat: 3 panes.",
                                 "Ryan: anything waiting on me -> seat: Two questions."])

    def test_each_hotkey_gets_only_its_own_reply(self):
        # Live 2026-10-11: QQQ/VOO and "make the title blue" shared a turn and the title-blue
        # bubble showed the VOO answer.
        self.pair_up()
        log = load_hook("seat-pair-log")
        voo = "im torn on more qqq or just voo whats the move"
        hk = "[hotkey id=k11] make the title blue | screenshot: C:/s.png"
        self.say(voo)
        self.say(hk)
        self.stop(log, self.transcript((voo, ["Opus says stay on VOO for the core."]),
                                       (hk, ["[hotkey id=k11] Clicking: Make the title blue"])))
        self.assertEqual(SP._read_json(SP._p("replies/k11.json"))["text"], "Clicking: Make the title blue")
        hk2 = "[hotkey id=k12] what is this page | screenshot: C:/s.png"
        self.say(hk2)
        self.stop(log, self.transcript((hk2, ["It is the practice page."])))
        self.assertEqual(SP._read_json(SP._p("replies/k12.json"))["text"], "It is the practice page.")

    def test_joined_queued_hotkeys_split_by_tag_or_fall_back_safely(self):
        self.pair_up()
        log = load_hook("seat-pair-log")
        a = "[hotkey id=k7] make the title blue | screenshot: C:/s.png"
        b = "[hotkey id=k8] what is this button | screenshot: C:/t.png"
        self.say(a)
        self.say(b)
        self.stop(log, self.transcript((a + "\n" + b, ["[hotkey id=k7] Clicking: Make the title blue",
                                                       "That button saves the draft."])))
        self.assertEqual(SP._read_json(SP._p("replies/k7.json"))["text"], "Clicking: Make the title blue")
        self.assertEqual(SP._read_json(SP._p("replies/k8.json"))["kind"], "unread")

    def test_an_unread_turn_still_writes_the_reply_file(self):
        self.pair_up()
        log = load_hook("seat-pair-log")
        hk = "[hotkey id=k10] make it red"
        self.say(hk)
        self.say("whats running")
        self.stop(log, self.transcript((hk, []), ("whats running", ["3 panes."])))
        self.assertEqual(SP._read_json(SP._p("replies/k10.json"))["kind"], "unread")

    def test_route_tells_the_seat_to_tag_a_hotkey_reply_and_skips_slash_commands(self):
        self.pair_up()
        route = load_hook("seat-pair-route")
        out = self.run_hook(route, {"session_id": "s1", "prompt": "[hotkey id=k13] make the title blue"})
        self.assertIn("[hotkey id=k13]", json.loads(out)["hookSpecificOutput"]["additionalContext"])
        self.run_hook(route, {"session_id": "s1", "prompt": "/compact"})
        self.assertEqual([r["prompt"] for r, _ in SP.pending_prompts()], ["[hotkey id=k13] make the title blue"])

    def test_route_reminds_the_seat_to_answer_every_back_to_back_message(self):
        self.pair_up()
        route = load_hook("seat-pair-route")
        self.assertEqual(self.run_hook(route, {"session_id": "s1", "prompt": "which panes are working"}), "")
        out = self.run_hook(route, {"session_id": "s1", "prompt": "whats the best account right now"})
        ctx = json.loads(out)["hookSpecificOutput"]["additionalContext"]
        self.assertIn("which panes are working", ctx)
        self.assertIn("Answer every one", ctx)

    def test_a_hotkey_turn_is_routed_like_ryan(self):
        self.pair_up()
        route = load_hook("seat-pair-route")
        started = []
        with mock.patch.object(SP, "start_send_ask", lambda aid: started.append(aid)):
            self.run_hook(route, {"session_id": "s1", "prompt": "[hotkey id=k42] which one should I click"})
        self.assertEqual(SP.ask_question(started[0]), "which one should I click")
        self.assertIsNone(SP.reply_id("[from x] [hotkey id=k1] hi"))

    def test_log_ignores_turns_with_no_message_from_ryan(self):
        self.pair_up()
        log = load_hook("seat-pair-log")
        self.say("[from x] done", from_ryan=False)
        self.stop(log, self.transcript(("[from x] done", ["noted"])))
        self.assertEqual(self.decisions(), [])
        self.assertEqual(SP.pending_prompts(), [])


class BoxTest(Base):
    def test_a_draft_in_the_seat_box_holds_the_prompt(self):
        screen = "● Ready.\n─────\n❯ restart the agent chrome\n─────\n  seat-haiku · main"
        with mock.patch.object(L, "pane_read", return_value=screen):
            self.assertEqual(SP.box_draft("w1:p1"), "restart the agent chrome")
            self.assertFalse(SP.wait_box_clear("w1:p1", timeout=0))
        with mock.patch.object(L, "pane_read", return_value="❯ /color cyan\n● ok\n─────\n❯ \n─────"):
            self.assertEqual(SP.box_draft("w1:p1"), "")
            self.assertTrue(SP.wait_box_clear("w1:p1", timeout=0))

    def test_dim_suggestion_text_is_not_a_draft(self):
        # Live tester r4: an empty box shows a dim suggestion; plain text read it as a draft.
        ghost = "\x1b[0m❯ \x1b[2mwhats running\x1b[0m"
        colour_then_draft = "\x1b[38;2;220;38;38m❯ \x1b[0mrestart chrome \x1b[2m(tab)\x1b[22m"
        with mock.patch.object(L, "pane_read", return_value=f"● ok\n─────\n{ghost}\n─────"):
            self.assertEqual(SP.box_draft("w1:p1"), "")
        with mock.patch.object(L, "pane_read", return_value=f"─────\n{colour_then_draft}\n─────"):
            self.assertEqual(SP.box_draft("w1:p1"), "restart chrome")

    def test_up_refuses_while_either_label_is_alive(self):
        with mock.patch.object(SP, "pane_state", side_effect=lambda lb: (lb == "seat-haiku", True)):
            with self.assertRaises(SystemExit) as e:
                SP.up(None, lambda *a, **k: self.fail("spawned"))
        self.assertIn("seat-haiku already running", str(e.exception))


class CliTest(unittest.TestCase):
    ORCH = os.path.join(HERE, "orch.py")

    def run_orch(self, *args):
        return subprocess.run([sys.executable, self.ORCH, *args], capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=60)

    def test_help_and_bad_flag(self):
        for cmd in ("decide", "ask-opus", "seat-pair"):
            r = self.run_orch(cmd, "--help")
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn("usage: orch.py " + cmd, r.stdout)
        self.assertEqual(self.run_orch("decide", "--nope", "x").returncode, 2)
        self.assertEqual(self.run_orch("seat-pair", "sideways").returncode, 2)


if __name__ == "__main__":
    unittest.main()
