"""Idempotence, apply-order, loud-anchor-failure and behavior checks for patch_lavish_state_cache.py.

Works on COPIES of the pristine 0.1.84 dist in a temp dir (lavish_testkit; LAVISH_CLI_MJS / LAVISH_DIST), never the
live install. Each copy is first brought to the stock + atomic-state shape, which the cache layers on.

Behavior half: the fully patched copy (atomic state + state cache) runs as a real server on a free port with a
throwaway state dir, takes ~200 concurrent state-changing requests, and must end with a valid state.json that
holds every update. It also checks the external-change re-read (stat stamp) and the synchronous flush on shutdown.

    py -m pytest test_patch_lavish_memory.py
"""
import json
import os
import random
import threading
import time

import pytest

import lavish_testkit as kit

pytestmark = pytest.mark.skipif(not kit.available(), reason="pristine lavish-axi dist or its node_modules not found")

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(HERE, "patch_lavish_state_cache.py")
ATOMIC = os.path.join(HERE, "patch_lavish_atomic_state.py")
CACHE_MARK = "patched: state stays in memory, flushed to disk at most every 250 ms"


@pytest.fixture(scope="module")
def root():
    r = kit.make_root("lavish-mem-test-")
    os.makedirs(os.path.join(r, "art"))
    yield r
    time.sleep(0.3)
    kit.remove_tree(r)


@pytest.fixture
def pkg(root):
    p = kit.make_pkg(root, f"pkg{random.randrange(10**9)}")
    r = kit.run_patch(ATOMIC, p)
    assert r.returncode == 0, r.stdout + r.stderr
    return p


def cli_path(pkg):
    return os.path.join(pkg, "dist", "cli.mjs")


def read(pkg):
    with open(cli_path(pkg), encoding="utf-8") as f:
        return f.read()


def write(pkg, text):
    with open(cli_path(pkg), "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


def test_idempotent_and_valid_js(pkg):
    assert kit.run_patch(CACHE, pkg, "--check").returncode == 1
    first = kit.run_patch(CACHE, pkg)
    assert first.returncode == 0, first.stdout + first.stderr
    once = read(pkg)
    assert once.count(CACHE_MARK) == 1
    assert once.count("async writeStateToDisk(state) {") == 1   # the rename hit only the atomic writer
    assert once.count("async writeState(state) {") == 1         # and the wrapper kept its own name
    assert kit.run_patch(CACHE, pkg, "--check").returncode == 0
    for script in (ATOMIC, CACHE):                              # second pass: no diff at all
        again = kit.run_patch(script, pkg)
        assert again.returncode == 0
        assert "already patched" in again.stdout
    assert read(pkg) == once
    node = kit.node_check(cli_path(pkg))
    assert node.returncode == 0, node.stderr


def test_refuses_without_atomic_state(pkg):
    src = read(pkg).replace("atomic state.json write, tolerant read", "XX", 1)
    write(pkg, src)
    res = kit.run_patch(CACHE, pkg)
    assert res.returncode == 1
    assert "run patch_lavish_atomic_state.py first" in res.stdout
    assert read(pkg) == src


def test_moved_anchor_fails_loudly_and_writes_nothing(pkg):
    src = read(pkg)
    old = "    if (shuttingDown) return;\n    shuttingDown = true;\n"
    assert old in src
    moved = src.replace(old, "    if (shuttingDown) return;\n    shuttingDown2 = true;\n", 1)
    write(pkg, moved)
    res = kit.run_patch(CACHE, pkg)
    assert res.returncode == 1, res.stdout
    assert "anchor count 0 != 1" in res.stdout
    assert read(pkg) == moved


@pytest.fixture
def served(root, pkg):
    assert kit.run_patch(CACHE, pkg).returncode == 0
    state_dir = os.path.join(root, f"state{random.randrange(10**9)}")
    srv, base = kit.start_server(pkg, state_dir, kit.free_port())
    yield pkg, srv, base, state_dir
    srv.kill()


def load_state(state_dir):
    return json.load(open(os.path.join(state_dir, "state.json"), encoding="utf-8"))


def test_200_concurrent_updates_all_land_and_state_json_is_valid(root, served):
    pkg, srv, base, state_dir = served
    art = os.path.join(root, "art")
    keys = []
    for i in range(10):
        key, status = kit.open_session(base, art, f"c{random.randrange(10**9)}.html")
        assert status == 200
        keys.append(key)
    sent = []

    def reply(k, i):
        sent.append(kit.post(base, f"/api/{k}/agent-reply", {"text": f"reply-{k}-{i}"}))

    def prompt(k, i):
        sent.append(kit.post(base, f"/api/{k}/prompts", {"prompts": [{"uid": f"u{i}", "prompt": f"prompt-{k}-{i}",
                                                                      "selector": "h1", "tag": "h1", "text": "x"}]}))
    jobs = [(reply, keys[i % 10], i) for i in range(120)] + [(prompt, keys[i % 10], i) for i in range(80)]
    random.shuffle(jobs)
    ts = [threading.Thread(target=f, args=(k, i)) for f, k, i in jobs]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert len(sent) == 200 and set(sent) == {200}
    kit.post(base, "/shutdown", {})                             # flushes the cache synchronously, so no timing guess
    srv.wait(timeout=20)
    sessions = load_state(state_dir)["sessions"]
    replies = {e["text"] for s in sessions.values() for e in s.get("chat", []) if e.get("text", "").startswith("reply-")}
    prompts = {p["prompt"] for s in sessions.values() for p in s.get("prompts", [])}
    print(f"concurrent soak: replies {len(replies)}/120, prompts {len(prompts)}/80")
    assert len(replies) == 120
    assert len(prompts) == 80
    assert not [n for n in os.listdir(state_dir) if n.endswith(".tmp")]


def test_external_change_to_state_json_is_reread(root, served):
    pkg, srv, base, state_dir = served
    key, status = kit.open_session(base, os.path.join(root, "art"), "ext.html")
    assert status == 200
    assert kit.post(base, f"/api/{key}/agent-reply", {"text": "first"}) == 200
    path = os.path.join(state_dir, "state.json")
    deadline = time.time() + 30
    while not os.path.exists(path) and time.time() < deadline:  # first flush lands after the 250 ms debounce; load stretches it
        time.sleep(0.05)
    assert kit.post(base, "/api/extsession/agent-reply", {"text": "x"}) == 404
    state = load_state(state_dir)
    state["sessions"]["extsession"] = {**state["sessions"][key], "key": "extsession", "chat": []}
    with open(os.path.join(state_dir, "state.json"), "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)
    assert kit.post(base, "/api/extsession/agent-reply", {"text": "from outside"}) == 200


def test_shutdown_flushes_pending_write_synchronously(root, served):
    pkg, srv, base, state_dir = served
    key, status = kit.open_session(base, os.path.join(root, "art"), "bye.html")
    assert status == 200
    assert kit.post(base, f"/api/{key}/agent-reply", {"text": "last words"}) == 200
    kit.post(base, "/shutdown", {})                             # inside the 250 ms window
    srv.wait(timeout=20)
    chat = load_state(state_dir)["sessions"][key]["chat"]
    assert any(e.get("text") == "last words" for e in chat)
