"""Monitor script: every open Bridge page has someone polling it, always.

  Monitor(command='py ".../scripts/podium_watch.py"', persistent=true)

Ryan, 2026-09-10, after feedback on a learn page sat unread because its pane had gone stale and
nobody else was polling: "make sure to always be polling if I have a podium page open on a local
server." A lavish-axi page only delivers feedback to a running `lavish-axi poll`; with no poller
the send lands in a queue nobody reads, and he cannot tell the difference.

What it does, every SCAN_EVERY seconds:
  1. read ~/.lavish-axi/state.json - every session with status "open" is a page he may have open
  2. for each page whose owner is not currently *working*, start a short poll of our own,
     POLL_MS long, and loop it. A leftover `lavish-axi poll` Monitor on an idle/DONE pane does
     not count (Meditations Probe 2, 2026-09-17: the send was consumed and learn never woke).
     Stand down only while the owner pane's agent_status is working.
  3. when feedback arrives, print it IN FULL (never filtered - a grep'd poll dropped a prompt on
     2026-09-10) as one event, and forward it to the owner pane if podium-owners.json names one

The short poll is what keeps this from stealing: a page's own pane can start polling at any
moment, and within POLL_MS our poll returns empty, sees the new process, and stands down.

  py podium_watch.py [--once] [--list]

Owners: board/podium-owners.json is {<html file path>: <pane name>}; a lead registers itself by
writing that json. Nothing writes it for you - `orch.py own` is session re-parenting, a different
thing, and the `orch.py podium own` named here until 2026-09-15 never existed. Unowned feedback goes to whoever runs
this Monitor - the orchestrator - as a BRIDGE line, which is still a person reading it.
"""
import json, os, re, subprocess, sys, threading, time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import board as B
import board_watch as BW
import podium_home as PH
import orchlib as L

STATE = os.path.join(os.environ.get("LAVISH_AXI_STATE_DIR") or os.path.join(os.path.expanduser("~"), ".lavish-axi"),
                     "state.json")
OWNER_LABEL = "podium_watch"
OWNERS = os.path.join(B.BOARD, "podium-owners.json")
HEARTBEAT = os.path.join(B.BOARD, "heartbeat-podium_watch.json")
SCAN_EVERY = 20
POLL_MS = 25000
RECENT_DAYS = 7
BOARD_FILE = os.path.normcase(os.path.abspath(B.HTML))


def open_sessions():
    try:
        s = json.load(open(STATE, encoding="utf-8")).get("sessions", {})
    except Exception:
        return {}
    out = {}
    cutoff = time.time() - RECENT_DAYS * 86400
    for key, v in s.items():
        if v.get("status") != "open" or not v.get("file"):
            continue
        # lavish-axi never closes a session on its own, so state.json carries weeks of "open" design
        # pages nobody has in a tab (41 on 2026-09-10). A page he can actually be looking at was
        # touched recently: opened, polled, or sent to.
        if _epoch(v.get("updated_at")) < cutoff:
            continue
        f = os.path.normcase(os.path.abspath(v["file"]))
        if f == BOARD_FILE or f.endswith(("\\board.html", "\\board-calm.html")):
            continue                      # board_watch owns the board, its calm page and worktree twins
        out[f] = key
    return out


def _epoch(iso):
    try:
        from datetime import datetime
        return datetime.fromisoformat(str(iso).replace("Z", "+00:00")).timestamp()
    except Exception:
        return 0


_pollers_cache = (0.0, set())


def live_pollers():
    """Files that already have a `lavish-axi ... poll <file>` process. PowerShell reads the command
    lines; a cmd-shim wrapper and the node child both show up, which is fine. Cached for a few
    seconds because every page thread asks before every poll."""
    global _pollers_cache
    ts, cached = _pollers_cache
    if time.time() - ts < 8:
        return cached
    ps = ("Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*lavish*' -and "
          "$_.CommandLine -like '* poll *' } | ForEach-Object { $_.CommandLine }")
    try:
        r = subprocess.run(["pwsh", "-NoProfile", "-Command", ps], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=30)
        lines = (r.stdout or "").splitlines()
    except Exception:
        return cached                     # one slow pwsh must not make every page look unpolled
    files = set()
    for line in lines:
        if "podium_watch" in line or "--timeout-ms" in line:
            continue                      # our own short polls are not an owner
        m = re.search(r'poll\s+"?([A-Za-z]:[^"]+?\.html)"?', line)
        if m:
            files.add(os.path.normcase(os.path.abspath(m.group(1))))
    _pollers_cache = (time.time(), files)
    return files


def owners():
    try:
        d = json.load(open(OWNERS, encoding="utf-8"))
        return {os.path.normcase(os.path.abspath(k)): v for k, v in d.items()}
    except Exception:
        return {}


def live_owner(path):
    """The owner pane named for this page, but ONLY while that pane is still alive.

    A name in podium-owners.json outlives the pane that wrote it, and nothing ever cleaned it up.
    On 2026-09-15 Ryan asked a side question on his CS 549 linear-regression page and got nothing:
    the page's owner `learn-linreg` had been dead for hours, `forward()` still fired
    `herdr agent prompt learn-linreg` at it, herdr answered with an error nobody read, and the
    watcher printed "forwarded to learn-linreg" - so the feedback looked delivered and was not.
    A ghost owner is worse than no owner, because no owner at least routes to this seat.
    """
    owner = owners().get(path)
    if not owner:
        return ""
    if L.owner_is_live(owner):
        return owner
    if any(p.get("label") == owner for p in L.sidecar_load().values()):
        return owner            # a pi pane: not in the claude registry, alive in the sidecar
    _drop_owner(path, owner)
    return ""


def _drop_owner(path, owner):
    """Forget a dead page owner, so the next scan routes its feedback here instead of the void."""
    try:
        d = json.load(open(OWNERS, encoding="utf-8"))
    except Exception:
        return
    for k in [k for k in d if os.path.normcase(os.path.abspath(k)) == path]:
        d.pop(k, None)
    tmp = OWNERS + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(d, fh, indent=2)
    os.replace(tmp, OWNERS)
    print(f"BRIDGE {os.path.basename(path)}: owner {owner} is dead - dropped, "
          f"its feedback is yours now", flush=True)


def owner_is_working(path):
    """True only while the page's owner is in a live turn.

    An idle/DONE pane can still hold a lavish-axi poll Monitor. That poll consumes Ryan's
    send and nobody reads it (Meditations Probe 2, 2026-09-17). A process in live_pollers()
    is not enough.
    """
    owner = live_owner(path)
    if not owner:
        return False
    for p in L.panes():
        if (p.get("label") or "") != owner:
            continue
        return (p.get("agent_status") or "") == "working"
    return False


def poll_once(path, takeover=False):
    args = ["poll", path, "--owner", OWNER_LABEL, "--timeout-ms", str(POLL_MS)]
    if takeover:
        args.append("--takeover")
    r = subprocess.run(B.lavish_cmd(*args),
                       capture_output=True, text=True, encoding="utf-8", errors="replace",
                       timeout=POLL_MS / 1000 + 60)
    return (r.stdout or "") + (r.stderr or "")


HOLDER_RE = re.compile(r"current listener:\s*([^;)]+?)\s*;")


def listener_conflict(out):
    """lavish-axi 0.1.84 allows one poller per session. A second poll fails with LISTENER_ACTIVE
    (the holder's --owner label is in the message), and a poller that someone displaced gets
    LISTENER_REPLACED. -> (kind, holder): kind is "active", "replaced" or "".
    """
    if "LISTENER_REPLACED" in out:
        return "replaced", ""
    if "LISTENER_ACTIVE" in out:
        m = HOLDER_RE.search(out)
        return "active", (m.group(1) if m else "")
    return "", ""


def forward(path, prompts):
    owner = live_owner(path)
    if not owner:
        return ""
    text = "; ".join(f"[{p.get('tag','')}] {p.get('selector','')} {p.get('prompt','')}".strip()
                     for p in prompts)
    msg = (f"podium_watch: Ryan sent feedback on {os.path.basename(path)} while you were not "
           f"polling it. Act on it, then poll that page and keep polling: {text[:1500]}")
    try:
        subprocess.run(["herdr", "agent", "prompt", owner, msg], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=30)
        return owner
    except Exception:
        return ""


_threads = {}

# Pages already announced as "nobody polling": one line per page, not per scan, and not per
# restart either. An in-memory set was not enough - reloader.py restarts this watcher whenever
# board.py or orchlib.py changes on disk, and every restart re-announced the same abandoned pages
# (2026-09-16). watch_sessions.py has had `watcher-seen.json` for exactly this reason; this is the
# same idea for Bridge pages.
SEEN = os.path.join(B.BOARD, "podium-seen.json")


def _seen_load():
    try:
        with open(SEEN, encoding="utf-8") as fh:
            d = json.load(fh)
        return set(d) if isinstance(d, list) else set()
    except (OSError, ValueError):
        return set()


def _seen_save(s):
    try:
        os.makedirs(B.BOARD, exist_ok=True)
        tmp = SEEN + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(sorted(s), fh, indent=2)
        os.replace(tmp, SEEN)
    except OSError:
        pass                  # a watcher must never die over its own scratch file


_announced = _seen_load()


_last_warns = {}          # per page, across watch restarts (a page's own pane polling, then not)


def watch(path, stop):
    name = os.path.basename(path)
    last_warns = _last_warns.get(path, 0)
    takeover = False
    while not stop.is_set():
        if owner_is_working(path):
            print(f"BRIDGE {name}: owner is working, standing down", flush=True)
            return
        try:
            out = poll_once(path, takeover)
        except subprocess.TimeoutExpired:
            continue
        kind, holder = listener_conflict(out)
        if kind == "replaced":
            print(f"BRIDGE {name}: someone took the poll over, standing down", flush=True)
            return
        if kind == "active":
            # Someone is already polling. A working owner, or another podium_watch, is a real
            # poller: stand down. An idle/DONE pane's leftover poll is the Meditations Probe 2 case
            # (it eats the send and nobody wakes), so displace it; it gets LISTENER_REPLACED.
            if holder == OWNER_LABEL or takeover:
                print(f"BRIDGE {name}: {holder or 'another poller'} holds the poll, standing down",
                      flush=True)
                return
            takeover = True
            print(f"BRIDGE {name}: stale poller {holder or '?'} holds the poll, taking over",
                  flush=True)
            continue
        takeover = False
        prompts, warns, ended = BW.parse(out)
        if ended:
            print(f"BRIDGE {name}: session ended", flush=True)
            return
        # The browser re-reports the same overflow on every poll, and an empty poll reads as
        # 0, so the count flips N -> 0 -> N. Key on the last NON-ZERO count: say it once per
        # real change, prompt the owner once, then stay quiet until the number itself moves.
        if warns and warns != last_warns:
            last_warns = _last_warns[path] = warns
            print(f"BRIDGE {name}: LAYOUT {warns} warnings", flush=True)
            owner = live_owner(path)
            if owner:
                subprocess.run(["herdr", "agent", "prompt", owner,
                                f"podium_watch: {name} reports {warns} layout warning(s) and you "
                                f"are not polling it. Poll it, fix the overflow, keep polling."],
                               capture_output=True, text=True, timeout=30)
        if prompts:
            to = forward(path, prompts)
            for p in prompts:
                tag = p.get("tag", "")
                sel = p.get("selector", "")
                print(f"BRIDGE {name} | {tag} {sel} | {p.get('prompt', '')}".rstrip(), flush=True)
            note = f"BRIDGE {name}: {'forwarded to ' + to if to else 'NO OWNER - yours to act on'}"
            print(note, flush=True)
            if not to:
                BW.wake_orchestrator("[bridge] " + note + "\n" +
                                     "; ".join((p.get("prompt") or "")[:200] for p in prompts))


_last_session_set = None


def scan(once=False):
    global _last_session_set
    while True:
        sessions = open_sessions()
        polled = live_pollers()
        cur = frozenset(sessions)
        if cur != _last_session_set:
            _last_session_set = cur
            try:
                PH.render()
            except Exception as e:
                print(f"HOME render failed: {e}", flush=True)
        for path in sessions:
            t = _threads.get(path)
            if t and t[0].is_alive():
                continue
            if path in polled and owner_is_working(path):
                continue
            stop = threading.Event()
            th = threading.Thread(target=watch, args=(path, stop), daemon=True)
            th.start()
            _threads[path] = (th, stop)
            # Say it ONCE per page. A watch thread ends whenever its poll returns, so the scan
            # restarts it every cycle and this line was re-announcing the same abandoned pages
            # forever - five identical lines every SCAN_EVERY seconds, each one an interruption
            # for the seat reading them (2026-09-16). Taking a page over is news; still having it
            # is not. The announcement resets when the page genuinely goes away, below.
            if path not in _announced:
                _announced.add(path)
                _seen_save(_announced)
                print(f"BRIDGE {os.path.basename(path)}: nobody polling, watching it", flush=True)
        for path, (th, stop) in list(_threads.items()):
            if path not in sessions:
                stop.set()
                _threads.pop(path, None)
                _announced.discard(path)     # gone for real, so say it again if it comes back
                _seen_save(_announced)
        if once:
            return
        time.sleep(SCAN_EVERY)


def main():
    if "--list" in sys.argv:
        polled = live_pollers()
        for path, key in open_sessions().items():
            who = "polled" if path in polled else "NOBODY"
            print(f"{who:7} {key} {path} owner={owners().get(path, '-')}")
        return
    from watch_sessions import start_heartbeat
    start_heartbeat(HEARTBEAT)
    scan(once="--once" in sys.argv)
    if "--once" not in sys.argv:
        while True:
            time.sleep(3600)


if __name__ == "__main__":
    main()
