"""`orch.py chrome open <url>` and `chrome close <id>` - THE way an agent opens a tab for Ryan.

Why not `PUT /json/new?<url>`: Chrome's DevTools HTTP endpoint takes the rest of the query string as
the URL and silently drops everything after the first `&`, so a Gmail compose link opened as a bare
inbox. Here a raw CDP websocket call (Target.createTarget) carries the URL, so it arrives byte for byte.

Why not Playwright `connect_over_cdp`: it attaches to EVERY target and hangs forever when one never
answers (2026-10-02: a chrome://newtab/ target on :9334 never acked attach, so even about:blank hung).
This module only ever attaches to the tab it just made, and only when --wait-for needs it.

`open` records the target id in a small file. `close` only closes an id found in that file, over the
plain `/json/close/<id>` HTTP call - no CDP client is attached when the tab dies, so nothing
can drop another pane's tabs the way the 2026-09-11 crash on :9334 did.
"""
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request

PORTS = (9333, 9334)
DEFAULT_PORT = 9334
SHOW_WINDOW = os.path.join(os.path.expanduser("~"), ".claude", "skills", "browser-as-ryan",
                           "scripts", "show_window.py")


def tabs_file():
    import orchlib as L
    return os.path.join(L.BOARD_DIR, "chrome-tabs.json")


def _load(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _save(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=1)
    os.replace(tmp, path)


def _cdp(port):
    return f"http://127.0.0.1:{port}"


def _targets(port):
    return json.loads(urllib.request.urlopen(f"{_cdp(port)}/json/list", timeout=5).read())


def _front_os_window(title):
    """Bring the OS window forward with show_window.py. Returns (foreground, note)."""
    r = subprocess.run([sys.executable, SHOW_WINDOW, "--title", title], capture_output=True, text=True)
    if r.returncode != 0:
        return False, (r.stderr or "show_window failed").strip().splitlines()[-1]
    try:
        info = json.loads(r.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return False, "show_window gave no JSON"
    return bool(info.get("foreground")), info.get("screenshot", "")


class _Cdp:
    """One raw CDP websocket on the browser endpoint."""

    def __init__(self, port, timeout=10):
        from websockets.sync.client import connect
        ver = json.loads(urllib.request.urlopen(f"{_cdp(port)}/json/version", timeout=5).read())
        self.ws = connect(ver["webSocketDebuggerUrl"], max_size=None, open_timeout=5)
        self.timeout = timeout
        self.n = 0

    def call(self, method, params=None, session=None):
        self.n += 1
        msg = {"id": self.n, "method": method, "params": params or {}}
        if session:
            msg["sessionId"] = session
        self.ws.send(json.dumps(msg))
        end = time.time() + self.timeout
        while True:
            left = end - time.time()
            if left <= 0:
                raise TimeoutError(f"{method} got no answer in {self.timeout}s")
            r = json.loads(self.ws.recv(timeout=left))
            if r.get("id") == self.n:
                if "error" in r:
                    raise RuntimeError(f"{method}: {r['error']}")
                return r.get("result", {})

    def close(self):
        self.ws.close()


def _wait_for_selector(c, tid, selector, timeout_ms):
    """Attach to ONLY the new target and poll for the selector."""
    sid = c.call("Target.attachToTarget", {"targetId": tid, "flatten": True})["sessionId"]
    expr = f"!!document.querySelector({json.dumps(selector)})"
    end = time.time() + timeout_ms / 1000
    try:
        while time.time() < end:
            r = c.call("Runtime.evaluate", {"expression": expr}, session=sid)
            if r.get("result", {}).get("value") is True:
                return
            time.sleep(0.1)
        raise TimeoutError(f"selector {selector!r} not found in {timeout_ms}ms")
    finally:
        try:
            c.call("Target.detachFromTarget", {"sessionId": sid})
        except Exception:
            pass


def open_tab(url, port=DEFAULT_PORT, front=False, wait_for=None, timeout_ms=30000, path=None):
    """Open `url` in a new tab on `port`. Returns {"id", "title", "url", "port", ...}."""
    if port not in PORTS:
        raise ValueError(f"port must be one of {PORTS}")
    path = path or tabs_file()
    c = _Cdp(port)
    try:
        tid = c.call("Target.createTarget", {"url": url, "background": not front})["targetId"]
        try:
            if wait_for:
                _wait_for_selector(c, tid, wait_for, timeout_ms)
            if front:
                c.call("Target.activateTarget", {"targetId": tid})
        except Exception:
            try:
                c.call("Target.closeTarget", {"targetId": tid})  # our own tab, never someone else's
            except Exception:
                pass
            raise
    finally:
        c.close()
    tabs = _load(path)
    tabs[tid] = {"port": port, "url": url}
    _save(path, tabs)
    info = next((t for t in _targets(port) if t["id"] == tid), {})
    result = {"id": tid, "title": info.get("title", ""), "url": info.get("url") or url, "port": port}
    if front:
        result["foreground"], result["note"] = _front_os_window(result["title"])
    return result


def close_tab(tid, path=None):
    """Close a tab this module opened. Returns (ok, message)."""
    path = path or tabs_file()
    tabs = _load(path)
    entry = tabs.get(tid)
    if not entry:
        return False, f"{tid} was not opened by `chrome open` - refusing to close a tab I do not own"
    try:
        urllib.request.urlopen(f"{_cdp(entry['port'])}/json/close/{tid}", timeout=5).read()
        # /json/close answers before the target is gone; with 120 tabs open (2026-10-06) it was
        # still listed right after. Wait so "closed" is true when it is printed.
        end = time.time() + 5
        while time.time() < end and any(t["id"] == tid for t in _targets(entry["port"])):
            time.sleep(0.1)
        msg = f"closed {tid}"
    except urllib.error.URLError as e:
        msg = f"{tid} not closed ({e}); dropped from my list"
    tabs.pop(tid, None)
    _save(path, tabs)
    return not msg.startswith(f"{tid} not"), msg


def cli(args):
    """args = everything after `chrome open` / `chrome close`, with the verb first."""
    usage = ("usage: orch.py chrome open <url> [--port 9333|9334] [--front] [--wait-for <css>]\n"
             "       orch.py chrome close <id>")
    if not args or args[0] not in ("open", "close"):
        sys.exit(usage)
    verb, rest = args[0], args[1:]
    if verb == "close":
        if len(rest) != 1:
            sys.exit(usage)
        ok, msg = close_tab(rest[0])
        print(msg)
        sys.exit(0 if ok else 1)
    url, port, front, wait_for = None, DEFAULT_PORT, False, None
    i = 0
    while i < len(rest):
        a = rest[i]
        if a == "--front":
            front = True
        elif a in ("--port", "--wait-for") and i + 1 < len(rest):
            i += 1
            if a == "--port":
                port = int(rest[i])
            else:
                wait_for = rest[i]
        elif url is None and not a.startswith("--"):
            url = a
        else:
            sys.exit(usage)
        i += 1
    if not url:
        sys.exit(usage)
    r = open_tab(url, port=port, front=front, wait_for=wait_for)
    print(f"id: {r['id']}")
    print(f"title: {r['title']}")
    print(f"url: {r['url']}")
    if front:
        print(f"foreground: {r['foreground']} ({r['note']})")
