"""Monitor script: one line whenever a session needs the orchestrator.

  Monitor(command='py ".../watch_sessions.py"', persistent=true)

Emits:
  NEW session <name> status=<s>
  GONE session <name> (exited)
  NEW codex <name> pane=<id>                                    a non-claude agent pane appeared
  GONE codex <name> (pane closed)
  <name> REPORT | <path> (<n> bytes)                            a codex task's report file landed
  <name> QUESTION|BLOCKED|OFFER|ERROR|DONE | <last 300 chars it said>  when it has genuinely stopped
  <name> PROMPT | <the permission question>                     pane stuck on a prompt
  <name> MODE auto | <pane>                                     pane came up in auto, not bypass
  <name> TEACH-DIALOG dismissed | <pane>                        the "Teach auto mode..." dialog
  <name> CTX 4xxk - rotate                                      once, at/over the rotate threshold
  <name> STALE 30m                                              once, busy but no new assistant
                                                                 message in 30 min
  REAP: <n> candidates                                          at most hourly
  ` -> board q7`                                                a line that also landed on the board

A stop-reason line only fires once a session has genuinely stopped: registry status is idle/waiting
AND the herdr pane's agent_status is idle/done AND that has held for READY_HOLD seconds AND the text
differs from the last line emitted for that session id. Without this, a builder mid-task fires
DONE/QUESTION/OFFER on every intermediate assistant sentence (observed: "Let me start CG4-08..."
reported DONE three times) because the registry status flickers idle between tool calls.

Every print flushes or Monitor never sees it. Poll is 8 s; panes are read every 4th pass because
`herdr pane read` costs a subprocess per pane (`herdr pane list`, used for agent_status, is one call
for all panes and is cheap enough to run every poll).
"""
import os, sys, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import orchlib as L
import board as B
from orch import reap_candidates


def to_board(kind, name, said):
    """A builder's QUESTION becomes a board question; a BLOCKED one becomes a question carrying the
    exact command a human must paste; an ERROR becomes a Show block.

    Free text, no options and no pick: the orchestrator reads it, then edits the options and the
    pick in before the human ever sees it. `said` is the session's own last message, so the question
    carries the real thing rather than a summary of it.

    Never lets a board problem kill the watcher: this runs inside the Monitor loop.
    """
    try:
        key = " ".join(said.split())[:80]
        s = B.load()
        if kind in ("QUESTION", "BLOCKED"):
            # dedup on pane + the first 80 chars, so a session that re-states the same question
            # (or a watcher restart) does not stack duplicates on the board
            if any(q.get("from") == name and not q.get("answered")
                   and " ".join(q.get("context_md", "").split())[:80] == key
                   for q in s["questions"]):
                return
            if kind == "BLOCKED":
                cmd = L.blocked_command(said)
                ctx = said + (
                    f"\n\n**Run this yourself with the `!` prefix:**\n\n```\n{cmd}\n```"
                    if cmd else
                    "\n\n(No single command found in its message - read it and paste the right one.)")
                title = f"{name} is blocked by the permission classifier"
            else:
                ctx, title = said, f"{name} is waiting on an answer"
            qid = B.add_question_dict({"title": title, "from": name, "context_md": ctx})
            print(f"  -> board {qid}", flush=True)
        else:
            cap = f"{name} hit an error"
            if any(b.get("caption") == cap
                   and " ".join(b.get("body_md", "").split())[:80] == key for b in s["show"]):
                return
            sid_ = B.add_show(cap, said)
            print(f"  -> board {sid_}", flush=True)
    except Exception as e:
        print(f"  -> board write failed ({type(e).__name__}: {e})", flush=True)


POLL = 8
CTX_EVERY = 2           # passes between context scans (~16 s)
PANE_EVERY = 4          # passes between pane-content scans (~32 s)
READY_HOLD = 30         # seconds idle/waiting + pane idle/done must hold before it counts as stopped
STALE_SECS = int(os.environ.get("WATCH_STALE_MIN", 30)) * 60
REAP_HOURS = float(os.environ.get("WATCH_REAP_HOURS", 6))   # set 0 to see today's candidates
REAP_EVERY = 3600

prev = {}                # sid -> (status, name), previous poll
said_ctx = set()         # sids already warned about context
said_stale = set()
said_prompt = set()
said_mode = set()        # sids already warned that the pane is in auto, not bypass
ready_since = {}         # sid -> time it first became idle/waiting + pane idle/done
last_said = {}           # sid -> text of the last stop-reason line emitted for it
prev_agents = {}         # pane_id -> (label, kind) for non-claude panes, previous poll
agent_ready = {}         # pane_id -> time that pane first went idle/done
agent_said = {}          # pane_id -> last stop-reason text emitted for it
said_report = set()      # pane_ids whose report file has already been announced
first = True
last_reap = 0
loop = 0

while True:
    loop += 1
    try:
        me = L.orchestrator_sid()
        cur = {}
        for sid, m in L.sessions().items():
            if sid == me:
                continue
            cur[sid] = (m["status"], m["name"])
        by_sid = L.panes_by_sid()
        now = time.time()

        if not first:
            for sid, (st, name) in cur.items():
                if sid not in prev:
                    print(f"NEW session {name} status={st}", flush=True)

            for sid, (st, name) in prev.items():
                if sid not in cur:
                    print(f"GONE session {name} (exited)", flush=True)
                    ready_since.pop(sid, None)
                    last_said.pop(sid, None)

            # stop-reason lines: (a) registry idle/waiting, (b) pane agent_status idle/done,
            # (c) held for READY_HOLD seconds straight, (d) text differs from what was last emitted
            for sid, (st, name) in cur.items():
                p = by_sid.get(sid, {})
                ast = p.get("agent_status")
                ready = st in ("idle", "waiting") and ast in ("idle", "done")
                if not ready:
                    ready_since.pop(sid, None)
                    continue
                since = ready_since.setdefault(sid, now)
                if now - since < READY_HOLD:
                    continue
                kind, said = L.classify(sid)
                if last_said.get(sid) == said:
                    continue
                print(f"{name} {kind} | {said}", flush=True)
                if kind in ("QUESTION", "BLOCKED", "ERROR"):
                    to_board(kind, name, said)
                last_said[sid] = said
                said_stale.discard(sid)
                said_prompt.discard(sid)

            # context pressure - once per session, and only on a scan pass
            if loop % CTX_EVERY == 0:
                for sid, (st, name) in cur.items():
                    if sid in said_ctx:
                        continue
                    ctx, _ = L.last_usage(sid)
                    if ctx >= L.ROTATE_AT:
                        print(f"{name} CTX {ctx}k - rotate", flush=True)
                        said_ctx.add(sid)

            # a busy session whose last assistant message stopped moving - not file mtime, which
            # every incoming peer message and idle-notice subscription also touches
            for sid, (st, name) in cur.items():
                if st != "busy" or sid in said_stale:
                    continue
                ts = L.last_assistant_ts(sid)
                if ts and now - ts > STALE_SECS:
                    print(f"{name} STALE {STALE_SECS // 60}m", flush=True)
                    said_stale.add(sid)

            # pane state: a permission prompt, or the wrong permission mode. The auto-updater
            # flipped settings.json's defaultMode to `auto` twice on 2026-09-03, and every pane
            # spawned after that came up in auto and stalled on its first classifier hit. The
            # "Teach auto mode about your environment?" dialog swallows keystrokes until dismissed,
            # so that one is escaped here and only then reported.
            if loop % PANE_EVERY == 0:
                for sid, (st, name) in cur.items():
                    p = by_sid.get(sid)
                    if not p:
                        continue
                    txt = L.pane_read(p["pane_id"], 30)
                    mode = L.pane_mode(txt)
                    if mode == "teach-dialog":
                        L.send_keys(p["pane_id"], "escape")
                        print(f"{name} TEACH-DIALOG dismissed | {p['pane_id']}", flush=True)
                        said_mode.discard(sid)
                        continue
                    if mode == "auto" and sid not in said_mode:
                        print(f"{name} MODE auto | {p['pane_id']} - expected bypass; "
                              f"run fix_mode.py {p['pane_id']}", flush=True)
                        said_mode.add(sid)
                    elif mode == "bypass":
                        said_mode.discard(sid)
                    q = L.pending_prompt(txt)
                    if q and sid not in said_prompt:
                        print(f"{name} PROMPT | {q[:200]}", flush=True)
                        said_prompt.add(sid)

            # non-claude panes (codex): no registry entry and no .jsonl, so everything below comes
            # from herdr plus the text on the pane. Same word rules, same READY_HOLD, same dedup.
            side = L.sidecar_load()
            cur_agents = {}
            for pane_id, p in L.agent_panes().items():
                label = side.get(pane_id, {}).get("label") or p.get("label") or pane_id
                cur_agents[pane_id] = (label, p.get("agent", "?"), p.get("agent_status"))
            for pane_id, (name, akind, _) in cur_agents.items():
                if pane_id not in prev_agents:
                    print(f"NEW {akind} {name} pane={pane_id}", flush=True)
            for pane_id, (name, akind, _) in prev_agents.items():
                if pane_id not in cur_agents:
                    print(f"GONE {akind} {name} (pane closed)", flush=True)
                    agent_ready.pop(pane_id, None)
                    agent_said.pop(pane_id, None)
                    said_report.discard(pane_id)

            for pane_id, (name, akind, ast) in cur_agents.items():
                rep = side.get(pane_id, {}).get("report")
                if rep and pane_id not in said_report and os.path.exists(rep):
                    print(f"{name} REPORT | {rep} ({os.path.getsize(rep)} bytes)", flush=True)
                    said_report.add(pane_id)
                if ast not in ("idle", "done"):
                    agent_ready.pop(pane_id, None)
                    continue
                since = agent_ready.setdefault(pane_id, now)
                # reading a pane costs a subprocess each, so only classify on a scan pass
                if now - since < READY_HOLD or loop % PANE_EVERY:
                    continue
                kind, said = L.classify_pane(pane_id)
                if not said or agent_said.get(pane_id) == said:
                    continue
                print(f"{name} {kind} | {said}", flush=True)
                if kind in ("QUESTION", "BLOCKED", "ERROR"):
                    to_board(kind, name, said)
                agent_said[pane_id] = said
            prev_agents = cur_agents

            if now - last_reap > REAP_EVERY:
                n = len(reap_candidates(REAP_HOURS))
                if n:
                    print(f"REAP: {n} candidates", flush=True)
                last_reap = now

        prev = cur
        first = False
    except Exception as e:
        print(f"watch error (continuing): {type(e).__name__}: {e}", flush=True)
    time.sleep(POLL)
