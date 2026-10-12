"""py test_host_guard.py - host_guard: the verdict, the opt-in switches, the per-repo cap."""
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import host_guard as H  # noqa: E402

LIM = {"ram": 6, "commit": 90, "disk": 10, "cpu": 95}


class Judge(unittest.TestCase):
    def test_healthy_box_is_ok(self):
        v = H.judge(40, 38, 800, 9, LIM)
        self.assertEqual((v["verdict"], v["why"]), ("ok", []))

    def test_each_limit_alone_starves(self):
        for args, word in (((5.9, 10, 500, 10), "ram"), ((40, 90, 500, 10), "commit"),
                           ((40, 10, 9, 10), "disk"), ((40, 10, 500, 95), "cpu")):
            v = H.judge(*args, LIM)
            self.assertEqual(v["verdict"], "starved", word)
            self.assertEqual(len(v["why"]), 1)
            self.assertTrue(v["why"][0].startswith(word))

    def test_exact_floor_is_ok_for_ram_and_disk(self):
        self.assertEqual(H.judge(6, 10, 10, 10, LIM)["verdict"], "ok")

    def test_two_reasons_listed(self):
        self.assertEqual(len(H.judge(1, 99, 500, 10, LIM)["why"]), 2)

    def test_env_overrides_limits(self):
        with mock.patch.dict(os.environ, {"ORCH_MIN_FREE_GB": "50"}):
            self.assertEqual(H.limits()["ram"], 50)
            self.assertEqual(H.judge(40, 10, 500, 10)["verdict"], "starved")
        with mock.patch.dict(os.environ, {"ORCH_MIN_FREE_GB": "junk"}):
            self.assertEqual(H.limits()["ram"], H.MIN_FREE_GB)

    def test_line_is_one_line_with_numbers(self):
        s = H.line(H.judge(41.24, 38.4, 812.2, 9.1, LIM))
        self.assertNotIn("\n", s)
        self.assertEqual(s, "verdict: ok  ram_free_gb: 41.2  commit_pct: 38  disk_free_gb: 812  cpu_pct: 9")
        self.assertIn("why: ram 1.0 GB free < 6", H.line(H.judge(1, 10, 500, 10, LIM)))


class Switches(unittest.TestCase):
    def test_off_by_default(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("HOST_GUARD", None)
            self.assertFalse(H.enabled({}))
            self.assertEqual(H.note(cfg={}, measure_fn=lambda: self.fail("must not measure when off")), "")

    def test_config_or_env_turns_on(self):
        self.assertTrue(H.enabled({"enabled": True}))
        with mock.patch.dict(os.environ, {"HOST_GUARD": "on"}):
            self.assertTrue(H.enabled({}))

    def test_note_empty_when_ok_and_text_when_starved(self):
        ok = H.judge(40, 10, 500, 10, LIM)
        bad = H.judge(1, 10, 500, 10, LIM)
        self.assertEqual(H.note(cfg={"enabled": True}, measure_fn=lambda: ok), "")
        self.assertIn("box starved (ram 1.0", H.note(cfg={"enabled": True}, measure_fn=lambda: bad))

    def test_note_never_raises(self):
        def boom():
            raise OSError("x")
        self.assertEqual(H.note(cfg={"enabled": True}, measure_fn=boom), "")

    def test_config_reads_file_and_tolerates_junk(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "host-guard.json")
            self.assertEqual(H.config(p), {})
            with open(p, "w") as fh:
                fh.write("{not json")
            self.assertEqual(H.config(p), {})
            with open(p, "w") as fh:
                json.dump({"enabled": True, "repo_cap": 3}, fh)
            self.assertEqual(H.config(p), {"enabled": True, "repo_cap": 3})


class RepoCap(unittest.TestCase):
    key = staticmethod(lambda c: c.split("/")[0])

    def test_no_cap_allows_everything(self):
        self.assertEqual(H.spawn_over_cap("a/x", ["a/1"] * 50, cap=0, key_fn=self.key), "")

    def test_cap_off_when_guard_off(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("HOST_GUARD", None)
            os.environ.pop("ORCH_REPO_SPAWN_CAP", None)
            self.assertEqual(H.repo_cap({"repo_cap": 2}), 0)

    def test_cap_read_from_config_and_env(self):
        self.assertEqual(H.repo_cap({"enabled": True, "repo_cap": 2}), 2)
        with mock.patch.dict(os.environ, {"HOST_GUARD": "on", "ORCH_REPO_SPAWN_CAP": "5"}):
            self.assertEqual(H.repo_cap({}), 5)
        self.assertEqual(H.repo_cap({"enabled": True, "repo_cap": "junk"}), 0)

    def test_refuses_at_the_cap_only_for_that_repo(self):
        live = ["a/1", "a/2", "b/1", None]
        self.assertIn("cap is 2", H.spawn_over_cap("a/new", live, cap=2, key_fn=self.key))
        self.assertEqual(H.spawn_over_cap("a/new", live, cap=3, key_fn=self.key), "")
        self.assertEqual(H.spawn_over_cap("c/new", live, cap=1, key_fn=self.key), "")

    def test_worktrees_share_one_repo_key(self):
        with tempfile.TemporaryDirectory() as d:
            repo, wt = os.path.join(d, "repo"), os.path.join(d, "wt")
            git = lambda *a: subprocess.run(["git", "-C", repo, *a], check=True, capture_output=True)  # noqa: E731
            os.makedirs(repo)
            git("init", "-q")
            git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "--allow-empty", "-qm", "x")
            git("worktree", "add", "-q", "-b", "side", wt)
            self.assertEqual(H.repo_key(repo), H.repo_key(wt))
            self.assertNotEqual(H.repo_key(repo), H.repo_key(d))


class Cli(unittest.TestCase):
    def run_main(self, argv, verdict):
        buf = io.StringIO()
        with mock.patch.object(H, "measure", return_value=verdict), redirect_stdout(buf):
            code = H.main(argv)
        return code, buf.getvalue()

    def test_ok_exits_0_and_starved_exits_1(self):
        code, out = self.run_main([], H.judge(40, 10, 500, 10, LIM))
        self.assertEqual((code, out.count("\n")), (0, 1))
        self.assertEqual(self.run_main([], H.judge(1, 10, 500, 10, LIM))[0], 1)

    def test_json_flag(self):
        _, out = self.run_main(["--json"], H.judge(40, 10, 500, 10, LIM))
        self.assertEqual(json.loads(out)["verdict"], "ok")

    def test_unknown_flag_exits_2(self):
        err = io.StringIO()
        with mock.patch("sys.stderr", err):
            self.assertEqual(H.main(["--bogus"]), 2)
        self.assertIn("--help", err.getvalue())

    def test_help_exits_0(self):
        with redirect_stdout(io.StringIO()) as buf:
            self.assertEqual(H.main(["--help"]), 0)
        self.assertIn("never kills", buf.getvalue())


class Live(unittest.TestCase):
    def test_real_readings_are_sane(self):
        ram, commit = H.read_memory()
        self.assertGreater(ram, 0)
        self.assertTrue(0 < commit <= 100)
        self.assertGreater(H.read_disk(), 0)
        self.assertTrue(0 <= H.read_cpu(0.1) <= 100)


if __name__ == "__main__":
    unittest.main()
