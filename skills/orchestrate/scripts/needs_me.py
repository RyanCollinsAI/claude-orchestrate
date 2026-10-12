"""needs-me: one key jumps herdr focus to the next pane that is waiting on Ryan.

  py orch.py needs-me            list the waiting panes, oldest first (moves nothing)
  py orch.py needs-me --next     focus the oldest waiting pane not visited yet, show "N left"

A pane is waiting on Ryan when any of these holds:
  - herdr says its agent is `blocked` (a permission prompt or question UI)
  - an open Bridge question names it as the asker (`from` = its label)
  - it is parked with `orch.py hold` (waiting on Ryan)
Oldest first: a Bridge question counts from `created`, a hold from its `ts`, a blocked pane from the
first time this module saw it blocked (kept in board/needs-me.json).

Focus only ever moves inside `--next`, which runs from the herdr key Ryan presses
(`[[keys.command]]` with `type = "shell"`, see SKILL.md). Nothing here runs on a timer.
Each press visits one pane. A pane stays "visited" until it stops waiting, so repeated presses
walk the whole list; after the last one the next press starts the list over.
"""
import datetime, json, os, subprocess, time

import orchlib as L

STATE = os.path.join(L.BOARD_DIR, "needs-me.json")
BOARD_STATE = os.path.join(L.BOARD_DIR, "state.json")
HOLDS = os.path.join(L.BOARD_DIR, "holds.json")


def _load(path, default):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return default


def _save(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=1)
    os.replace(tmp, path)


def _iso_epoch(s):
    try:
        return datetime.datetime.fromisoformat(s).timestamp()
    except (TypeError, ValueError):
        return None


def waiting(panes, questions, holds, seen, now=None):
    """([(since_epoch, pane_id, label, reason)] oldest first, updated first-seen map).

    Pure: callers pass herdr's pane list, the board questions, the hold map and the first-seen
    map. A pane gets one row; its oldest reason wins."""
    now = now or time.time()
    by_label = {p.get("label"): p for p in panes if p.get("label")}
    by_id = {p["pane_id"]: p for p in panes}
    found = {}

    def add(p, since, reason):
        cur = found.get(p["pane_id"])
        if cur is None or since < cur[0]:
            found[p["pane_id"]] = (since, p["pane_id"], p.get("label") or p["pane_id"], reason)

    new_seen = {}
    for p in panes:
        if p.get("agent_status") == "blocked":
            since = seen.get(p["pane_id"], now)
            new_seen[p["pane_id"]] = since
            add(p, since, "blocked")
    for q in questions:
        if q.get("answered"):
            continue
        p = by_label.get(q.get("from")) or by_id.get(q.get("from"))
        if p:
            add(p, _iso_epoch(q.get("created")) or now, f"question {q.get('id', '')}".strip())
    for name, h in holds.items():
        p = by_label.get(name) or by_id.get(name)
        if p:
            add(p, float((h or {}).get("ts") or now), "held: " + ((h or {}).get("reason") or "waiting on Ryan"))
    return sorted(found.values()), new_seen


def _gather():
    panes = L.panes()
    board = _load(BOARD_STATE, {})
    state = _load(STATE, {})
    rows, seen = waiting(panes, board.get("questions", []), _load(HOLDS, {}), state.get("seen", {}))
    return panes, rows, seen, state


def pick_next(rows, visited, focused_pane=None):
    """(row or None, new_visited). Oldest unvisited row; the pane Ryan is already on counts as
    visited. Once every row is visited the list starts over."""
    ids = {r[1] for r in rows}
    visited = [v for v in visited if v in ids]
    if focused_pane in ids and focused_pane not in visited:
        visited.append(focused_pane)
    todo = [r for r in rows if r[1] not in visited]
    if not todo and rows:
        visited = [focused_pane] if focused_pane in ids else []
        todo = [r for r in rows if r[1] not in visited] or list(rows)
    if not todo:
        return None, visited
    row = todo[0]
    visited.append(row[1])
    return row, visited


def _run(*argv):
    return subprocess.run(["herdr", *argv], capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=15).returncode == 0


def _focus(pane, workspace_id="", tab_id=""):
    """Agent panes focus by pane id; a plain shell pane has no agent, so land on its tab."""
    if _run("agent", "focus", pane):
        return True
    return bool(workspace_id and tab_id) and _run("workspace", "focus", workspace_id) \
        and _run("tab", "focus", tab_id)


def _toast(title, body=""):
    args = ["herdr", "notification", "show", title, "--position", "bottom-right", "--sound", "none"]
    if body:
        args += ["--body", body]
    try:
        subprocess.run(args, capture_output=True, timeout=10)
    except Exception:
        pass


def _age(since, now):
    m = max(0, int((now - since) / 60))
    return f"{m}m" if m < 120 else f"{m // 60}h"


def cmd_needs_me(go=False, out=print):
    panes, rows, seen, state = _gather()
    now = time.time()
    if not go:
        _save(STATE, {"seen": seen, "visited": state.get("visited", [])})
        if not rows:
            out("nothing needs you")
        for since, pane, label, reason in rows:
            out(f"{pane:8} {label:22} {_age(since, now):>5}  {reason}")
        return rows
    focused = next((p["pane_id"] for p in panes if p.get("focused")), None)
    row, visited = pick_next(rows, state.get("visited", []), focused)
    _save(STATE, {"seen": seen, "visited": visited})
    if not row:
        _toast("Nothing needs you")
        out("nothing needs you")
        return rows
    since, pane, label, reason = row
    left = len([r for r in rows if r[1] not in visited])
    target = next((p for p in panes if p["pane_id"] == pane), {})
    ok = _focus(pane, target.get("workspace_id", ""), target.get("tab_id", ""))
    line = f"{label} ({reason}, {_age(since, now)}) - {left} more waiting" if left \
        else f"{label} ({reason}, {_age(since, now)}) - last one"
    _toast("Needs you" if ok else "Could not focus", line)
    out(("jumped to " if ok else "focus failed for ") + line)
    return rows
