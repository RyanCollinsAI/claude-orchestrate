"""Tests for board-calm.html, the Calm inbox page written beside board.html (Bridge redesign, option A).

The page is additive: board.html and everything that writes it must come out byte-for-byte as before.
Every CLI call runs against its own ORCH_BOARD_DIR with Slack off; the in-process cases patch
board.py's path constants inside a scoped mock, the same way test_board_groups.py does.

    py -m pytest test_board_calm.py -q        or        py test_board_calm.py
"""
import os, subprocess, sys, tempfile, time, unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
BOARD_PY = os.path.join(HERE, "board.py")
sys.path.insert(0, HERE)
import board as B  # noqa: E402


def run_cli(tmpdir, *args):
    env = dict(os.environ, ORCH_BOARD_DIR=tmpdir, PODIUM_SLACK="0")
    r = subprocess.run([sys.executable, BOARD_PY, *args], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", env=env, timeout=60)
    if r.returncode != 0:
        raise AssertionError(f"board.py {args} exited {r.returncode}\n{r.stdout}\n{r.stderr}")
    return r.stdout


def read(path):
    return open(path, "rb").read()


class TestCalmPage(unittest.TestCase):
    def setUp(self):
        self.ctx = tempfile.TemporaryDirectory()
        self.tmp = self.ctx.name
        self.addCleanup(self.ctx.cleanup)
        self.calm = os.path.join(self.tmp, "board-calm.html")

    def test_render_writes_the_calm_page_with_the_live_contract(self):
        run_cli(self.tmp, "render")
        self.assertTrue(os.path.exists(self.calm), "render should write board-calm.html")
        html = read(self.calm).decode("utf-8")
        self.assertIn("podium-own-home", html[:8192], "own-home marker must sit in the first 8 KB")
        self.assertIn("Bridge", html[html.index("<body>"):], "the word Bridge belongs in the body")
        # the answer path, exactly as send() in board.html builds it
        self.assertIn("String(q.id).toUpperCase() + ' ' + (q.title || '') + ': ' + ans", html)
        self.assertIn("window.lavish.queuePrompt(prompt, {tag: 'answer', text: prompt, element: el || document.body,", html)
        self.assertIn("queueKey: q.id, data: {q: q.id, answer: ans}});", html)
        self.assertIn("/=\\s*(,|$)/.test(ans)", html, "half-filled answers are refused, as in send()")
        # state arrives by <script src>, re-render only when `updated` changed
        self.assertIn('<script src="state.js"></script>', html)
        self.assertIn("s.src = 'state.js?t=' + Date.now()", html)
        self.assertIn("s.updated !== state.updated", html)
        self.assertIn("setInterval(tick, POLL_MS)", html)
        self.assertNotIn("await fetch", html, "the sandboxed iframe cannot fetch a sibling file")
        self.assertNotIn("localStorage", html)
        self.assertNotIn("sessionStorage", html)
        self.assertIn("[hidden]{display:none!important}", html)
        self.assertNotIn("<details", html.lower())

    def test_stable_across_renders_and_blind_to_content(self):
        run_cli(self.tmp, "render")
        first, mt = read(self.calm), os.stat(self.calm).st_mtime_ns
        time.sleep(0.05)
        run_cli(self.tmp, "render")
        self.assertEqual(read(self.calm), first, "a second render must not change the bytes")
        self.assertEqual(os.stat(self.calm).st_mtime_ns, mt, "and must not touch the file at all")
        # content lives in state.js: adding a question and a done line must not touch the skeleton
        run_cli(self.tmp, "add-question", "--id", "q1", "--title", "t", "--context", "c",
                "--option", "A=yes", "--group", "g", "--time", "2 min", "--due", "Fri Oct 9")
        run_cli(self.tmp, "done", "something shipped")
        self.assertEqual(read(self.calm), first, "board content must never reach the calm skeleton")
        self.assertEqual(os.stat(self.calm).st_mtime_ns, mt)

    def test_optional_fields_only_when_given(self):
        run_cli(self.tmp, "add-question", "--id", "q1", "--title", "plain", "--context", "c", "--group", "g1")
        run_cli(self.tmp, "add-question", "--id", "q2", "--title", "rich", "--context", "c", "--group", "g2",
                "--time", "2 min", "--due", "Fri Oct 9", "--kind", "Housing", "--one", "One line.")
        import json
        qs = {q["id"]: q for q in json.load(open(os.path.join(self.tmp, "state.json"), encoding="utf-8"))["questions"]}
        for k in ("time", "due", "kind", "one"):
            self.assertNotIn(k, qs["q1"], f"{k} must not appear on a question added without it")
        self.assertEqual((qs["q2"]["time"], qs["q2"]["due"], qs["q2"]["kind"], qs["q2"]["one"]),
                         ("2 min", "Fri Oct 9", "Housing", "One line."))


class TestBoardHtmlUntouched(unittest.TestCase):
    """board.html and the save() path around it are exactly what they were."""

    def setUp(self):
        self.ctx = tempfile.TemporaryDirectory()
        self.fresh = os.path.join(self.ctx.name, "fresh")
        os.makedirs(self.fresh)
        self.addCleanup(self.ctx.cleanup)
        for p in (mock.patch.object(B, "BOARD", self.fresh),
                  mock.patch.object(B, "STATE", os.path.join(self.fresh, "state.json")),
                  mock.patch.object(B, "STATEJS", os.path.join(self.fresh, "state.js")),
                  mock.patch.object(B, "HTML", os.path.join(self.fresh, "board.html")),
                  mock.patch.object(B, "PLAIN", os.path.join(self.fresh, "board-plain.html")),
                  mock.patch.dict(os.environ, {"PODIUM_SLACK": "0"})):
            p.start()
            self.addCleanup(p.stop)

    def test_board_html_bytes_do_not_depend_on_the_calm_page(self):
        B.save(B.default_state())
        with_calm = read(os.path.join(self.fresh, "board.html"))
        self.assertTrue(os.path.exists(os.path.join(self.fresh, "board-calm.html")),
                        "the calm page follows HTML's directory, never a live path")
        os.remove(os.path.join(self.fresh, "board.html"))
        with mock.patch.object(B, "write_calm_html", lambda: None):
            B.save(B.default_state())
        self.assertEqual(read(os.path.join(self.fresh, "board.html")), with_calm,
                         "board.html must be byte-identical with and without the calm hook")

    def test_a_calm_page_failure_never_costs_the_board_write(self):
        with mock.patch.object(B, "write_calm_html", side_effect=OSError("disk says no")):
            B.save(B.default_state())
        self.assertTrue(os.path.exists(os.path.join(self.fresh, "state.json")))
        self.assertTrue(os.path.exists(os.path.join(self.fresh, "board.html")))


def _node():
    import shutil
    return shutil.which("node")


@unittest.skipUnless(_node(), "node is needed to run the page script")
class TestCalmPageScript(unittest.TestCase):
    """The page's own JS, run against a tiny fake DOM (calm_js_probe.js)."""

    @classmethod
    def setUpClass(cls):
        import json
        with tempfile.TemporaryDirectory() as d:
            js = os.path.join(d, "calm.js")
            open(js, "w", encoding="utf-8").write(B.CALM_JS.strip())
            r = subprocess.run([_node(), os.path.join(HERE, "calm_js_probe.js"), js], capture_output=True,
                               text=True, encoding="utf-8", errors="replace", timeout=60)
        if r.returncode != 0:
            raise AssertionError(r.stdout + r.stderr)
        cls.out = json.loads(r.stdout.strip().splitlines()[-1])

    def test_a_failed_load_is_not_all_clear_and_recovers(self):
        o = self.out
        for k in ("failShows", "failStaysAfterBadTick"):
            self.assertIn("Could not load the board. Retrying.", o[k])
            self.assertNotIn("All clear", o[k])
        self.assertIn("Old question", o["recovered"])
        self.assertNotIn("Could not load", o["recovered"])

    def test_same_id_with_new_title_or_options_redraws(self):
        self.assertIn("Replacement", self.out["changed"])
        self.assertIn('data-val="no"', self.out["changed"])
        self.assertNotIn('data-val="yes"', self.out["changed"])

    def test_a_new_urgent_question_does_not_wipe_a_half_typed_answer(self):
        o = self.out
        self.assertIn("Old question", o["draftHost"])
        self.assertNotIn("New urgent", o["draftHost"])
        self.assertIn("New question waiting", o["draftProg"])
        self.assertIn("Old question", o["focusHost"], "a focused field counts as a draft")

    def test_without_a_draft_the_urgent_question_still_takes_over(self):
        o = self.out
        self.assertIn("New urgent", o["cleanHost"])
        self.assertNotIn("New question waiting", o["cleanProg"])
        self.assertIn("New urgent", o["goneHost"], "a draft on a question that left the deck cannot hold the card")


class TestCalmWrite(unittest.TestCase):
    def setUp(self):
        self.ctx = tempfile.TemporaryDirectory()
        self.fresh = self.ctx.name
        self.addCleanup(self.ctx.cleanup)
        p = mock.patch.object(B, "HTML", os.path.join(self.fresh, "board.html"))
        p.start()
        self.addCleanup(p.stop)

    def test_temp_name_is_unique_per_write_and_never_left_behind(self):
        seen = []
        real = B._replace

        def spy(tmp, dst, tries=8):
            seen.append(tmp)
            return real(tmp, dst, tries)

        with mock.patch.object(B, "_replace", spy):
            with mock.patch.object(B, "_calm_page", return_value="one"):
                B.write_calm_html()
            with mock.patch.object(B, "_calm_page", return_value="two"):
                B.write_calm_html()
        self.assertEqual(len(set(seen)), 2, "each write needs its own temp file")
        self.assertTrue(all(str(os.getpid()) in os.path.basename(t) for t in seen))
        self.assertEqual([f for f in os.listdir(self.fresh) if f.endswith(".tmp")], [])

    def test_a_failed_replace_cleans_its_temp_file(self):
        with mock.patch.object(B, "_replace", side_effect=PermissionError("locked")):
            with self.assertRaises(PermissionError):
                B.write_calm_html()
        self.assertEqual([f for f in os.listdir(self.fresh) if f.endswith(".tmp")], [])


class TestCalmAnswersReachTheBoard(unittest.TestCase):
    """A calm answer must be polled and recorded, and no generic page watcher may claim the page."""

    def setUp(self):
        import json
        import board_watch as BW
        import podium_home as PH
        import podium_watch as PW
        self.BW, self.PH, self.PW, self.json = BW, PH, PW, json
        self.ctx = tempfile.TemporaryDirectory()
        self.fresh = self.ctx.name
        self.addCleanup(self.ctx.cleanup)
        self.calm = os.path.join(self.fresh, "board-calm.html")
        self.lavish = os.path.join(self.fresh, "lavish-state.json")
        for p in (mock.patch.object(B, "BOARD", self.fresh),
                  mock.patch.object(B, "STATE", os.path.join(self.fresh, "state.json")),
                  mock.patch.object(B, "STATEJS", os.path.join(self.fresh, "state.js")),
                  mock.patch.object(B, "HTML", os.path.join(self.fresh, "board.html")),
                  mock.patch.object(B, "PLAIN", os.path.join(self.fresh, "board-plain.html")),
                  mock.patch.object(PH, "STATE", self.lavish),
                  mock.patch.object(PW, "STATE", self.lavish),
                  mock.patch.object(BW, "wake_orchestrator", lambda m: None),
                  mock.patch.object(BW, "render_home", lambda: None),
                  mock.patch.dict(os.environ, {"PODIUM_SLACK": "0"})):
            p.start()
            self.addCleanup(p.stop)

    def _sessions(self, **by_file):
        now = time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime())
        rows = {k: {"file": f, "status": st, "updated_at": now} for k, (f, st) in by_file.items()}
        self.json.dump({"sessions": rows}, open(self.lavish, "w", encoding="utf-8"))

    def _poll_output(self):
        prompt = "Q7 Pick one: yes\n\nContext data: " + self.json.dumps({"q": "q7", "answer": "yes"})
        return 'prompts[1]{prompt,tag}:\n  ' + self.json.dumps(prompt) + ',answer\nlayout_warnings[0]:\n'

    def test_podium_filters_skip_the_calm_page(self):
        other = os.path.join(self.fresh, "design.html")
        self._sessions(a=(self.calm, "open"), b=(other, "open"))
        norm = lambda p: os.path.normcase(os.path.abspath(p))
        for name, fn in (("podium_home", self.PH.open_sessions), ("podium_watch", self.PW.open_sessions)):
            got = fn()
            self.assertNotIn(norm(self.calm), got, f"{name} must not treat board-calm.html as a design page")
            self.assertIn(norm(other), got, f"{name} still lists ordinary pages")

    def test_calm_answer_is_recorded_like_a_board_answer(self):
        B.save(B.default_state())
        B.cmd_add_question(type("A", (), dict(
            id="q7", title="Pick one", from_="", group="g", context="c", context_file=None,
            option=["yes=Yes"], pick=None, why=None, input=None))())
        self._sessions(a=(self.calm, "open"))
        polled = []
        res = self.BW.calm_cycle(poll=lambda: polled.append(1) or self._poll_output())
        self.assertEqual(res, "ok")
        q = next(x for x in B.load()["questions"] if x["id"] == "q7")
        self.assertTrue(q.get("answered"), "the calm answer must reach the board")
        self.assertEqual(q.get("answer"), "yes")

    def test_no_calm_session_means_no_poll(self):
        self._sessions(a=(self.calm, "ended"))
        polled = []
        self.assertEqual(self.BW.calm_cycle(poll=lambda: polled.append(1) or ""), "none")
        self.assertEqual(polled, [])
        self.json.dump({"sessions": {}}, open(self.lavish, "w", encoding="utf-8"))
        self.assertEqual(self.BW.calm_cycle(poll=lambda: polled.append(1) or ""), "none")
        self.assertEqual(polled, [])

    def test_calm_poll_waits_when_another_poller_holds_the_page(self):
        self._sessions(a=(self.calm, "open"))
        self.assertEqual(self.BW.calm_cycle(poll=lambda: "error: LISTENER_ACTIVE owner=x"), "busy")


class TestCalmIsTheDefaultBoard(unittest.TestCase):
    """`open` serves board-calm.html, `old` serves board.html, Home and the Calm header link both."""

    def setUp(self):
        import json
        import podium_home as PH
        self.PH, self.json = PH, json
        self.ctx = tempfile.TemporaryDirectory()
        self.fresh = self.ctx.name
        self.addCleanup(self.ctx.cleanup)
        self.lavish = os.path.join(self.fresh, "lavish-state.json")
        for p in (mock.patch.object(B, "BOARD", self.fresh),
                  mock.patch.object(B, "STATE", os.path.join(self.fresh, "state.json")),
                  mock.patch.object(B, "STATEJS", os.path.join(self.fresh, "state.js")),
                  mock.patch.object(B, "HTML", os.path.join(self.fresh, "board.html")),
                  mock.patch.object(B, "PLAIN", os.path.join(self.fresh, "board-plain.html")),
                  mock.patch.object(B, "URLFILE", os.path.join(self.fresh, "podium-url.txt")),
                  mock.patch.object(B, "ensure_lavish_brand", lambda: None),
                  mock.patch.object(B, "_LINKS_CACHE", {"at": 0.0, "rows": []}),
                  mock.patch.object(PH, "STATE", self.lavish),
                  mock.patch.dict(os.environ, {"PODIUM_SLACK": "0"})):
            p.start()
            self.addCleanup(p.stop)

    def _fake_lavish(self, calls):
        def run(cmd, *a, **k):
            calls.append(cmd)
            page = next(c for c in cmd if str(c).endswith(".html"))
            key = "calm1" if page.endswith("board-calm.html") else "old1"
            return type("R", (), {"stdout": f'url: "http://127.0.0.1:4387/session/{key}"\n', "stderr": ""})()
        return run

    def _opened_pages(self, calls):
        return [os.path.basename(next(c for c in cmd if str(c).endswith(".html"))) for cmd in calls]

    def test_open_serves_calm_and_remembers_its_url(self):
        calls = []
        with mock.patch.object(B.subprocess, "run", self._fake_lavish(calls)):
            B.cmd_open()
        self.assertEqual(self._opened_pages(calls), ["board.html", "board-calm.html"],
                         "board.html is only registered (--no-open); the page opened is Calm")
        self.assertIn("--no-open", calls[0])
        self.assertNotIn("--no-open", calls[1])
        self.assertEqual(open(B.URLFILE, encoding="utf-8").read().strip(), "http://127.0.0.1:4387/session/calm1")

    def test_old_serves_board_html_and_leaves_the_default_url_alone(self):
        open(B.URLFILE, "w", encoding="utf-8").write("http://127.0.0.1:4387/session/calm1\n")
        calls = []
        with mock.patch.object(B.subprocess, "run", self._fake_lavish(calls)):
            B.cmd_old()
        self.assertEqual(self._opened_pages(calls), ["board.html"])
        self.assertEqual(open(B.URLFILE, encoding="utf-8").read().strip(), "http://127.0.0.1:4387/session/calm1")

    def test_parser_routes_open_calm_and_old(self):
        p = B.build_parser()
        self.assertIs(p.parse_args(["open"]).fn, B.cmd_open)
        self.assertIs(p.parse_args(["calm"]).fn, B.cmd_open)
        self.assertIs(p.parse_args(["old"]).fn, B.cmd_old)

    def test_old_board_link_reaches_state_and_the_calm_header(self):
        now = time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime())
        self.json.dump({"sessions": {"old1": {"file": B.HTML, "status": "open", "updated_at": now,
                                              "url": "http://127.0.0.1:4387/session/old1"}}},
                       open(self.lavish, "w", encoding="utf-8"))
        s = B.save(B.default_state())
        old = [l for l in s["links"] if l.get("alt") == "old"]
        self.assertEqual([l["url"] for l in old], ["http://127.0.0.1:4387/session/old1"])
        calm = open(B.calm_path(), encoding="utf-8").read()
        self.assertIn('id="oldlink"', calm)
        self.assertIn("Old board", calm)
        self.assertIn("!l.home && !l.alt", open(B.HTML, encoding="utf-8").read(),
                      "the old page's own link bar must not list the Old board row")

    def test_home_points_at_calm_and_lists_the_old_board(self):
        now = time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime())
        sess = {k: {"file": f, "status": "open", "updated_at": now, "url": f"http://127.0.0.1:4387/session/{k}"}
                for k, f in (("calm1", B.calm_path()), ("old1", B.HTML))}
        self.json.dump({"sessions": sess}, open(self.lavish, "w", encoding="utf-8"))
        self.assertEqual(self.PH.board_url(), "http://127.0.0.1:4387/session/calm1")
        self.assertEqual(self.PH.board_url(B.HTML), "http://127.0.0.1:4387/session/old1")
        with mock.patch.object(self.PH, "live_pollers", lambda: set()):
            page = self.PH._page(*self.PH.build())
        self.assertIn("session/calm1", page)
        self.assertIn("Old board", page)
        self.assertIn("session/old1", page)


if __name__ == "__main__":
    unittest.main()
