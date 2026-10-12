"""patch_lavish_chat_status.py (needs patch_lavish_brand.py first) on lavish-axi 0.1.84: applies,
is idempotent, parses, and the agent-status route reaches a browser's WebSocket. Runs against a COPY of
the pristine dist (see lavish_testkit.py).

    py -m pytest test_patch_lavish_chat_status.py
"""
import pytest

import lavish_chat_testkit as C
import lavish_testkit as K

pytestmark = pytest.mark.skipif(not K.available(), reason="no pristine lavish-axi dist on this box")


@pytest.fixture()
def root():
    r = K.make_root("lav-status-")
    try:
        yield r
    finally:
        K.remove_tree(r)


@pytest.fixture()
def pkg(root):
    return K.make_pkg(root)


def test_not_patched_then_patched_then_idempotent(pkg):
    C.apply_chain(pkg, upto="brand")
    assert K.run_patch(C.CHAT_STATUS, pkg, "--check").returncode == 1
    first = K.run_patch(C.CHAT_STATUS, pkg)
    assert first.returncode == 0, first.stdout + first.stderr
    assert first.stdout.count("patched ") == 3
    checked = K.run_patch(C.CHAT_STATUS, pkg, "--check")
    assert checked.returncode == 0 and "not patched" not in checked.stdout
    again = K.run_patch(C.CHAT_STATUS, pkg)
    assert again.returncode == 0 and again.stdout.count("already patched") == 3


def test_patched_files_parse(pkg):
    C.apply_chain(pkg, upto="chat_status")
    assert C.node_check(C.dist_file(pkg, "cli.mjs")).returncode == 0
    r = C.node_check(C.dist_file(pkg, "chrome-client.js"))
    assert r.returncode == 0, r.stderr
    client = C.read(pkg, "chrome-client.js")
    assert 'events.set("agent-status"' in client and "EventSource" not in client
    assert 'bubbleHeadHtml("Agent", entry.at)' in client and 'bubbleHeadHtml("You", entry.at)' in client


def test_moved_anchor_fails_closed_and_writes_nothing(pkg):
    C.apply_chain(pkg, upto="brand")
    cli = C.dist_file(pkg, "cli.mjs")
    src = open(cli, encoding="utf-8").read()
    src = src.replace('client.sendEvent("agent-presence", presenceEventData(key, presence, activePolls, deliveredFeedback));',
                      'client.sendEvent("agent-presence", presenceEventData(key, presence, activePolls, deliveredFeedback)); // moved')
    open(cli, "w", encoding="utf-8", newline="\n").write(src)
    r = K.run_patch(C.CHAT_STATUS, pkg)
    assert r.returncode == 1 and "anchor count 0" in r.stdout
    assert "chat status route" not in open(cli, encoding="utf-8").read()


def test_agent_status_reaches_websocket_and_replays(pkg, root):
    C.apply_chain(pkg, upto="chat_status")
    srv, base, key, _ = C.start(pkg, root)
    ev = other = None
    try:
        ev = C.Events(base, key)
        assert ev.wait("chat-sync") is not None and ev.wait("agent-presence") is not None
        assert K.post(base, f"/api/{key}/agent-status", {"text": "  Reading   the file "}) == 200
        assert ev.wait("agent-status", lambda d: d.get("text") == "Reading the file") is not None
        # a tab that connects later (reload, or a background tab coming back) gets the last status
        other = C.Events(base, key)
        assert other.wait("agent-status", lambda d: d.get("text") == "Reading the file") is not None
        assert K.post(base, f"/api/{key}/agent-status", {"text": ""}) == 200
        assert ev.wait("agent-status", lambda d: d.get("text") == "") is not None
        assert K.post(base, "/api/0000000000000000/agent-status", {"text": "x"}) == 404
        assert K.post(base, f"/api/{key}/agent-status", {"text": "x"}, headers={"origin": "http://evil.example"}) == 403
    finally:
        for e in (ev, other):
            if e:
                e.close()
        srv.kill()
