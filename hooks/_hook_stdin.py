"""Read a hook's JSON payload from stdin without waiting for EOF.

Claude Code does not always close a hook's stdin (measured 2026-09-29 on SessionStart and
2026-09-30 on PreToolUse), so sys.stdin.read() hung every such hook until its timeout - panes sat on
"running PreToolUse hook" for minutes. This returns as soon as the bytes read so far parse as JSON,
on EOF, or after `cap` seconds, whichever comes first. Callers keep their own fail-open parsing.
"""
import json
import os
import queue
import threading
import time


def read_stdin(cap: float = 3.0, first_byte_cap: float = 1.0) -> str:
    """`first_byte_cap` bounds the wait when nothing at all has arrived: the harness writes the payload
    at once, so a stdin that stays silent has no payload to wait for."""
    q: queue.Queue = queue.Queue()

    def pump() -> None:
        while True:
            try:
                chunk = os.read(0, 65536)
            except OSError:
                chunk = b""
            q.put(chunk)
            if not chunk:
                return

    threading.Thread(target=pump, daemon=True).start()
    buf = b""
    start = time.monotonic()
    deadline = start + cap
    while True:
        left = (deadline if buf else min(deadline, start + first_byte_cap)) - time.monotonic()
        if left <= 0:
            break
        try:
            chunk = q.get(timeout=left)
        except queue.Empty:
            break
        if not chunk:
            break
        buf += chunk
        try:
            json.loads(buf.decode("utf-8"))
            break
        except ValueError:
            continue
    return buf.decode("utf-8", errors="replace")
