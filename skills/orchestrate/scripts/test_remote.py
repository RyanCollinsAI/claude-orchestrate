"""Tests for remote.py (M1): a mate on another machine over ssh, against a FAKE host.

fake_host.py stands in for ssh, scp and the claude CLI; it runs remote.py's own host verbs against a
temp dir, so the seat code and the host code are both the real code. Run `py test_remote.py`.
"""
import contextlib, io, json, os, shutil, sys, tempfile, time, unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import orchlib as L
import orch
import remote as R
import dispatch_rule as DR

HERE = os.path.dirname(os.path.abspath(__file__))
FAKE = os.path.join(HERE, "fake_host.py")


class FakeHostCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        self.board, self.handoffs, self.home = (os.path.join(tmp, d) for d in ("board", "handoffs", "home"))
        for d in (self.board, self.handoffs, self.home):
            os.makedirs(d)
        home = self.home.replace("\\", "/")
        self.write_hosts({"laptop": {"ssh": "YOURNAME@laptop", "home": home}})
        os.makedirs(os.path.join(self.home, ".claude"))
        with open(os.path.join(self.home, ".claude", "remote-host.json"), "w") as fh:
            json.dump({"claude": [sys.executable, FAKE, "claude"], "claude_args": []}, fh)
        env = {"ORCH_SSH_BIN": json.dumps([sys.executable, FAKE, "ssh"]),
               "ORCH_SCP_BIN": json.dumps([sys.executable, FAKE, "scp"]),
               "FAKE_HOME": self.home, "FAKE_MODE": "ok", "FAKE_CLAUDE_SLEEP": "0",
               "ORCH_REMOTE_MIN_AGE": "0"}
        p = mock.patch.dict(os.environ, env)
        p.start()
        self.addCleanup(p.stop)
        for attr, val in (("BOARD_DIR", self.board), ("HANDOFFS", self.handoffs)):
            q = mock.patch.object(L, attr, val)
            q.start()
            self.addCleanup(q.stop)
        self.addCleanup(self.kill_all)

    def write_hosts(self, d):
        with open(os.path.join(self.board, "hosts.json"), "w") as fh:
            json.dump(d, fh)

    def kill_all(self):
        sd = os.path.join(self.home, R.STATE_DIR)
        for f in os.listdir(sd) if os.path.isdir(sd) else []:
            if f.endswith(".json"):
                with open(os.path.join(sd, f)) as fh:
                    pid = json.load(fh).get("pid")
                if L.pid_alive(pid):
                    L.kill_tree(pid)

    def start(self, label="job1", **kw):
        return R.start_task("laptop", label, "do the thing", "the thing is done", owner="axi-lead", **kw)

    def wait_for(self, fn, secs=15):
        end = time.time() + secs
        while time.time() < end:
            if fn():
                return True
            time.sleep(0.2)
        return False


class TestStartAndLs(FakeHostCase):
    def test_start_pushes_the_task_file_and_runs_a_detached_mate(self):
        os.environ["FAKE_CLAUDE_SLEEP"] = "30"
        line = self.start()
        self.assertIn("started job1@laptop", line)
        task = os.path.join(self.home, ".claude", "handoffs", "inbox", "task-job1.md")
        self.assertTrue(os.path.exists(task))
        text = open(task, encoding="utf-8").read()
        self.assertIn("do the thing", text)
        self.assertIn("report-job1.md", text)
        rows, notes = R.ls_rows()
        self.assertEqual([], notes)
        self.assertEqual(1, len(rows))
        name, status, _ctx, harness, _model, pane, *_rest = rows[0]
        self.assertEqual(("job1@laptop", "busy", "claude"), (name, status, harness))
        self.assertEqual("laptop", rows[0][9])                       # WHERE
        self.assertTrue(pane.startswith("laptop:"))
        self.assertEqual("axi-lead", rows[0][8])                     # OWNER

    def test_a_finished_mate_with_a_report_reads_done(self):
        self.start()
        out = os.path.join(self.home, ".claude", "handoffs", "outbox", "report-job1.md")
        self.assertTrue(self.wait_for(lambda: os.path.exists(out)))
        self.assertTrue(self.wait_for(lambda: R.ls_rows()[0][0][1] == "done"))

    def test_a_mate_that_ended_with_no_report_reads_failed(self):
        os.environ["FAKE_CLAUDE_SLEEP"] = "30"
        self.start()
        sd = os.path.join(self.home, R.STATE_DIR, "job1.json")
        s = json.load(open(sd))
        s["pid"] = 999999999                                          # no such process
        json.dump(s, open(sd, "w"))
        self.assertEqual("failed", R.ls_rows()[0][0][1])

    def test_bad_label_and_bad_model_are_refused_before_ssh(self):
        for kw in ({"label": "a b"}, {"label": "x;rm"}, {"label": "ok", "model": "a;b"}):
            with self.assertRaises(R.HostError):
                R.start_task("laptop", kw.get("label", "ok"), "g", "d", model=kw.get("model", "sonnet"))

    def test_ls_through_orch_prints_where_laptop(self):
        os.environ["FAKE_CLAUDE_SLEEP"] = "30"
        self.start()
        with mock.patch.object(L, "panes_by_sid", return_value={}), \
             mock.patch.object(L, "sessions", return_value={}), \
             mock.patch.object(L, "sidecar_load", return_value={}), \
             mock.patch.object(L, "other_panes", return_value={}), \
             mock.patch.object(L, "panes", return_value=[]), \
             mock.patch.object(L, "workspaces", return_value=[]):
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                orch.cmd_ls("text")
            text = buf.getvalue()
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                orch.cmd_ls("toon")
        line = next(ln for ln in text.splitlines() if ln.startswith("job1@laptop"))
        self.assertIn("busy", line)
        self.assertIn("claude", line)
        self.assertTrue(line.rstrip().endswith("laptop"))
        self.assertIn("job1@laptop", buf.getvalue())

    def test_no_hosts_file_means_ls_never_calls_ssh(self):
        os.remove(os.path.join(self.board, "hosts.json"))
        with mock.patch.object(R.subprocess, "run", side_effect=AssertionError("ssh was called")):
            self.assertEqual(([], []), R.ls_rows())


class TestPull(FakeHostCase):
    def test_pull_copies_new_reports_once_and_prints_the_done_line(self):
        self.start()
        out = os.path.join(self.home, ".claude", "handoffs", "outbox", "report-job1.md")
        self.assertTrue(self.wait_for(lambda: os.path.exists(out)))
        new = R.pull("laptop")
        self.assertEqual([("report-job1.md", "Done: fake task finished")], new)
        self.assertTrue(os.path.exists(os.path.join(self.handoffs, "remote", "laptop", "report-job1.md")))
        self.assertEqual([], R.pull("laptop"))                        # already seen by name

    def test_pull_never_overwrites_a_local_handoff(self):
        mine = os.path.join(self.handoffs, "report-job1.md")
        open(mine, "w").write("local")
        self.start()
        self.assertTrue(self.wait_for(lambda: os.path.exists(
            os.path.join(self.home, ".claude", "handoffs", "outbox", "report-job1.md"))))
        R.pull("laptop")
        self.assertEqual("local", open(mine).read())

    def test_a_re_run_report_with_the_same_name_is_pulled_again(self):
        out = os.path.join(self.home, ".claude", "handoffs", "outbox")
        os.makedirs(out)
        open(os.path.join(out, "report-a.md"), "w").write("Done: first\n")
        self.assertEqual([("report-a.md", "Done: first")], R.pull("laptop"))
        open(os.path.join(out, "report-a.md"), "w").write("Done: second run, longer\n")
        self.assertEqual([("report-a.md", "Done: second run, longer")], R.pull("laptop"))

    def test_a_file_still_being_written_is_left_for_the_next_pull(self):
        out = os.path.join(self.home, ".claude", "handoffs", "outbox")
        os.makedirs(out)
        open(os.path.join(out, "report-b.md"), "w").write("Done: x\n")
        with mock.patch.dict(os.environ, {"ORCH_REMOTE_MIN_AGE": "3600"}):
            self.assertEqual([], R.pull("laptop"))
        self.assertEqual(1, len(R.pull("laptop")))

    def test_a_sync_path_outside_handoffs_is_refused(self):
        for bad in ("Vault/vault", ".claude/handoffs/../../secret", ".claude/settings.json",
                    ".claude/handoffs/a b", ".claude/handoffs/x;rm", ".claude/handoffs/$(id)"):
            self.write_hosts({"laptop": {"ssh": "x", "home": "C:/h", "sync": [bad]}})
            with self.assertRaises(R.HostError, msg=bad):
                R.pull("laptop")

    def test_host_list_refuses_a_path_outside_handoffs(self):
        with self.assertRaises(SystemExit):
            R.host_list("Vault/vault")


class TestKill(FakeHostCase):
    def test_kill_stops_the_process_over_ssh(self):
        os.environ["FAKE_CLAUDE_SLEEP"] = "60"
        self.start()
        pid = json.load(open(os.path.join(self.home, R.STATE_DIR, "job1.json")))["pid"]
        self.assertTrue(L.pid_alive(pid))
        self.assertIn("killed job1", R.kill("laptop", "job1"))
        self.assertTrue(self.wait_for(lambda: not L.pid_alive(pid)))
        self.assertEqual("killed", R.ls_rows()[0][0][1])

    def test_kill_through_orch_accepts_name_at_host(self):
        os.environ["FAKE_CLAUDE_SLEEP"] = "60"
        self.start()
        buf = io.StringIO()
        with mock.patch.object(sys, "argv", ["orch.py", "kill", "job1@laptop"]), \
             mock.patch.object(L, "owner_is_live", return_value=False), \
             contextlib.redirect_stdout(buf):
            orch.main()
        self.assertIn("killed job1", buf.getvalue())

    def test_split_name(self):
        self.assertEqual(("job1", "laptop"), R.split_name("job1@laptop"))
        self.assertEqual(("job1", "laptop"), R.split_name("job1", "laptop"))
        self.assertEqual(("a@b", None), R.split_name("a@b"))          # b is not a host
        self.assertEqual(("w1", None), R.split_name("w1"))


class TestUnreachable(FakeHostCase):
    def test_ssh_timeout_shows_cached_rows_as_unreachable_never_vanished(self):
        os.environ["FAKE_CLAUDE_SLEEP"] = "30"
        self.start()
        R.ls_rows(now=1000.0)                                         # caches the good snapshot
        os.environ["FAKE_MODE"] = "timeout"
        rows, notes = R.ls_rows(now=1010.0)
        self.assertEqual("UNREACHABLE", rows[0][1])
        self.assertEqual("laptop", rows[0][9])
        self.assertNotIn("VANISHED", repr(rows) + repr(notes))
        self.assertIn("unreachable (timeout", notes[0])
        self.assertIn("last seen 10s ago", notes[0])

    def test_a_dead_host_is_not_asked_again_for_a_minute(self):
        t = time.time()
        os.environ["FAKE_MODE"] = "timeout"
        R.ls_rows(now=t)
        with mock.patch.object(R.subprocess, "run", side_effect=AssertionError("asked again")):
            rows, notes = R.ls_rows(now=t + 30)
        self.assertIn("never seen", notes[0])
        os.environ["FAKE_MODE"] = "ok"
        rows, notes = R.ls_rows(now=t + 61)                           # window over: asks, host is back
        self.assertEqual([], notes)

    def test_task_and_pull_and_kill_refuse_with_the_reason_when_unreachable(self):
        os.environ["FAKE_MODE"] = "timeout"
        for call in (lambda: self.start(), lambda: R.pull("laptop"), lambda: R.kill("laptop", "x")):
            with self.assertRaises(R.HostError) as cm:
                call()
            self.assertEqual("timeout", cm.exception.kind)
            self.assertIn(cm.exception.kind, R.UNREACHABLE_KINDS)

    def test_a_garbled_host_answer_does_not_take_down_ls(self):
        for bad in ('[1, 2]', '{"epoch": 1, "rows": [5]}', '{"epoch": 1, "rows": [{}]}', "null"):
            with mock.patch.object(R, "ssh", return_value=bad):
                rows, notes = R.ls_rows()
            self.assertEqual([], rows, bad)
            self.assertIn("bad answer from host", notes[0], bad)

    def test_clock_skew_over_a_minute_is_noted(self):
        answer = json.dumps({"epoch": time.time() + 95, "rows": []})
        with mock.patch.object(R, "ssh", return_value=answer):
            rows, notes = R.ls_rows()
        self.assertEqual([], rows)
        self.assertIn("clock skew +9", notes[0])

    def test_each_ssh_failure_text_is_classified(self):
        for mode, kind, word in (("timeout", "timeout", "timeout"), ("refused", "refused", "sshd"),
                                 ("key", "key", "key refused"), ("hostkey", "hostkey", "host key")):
            os.environ["FAKE_MODE"] = mode
            with self.assertRaises(R.HostError, msg=mode) as cm:
                self.start(f"j-{mode}")
            self.assertEqual(kind, cm.exception.kind)
            self.assertIn(word, cm.exception.message)

    def test_orch_exits_with_one_unreachable_line_not_a_traceback(self):
        os.environ["FAKE_MODE"] = "key"
        with mock.patch.object(sys, "argv", ["orch.py", "pull", "--host", "laptop"]):
            with self.assertRaises(SystemExit) as cm:
                orch.main()
        self.assertIn("UNREACHABLE", str(cm.exception.code))


class TestOnlySshFailuresAreClassified(FakeHostCase):
    """classify() reads ssh's own text, so it runs only on ssh's own exit code, 255."""

    def run_rc(self, rc, err="", out=""):
        return mock.patch.object(R, "_run", return_value=mock.Mock(
            returncode=rc, stderr=err, stdout=out))

    def test_a_remote_permission_error_is_a_remote_failure_not_a_key_failure(self):
        with self.run_rc(1, "PermissionError: [Errno 13] Permission denied: 'x'"):
            with self.assertRaises(R.HostError) as cm:
                R.ssh(R.get_host("laptop"), "cmd")
        self.assertEqual("remote", cm.exception.kind)
        self.assertNotIn(cm.exception.kind, R.UNREACHABLE_KINDS)
        with self.run_rc(1, "scp: /x: Permission denied"):
            with self.assertRaises(R.HostError) as cm:
                R.scp(R.get_host("laptop"), "a", "b")
        self.assertEqual("remote", cm.exception.kind)

    def test_ls_does_not_mark_a_host_unreachable_for_remote_text(self):
        with self.run_rc(1, "x: Permission denied"):
            rows, notes = R.ls_rows()
        self.assertNotIn("UNREACHABLE", repr(rows) + repr(notes))
        self.assertFalse(R._load(R._cache_path()).get("laptop", {}).get("fail"))

    def test_a_clean_run_that_prints_a_banner_with_timed_out_is_fine(self):
        with self.run_rc(0, "warning: previous login timed out", "ok"):
            self.assertEqual("ok", R.ssh(R.get_host("laptop"), "cmd"))

    def test_rc_255_is_still_classified(self):
        with self.run_rc(255, "x: Permission denied (publickey)."):
            with self.assertRaises(R.HostError) as cm:
                R.ssh(R.get_host("laptop"), "cmd")
        self.assertEqual("key", cm.exception.kind)


class TestKillOwnerGate(FakeHostCase):
    def kill_main(self, *extra):
        buf = io.StringIO()
        with mock.patch.object(sys, "argv", ["orch.py", "kill", "job1@laptop", *extra]), \
             mock.patch.object(L, "caller_name", return_value="other-lead"), \
             mock.patch.object(L, "owner_is_live", return_value=True), \
             mock.patch.object(orch, "_board_done"), contextlib.redirect_stdout(buf):
            orch.main()
        return buf.getvalue()

    def pid(self):
        return json.load(open(os.path.join(self.home, R.STATE_DIR, "job1.json")))["pid"]

    def test_the_host_reports_who_owns_the_mate(self):
        os.environ["FAKE_CLAUDE_SLEEP"] = "60"
        self.start()
        self.assertEqual("axi-lead", R.owner("laptop", "job1"))

    def test_another_live_sessions_mate_is_refused_without_force(self):
        os.environ["FAKE_CLAUDE_SLEEP"] = "60"
        self.start()
        with self.assertRaises(SystemExit) as cm:
            out = self.kill_main()
        self.assertEqual(1, cm.exception.code)
        self.assertTrue(L.pid_alive(self.pid()))

    def test_force_kills_it_and_the_owner_may_kill_its_own(self):
        os.environ["FAKE_CLAUDE_SLEEP"] = "60"
        self.start()
        pid = self.pid()
        self.assertIn("FORCED kill of job1@laptop", self.kill_main("--force"))
        self.assertTrue(self.wait_for(lambda: not L.pid_alive(pid)))
        os.environ["FAKE_CLAUDE_SLEEP"] = "60"
        self.start("job2")
        buf = io.StringIO()
        with mock.patch.object(sys, "argv", ["orch.py", "kill", "job2@laptop"]), \
             mock.patch.object(L, "caller_name", return_value="axi-lead"), \
             mock.patch.object(L, "owner_is_live", return_value=True), contextlib.redirect_stdout(buf):
            orch.main()
        self.assertIn("killed job2", buf.getvalue())

    def test_an_unknown_mate_is_one_clear_line(self):
        with self.assertRaises(SystemExit) as cm:
            self.kill_main()
        self.assertIn("no mate", str(cm.exception.code))


class TestPidReuse(FakeHostCase):
    def state_path(self):
        return os.path.join(self.home, R.STATE_DIR, "job1.json")

    def test_start_saves_the_process_start_time(self):
        os.environ["FAKE_CLAUDE_SLEEP"] = "60"
        self.start()
        s = json.load(open(self.state_path()))
        self.assertIsNotNone(s["pstart"])
        self.assertEqual(s["pstart"], R.proc_start(s["pid"]))

    def test_a_reused_pid_reads_ended_and_kill_leaves_it_alone(self):
        os.environ["FAKE_CLAUDE_SLEEP"] = "60"
        self.start()
        s = json.load(open(self.state_path()))
        stranger = s["pid"]                                  # alive, but not the process we started
        s["pstart"] = s["pstart"] + 1 if isinstance(s["pstart"], int) else "not the same start"
        json.dump(s, open(self.state_path(), "w"))
        self.assertEqual("failed", R.ls_rows()[0][0][1])
        out = R.kill("laptop", "job1")
        self.assertIn("not killed", out)
        self.assertTrue(L.pid_alive(stranger))
        self.assertNotIn("killed", json.load(open(self.state_path())))


class TestLowItems(FakeHostCase):
    def test_ls_gives_a_hung_host_only_the_short_timeout(self):
        with mock.patch.object(R.subprocess, "run",
                               side_effect=R.subprocess.TimeoutExpired("ssh", 1)) as run:
            rows, notes = R.ls_rows()
        self.assertEqual(R.ROWS_TIMEOUT + R.CONNECT_TIMEOUT, run.call_args.kwargs["timeout"])
        self.assertLessEqual(R.ROWS_TIMEOUT + R.CONNECT_TIMEOUT, 20)
        self.assertIn("unreachable (timeout", notes[0])

    def test_pull_with_a_garbled_list_answer_is_a_host_error_not_a_traceback(self):
        for bad in ("not json", "{}", '[{"name": "a"}]', "[1]"):
            with mock.patch.object(R, "ssh", return_value=bad):
                with self.assertRaises(R.HostError, msg=bad) as cm:
                    R.pull("laptop")
            self.assertEqual("remote", cm.exception.kind)

    def test_the_status_column_fits_unreachable(self):
        os.environ["FAKE_CLAUDE_SLEEP"] = "30"
        self.start()
        R.ls_rows()
        c = R._load(R._cache_path())
        c["laptop"]["fail_ts"], c["laptop"]["fail"], c["laptop"]["reason"] = time.time(), "timeout", "t"
        R._save(R._cache_path(), c)
        buf = io.StringIO()
        with mock.patch.object(L, "panes_by_sid", return_value={}), \
             mock.patch.object(L, "sessions", return_value={}), \
             mock.patch.object(L, "sidecar_load", return_value={}), \
             mock.patch.object(L, "other_panes", return_value={}), \
             mock.patch.object(L, "panes", return_value=[]), \
             mock.patch.object(L, "workspaces", return_value=[]), contextlib.redirect_stdout(buf):
            orch.cmd_ls("text")
        lines = buf.getvalue().splitlines()
        row = next(ln for ln in lines if ln.startswith("job1@laptop"))
        self.assertEqual("UNREACHABLE", row.split()[1])
        # "ctx=" + 4 chars is 2 wider than the 6-wide CTX header; any more means STATUS overflowed
        self.assertEqual(2, row.index("claude") - lines[0].index("HARNESS"))


class TestWiring(FakeHostCase):
    def test_a_remote_task_needs_no_rule_in_the_dispatch_gate(self):
        a = ["task", "job1", "--host", "laptop", "--goal", "g", "--done", "d"]
        decision, _msg, rec = DR.decide("task", a, DR.load_table(), env={})
        self.assertEqual("allow", decision)
        self.assertEqual("remote host", rec["why"])
        decision, _m, _r = DR.decide("task", ["task", "job1", "--goal", "g"], DR.load_table(), env={})
        self.assertEqual("deny", decision)                            # a local task still needs one

    def test_task_host_through_orch_main(self):
        os.environ["FAKE_CLAUDE_SLEEP"] = "30"
        buf = io.StringIO()
        argv = ["orch.py", "task", "job2", "--host", "laptop", "--goal", "g", "--done", "d",
                "--owner", "axi-lead", "--read", "a.md", "--not", "no vault"]
        with mock.patch.object(sys, "argv", argv), contextlib.redirect_stdout(buf):
            orch.main()
        self.assertIn("started job2@laptop", buf.getvalue())
        task = open(os.path.join(self.home, ".claude", "handoffs", "inbox", "task-job2.md"),
                    encoding="utf-8").read()
        self.assertIn("- a.md", task)
        self.assertIn("- no vault", task)


@unittest.skipUnless(os.name == "nt", "creation flags are Windows-only")
class TestHostStartFlags(FakeHostCase):
    def test_host_start_hides_the_console_and_never_detaches(self):
        os.environ["ORCH_REMOTE_HOME"] = self.home
        popen = mock.MagicMock(return_value=mock.Mock(pid=4242))
        with mock.patch.object(R.subprocess, "Popen", popen), mock.patch.object(R, "proc_start", return_value=1):
            R.host_start("job1", ".claude/handoffs/inbox/task-job1.md", "sonnet", "axi-lead")
        flags = popen.call_args.kwargs["creationflags"]
        self.assertTrue(flags & 0x08000000)                           # CREATE_NO_WINDOW
        self.assertTrue(flags & 0x00000200)                           # CREATE_NEW_PROCESS_GROUP
        self.assertFalse(flags & 0x00000008)                          # never DETACHED_PROCESS


if __name__ == "__main__":
    unittest.main()
