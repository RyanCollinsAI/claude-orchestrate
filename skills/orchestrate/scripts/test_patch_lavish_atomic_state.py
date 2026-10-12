"""Race test for patch_lavish_atomic_state.py. Never touches the live Bridge or ~/.lavish-axi.

Copies the PRISTINE 0.1.84 dist (lavish_testkit) to a temp dir, starts the UNPATCHED copy and the PATCHED copy on
free ports with their own state dir (seeded big, with 60 ended sessions), fires concurrent writes from many
threads while a reader thread parses state.json, and checks the file after each run.

    py -m pytest test_patch_lavish_atomic_state.py
Passes only if the patched copy stays valid, never shows a torn read, leaves no .tmp file, prunes ended
sessions to 30, survives a 72-char garbage tail, and the patch script is idempotent and passes node --check.
The unpatched copy's torn-read count is printed for comparison but not asserted (it is a race).
"""
import json
import os
import threading
import time

import pytest

import lavish_testkit as kit

pytestmark = pytest.mark.skipif(not kit.available(), reason="pristine lavish-axi dist or its node_modules not found")

HERE = os.path.dirname(os.path.abspath(__file__))
PATCHER = os.path.join(HERE, "patch_lavish_atomic_state.py")


def seed_state(path, artifact_dir):
    pad = "x" * 4000
    sessions = {}
    for i in range(60):
        sessions[f"ended{i:03d}"] = {
            "key": f"ended{i:03d}", "file": f"{artifact_dir}\\gone{i}.html", "url": "http://x", "status": "ended",
            "pending_prompts": 0, "prompts": [], "layout_warnings": [], "dom_snapshot": "", "delivery_seq": 1,
            "chat": [{"role": "agent", "text": pad, "at": "2026-01-01T00:00:00Z"}],
            "updated_at": f"2026-01-01T00:{i // 60:02d}:{i % 60:02d}Z"}
    json.dump({"sessions": sessions}, open(path, "w"), indent=2)


@pytest.fixture(scope="module")
def root():
    r = kit.make_root("lavish-race-")
    os.makedirs(os.path.join(r, "art"))
    yield r
    time.sleep(0.3)
    kit.remove_tree(r)


@pytest.fixture(scope="module")
def pkgs(root):
    orig = kit.make_pkg(root, "orig")
    patched = kit.make_pkg(root, "patched")
    for _ in range(2):
        r = kit.run_patch(PATCHER, patched)
        assert r.returncode == 0, r.stdout + r.stderr
    return orig, patched


def hammer_run(label, pkg, root):
    state_dir = os.path.join(root, label + "-state")
    art = os.path.join(root, "art")
    os.makedirs(state_dir)
    seed_state(os.path.join(state_dir, "state.json"), art)
    srv, base = kit.start_server(pkg, state_dir, kit.free_port())
    try:
        keys = []
        for i in range(30):
            key, status = kit.open_session(base, art, f"{label}{i}.html")
            assert status == 200
            keys.append(key)
        torn = [0]
        stop = threading.Event()

        def reader():
            p = os.path.join(state_dir, "state.json")
            while not stop.is_set():
                try:
                    json.loads(open(p, encoding="utf-8").read())
                except OSError:
                    pass
                except ValueError:
                    torn[0] += 1
        rt = threading.Thread(target=reader)
        rt.start()
        big = "y" * 3000

        def hammer(k, n):
            for j in range(n):
                kit.post(base, f"/api/{k}/agent-reply", {"text": big if j % 2 else "s"})
        hs = [threading.Thread(target=hammer, args=(keys[i % len(keys)], 15)) for i in range(60)]
        [t.start() for t in hs]
        [t.join() for t in hs]
        stop.set()
        rt.join()
        raw = open(os.path.join(state_dir, "state.json"), encoding="utf-8").read()
        try:
            data = json.loads(raw)
            final_ok = True
            ended = sum(1 for s in data["sessions"].values() if s.get("status") == "ended")
        except ValueError:
            final_ok, ended = False, -1
        leftovers = [n for n in os.listdir(state_dir) if n.endswith(".tmp")]
        return {"label": label, "final_file_valid": final_ok, "torn_reads": torn[0], "ended_left": ended,
                "tmp_leftovers": len(leftovers)}
    finally:
        srv.kill()


def test_patch_is_idempotent_and_valid_js(pkgs):
    _, patched = pkgs
    assert kit.run_patch(PATCHER, patched, "--check").returncode == 0
    again = kit.run_patch(PATCHER, patched)
    assert again.returncode == 0 and "already patched" in again.stdout
    node = kit.node_check(os.path.join(patched, "dist", "cli.mjs"))
    assert node.returncode == 0, node.stderr


def test_unpatched_copy_reports_not_patched(pkgs):
    orig, _ = pkgs
    assert kit.run_patch(PATCHER, orig, "--check").returncode == 1


def test_moved_anchor_fails_loudly_and_writes_nothing(root):
    pkg = kit.make_pkg(root, "moved")
    cli = os.path.join(pkg, "dist", "cli.mjs")
    src = open(cli, encoding="utf-8").read()
    moved = src.replace("  async writeState(state) {\n", "  async writeState2(state) {\n", 1)
    open(cli, "w", encoding="utf-8", newline="\n").write(moved)
    res = kit.run_patch(PATCHER, pkg)
    assert res.returncode == 1
    assert "anchor count 0 != 1" in res.stdout
    assert open(cli, encoding="utf-8").read() == moved


def test_patched_state_stays_valid_under_concurrent_writes(root, pkgs):
    orig, patched = pkgs
    before = hammer_run("orig", orig, root)
    after = hammer_run("patched", patched, root)
    print("unpatched:", before)
    print("patched:  ", after)
    assert after["final_file_valid"]
    assert after["torn_reads"] == 0
    assert after["tmp_leftovers"] == 0
    assert 0 <= after["ended_left"] <= 30


def test_patched_server_heals_a_garbage_tail(root, pkgs):
    _, patched = pkgs
    state_dir = os.path.join(root, "garbage-state")
    os.makedirs(state_dir)
    p = os.path.join(state_dir, "state.json")
    seed_state(p, os.path.join(root, "art"))
    open(p, "a").write("x" * 72)
    srv, base = kit.start_server(patched, state_dir, kit.free_port())
    try:
        key, status = kit.open_session(base, os.path.join(root, "art"), "g.html")
        assert status == 200
        json.loads(open(p, encoding="utf-8").read())
    finally:
        srv.kill()
