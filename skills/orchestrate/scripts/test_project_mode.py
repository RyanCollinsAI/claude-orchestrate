"""Tests for project_mode.py, run with `py test_project_mode.py`.

  root      nearest .git wins; a worktree resolves to its main repo; a separate-git-dir tree is its own repo
  resolve   a listed repo gets its mode and yolo; an unlisted repo gets the default; no repo gives None
  registry  the real projects.json has every required repo, valid modes, and no yolo anywhere
  cli       prints `<mode> <on|off>`, exit 2 outside a repo
"""
import contextlib, io, json, os, shutil, subprocess, sys, tempfile, unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import project_mode as PM


def git(*args, cwd):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def mk(self, *parts):
        p = os.path.join(self.tmp, *parts)
        os.makedirs(p, exist_ok=True)
        return p

    def registry(self, projects, default=None):
        path = os.path.join(self.tmp, "projects.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"default": default or {"mode": "local-only", "yolo": False}, "projects": projects}, f)
        return path


class TestRoot(Base):
    def test_nearest_git_wins(self):
        outer = self.mk("outer")
        self.mk("outer", ".git")
        inner = self.mk("outer", "sub", "inner")
        self.mk("outer", "sub", "inner", ".git")
        deep = self.mk("outer", "sub", "inner", "a", "b")
        self.assertEqual(PM.repo_root(deep), inner)
        self.assertEqual(PM.repo_root(os.path.join(outer, "sub")), outer)

    def test_file_path_uses_its_folder(self):
        repo = self.mk("r")
        self.mk("r", ".git")
        f = os.path.join(repo, "x.txt")
        open(f, "w").close()
        self.assertEqual(PM.repo_root(f), repo)

    def test_no_repo_is_none(self):
        self.assertIsNone(PM.repo_root(self.mk("plain")))

    def test_separate_git_dir_tree_is_its_own_repo(self):
        tree = self.mk("tree")
        gitdir = self.mk("store", "Mad.git")
        with open(os.path.join(tree, ".git"), "w") as f:
            f.write(f"gitdir: {gitdir}\n")
        self.assertEqual(PM.repo_root(tree), tree)

    def test_real_worktree_resolves_to_main_repo(self):
        main = self.mk("main")
        git("init", "-q", cwd=main)
        git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "i", cwd=main)
        wt = os.path.join(self.tmp, "wt", "feat")
        git("worktree", "add", "-q", "-b", "feat", wt, cwd=main)
        self.assertTrue(os.path.isfile(os.path.join(wt, ".git")))
        self.assertEqual(PM.norm(PM.repo_root(wt)), PM.norm(main))
        sub = os.path.join(wt, "skills")
        os.makedirs(sub)
        self.assertEqual(PM.norm(PM.repo_root(sub)), PM.norm(main))


class TestResolve(Base):
    def test_listed_repo(self):
        repo = self.mk("cg")
        self.mk("cg", ".git")
        reg = self.registry([{"name": "cg", "path": repo, "mode": "direct-PR", "yolo": False}])
        self.assertEqual(PM.resolve(os.path.join(repo, "a"), reg)[:3], ("direct-PR", False, "cg"))

    def test_yolo_on(self):
        repo = self.mk("y")
        self.mk("y", ".git")
        reg = self.registry([{"name": "y", "path": repo, "mode": "no-mistakes", "yolo": True}])
        self.assertEqual(PM.resolve(repo, reg)[:2], ("no-mistakes", True))

    def test_unlisted_repo_gets_default(self):
        repo = self.mk("u")
        self.mk("u", ".git")
        reg = self.registry([])
        self.assertEqual(PM.resolve(repo, reg)[:3], ("local-only", False, None))

    def test_outside_repo_has_no_root(self):
        reg = self.registry([])
        self.assertIsNone(PM.resolve(self.mk("nope"), reg)[3])

    def test_bad_mode_is_refused(self):
        reg = self.registry([{"name": "b", "path": self.tmp, "mode": "yolo-mode", "yolo": False}])
        with self.assertRaises(ValueError):
            PM.load(reg)

    def test_tilde_path_matches(self):
        home = os.path.expanduser("~")
        self.assertEqual(PM.norm("~/x"), PM.norm(os.path.join(home, "x")))


class TestShippedRegistry(unittest.TestCase):
    def setUp(self):
        self.data = PM.load()
        self.by = {p["name"]: p for p in self.data["projects"]}

    def test_default_is_local_only_without_yolo(self):
        self.assertEqual((self.data["default"]["mode"], self.data["default"]["yolo"]), ("local-only", False))

    def test_no_repo_starts_with_yolo(self):
        self.assertEqual([n for n, p in self.by.items() if p["yolo"]], [])


class TestCli(Base):
    def run_cli(self, path):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = PM.main(["project_mode.py", path])
        return code, out.getvalue(), err.getvalue()

    def test_prints_mode_and_yolo(self):
        code, out, _ = self.run_cli(os.path.dirname(os.path.abspath(__file__)))
        self.assertEqual((code, out), (0, "local-only off\n"))

    def test_outside_repo_exits_2(self):
        code, out, err = self.run_cli(self.mk("plain"))
        self.assertEqual((code, out), (2, ""))
        self.assertIn("not inside a git repo", err)


if __name__ == "__main__":
    unittest.main()
