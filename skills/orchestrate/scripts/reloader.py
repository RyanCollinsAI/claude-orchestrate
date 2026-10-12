"""A tiny supervisor Monitor launches directly, instead of the watcher script itself:

  Monitor(command='py ".../scripts/reloader.py" ".../scripts/board_watch.py"', persistent=true)

It runs the given script as a CHILD process, relays every line of the child's stdout to its own
stdout the moment it arrives (Monitor reads THIS process's pipe, never the child's), and restarts
that child whenever the script's own file, `board.py`, or `orchlib.py` next to it changes on disk
(add more with `--also FILE`). It prints one `RELOAD <script>: <file> changed` line first.

Built 2026-09-10 after `board.py` was fixed but `board_watch.py`, `podium_watch.py`, and
`watch_sessions.py` each held a stale import and kept rewriting the old `board.html` on every poll
- the fix looked broken for an hour until all three were restarted by hand.

The first version did the reload from *inside* the watcher with `os.execv(sys.executable,
[sys.executable] + sys.argv)`, self-re-exec. A live review caught the flaw before ship: on
Windows, `os.execv` is spawn-and-exit under the hood, not a true in-place replace - the ORIGINAL
process actually exits the moment a reload fires and a new, unrelated pid takes over. A Monitor
tracking that pid by its process handle sees the watcher die on the very first reload - exactly
the silent-watcher failure this feature exists to fix. This version never exits the process
Monitor is watching at all: only a CHILD gets killed and relaunched, forever, from the one
long-lived parent pid Monitor holds.

  py reloader.py [--also FILE ...] [--interval N] <script> [script's own args...]
"""
import argparse
import os
import subprocess
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import orchlib as L         # noise_gate only

DEFAULT_INTERVAL = 5
SETTLE = 45                 # seconds the watched files must sit still before one reload fires


def _mtimes(files):
    """{path: mtime} for whichever of `files` exist right now. A missing file (typo, not yet
    written) is left out rather than raising, so it can never trigger a reload on its own."""
    out = {}
    for f in files:
        try:
            out[f] = os.path.getmtime(f)
        except OSError:
            pass
    return out


def _first_changed(baseline, current):
    """The first path in `current` whose mtime differs from `baseline`, or None. A path missing
    from `baseline` (didn't exist at startup) is not a change - only a path both sides already
    know about and now disagree on the mtime of."""
    for f, mtime in current.items():
        if f in baseline and mtime != baseline[f]:
            return f
    return None


def default_watch_set(script):
    """The script itself, plus `board.py`/`orchlib.py` next to it, whichever actually exist -
    every one of the three watchers imports both."""
    here = os.path.dirname(os.path.abspath(script))
    candidates = [os.path.abspath(script), os.path.join(here, "board.py"),
                  os.path.join(here, "orchlib.py")]
    return [f for f in candidates if os.path.exists(f)]


def _pump(pipe):
    """Relay a child's stdout to ours, one line at a time, flushed immediately - this line IS
    what a Monitor reads, so buffering it would make the watcher look stalled."""
    for line in iter(pipe.readline, ""):
        sys.stdout.write(line)
        sys.stdout.flush()
    pipe.close()


def run(script, script_args, files, interval=DEFAULT_INTERVAL, settle=SETTLE):
    """The supervisor loop. Never returns on its own while the child keeps running - this is the
    process Monitor holds, so a reload must never look like this process exiting; only the child
    churns. Exits (relaying the child's own exit code) only once the child exits by itself, e.g.
    `--once`, or a crash - that is a real stop, not a reload, and should surface as one."""
    argv = [sys.executable, os.path.abspath(script)] + list(script_args)
    while True:
        baseline = _mtimes(files)
        proc = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                 text=True, encoding="utf-8", errors="replace", bufsize=1)
        pump = threading.Thread(target=_pump, args=(proc.stdout,), daemon=True)
        pump.start()
        while True:
            time.sleep(interval)
            if proc.poll() is not None:
                pump.join(timeout=5)
                sys.exit(proc.returncode)
            changed = _first_changed(baseline, _mtimes(files))
            if changed:
                # A lead editing board.py saved it 14 times in an hour on 2026-09-14 and every
                # save restarted all three watchers. Wait until the watched files have sat still
                # for SETTLE seconds, then reload once for the whole burst.
                quiet = _mtimes(files)
                while True:
                    time.sleep(settle)
                    now = _mtimes(files)
                    if proc.poll() is not None or now == quiet:
                        break
                    quiet = now
                # A reload is routine - somebody edited a watcher and it restarted. The same script
                # reloading over and over is a crash loop, and that he wants (audit 0913, E).
                say, hits = L.noise_gate(f"reload:{os.path.basename(script)}")
                if say:
                    print(f"RELOAD {os.path.basename(script)}: {changed} changed "
                          f"({hits}x recently)", flush=True)
                proc.terminate()
                try:
                    proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait()
                pump.join(timeout=5)
                break                      # respawn with a fresh baseline


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--also", action="append", default=[],
                     help="extra file to watch, beyond the script itself + board.py/orchlib.py")
    ap.add_argument("--interval", type=int, default=DEFAULT_INTERVAL)
    ap.add_argument("--settle", type=float, default=SETTLE,
                     help="seconds the watched files must sit still before one reload fires")
    ap.add_argument("script")
    ap.add_argument("script_args", nargs=argparse.REMAINDER)
    a = ap.parse_args(argv)
    files = default_watch_set(a.script) + a.also
    run(a.script, a.script_args, files, a.interval, a.settle)


if __name__ == "__main__":
    main()
