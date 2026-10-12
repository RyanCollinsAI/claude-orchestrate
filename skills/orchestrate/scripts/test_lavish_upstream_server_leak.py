"""Does stock lavish-axi 0.1.84 still leak live-event clients, and still lose concurrent updates?

Replaces the proof behind patch_lavish_server_leak.py (retired). Upstream 0.1.65 moved live events from SSE
to WebSocket, and SessionStore now runs every read-modify-write under an AsyncMutex. This test measures
both on the PRISTINE dist, copied to a temp dir, with its own port and state dir. Nothing here touches the
global install or ~/.lavish-axi.

Half 1 (live-event leak): ~500 WebSocket upgrades to /events/:key are aborted right after the request is
sent, while the server is still inside its first state lookup (state.json is seeded big so that await is
slow), then 50 normal connections are held open and closed. The copy gets one extra route, /debug, which
reports liveEventClients.size, wss.clients.size, the per-event listener counts on `events`, and heap after
a forced gc. Pass means all of them return to baseline.
A control copy re-creates the old bug shape (close handler registered AFTER the await, no closed-client guard) and must leak,
otherwise the harness would pass vacuously.

Half 2 (lost updates): 100 concurrent agent-reply POSTs and 60 concurrent prompt POSTs to one session must
all be present in state.json afterwards.

    py -m pytest test_lavish_upstream_server_leak.py
"""
import base64
import json
import os
import random
import socket
import struct
import threading
import time

import pytest

import lavish_testkit as kit

pytestmark = pytest.mark.skipif(not kit.available(), reason="pristine lavish-axi dist or its node_modules not found")

HEALTH_ANCHOR = '  app.get("/health", async (req, res) => {\n'
DEBUG_ROUTE = (
    '  app.get("/debug", (req, res) => { global.gc && global.gc(); const m = process.memoryUsage(); '
    "res.json({ live: liveEventClients.size, wss: eventWebSocketServer.clients.size, "
    "lst: Object.fromEntries(events.eventNames().map((n) => [String(n), events.listenerCount(n)])), "
    "heapMB: Math.round(m.heapUsed / 1048576), rssMB: Math.round(m.rss / 1048576) }); });\n"
    + HEALTH_ANCHOR)
CONTROL_EDITS = [
    ('const cleanup = attachLiveEventClient(client, key, (remove) => webSocket.once("close", remove));\n',
     'const cleanup = attachLiveEventClient(client, key, () => {});\n'),
    ("sendInitialLiveEventState(client, key, cleanup).catch((error) => {",
     'sendInitialLiveEventState(client, key, cleanup).finally(() => webSocket.once("close", cleanup)).catch((error) => {'),
    ("    if (client.isClosed()) {\n      cleanup();\n      return;\n    }\n", ""),
]


def seed_state(path, art_dir):
    pad = "x" * 8000
    sessions = {}
    for i in range(200):
        sessions[f"ended{i:03d}"] = {
            "key": f"ended{i:03d}", "file": os.path.join(art_dir, f"gone{i}.html"), "url": "http://x", "status": "ended",
            "pending_prompts": 0, "prompts": [], "layout_warnings": [], "dom_snapshot": "", "delivery_seq": 1,
            "chat": [{"role": "agent", "text": pad, "at": "2026-01-01T00:00:00Z"}],
            "updated_at": "2026-01-01T00:00:00Z"}
    json.dump({"sessions": sessions}, open(path, "w"), indent=2)


def upgrade_request(port, key):
    nonce = base64.b64encode(os.urandom(16)).decode()
    return (f"GET /events/{key} HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nOrigin: http://127.0.0.1:{port}\r\n"
            f"Upgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Key: {nonce}\r\n"
            "Sec-WebSocket-Version: 13\r\n\r\n").encode()


def abort_connect(port, key):
    s = socket.create_connection(("127.0.0.1", port))
    s.sendall(upgrade_request(port, key))
    time.sleep(random.choice((0, 0, 0.001, 0.003)))
    if random.random() < 0.5:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
    s.close()


def hold_connect(port, key):
    s = socket.create_connection(("127.0.0.1", port))
    s.sendall(upgrade_request(port, key))
    buf = b""
    while b"\r\n\r\n" not in buf:
        chunk = s.recv(4096)
        assert chunk, "server closed before 101"
        buf += chunk
    assert buf.startswith(b"HTTP/1.1 101"), buf[:80]
    return s


def in_threads(fn, jobs, width=50):
    for i in range(0, len(jobs), width):
        ts = [threading.Thread(target=fn, args=j) for j in jobs[i:i + width]]
        [t.start() for t in ts]
        [t.join() for t in ts]


def settle(base, want, tries=40):
    d = None
    for _ in range(tries):
        d = kit.get_json(base, "/debug")
        if d["live"] == want["live"] and d["wss"] == want["wss"]:
            break
        time.sleep(0.25)
    return d


@pytest.fixture(scope="module")
def root():
    r = kit.make_root("lavish-upstream-leak-")
    yield r
    time.sleep(0.3)
    kit.remove_tree(r)


def boot(root, label, mutate=None):
    pkg = kit.make_pkg(root, label)
    cli = os.path.join(pkg, "dist", "cli.mjs")
    src = open(cli, encoding="utf-8").read()
    assert src.count(HEALTH_ANCHOR) == 1
    src = src.replace(HEALTH_ANCHOR, DEBUG_ROUTE, 1)
    for old, new in mutate or []:
        assert src.count(old) == 1, old
        src = src.replace(old, new, 1)
    open(cli, "w", encoding="utf-8", newline="\n").write(src)
    state = os.path.join(root, label + "-state")
    art = os.path.join(root, label + "-art")
    os.makedirs(state)
    os.makedirs(art)
    seed_state(os.path.join(state, "state.json"), art)
    srv, base = kit.start_server(pkg, state, kit.free_port(), node_args=("--expose-gc",))
    return srv, base, state, art


def soak(base, art, n_abort=500, n_hold=50):
    port = int(base.rsplit(":", 1)[1])
    keys = []
    for i in range(8):
        key, status = kit.open_session(base, art, f"p{i}.html")
        assert status == 200
        keys.append(key)
    base0 = settle(base, {"live": 0, "wss": 0})
    in_threads(abort_connect, [(port, keys[i % len(keys)]) for i in range(n_abort)])
    after_abort = settle(base, {"live": 0, "wss": 0})
    held = [hold_connect(port, keys[i % len(keys)]) for i in range(n_hold)]
    while_held = settle(base, {"live": n_hold, "wss": n_hold})
    for s in held:
        s.close()
    after_hold = settle(base, {"live": 0, "wss": 0})
    return {"baseline": base0, "after_abort": after_abort, "while_held": while_held, "after_hold": after_hold}


def test_stock_0_1_84_does_not_leak_live_event_clients(root):
    srv, base, state, art = boot(root, "stock")
    try:
        r = soak(base, art)
        print("stock 0.1.84 soak:", json.dumps(r))
        b = r["baseline"]
        assert (b["live"], b["wss"]) == (0, 0)
        assert (r["while_held"]["live"], r["while_held"]["wss"]) == (50, 50)
        for phase in ("after_abort", "after_hold"):
            assert (r[phase]["live"], r[phase]["wss"]) == (0, 0), (phase, r[phase])
            assert r[phase]["lst"] == b["lst"], (phase, r[phase]["lst"], b["lst"])
        assert r["after_hold"]["heapMB"] - b["heapMB"] < 20
    finally:
        srv.kill()


def test_control_with_old_bug_shape_does_leak(root):
    srv, base, state, art = boot(root, "control", mutate=CONTROL_EDITS)
    try:
        r = soak(base, art, n_abort=500, n_hold=0)
        print("control (close registered after await) soak:", json.dumps(r))
        assert r["after_abort"]["live"] > 0, "harness never hit the abort-during-await window"
    finally:
        srv.kill()


NO_MUTEX = [("    const run2 = this._tail.then(() => fn());\n", "    const run2 = Promise.resolve().then(() => fn());\n")]


def test_control_without_mutex_does_lose_updates(root):
    srv, base, state, art = boot(root, "nomutex", mutate=NO_MUTEX)
    try:
        stored, sent = concurrent_writes(base, state, art)
        print(f"control (mutex removed): replies stored {stored[0]}/100, prompts stored {stored[1]}/60")
        assert stored[0] < 100 or stored[1] < 60, "harness never produced a lost update"
    finally:
        srv.kill()


def test_stock_0_1_84_does_not_lose_concurrent_updates(root):
    srv, base, state, art = boot(root, "lost")
    try:
        stored, sent = concurrent_writes(base, state, art)
        print(f"lost-update probe: replies stored {stored[0]}/100, prompts stored {stored[1]}/60")
        assert all(s == 200 for s in sent), sorted(set(sent))
        assert stored == (100, 60)
    finally:
        srv.kill()


def concurrent_writes(base, state, art):
    key, status = kit.open_session(base, art, "w.html")
    assert status == 200
    results = []

    def reply(i):
        results.append(kit.post(base, f"/api/{key}/agent-reply", {"text": f"reply-{i}"}))

    def prompt(i):
        results.append(kit.post(base, f"/api/{key}/prompts",
                                {"prompts": [{"uid": f"u{i}", "prompt": f"prompt-{i}", "selector": "h1",
                                              "tag": "h1", "text": "x"}]}))
    jobs = [(reply, i) for i in range(100)] + [(prompt, i) for i in range(60)]
    random.shuffle(jobs)
    ts = [threading.Thread(target=f, args=(i,)) for f, i in jobs]
    [t.start() for t in ts]
    [t.join() for t in ts]
    time.sleep(0.3)
    try:
        session = json.load(open(os.path.join(state, "state.json"), encoding="utf-8"))["sessions"][key]
    except ValueError:
        return (-1, -1), results
    replies = {e.get("text", "") for e in session["chat"] if e.get("text", "").startswith("reply-")}
    prompts = {p["prompt"] for p in session["prompts"]}
    return (len(replies), len(prompts)), results
