"""Regression test for the board's group/tab logic (board-tabs, 2026-09-04).

No herdr needs to be running: every case below either passes --group explicitly or relies on
inheritance from a question/session already on the board, so `group_for_who()`'s live pane lookup
is exercised on its empty-result path only (no live panes to find -> "").

Runs under `py -m unittest discover` and standalone (`py test_board_groups.py`). Every board.py
CLI call goes through a subprocess with its own ORCH_BOARD_DIR env var, so those never touch the
real board/state.json and never collide with another test's process. The in-process `sync_board`
calls patch board.py's own module-level path constants (STATE, HTML, ...) inside a scoped
`mock.patch.object` context instead of mutating ORCH_BOARD_DIR and reloading the module - unittest
discovery imports `board` (and the `orchlib` it wraps) once, up front, for other test files, so a
global env mutation + reload here previously left `board.STATE` pointed at the wrong directory for
every test that ran after this one, and `sync_board`'s `next(... p1 ...)` raised StopIteration.
"""
import json, os, subprocess, sys, tempfile, unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
BOARD_PY = os.path.join(HERE, "board.py")

sys.path.insert(0, HERE)
import orchlib as L          # noqa: E402
import board as B            # noqa: E402


def run_cli(tmpdir, *args):
    env = dict(os.environ, ORCH_BOARD_DIR=tmpdir, PODIUM_SLACK="0")
    r = subprocess.run([sys.executable, BOARD_PY, *args], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", env=env, timeout=60)
    if r.returncode != 0:
        raise AssertionError(f"board.py {args} exited {r.returncode}\n{r.stdout}\n{r.stderr}")
    return r.stdout


def read_state(tmpdir):
    return json.load(open(os.path.join(tmpdir, "state.json"), encoding="utf-8"))


class TestBoardGroupsCLI(unittest.TestCase):
    """The subprocess-driven scenario: add-question/show/done/answer/session/render/prune all
    going through board.py's real CLI entry point, each call against its own isolated
    ORCH_BOARD_DIR so nothing here ever touches the real board."""

    def setUp(self):
        self.tmp_ctx = tempfile.TemporaryDirectory()
        self.tmp = self.tmp_ctx.name
        self.addCleanup(self.tmp_ctx.cleanup)

    def test_group_inheritance_and_overrides(self):
        tmp = self.tmp
        # explicit --group on add-question
        run_cli(tmp, "add-question", "--id", "q1", "--title", "t1", "--context", "c1",
                "--group", "webapp")
        s = read_state(tmp)
        self.assertEqual(next(q for q in s["questions"] if q["id"] == "q1")["group"],
                         "webapp", "add-question --group is stored verbatim")

        # a question with no --group and no live pane resolves to "" (Needs-you strip, no tab)
        run_cli(tmp, "add-question", "--id", "q2", "--title", "t2", "--context", "c2",
                "--from", "no-such-live-pane")
        s = read_state(tmp)
        self.assertEqual(next(q for q in s["questions"] if q["id"] == "q2")["group"], "",
                         "unresolvable --from should fall back to '' (All only)")

        # show inherits its group from --for's question when no --group is given
        run_cli(tmp, "show", "--caption", "cap1", "--text", "body1", "--for", "q1")
        s = read_state(tmp)
        self.assertEqual(s["show"][0]["group"], "webapp",
                         "show --for should inherit the question's group")

        # show's own --group wins over inheritance
        run_cli(tmp, "show", "--caption", "cap2", "--text", "body2", "--for", "q1",
                "--group", "fh-ideas")
        s = read_state(tmp)
        self.assertEqual(s["show"][0]["group"], "fh-ideas",
                         "show --group should override --for inheritance")

        # done with --group
        run_cli(tmp, "done", "shipped it", "--group", "routines")
        s = read_state(tmp)
        self.assertEqual(s["done"][0]["group"], "routines", "done --group should be stored")

        # answering a question appends a done line that inherits the question's group
        run_cli(tmp, "answer", "q1", "A")
        s = read_state(tmp)
        ans_line = next(d for d in s["done"] if d["text"].startswith("Q1 "))
        self.assertEqual(ans_line["group"], "webapp",
                         "answer's done line should inherit the question's group")

        # session rows carry a group key (empty without a live pane), never a KeyError
        run_cli(tmp, "session", "some-pane", "--doing", "x", "--state", "working")
        s = read_state(tmp)
        self.assertIn("group", s["sessions"][0], "session row should always have a group key")

    def test_legacy_state_backfills_group_without_crashing(self):
        tmp = self.tmp
        legacy = {
            "updated": "2026-01-01T00:00:00-08:00", "header": {"usage": "", "note": ""},
            "questions": [{"id": "qx", "title": "old", "from": "", "context_md": "", "options": [],
                           "pick": "", "pick_why": "", "inputs": [],
                           "created": "2026-01-01T00:00:00-08:00", "answered": None, "answer": None}],
            "show": [], "sessions": [], "done": [],
        }
        json.dump(legacy, open(os.path.join(tmp, "state.json"), "w", encoding="utf-8"))
        run_cli(tmp, "render")
        s = read_state(tmp)
        self.assertEqual(s["questions"][0].get("group"), "",
                         "a legacy question with no group key should render clean, not crash")

        # The group tab bar went away in the 2026-09-13 redesign: everything is on screen at once,
        # so a filter that hides part of it had nothing left to do. Grouping did not go away - it
        # moved into the questions column itself, one headed block per project, which is what
        # these two assertions now guard.
        html = open(os.path.join(tmp, "board.html"), encoding="utf-8").read()
        self.assertIn("'ghead'", html)
        self.assertIn("q.group || 'no group'", html,
                      "board.html should group the questions column by project")
        self.assertNotIn('id="tabs"', html)
        self.assertNotIn("function renderTabs", html, "board.html should have no leftover tab filter")

    def test_next_qid_never_reuses_a_pruned_id(self):
        # next_qid never reuses an id `prune` has already dropped from questions[] (2026-09-04:
        # two unrelated auto-posted questions both landing on "Q1" read as the same question)
        fresh = os.path.join(self.tmp, "fresh")
        os.makedirs(fresh, exist_ok=True)
        run_cli(fresh, "add-question", "--id", "q1", "--title", "first", "--context", "c")
        run_cli(fresh, "answer", "q1", "A")
        run_cli(fresh, "prune", "--days", "0")
        s = read_state(fresh)
        self.assertFalse(s["questions"], "prune should have dropped the answered question")
        run_cli(fresh, "add-question", "--title", "second, id picked automatically", "--context", "c")
        s = read_state(fresh)
        self.assertNotEqual(s["questions"][0]["id"], "q1",
                            "next auto-id must not reuse the pruned q1")


class TestSyncBoard(unittest.TestCase):
    """sync_board() is called in-process by watch_sessions.py's poll loop, so this exercises the
    real already-imported `board` module rather than a subprocess - patching its path constants
    scoped to this test instead of mutating ORCH_BOARD_DIR/reloading, so the patch never leaks
    into another test that imported `board` earlier under discovery."""

    def setUp(self):
        self.tmp_ctx = tempfile.TemporaryDirectory()
        fresh = os.path.join(self.tmp_ctx.name, "fresh")
        os.makedirs(fresh, exist_ok=True)
        self.fresh = fresh
        self.addCleanup(self.tmp_ctx.cleanup)
        patches = [
            mock.patch.object(B, "BOARD", fresh),
            mock.patch.object(B, "STATE", os.path.join(fresh, "state.json")),
            mock.patch.object(B, "STATEJS", os.path.join(fresh, "state.js")),
            mock.patch.object(B, "HTML", os.path.join(fresh, "board.html")),
            mock.patch.object(B, "PLAIN", os.path.join(fresh, "board-plain.html")),
            mock.patch.dict(os.environ, {"PODIUM_SLACK": "0"}),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def test_one_call_merges_session_rows_by_pane_instead_of_duplicating(self):
        B.sync_board(session_rows=[{"pane": "p1", "doing": "x", "model": "sonnet",
                                     "state": "working", "note": "", "group": "g1", "updated": ""}])
        B.sync_board(session_rows=[{"pane": "p1", "state": "done"}])
        s = read_state(self.fresh)
        rows = [r for r in s["sessions"] if r["pane"] == "p1"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["state"], "done")
        self.assertEqual(rows[0]["doing"], "x")

    def test_new_updates_are_prepended_newest_first(self):
        B.sync_board(new_updates=[{"ts": "t1", "pane": "p1", "kind": "DONE", "text": "first",
                                    "group": ""}])
        B.sync_board(new_updates=[{"ts": "t2", "pane": "p1", "kind": "DONE", "text": "second",
                                    "group": ""}])
        s = read_state(self.fresh)
        self.assertEqual(s["updates"][0]["text"], "second")


class TestLooksLikeARealQuestion(unittest.TestCase):
    """looks_like_a_real_question: the auto-post gate, narrower than classify_words' QUESTION."""

    def test_a_routine_status_update_is_not_a_real_question(self):
        self.assertFalse(L.looks_like_a_real_question(
            "Nothing to do right now, waiting on your next instruction."))

    def test_holding_for_go_is_not_a_real_question(self):
        self.assertFalse(L.looks_like_a_real_question("I am holding for go."))

    def test_a_literal_question_mark_is_a_real_question(self):
        self.assertTrue(L.looks_like_a_real_question("Should I ship this to prod?"))

    def test_an_explicit_decision_ask_is_a_real_question_with_no_question_mark(self):
        self.assertTrue(L.looks_like_a_real_question("Your call on which option to take."))


if __name__ == "__main__":
    unittest.main()
