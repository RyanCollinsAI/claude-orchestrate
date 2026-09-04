"""Tests for the orch-fixes-1 bugs, run with `py test_orch.py` (or `python3` on Mac/Linux).

Plain unittest, no extra dependencies except a `node` on PATH for the board.py security test -
that one test is skipped, not failed, if node is missing.

  ORC-001  ls must never print zero bytes on an empty registry
  ORC-002  spawn must not report success on a pane with no live agent
  ORC-022  log_path() falls back to a glob search across every per-cwd folder;
           resume calls a session DEAD only when herdr has no pane for it at all
  ORC-042  board.py's markdown renderer escapes the $$math$$ body and sanitizes the
           final HTML, so a literal <script> in an agent's message never runs
  doctor   the orchestrator-tab and claude-auth lines say found/not found with a remedy
  chrome   a bare `chrome free` (no name) can never drop someone else's lock
"""
import contextlib, io, json, os, re, shutil, subprocess, sys, tempfile, unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import orchlib as L
import orch

HERE = os.path.dirname(os.path.abspath(__file__))


class TestLsNeverPrintsNothing(unittest.TestCase):
    """ORC-001: a fresh install's first command printed zero bytes when no session was live."""

    def test_empty_registry_prints_header_and_a_reason(self):
        with mock.patch.object(L, "panes_by_sid", return_value={}), \
             mock.patch.object(L, "sessions", return_value={}), \
             mock.patch.object(L, "sidecar_load", return_value={}), \
             mock.patch.object(L, "agent_panes", return_value={}):
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                orch.cmd_ls()
            out = buf.getvalue()
        self.assertTrue(out.strip(), "ls printed zero bytes on an empty registry")
        self.assertIn("no sessions found", out)
        self.assertIn(L.SESS, out)          # says where it looked, not just that it found nothing


class TestSpawnVerifiesTheAgent(unittest.TestCase):
    """ORC-002: spawn printed `spawned ... sid=...` and exited 0 on a pane holding a bare shell."""

    def test_no_agent_on_the_pane_is_not_alive(self):
        with mock.patch.object(L, "panes", return_value=[{"pane_id": "w3:p2T"}]):
            self.assertFalse(orch._agent_alive("w3:p2T", "260fb3c2"))

    def test_mismatched_session_id_is_not_alive(self):
        pane = {"pane_id": "w3:p2T", "agent": "claude", "agent_session": {"value": "other-sid"}}
        with mock.patch.object(L, "panes", return_value=[pane]):
            self.assertFalse(orch._agent_alive("w3:p2T", "expected-sid"))

    def test_matching_live_claude_agent_is_alive(self):
        pane = {"pane_id": "w3:p2T", "agent": "claude", "agent_session": {"value": "sid123"}}
        with mock.patch.object(L, "panes", return_value=[pane]):
            self.assertTrue(orch._agent_alive("w3:p2T", "sid123"))


class TestLogPathFallback(unittest.TestCase):
    """ORC-022: a session's log wasn't under the registry's cwd (or had none), so log_path()
    returned a path that didn't exist and every reader silently saw ctx=0k / no model."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        projects = os.path.join(self.tmp.name, "projects")
        os.makedirs(projects)
        patches = [
            mock.patch.object(L, "PROJECTS_DIR", projects),
            mock.patch.object(L, "LOGS", os.path.join(projects, "default-cwd-slug")),
            mock.patch.object(L, "_CWD_CACHE", {}),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.projects = projects

    def test_finds_the_log_in_another_cwd_via_the_glob_fallback(self):
        sid = "cross-cwd-sid-0001"
        other_dir = os.path.join(self.projects, "C--Users-someone-OtherRepo")
        os.makedirs(other_dir)
        log = os.path.join(other_dir, sid + ".jsonl")
        open(log, "w", encoding="utf-8").write("{}\n")
        with mock.patch.object(L, "sessions", return_value={sid: {"sessionId": sid, "name": "x"}}):
            found = L.log_path(sid)
        self.assertEqual(os.path.normcase(found), os.path.normcase(log))

    def test_no_log_anywhere_returns_a_path_without_raising(self):
        sid = "nonexistent-sid-0002"
        with mock.patch.object(L, "sessions", return_value={}):
            found = L.log_path(sid)          # must never sys.exit/raise; ls calls this per row
        self.assertTrue(found.endswith(sid + ".jsonl"))


class TestResumeDeadOnlyWhenPaneGone(unittest.TestCase):
    """ORC-022: `resume` called a session DEAD whenever ctx read 0k, which is what a session in
    another cwd looked like before the log_path fallback - a busy, healthy pane got told to be
    killed and respawned. DEAD should mean herdr has no pane for it, nothing softer."""

    def test_pane_present_but_ctx_unreadable_is_not_dead(self):
        sid = "s1"
        with mock.patch.object(L, "panes_by_sid", return_value={sid: {"pane_id": "w3:p1"}}), \
             mock.patch.object(L, "orchestrator_sid", return_value=None), \
             mock.patch.object(L, "sessions", return_value={sid: {"name": "n1", "status": "busy"}}), \
             mock.patch.object(L, "last_usage", return_value=(0, "")), \
             mock.patch.object(L, "classify", return_value=("DONE", "")), \
             mock.patch.object(L, "pane_read", return_value=""), \
             mock.patch.object(L, "pending_prompt", return_value=""), \
             mock.patch.object(L, "pane_mode", return_value=""):
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                orch.cmd_resume()
            out = buf.getvalue()
        self.assertNotIn("DEAD  n1", out)
        self.assertIn("OK    n1", out)

    def test_no_pane_at_all_is_dead(self):
        sid = "s2"
        with mock.patch.object(L, "panes_by_sid", return_value={}), \
             mock.patch.object(L, "orchestrator_sid", return_value=None), \
             mock.patch.object(L, "sessions", return_value={sid: {"name": "n2", "status": "idle"}}), \
             mock.patch.object(L, "last_usage", return_value=(0, "")):
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                orch.cmd_resume()
            out = buf.getvalue()
        self.assertIn("DEAD  n2", out)


class TestDoctorRemedies(unittest.TestCase):
    """ORC-006/007: a BAD line said `OK ... looking for 'orchestrator'` or `none / ?` - true or
    false, and no remedy either way."""

    def test_bad_lines_say_not_found_and_carry_a_fix(self):
        def fake_herdr(*args, **kwargs):
            if args[:2] == ("tab", "list"):
                return {"result": {"tabs": [{"label": "some-other-tab"}]}}
            return None

        def fake_run(cmd, *a, **k):
            r = mock.Mock()
            if cmd[0] == "claude":
                r.stdout, r.stderr, r.returncode = json.dumps({"loggedIn": False}), "", 1
            else:
                r.stdout, r.stderr, r.returncode = "", "", 1
            return r

        with mock.patch.object(L, "herdr", side_effect=fake_herdr), \
             mock.patch("orch.subprocess.run", side_effect=fake_run):
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                orch.cmd_doctor()
            out = buf.getvalue()
        self.assertIn("not found - run: herdr tab create --label", out)
        self.assertIn("not found - run: claude login", out)

    def test_good_lines_say_found(self):
        def fake_herdr(*args, **kwargs):
            if args[:2] == ("tab", "list"):
                return {"result": {"tabs": [{"label": L.ORCHESTRATOR_TAB}]}}
            return None

        def fake_run(cmd, *a, **k):
            r = mock.Mock()
            if cmd[0] == "claude":
                r.stdout = json.dumps({"loggedIn": True, "authMethod": "oauth",
                                       "subscriptionType": "max"})
                r.stderr, r.returncode = "", 0
            else:
                r.stdout, r.stderr, r.returncode = "", "", 1
            return r

        with mock.patch.object(L, "herdr", side_effect=fake_herdr), \
             mock.patch("orch.subprocess.run", side_effect=fake_run):
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                orch.cmd_doctor()
            out = buf.getvalue()
        self.assertIn(f"found tab '{L.ORCHESTRATOR_TAB}'", out)
        self.assertIn("found: oauth / max", out)


class TestChromeLockOwnership(unittest.TestCase):
    """A bare `chrome free` (no name) used to skip the ownership check entirely and silently
    release whoever held the lock - reproduced live 2026-09-04, right after this exact bug was
    reported: `take X && work && free` that forgot to repeat X dropped another session's lock."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        patch = mock.patch.object(L, "CHROME_LOCK", os.path.join(self.tmp.name, "chrome-lock.json"))
        patch.start()
        self.addCleanup(patch.stop)

    def test_take_refuses_when_someone_else_holds_it(self):
        ok, _ = L.chrome_take("holder-a")
        self.assertTrue(ok)
        ok, msg = L.chrome_take("holder-b")
        self.assertFalse(ok)
        self.assertIn("busy", msg)

    def test_free_with_no_name_no_longer_drops_someone_elses_lock(self):
        L.chrome_take("holder-a")
        ok, msg = L.chrome_free(None)
        self.assertFalse(ok, "a bare free must not release another session's lock")
        holder, _ = L.chrome_holder()
        self.assertEqual(holder, "holder-a")

    def test_free_with_the_wrong_name_is_refused(self):
        L.chrome_take("holder-a")
        ok, _ = L.chrome_free("holder-b")
        self.assertFalse(ok)
        self.assertEqual(L.chrome_holder()[0], "holder-a")

    def test_free_with_force_still_works(self):
        L.chrome_take("holder-a")
        ok, _ = L.chrome_free("force")
        self.assertTrue(ok)
        self.assertIsNone(L.chrome_holder()[0])

    def test_cli_refuses_a_bare_free_before_it_reaches_the_library(self):
        with self.assertRaises(SystemExit) as cm:
            orch.cmd_chrome("free", None)
        self.assertNotEqual(cm.exception.code, 0)


def _extract_board_js_functions():
    """Pull `esc` and `mdToHtml` out of board.py's embedded JS blob by brace-matching, so the
    security test below exercises the real shipped code, not a hand copy of it."""
    with open(os.path.join(HERE, "board.py"), encoding="utf-8") as fh:
        src = fh.read()
    m = re.search(r'^JS = r"""\n(.*?)\n"""\n', src, re.S | re.M)
    if not m:
        raise AssertionError("could not find the JS = r\"\"\" ... \"\"\" block in board.py")
    js = m.group(1)
    esc_m = re.search(r"^const esc = .*?;$", js, re.S | re.M)
    fn_m = re.search(r"^function mdToHtml\(src\)\{", js, re.M)
    if not (esc_m and fn_m):
        raise AssertionError("could not find esc()/mdToHtml() in board.py's JS block")
    depth, idx = 1, fn_m.end()
    while depth > 0:
        if js[idx] == "{":
            depth += 1
        elif js[idx] == "}":
            depth -= 1
        idx += 1
    return esc_m.group(0) + "\n" + js[fn_m.start():idx]


@unittest.skipUnless(shutil.which("node"), "node not on PATH")
class TestBoardMarkdownIsSanitized(unittest.TestCase):
    """ORC-042: board.py:477-478 interpolated the $$math$$ body unescaped while the mermaid branch
    two lines above escaped its own, and marked@14.1.3 does not sanitize raw HTML in the source -
    so a literal <script> in a session's last message ran as live script in the board."""

    HARNESS = """
const marked = {{ parse: (src) => src }};                 // identity: only sanitization matters
const DOMPurify = {{ sanitize: (html) => html
  .replace(/<script[\\s\\S]*?<\\/script>/gi, '')
  .replace(/\\son\\w+\\s*=\\s*"[^"]*"/gi, '') }};
{src}
const results = {{}};
results.script = mdToHtml('hello <script>alert(1)</script> world');
results.math = mdToHtml('$$<img src=x onerror=alert(1)>$$');
console.log(JSON.stringify(results));
"""

    def test_a_done_line_style_script_tag_renders_inert(self):
        js_src = _extract_board_js_functions()
        script = self.HARNESS.format(src=js_src)
        r = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 0, f"node harness crashed:\n{r.stderr}")
        results = json.loads(r.stdout.strip().splitlines()[-1])
        self.assertNotIn("<script", results["script"],
                          "a literal <script> tag in agent-supplied markdown was not sanitized")

    def test_the_math_block_body_is_escaped_not_interpolated_raw(self):
        js_src = _extract_board_js_functions()
        script = self.HARNESS.format(src=js_src)
        r = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 0, f"node harness crashed:\n{r.stderr}")
        results = json.loads(r.stdout.strip().splitlines()[-1])
        self.assertNotIn("<img", results["math"],
                          "the $$...$$ body reached the page as a raw tag, not escaped text (ORC-042)")
        self.assertIn("&lt;img", results["math"])


if __name__ == "__main__":
    unittest.main()
