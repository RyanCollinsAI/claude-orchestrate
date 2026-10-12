"""chrome open / close: the URL arrives intact, and close only touches tabs open made."""
import http.server
import json
import os
import sys
import tempfile
import threading
import unittest
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import chrome_open as C  # noqa: E402

URL6 = "?a=1&b=two&c=3%204&d=x%26y&e=5&f=six"


def agent_chrome_up():
    try:
        urllib.request.urlopen("http://127.0.0.1:9334/json/version", timeout=2).read()
        return True
    except Exception:
        return False


class _Echo(http.server.BaseHTTPRequestHandler):
    seen = []

    def do_GET(self):
        _Echo.seen.append(self.path)
        body = b"<title>echo</title><p id=ok>ok</p>"
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


class TestTrackingFile(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "tabs.json")

    def tearDown(self):
        self.tmp.cleanup()

    def test_close_refuses_an_id_open_never_made(self):
        ok, msg = C.close_tab("NOTMINE123", path=self.path)
        self.assertFalse(ok)
        self.assertIn("refusing", msg)

    def test_close_refuses_even_when_other_ids_are_tracked(self):
        C._save(self.path, {"MINE": {"port": 9334, "url": "x"}})
        ok, _ = C.close_tab("THEIRS", path=self.path)
        self.assertFalse(ok)
        self.assertIn("MINE", C._load(self.path))

    def test_open_rejects_a_port_that_is_not_one_of_the_two(self):
        with self.assertRaises(ValueError):
            C.open_tab("http://example.com", port=9999, path=self.path)


@unittest.skipUnless(agent_chrome_up(), "agent Chrome :9334 is down")
class TestRealTab(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "tabs.json")
        _Echo.seen = []
        self.srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Echo)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        # `localhost`, not 127.0.0.1: the agent Chrome never reaches an arbitrary 127.0.0.1 port
        self.base = f"http://localhost:{self.srv.server_address[1]}/p"

    def tearDown(self):
        self.srv.shutdown()
        self.tmp.cleanup()

    def test_six_query_params_arrive_intact_and_close_works(self):
        url = self.base + URL6
        r = C.open_tab(url, wait_for="#ok", path=self.path)
        try:
            self.assertEqual(r["title"], "echo")
            self.assertIn("/p" + URL6, _Echo.seen)
            self.assertEqual(r["url"], url)
            listing = json.loads(urllib.request.urlopen("http://127.0.0.1:9334/json/list").read())
            self.assertIn(r["id"], [t["id"] for t in listing])
        finally:
            ok, _ = C.close_tab(r["id"], path=self.path)
        self.assertTrue(ok)
        listing = json.loads(urllib.request.urlopen("http://127.0.0.1:9334/json/list").read())
        self.assertNotIn(r["id"], [t["id"] for t in listing])
        self.assertEqual(C._load(self.path), {})

    def test_gmail_style_compose_link_keeps_subject_and_body(self):
        url = self.base + "?view=cm&fs=1&to=a%40b.com&su=Hello%20there&body=Hi%26bye"
        r = C.open_tab(url, wait_for="#ok", path=self.path)
        try:
            self.assertIn("/p?view=cm&fs=1&to=a%40b.com&su=Hello%20there&body=Hi%26bye", _Echo.seen)
        finally:
            C.close_tab(r["id"], path=self.path)

    def test_open_returns_under_3s_with_40_tabs_open(self):
        import time
        ids = []
        try:
            for _ in range(40):
                ids.append(C.open_tab("about:blank", path=self.path)["id"])
            t = time.time()
            r = C.open_tab(self.base + URL6, path=self.path)
            took = time.time() - t
            ids.append(r["id"])
            self.assertLess(took, 3.0, f"open took {took:.2f}s")
            self.assertIn("/p" + URL6, _poll_seen())
        finally:
            for i in ids:
                C.close_tab(i, path=self.path)


def _poll_seen():
    import time
    end = time.time() + 5
    while time.time() < end and not any(p.startswith("/p?a=1") for p in _Echo.seen):
        time.sleep(0.1)
    return _Echo.seen


if __name__ == "__main__":
    unittest.main()
