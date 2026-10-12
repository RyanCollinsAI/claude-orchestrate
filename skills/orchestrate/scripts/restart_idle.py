"""py orch.py restart-idle [--dry-run] [--include-busy-when-done] [--include-seat] [--timeout 30m]
                                  [--only NAME-or-PANE ...]   (repeatable; limits the run)

After a Claude Code update or a plugin change, a running pane still holds the OLD code. This closes
each IDLE claude pane and starts it again in the same pane with `claude --resume <its exact session
id>`, so it keeps its context, model, effort, name and cwd and loads the new code.

Never `claude -c`: several sessions share a cwd, and -c picks the newest one. The session id comes
from herdr's own agent record. The launch line comes from herdr's process-info for the pane (the
live claude command line), with any old --resume/--continue/--session-id taken out and the exact
--resume added - so every flag the pane was started with comes back.

Never restarted: a pane that is working (queued instead), a pane with an open permission prompt or
a dialog, a pane with a half-typed draft in its prompt box, the pane running this command, the
orchestrator seat (unless --include-seat), and any pane that is not claude.

Busy panes are listed as QUEUED. With --include-busy-when-done this command stays running and
restarts each queued pane the moment it goes idle (give up after --timeout, default 30m).

Focus: no herdr call here moves the human's focus.
"""
import json
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import orchlib as L

IDLE_STATES = ("idle", "done")
# `--continue`/`-c` take no value; `--resume`/`-r` take an optional one; `--session-id` takes one.
NO_VALUE_FLAGS = ("--continue", "-c", "--fork-session")
VALUE_FLAGS = ("--session-id",)
OPT_VALUE_FLAGS = ("--resume", "-r")
RULE = "─" * 8
PROMPT_CHAR = "❯"


def quote(tok):
    """One token as the pane's shell (pwsh or bash) will read it back."""
    if tok and re.fullmatch(r"[\w./:=@+,\\-]+", tok):
        return tok
    return '"' + tok.replace('"', '\\"') + '"'


def launch_line(argv, sid):
    """The command to type into the pane's shell, or None when argv is not a claude launch.
    The exe path is dropped for plain `claude` (as `spawn` types it); every flag stays."""
    if not argv or os.path.basename(argv[0]).lower() not in ("claude", "claude.exe"):
        return None
    keep, i, rest = [], 0, argv[1:]
    while i < len(rest):
        t = rest[i]
        if t in NO_VALUE_FLAGS:
            i += 1
        elif t in VALUE_FLAGS:
            i += 2
        elif t in OPT_VALUE_FLAGS:
            i += 2 if i + 1 < len(rest) and not rest[i + 1].startswith("-") else 1
        elif any(t.startswith(f + "=") for f in VALUE_FLAGS + OPT_VALUE_FLAGS):
            i += 1
        else:
            keep.append(t)
            i += 1
    # A launch prompt ("claude --model haiku 'do X'") would run again on resume; drop it. It is the
    # last token, holds spaces, and follows a plain word - a spaced flag value follows its flag.
    if len(keep) >= 2 and re.search(r"\s", keep[-1]) and not keep[-2].startswith("-"):
        keep.pop()
    return " ".join(["claude", *map(quote, keep), "--resume", sid])


def has_draft(txt):
    """True when the prompt box (between the two rule lines) holds typed text."""
    lines = txt.splitlines()
    rules = [i for i, l in enumerate(lines) if l.strip().startswith(RULE)]
    if len(rules) < 2:
        return False
    for l in lines[rules[-2] + 1:rules[-1]]:
        s = l.strip().strip("│").strip()
        if s.startswith(PROMPT_CHAR):
            s = s[1:].strip()
        if s:
            return True
    return False


def claude_agents():
    d = L.herdr("agent", "list", soft=True) or {}
    return [a for a in d.get("result", {}).get("agents", []) if a.get("agent") == "claude"]


def own_pane():
    return os.environ.get("HERDR_PANE_ID", "")


def agent_for(pane):
    return next((a for a in claude_agents() if a.get("pane_id") == pane), None)


def foreground_argv(pane):
    """argv of the pane's foreground process, or None when herdr cannot say."""
    info = L.herdr("pane", "process-info", "--pane", pane, soft=True, timeout=15) or {}
    procs = (info.get("result", {}).get("process_info") or {}).get("foreground_processes") or []
    return procs[0].get("argv") if procs else None


def seat_panes():
    """Every pane that could be the orchestrator seat. A worktree's own board/ has no seat file, so
    the real one under ~/.claude is read too, and a pane named like the seat tab counts (a restart
    of the live seat happened 2026-10-02 because only the worktree copy was checked)."""
    out = {L.orchestrator_pane()}
    try:
        with open(os.path.join(L.CLAUDE_HOME, "skills", "orchestrate", "board", "orchestrator.json"),
                  encoding="utf-8") as fh:
            out.add(json.load(fh).get("pane"))
    except (OSError, ValueError, AttributeError):
        pass
    for a in claude_agents():
        if L.ORCHESTRATOR_TAB in (str(a.get("name", "")).lower(),
                                  str(a.get("terminal_title_stripped", "")).lower()):
            out.add(a.get("pane_id"))
    out.discard(None)
    return out


def judge(a, seat_pane, include_seat, me):
    """('restart'|'queue'|'skip', why) for one claude agent record. Reads the pane only when it
    could be restarted now, so a large fleet costs one `agent list` plus a few reads."""
    pane, sid, st = a.get("pane_id"), (a.get("agent_session") or {}).get("value"), L.effective_status(a)
    if pane == me:
        return "skip", "this is the pane running the command"
    if pane in L.acp_pane_ids():
        return "skip", "ACP pane (resume is session/load, not claude --resume)"
    if pane in (seat_pane if isinstance(seat_pane, (set, frozenset)) else {seat_pane}) \
            and not include_seat:
        return "skip", "orchestrator seat (use --include-seat)"
    if not sid:
        return "skip", "no session id"
    if st == "working":
        return "queue", "working"
    if st not in IDLE_STATES:
        return "skip", f"state {st} (blocked or unclear)"
    txt = L.pane_read(pane, 40)
    if L.pending_prompt(txt) or L.pane_mode(txt) == "teach-dialog":
        return "skip", "permission prompt or dialog open"
    if has_draft(txt):
        return "skip", "text typed in the prompt box"
    return "restart", "idle"


def wait_until(pred, timeout, step=0.7):
    end = time.time() + timeout
    while time.time() < end:
        if pred():
            return True
        time.sleep(step)
    return pred()


def restart_pane(a, ignore_bg=False):
    """Close claude in the pane and resume the same session in the same pane. -> (ok, detail).
    ignore_bg: the human asked for this pane (the button), so a Monitor or shell running behind an
    idle prompt does not count as busy; it dies with the exit."""
    pane, sid = a["pane_id"], a["agent_session"]["value"]
    line = launch_line(foreground_argv(pane), sid)
    if not line:
        return False, "cannot read this pane's claude launch line"
    cur = agent_for(pane)
    st = (cur or {}).get("agent_status") if ignore_bg else L.effective_status(cur)
    if not cur or st not in IDLE_STATES:
        return False, "went busy before the restart; left alone"
    L.herdr("pane", "send-text", pane, "/exit", soft=True)
    L.send_keys(pane, "enter")

    def at_shell():
        argv = foreground_argv(pane)
        return bool(argv) and os.path.basename(argv[0]).lower() not in ("claude", "claude.exe")

    # A worktree session asks "Keep or remove the worktree?" on exit; keep is the safe answer.
    time.sleep(1.5)
    tail = L.pane_read(pane, 12)
    if "Keep worktree" in tail:
        L.send_keys(pane, "1")
    elif ignore_bg and "Background work is running" in tail:   # a Monitor or shell is still running; they die with the exit
        L.send_keys(pane, "1")
    if not wait_until(at_shell, 30):
        L.send_keys(pane, "ctrl+c")
        return False, "claude did not exit; left running (cleared the typed /exit)"
    time.sleep(0.8)
    L.herdr("pane", "run", pane, line, soft=True)

    def back():
        n = agent_for(pane)
        return (bool(n) and n.get("agent_status") in IDLE_STATES and n.get("interactive_ready", True)
                and bool((n.get("agent_session") or {}).get("value")))

    if not wait_until(back, 90, step=1.5):
        return False, f"closed but did not come back. In pane {pane} run: {line}"
    n = agent_for(pane)
    new = (n.get("agent_session") or {}).get("value")
    return True, "same session" if new == sid else f"resumed as new id {str(new)[:8]} (was {sid[:8]})"


def name_of(a):
    return a.get("name") or a.get("terminal_title_stripped") or a.get("pane_id")


def parse_age(s):
    m = re.fullmatch(r"(\d+(?:\.\d+)?)([smh]?)", s or "")
    if not m:
        sys.exit(f"bad --timeout {s!r}; use 90s, 30m or 2h")
    return float(m.group(1)) * {"s": 1, "m": 60, "h": 3600, "": 1}[m.group(2)]


def table(rows):
    if not rows:
        print("no claude panes found")
        return
    w = max(len(r[1]) for r in rows)
    for pane, name, act, why in rows:
        print(f"  {act:10} {pane:8} {name:{w}}  {why}")


def cmd_restart_idle(dry_run=False, include_busy=False, include_seat=False, timeout=1800.0,
                     only=()):
    seat, me = seat_panes(), own_pane()
    rows, queue = [], []
    for a in claude_agents():
        if only and a.get("pane_id") not in only and name_of(a) not in only:
            continue
        verdict, why = judge(a, seat, include_seat, me)
        if verdict == "skip":
            rows.append((a["pane_id"], name_of(a), "SKIPPED", why))
        elif verdict == "queue":
            queue.append(a)
            rows.append((a["pane_id"], name_of(a), "QUEUED", why))
        elif dry_run:
            rows.append((a["pane_id"], name_of(a), "WOULD", "restart now"))
        else:
            ok, detail = restart_pane(a)
            rows.append((a["pane_id"], name_of(a), "RESTARTED" if ok else "FAILED", detail))
    if include_busy and queue and not dry_run:
        print(f"waiting on {len(queue)} busy pane(s) (up to {int(timeout)}s)...", flush=True)
        end, queue = time.time() + timeout, {a["pane_id"]: a for a in queue}
        while queue and time.time() < end:
            time.sleep(5)
            for pane in list(queue):
                a = agent_for(pane)
                if not a:
                    rows.append((pane, name_of(queue.pop(pane)), "SKIPPED", "pane closed while queued"))
                    continue
                verdict, why = judge(a, seat, include_seat, me)
                if verdict == "restart":
                    ok, detail = restart_pane(a)
                    rows.append((pane, name_of(a), "RESTARTED" if ok else "FAILED", detail))
                    del queue[pane]
                elif verdict == "skip" and "state" not in why:
                    rows.append((pane, name_of(a), "SKIPPED", why))
                    del queue[pane]
        rows = [r for r in rows if not (r[2] == "QUEUED" and r[0] not in queue)]
        for pane in queue:
            rows.append((pane, name_of(queue[pane]), "TIMED OUT", "still busy at the deadline"))
    elif queue and not dry_run:
        print("busy panes are listed QUEUED and left alone; add --include-busy-when-done to wait "
              "for them and restart each as it goes idle")
    table(rows)
    done = sum(1 for r in rows if r[2] == "RESTARTED")
    print(f"\n{done} restarted, {sum(1 for r in rows if r[2] in ('QUEUED', 'TIMED OUT'))} queued, "
          f"{sum(1 for r in rows if r[2] == 'SKIPPED')} skipped, "
          f"{sum(1 for r in rows if r[2] == 'FAILED')} failed")
    return rows


def main(a):
    only = [a[i + 1] for i, t in enumerate(a) if t == "--only" and i + 1 < len(a)]
    cmd_restart_idle("--dry-run" in a, "--include-busy-when-done" in a, "--include-seat" in a,
                     parse_age(a[a.index("--timeout") + 1]) if "--timeout" in a else 1800.0, only)


if __name__ == "__main__":
    main(sys.argv[1:])
