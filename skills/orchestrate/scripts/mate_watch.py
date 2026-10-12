"""One pi DOMAIN MATE's crew watcher: its wake channel, because pi has no Monitor tool.

  py reloader.py mate_watch.py <mate-name>

A CLAUDE mate learns that its crew stopped by arming a Monitor - a tool that belongs to the
session that started it. A pi mate has no such tool, and the alternative it would otherwise reach
for (polling `orch.py ls` in a loop) spends the mate's own turns on nothing. So the poll lives
here, in a separate long-running process, and every line it would have printed is dropped into the
mate's orch-pane INBOX instead, where `orch-pane.ts` delivers it as a real user message. The mate
gets woken by its crew exactly the way the orchestrator seat does. (GAP 2, 2026-09-15.)

Three things make it a watcher rather than a script that happens to loop:

- **The classification is `watch_sessions.py`'s, imported, never re-implemented.** A second copy of
  the READY_HOLD rule, the fingerprint memory, or the word lists would drift from the first within
  a week, and the two would then disagree about whether a pane had stopped. Only the delivery is
  different, and that is one function.
- **It watches ONLY this mate's crew.** Every other pane on the box is someone else's business;
  the orchestrator's own watcher already drops a live mate's crew (`watch_sessions.py`, the
  mate_names block), so the two never both report the same pane.
- **It exits when the mate's pane is gone.** A watcher for a seat that no longer exists would fill
  an inbox nothing drains. Under `reloader.py` a clean child exit ends the supervisor too.

It writes `board/heartbeat-mate-<name>.json` every 30 s exactly like the other three, so
`routines\\orchestrator-health` and `orch.py resume` can tell a live one from a dead one.

**`herdr agent wait` was evaluated and NOT used**, though it is a real bounded-wait primitive
(`herdr agent wait <pane> --until done --until blocked --timeout 30000`, proven by the pi-mate
probe, 2026-09-15). Three reasons: it takes ONE target, while a mate's crew is several panes whose
membership changes under it; it cannot wait on a pi crew pane at all, because herdr never
registers one as an agent here; and the line it would make faster cannot fire early anyway -
`READY_HOLD` deliberately holds a pane idle for 30 s before believing it, so waking 8 s sooner
buys nothing. It stays the right tool for a one-shot "wait for this one child", which is what the
probe used it for.
"""
import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import orchlib as L
import watch_sessions as W

GRACE = 60          # seconds before a missing pane counts as gone - a spawn arms this watcher
                    # before the sidecar row is fully settled, and an instant exit would look
                    # exactly like a watcher that never started.


def heartbeat_path(mate):
    return os.path.join(L.BOARD_DIR, f"heartbeat-mate-{mate}.json")


def seen_path(mate):
    """This mate's own cross-restart memory. NOT `watcher-seen.json`: the two watchers would
    otherwise mark each other's lines as already said, and whichever polled second would go
    silent."""
    return os.path.join(L.BOARD_DIR, f"mate-seen-{mate}.json")


def mate_pane(mate):
    """The live herdr pane running the pi mate called `mate`, or None once it is gone.

    Keyed on the sidecar, because a pi pane has no registry row at all. Both halves matter: the
    row must still be there (`kill` drops it) AND its pane must still be open in herdr (herdr
    recycles pane ids, so a stale row can point at somebody else's pane).
    """
    row = L.pi_mate(mate)
    return row["pane"] if row else None


def crew_sessions(mate, sess=None):
    """{sid: name} for every live CLAUDE session this mate owns."""
    sess = L.sessions() if sess is None else sess
    return {sid: m["name"] for sid, m in sess.items() if m.get("owner") == mate}


def crew_panes(mate):
    """{pane_id: (label, agent_status)} for every live non-claude pane this mate owns."""
    side = L.sidecar_load()
    out = {}
    for pane_id, p in L.other_panes().items():
        e = side.get(pane_id, {})
        if e.get("owner") != mate:
            continue
        out[pane_id] = (e.get("label") or p.get("label") or pane_id, p.get("agent_status"))
    return out


def deliver(pane, line):
    """Put one watcher line in the mate's inbox. -> the file path it was written to."""
    return L.pi_inbox_put(pane, line)


def crew_line(name, kind, said):
    """The wording the mate actually reads. `[crew]` marks the source: this arrives as a real user
    message, and without it the mate cannot tell a crew report from something Ryan typed.

    An empty `said` is still worth waking the mate for - the pane HAS stopped - but a dangling
    `|` tells it nothing, so say what happened instead. That is the only difference from
    watch_sessions' line; the gate that decides whether to emit is identical.
    """
    return f"[crew] {name} {kind} | {said or '(stopped with no readable last words - read its pane)'}"


def main(argv=None):
    ap = argparse.ArgumentParser(description="watch one pi mate's crew and wake it by inbox")
    ap.add_argument("mate", help="the pi mate's label, as `orch.py ls` prints it")
    ap.add_argument("--poll", type=float, default=W.POLL)
    ap.add_argument("--grace", type=float, default=GRACE,
                    help="seconds to wait for the mate's pane before calling it gone")
    ap.add_argument("--once", action="store_true", help="one pass, then exit (tests)")
    a = ap.parse_args(argv)
    mate = a.mate

    W.start_heartbeat(heartbeat_path(mate), extra=lambda: {"mate": mate, "pane": mate_pane(mate)})
    print(f"mate_watch {mate}: started (pid {os.getpid()})", flush=True)

    seen = W.seen_load(seen_path(mate))
    ready_since = {}        # sid / pane_id -> when it first looked stopped
    started = time.time()
    warned_no_extension = False
    loop = 0

    while True:
        loop += 1
        try:
            pane = mate_pane(mate)
            if not pane:
                if time.time() - started > a.grace:
                    print(f"mate_watch {mate}: its pane is gone; exiting", flush=True)
                    return 0
                time.sleep(a.poll)
                continue

            # The inbox is only drained while `orch-pane.ts` is live in that pane. Say so once:
            # an inbox filling up with files nobody reads looks identical to a silent crew.
            if not warned_no_extension and not L.pi_state(pane):
                warned_no_extension = True
                print(f"mate_watch {mate}: pane {pane} has no live orch-pane extension, so inbox "
                      f"files will queue until it comes back", flush=True)

            now = time.time()
            seen_dirty = False
            # Reading a pane costs a subprocess EACH, so the non-claude crew is only looked at on
            # a scan pass - watch_sessions' own PANE_EVERY rule. The first pass always scans, so a
            # `--once` run is a complete pass and not a half one.
            scan = loop == 1 or loop % W.PANE_EVERY == 0
            sess = L.sessions()
            by_sid = L.panes_by_sid()

            # (a) claude crew. Identical gate to watch_sessions: registry idle/waiting, herdr
            # agent idle/done, held READY_HOLD seconds straight, and text not emitted before.
            crew = crew_sessions(mate, sess)
            for sid, name in crew.items():
                p = by_sid.get(sid, {})
                if not (sess[sid]["status"] in ("idle", "waiting")
                        and p.get("agent_status") in ("idle", "done")):
                    ready_since.pop(sid, None)
                    continue
                if now - ready_since.setdefault(sid, now) < W.READY_HOLD:
                    continue
                ents = L.tail_entries(sid)
                kind, said = L.classify(sid, ents)
                ts = L.last_assistant_ts(sid, ents)
                # No `not said` guard here: watch_sessions has none either, and a pane that
                # stopped is news to its mate whether or not its last words survived the log
                # window. `crew_line` says so rather than sending a dangling bar.
                if W.already_seen(seen, "sid:" + sid, said, ts):
                    continue
                path = deliver(pane, crew_line(name, kind, said))
                print(f"-> {mate} inbox: {name} {kind} ({os.path.basename(path)})", flush=True)
                W.mark_seen(seen, "sid:" + sid, said, ts, kind)
                seen_dirty = True

            # (b) codex / pi crew. No registry row and no .jsonl, so the whole row comes off herdr
            # plus the text on the pane - same word rules, keyed on the pane id. Reading a pane
            # costs a subprocess EACH, so this whole block runs on a scan pass only, exactly as
            # watch_sessions does it.
            agents = crew_panes(mate) if scan else {}
            for pane_id, (name, ast) in agents.items():
                if ast not in L.FINISHED_STATUS:
                    ready_since.pop(pane_id, None)
                    continue
                if now - ready_since.setdefault(pane_id, now) < W.READY_HOLD:
                    continue
                kind, said = L.classify_pane(pane_id)
                if not said or W.already_seen(seen, "pane:" + pane_id, said):
                    continue
                path = deliver(pane, crew_line(name, kind, said))
                print(f"-> {mate} inbox: {name} {kind} ({os.path.basename(path)})", flush=True)
                W.mark_seen(seen, "pane:" + pane_id, said, 0, kind)
                seen_dirty = True

            # Prune only on a scan pass. `agents` is deliberately empty between scans, so pruning
            # every pass would reset each pi crew pane's READY_HOLD timer to zero forever and no
            # non-claude crew line would ever fire.
            if scan:
                agent_keys = set(agents)
                for key in [k for k in ready_since if k not in crew and k not in agent_keys]:
                    ready_since.pop(key, None)
                if seen_dirty:
                    live = {"sid:" + s for s in crew} | {"pane:" + p for p in agent_keys}
                    seen = W.seen_save(seen, seen_path(mate), live_keys=live)
                    seen_dirty = False
            if seen_dirty:
                # A claude-only change between scans: keep every pane key, since this pass never
                # looked at the non-claude ones and must not drop their memory.
                seen = W.seen_save(seen, seen_path(mate))
        except Exception as e:
            # Same contract as the other watchers: a bad poll is a line, never a death.
            print(f"mate_watch {mate} error (continuing): {type(e).__name__}: {e}", flush=True)
        if a.once:
            return 0
        time.sleep(a.poll)


if __name__ == "__main__":
    sys.exit(main())
