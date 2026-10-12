"""Tests for reloader.py: the mtime-change detection as pure functions, plus a live end-to-end
test of the supervisor itself - the thing a 2026-09-10 review caught the first version missing.

    py test_reloader.py
"""
import os
import json
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import reloader as R  # noqa: E402

RELOADER_PY = os.path.join(os.path.dirname(os.path.abspath(__file__)), "reloader.py")


def test_mtimes_skips_missing_file():
    with tempfile.TemporaryDirectory() as d:
        real = os.path.join(d, "a.py")
        open(real, "w").close()
        missing = os.path.join(d, "does-not-exist.py")
        m = R._mtimes([real, missing])
        assert real in m, m
        assert missing not in m, m


def test_first_changed_none_when_untouched():
    with tempfile.TemporaryDirectory() as d:
        f = os.path.join(d, "a.py")
        open(f, "w").close()
        baseline = R._mtimes([f])
        current = R._mtimes([f])
        assert R._first_changed(baseline, current) is None


def test_first_changed_fires_on_touch():
    with tempfile.TemporaryDirectory() as d:
        f = os.path.join(d, "a.py")
        open(f, "w").close()
        baseline = R._mtimes([f])
        # bump the mtime forward explicitly - some filesystems have 1-2s mtime resolution, so a
        # bare re-write racing the same clock tick would flake this test.
        newer = baseline[f] + 5
        os.utime(f, (newer, newer))
        current = R._mtimes([f])
        assert R._first_changed(baseline, current) == f


def test_first_changed_ignores_a_brand_new_file():
    """A file that did not exist at baseline time is not a 'change' - only a path both sides
    already agree existed and now disagree on the mtime of."""
    with tempfile.TemporaryDirectory() as d:
        f = os.path.join(d, "a.py")
        baseline = R._mtimes([f])          # f does not exist yet -> {}
        open(f, "w").close()
        current = R._mtimes([f])
        assert R._first_changed(baseline, current) is None


PROBE_TPL = """
import time
n = 0
while True:
    print(f"POLL {tag} #{{n}}", flush=True)
    n += 1
    time.sleep(0.3)
"""


def _write_probe(path, tag):
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(PROBE_TPL.format(tag=tag))


def _read_until(proc, marker, timeout=20):
    """Lines from the supervisor's stdout until one contains `marker`, or raise on timeout."""
    deadline = time.time() + timeout
    lines = []
    while time.time() < deadline:
        line = proc.stdout.readline()
        if not line:
            time.sleep(0.05)
            continue
        lines.append(line)
        if marker in line:
            return line, lines
    raise AssertionError(f"never saw {marker!r} in: {lines}")


def _isolated_env(board_dir, script):
    """A child env with its own board dir, pre-seeded so the next reload actually prints.

    `reloader.run` gates its RELOAD line through `L.noise_gate` (threshold 3): the first two
    reloads inside the window are silent by design, so a test that wants to SEE the line has to
    start from two existing hits. Seeding a temp dir also keeps the real board/noise.json clean.
    """
    now = time.time()
    key = "reload:" + os.path.basename(script)
    with open(os.path.join(board_dir, "noise.json"), "w", encoding="utf-8") as fh:
        json.dump({key: [now - 5, now - 4]}, fh)
    return {**os.environ, "ORCH_BOARD_DIR": board_dir}


def test_supervisor_survives_a_reload():
    """The live case a 2026-09-10 review caught the execv-based first version failing: the pid
    Monitor launches and tracks must still be running - unexited - after a reload, with only a
    RELOAD line as the seam. Reproduces exactly how Monitor launches a watcher: one subprocess,
    read its stdout as it comes."""
    with tempfile.TemporaryDirectory() as d:
        script = os.path.join(d, "probe.py")
        _write_probe(script, "v1")
        proc = subprocess.Popen([sys.executable, RELOADER_PY, "--settle", "1", script],
                                 stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                 text=True, encoding="utf-8", errors="replace", bufsize=1,
                                 env=_isolated_env(d, script))
        launched_pid = proc.pid
        try:
            _read_until(proc, "POLL v1 #")
            _write_probe(script, "v2")            # bump mtime + change the running code
            line, _ = _read_until(proc, "RELOAD")
            assert "probe.py" in line, line
            assert proc.pid == launched_pid, "the pid Monitor holds must never change"
            assert proc.poll() is None, "the supervisor must not have exited on a reload"
            _read_until(proc, "POLL v2 #")
            assert proc.poll() is None, "still running after the child came back up"
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()


def test_burst_of_saves_reloads_once():
    """A lead saved board.py 14 times in an hour on 2026-09-14 and each save restarted all three
    watchers. With a settle window, saves that land inside it collapse into ONE reload."""
    with tempfile.TemporaryDirectory() as d:
        script = os.path.join(d, "probe.py")
        _write_probe(script, "v1")
        proc = subprocess.Popen([sys.executable, RELOADER_PY, "--interval", "1", "--settle", "3",
                                 script],
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                text=True, encoding="utf-8", errors="replace", bufsize=1,
                                env=_isolated_env(d, script))
        try:
            _read_until(proc, "POLL v1 #")
            for v in ("v2", "v3", "v4"):
                _write_probe(script, v)
                time.sleep(1)
            _, seen = _read_until(proc, "RELOAD")
            _, seen4 = _read_until(proc, "POLL v4 #")   # one reload, and it ran the LAST save
            # The load-bearing assertion, and the one the noise gate cannot affect: the
            # intermediate saves never got their own reload, so their probes never ran.
            body = "".join(seen + seen4)
            assert "POLL v2 #" not in body, body
            assert "POLL v3 #" not in body, body
            # a whole extra settle window of silence: the probe keeps polling every 0.3s, so
            # readline never blocks for long; no second RELOAD may appear in it
            deadline = time.time() + 5
            later = []
            while time.time() < deadline:
                line = proc.stdout.readline()
                if line:
                    later.append(line)
            assert not any("RELOAD" in l for l in later), later
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()


def test_supervisor_relays_the_childs_exit():
    """A child that stops on its own (crash, or a one-shot flag) is a real stop, not a reload -
    the supervisor must exit too, relaying that exit code, rather than silently hang forever."""
    with tempfile.TemporaryDirectory() as d:
        script = os.path.join(d, "once.py")
        with open(script, "w", encoding="utf-8") as fh:
            fh.write("print('done', flush=True)\n")
        proc = subprocess.Popen([sys.executable, RELOADER_PY, "--interval", "1", script],
                                 stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                 text=True, encoding="utf-8", errors="replace")
        out, _ = proc.communicate(timeout=15)
        assert "done" in out, out
        assert proc.returncode == 0, (proc.returncode, out)


if __name__ == "__main__":
    test_mtimes_skips_missing_file()
    test_first_changed_none_when_untouched()
    test_first_changed_fires_on_touch()
    test_first_changed_ignores_a_brand_new_file()
    test_supervisor_survives_a_reload()
    test_burst_of_saves_reloads_once()
    test_supervisor_relays_the_childs_exit()
    print("ok: 6 tests")
