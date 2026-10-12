"""Helpers for the chat-side lavish-axi patch tests (brand, chat_status, chat_send).

Builds on lavish_testkit.py (pristine dist copy, free port, throwaway state dir). Never touches the
global install or ~/.lavish-axi.

The brand script's main() also runs patches other tests own (atomic_state, state_cache, ...), so the
tests run only the sub-patches this chain owns: apply_brand() calls patch_lavish_brand.apply_own() in a
child process pointed at the copy by LAVISH_CLI_MJS / LAVISH_DIST, and chat_status / chat_send run as
their own scripts.
"""
import json
import os
import subprocess
import sys
import threading
import time

import lavish_testkit as K

HERE = os.path.dirname(os.path.abspath(__file__))
BRAND = os.path.join(HERE, "patch_lavish_brand.py")
CHAT_STATUS = os.path.join(HERE, "patch_lavish_chat_status.py")
CHAT_SEND = os.path.join(HERE, "patch_lavish_chat_send.py")

_BRAND_DRIVER = (
    "import sys; sys.path.insert(0, {here!r}); import patch_lavish_brand as b; "
    "sys.exit(int(any(b.apply_own({check}))))"
)


def run_brand(pkg, check=False):
    code = _BRAND_DRIVER.format(here=HERE, check=check)
    return subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=K.patch_env(pkg))


def apply_chain(pkg, upto="chat_send"):
    """brand -> chat_status -> chat_send, in the order the scripts' own dependency messages demand."""
    steps = [("brand", lambda: run_brand(pkg)),
             ("chat_status", lambda: K.run_patch(CHAT_STATUS, pkg)),
             ("chat_send", lambda: K.run_patch(CHAT_SEND, pkg))]
    for name, run in steps:
        r = run()
        assert r.returncode == 0, f"{name} failed: {r.stdout}{r.stderr}"
        if name == upto:
            break


def dist_file(pkg, name):
    return os.path.join(pkg, "dist", name)


def read(pkg, name):
    return open(dist_file(pkg, name), encoding="utf-8").read()


def node_check(path):
    """chrome-client.js is a plain browser script with no import/export, so node --check accepts it."""
    return subprocess.run(["node", "--check", path], capture_output=True, text=True)


def start(pkg, root):
    state = os.path.join(root, "state")
    port = K.free_port()
    srv, base = K.start_server(pkg, state, port)
    art = os.path.join(root, "art")
    os.makedirs(art, exist_ok=True)
    key, status = K.open_session(base, art, "page.html")
    assert status == 200
    return srv, base, key, os.path.join(art, "page.html")


class Events:
    """A live-event WebSocket for one session (the 0.1.65+ transport). Collects {type, data} frames."""

    def __init__(self, base, key):
        import websocket
        self.frames = []
        self._ws = websocket.create_connection(
            base.replace("http://", "ws://") + "/events/" + key, origin=base, timeout=5)
        self._stop = False
        self._t = threading.Thread(target=self._loop, daemon=True)
        self._t.start()

    def _loop(self):
        while not self._stop:
            try:
                raw = self._ws.recv()
            except Exception as e:
                if type(e).__name__ == "WebSocketTimeoutException":
                    continue
                return
            if raw:
                self.frames.append(json.loads(raw))

    def of(self, type_):
        return [f["data"] for f in self.frames if f["type"] == type_]

    def wait(self, type_, pred=lambda d: True, timeout=8, after=0):
        """First frame of this type, at index >= `after` among that type's frames, that satisfies pred."""
        end = time.time() + timeout
        while time.time() < end:
            for d in self.of(type_)[after:]:
                if pred(d):
                    return d
            time.sleep(0.05)
        return None

    def close(self):
        self._stop = True
        try:
            self._ws.close()
        except Exception:
            pass
