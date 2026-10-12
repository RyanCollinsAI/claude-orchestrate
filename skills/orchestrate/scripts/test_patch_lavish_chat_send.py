"""patch_lavish_chat_send.py (needs patch_lavish_brand.py and patch_lavish_chat_status.py first) on
lavish-axi 0.1.84. Runs against a COPY of the pristine dist (see lavish_testkit.py), never the live Bridge
or ~/.lavish-axi. Walks the server-side status machine over HTTP + the live-event WebSocket:

  poll delivers the prompts -> "Agent got it - working" with a `since` stamp
  agent-status              -> text overridden, `since` kept
  agent-reply               -> cleared
  agent polls again         -> cleared
plus: --check, a second apply is a no-op, node --check on both patched scripts, a moved anchor fails closed.

    py -m pytest test_patch_lavish_chat_send.py
"""
import urllib.parse

import pytest

import lavish_chat_testkit as C
import lavish_testkit as K

pytestmark = pytest.mark.skipif(not K.available(), reason="no pristine lavish-axi dist on this box")

PROMPT = {"prompts": [{"uid": "", "prompt": "hi", "tag": "message", "text": "Freeform message"}]}


@pytest.fixture()
def root():
    r = K.make_root("lav-send-")
    try:
        yield r
    finally:
        K.remove_tree(r)


@pytest.fixture()
def pkg(root):
    return K.make_pkg(root)


def test_not_patched_then_patched_then_idempotent(pkg):
    C.apply_chain(pkg, upto="chat_status")
    assert K.run_patch(C.CHAT_SEND, pkg, "--check").returncode == 1
    first = K.run_patch(C.CHAT_SEND, pkg)
    assert first.returncode == 0, first.stdout + first.stderr
    assert first.stdout.count("patched ") == 3
    checked = K.run_patch(C.CHAT_SEND, pkg, "--check")
    assert checked.returncode == 0 and "not patched" not in checked.stdout
    again = K.run_patch(C.CHAT_SEND, pkg)
    assert again.returncode == 0 and again.stdout.count("already patched") == 3


def test_refuses_without_chat_status(pkg):
    C.apply_chain(pkg, upto="brand")
    r = K.run_patch(C.CHAT_SEND, pkg)
    assert r.returncode == 1 and "run patch_lavish_chat_status.py first" in r.stdout


def test_full_chain_parses(pkg):
    C.apply_chain(pkg)
    assert C.node_check(C.dist_file(pkg, "cli.mjs")).returncode == 0
    r = C.node_check(C.dist_file(pkg, "chrome-client.js"))
    assert r.returncode == 0, r.stderr
    client = C.read(pkg, "chrome-client.js")
    assert "const SNAPSHOT_REQUEST_TIMEOUT_MS = 700;" in client
    assert 'events.set("agent-status", (data) => setAgentStatus(data));' in client
    assert "EventSource" not in client


def test_moved_anchor_fails_closed(pkg):
    C.apply_chain(pkg, upto="chat_status")
    client = C.dist_file(pkg, "chrome-client.js")
    src = open(client, encoding="utf-8").read().replace("const SNAPSHOT_REQUEST_TIMEOUT_MS = 5000;",
                                                        "const SNAPSHOT_REQUEST_TIMEOUT_MS = 6000;")
    open(client, "w", encoding="utf-8", newline="\n").write(src)
    r = K.run_patch(C.CHAT_SEND, pkg)
    assert r.returncode == 1 and "anchor count 0" in r.stdout
    assert "chat send smooth" not in open(client, encoding="utf-8").read()


def test_status_machine_over_http_and_websocket(pkg, root):
    C.apply_chain(pkg)
    srv, base, key, art = C.start(pkg, root)
    ev = None
    poll = lambda ms=1500: K.get_json(base, "/api/poll?file=" + urllib.parse.quote(art) + f"&timeoutMs={ms}")
    try:
        ev = C.Events(base, key)
        assert ev.wait("agent-presence") is not None

        assert K.post(base, f"/api/{key}/prompts", PROMPT) == 200
        got = poll()
        assert got.get("status") == "feedback"
        working = ev.wait("agent-status", lambda d: d.get("text") == "Agent got it - working")
        assert working is not None and isinstance(working.get("since"), (int, float))
        assert working.get("phase") == "working"

        assert K.post(base, f"/api/{key}/agent-status", {"text": "Reading the file"}) == 200
        own = ev.wait("agent-status", lambda d: d.get("text") == "Reading the file")
        assert own is not None and own.get("since") == working["since"]

        # a tab that connects mid-work gets the live status, with the clock
        late = C.Events(base, key)
        try:
            replay = late.wait("agent-status", lambda d: d.get("text") == "Reading the file")
            assert replay is not None and replay.get("since") == working["since"]
        finally:
            late.close()

        n = len(ev.of("agent-status"))
        assert K.post(base, f"/api/{key}/agent-reply", {"text": "done"}) == 200
        assert ev.wait("agent-status", lambda d: d.get("text") == "", after=n) is not None

        # the second path out of working: the agent polls again
        assert K.post(base, f"/api/{key}/prompts", PROMPT) == 200
        assert poll().get("status") == "feedback"
        assert ev.wait("agent-status", lambda d: d.get("text") == "Agent got it - working", after=n) is not None
        n = len(ev.of("agent-status"))
        poll(ms=600)
        assert ev.wait("agent-status", lambda d: d.get("text") == "", after=n) is not None
    finally:
        if ev:
            ev.close()
        srv.kill()
