"""orch.py board ready - what can be started now.

Reads the Markdown backlog tables in Vault\\_backlog\\*.md plus board/state.json and board/holds.json.
Read only: it never writes any of them.
An item is ready when it is queued, every item it names as a blocker is finished, and no active hold names it.

Idea from tasks-axi (MIT, https://github.com/kunchenguid/tasks-axi, src/derive.ts readyTasks/blockedIds).
Same rules: a blocker that cannot be found counts as finished, a hold with no `until` stays forever.
No code is copied.

Backlog row shape: `| ID-123 | title ... | ... | Status |`. The status is the last cell (first word):
  queued: new, queued, open, todo      active: picked, building, wip, in      finished: shipped, done, built, merged, skipped, dropped
Blockers: `blocked-by: ID, ID` or `depends on ID` anywhere in the row.
Holds: board/holds.json keys, matched to an item ID. A row with an `until` (UTC ISO) holds only until then.

Usage: ready.py [--json] [--limit N] [--all] [--backlog DIR] [--board DIR]
"""
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

from toon_out import count_line, to_toon

QUEUED = {"new", "queued", "open", "todo"}
ACTIVE = {"picked", "building", "wip", "in"}
FINISHED = {"shipped", "done", "built", "merged", "skipped", "dropped"}
ID_RE = re.compile(r"^([A-Z][A-Z0-9]*-\d+)\b")
DEP_RE = re.compile(r"(?:blocked[- ]by|depends on)[:\s]+((?:[A-Z][A-Z0-9]*-\d+(?:[,\s]+(?:and\s+)?)?)+)", re.I)
FIELDS = ["id", "title", "source"]
ALL_FIELDS = ["id", "state", "why", "title", "source"]


def _board_dir(arg):
    if arg:
        return Path(arg)
    here = Path(__file__).resolve().parent.parent / "board"
    return here if (here / "state.json").exists() else Path.home() / ".claude/skills/orchestrate/board"


def _load(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def parse_backlog(folder):
    """Return (items, malformed). An item is {id, title, status, deps, source}; a malformed line starts like an item but has no usable status."""
    items, malformed = [], 0
    for f in sorted(Path(folder).glob("*.md")):
        for line in f.read_text(encoding="utf-8", errors="replace").splitlines():
            if not line.startswith("|"):
                continue
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            m = ID_RE.match(cells[0].strip("` *"))
            if not m:
                continue
            word = (re.match(r"\W*([a-z]+)", cells[-1].lower()) or [0, ""])[1] if len(cells) > 2 else ""
            if word not in QUEUED | ACTIVE | FINISHED:
                malformed += 1
                continue
            deps = [d.upper() for g in DEP_RE.findall(line) for d in re.findall(r"[A-Za-z][A-Za-z0-9]*-\d+", g)]
            items.append(dict(id=m.group(1), title=re.sub(r"[`*]", "", cells[1])[:90], status=word, deps=deps, source=f.name))
    return items, malformed


def _hold_active(row, now):
    until = row.get("until") if isinstance(row, dict) else None
    if not until:
        return True
    try:
        return datetime.fromisoformat(str(until).replace("Z", "+00:00")).replace(tzinfo=None) > now
    except ValueError:
        return True


def classify(items, state, holds, now=None):
    """Tag every item ready / blocked / held / active / done. A blocker that is not an item, not a live pane and not in done_tasks counts as finished."""
    now = now or datetime.now(timezone.utc).replace(tzinfo=None)
    by_id = {}
    for it in items:
        by_id.setdefault((it["source"], it["id"]), it)
        by_id.setdefault((None, it["id"]), it)
    live = {s.get("pane"): s.get("state") for s in state.get("sessions", [])}
    held = {k.upper() for k, v in holds.items() if _hold_active(v, now)}

    def unfinished(dep, source):
        blocker = by_id.get((source, dep)) or by_id.get((None, dep))
        if blocker:
            return blocker["status"] not in FINISHED
        return live.get(dep.lower()) not in (None, "done")

    for it in items:
        it["why"] = ""
        if it["status"] in FINISHED:
            it["state"] = "done"
        elif it["status"] in ACTIVE:
            it["state"] = "active"
        elif it["id"].upper() in held:
            it["state"], it["why"] = "held", "hold names it"
        elif waits := [d for d in it["deps"] if unfinished(d, it["source"])]:
            it["state"], it["why"] = "blocked", "waits on " + ",".join(waits)
        else:
            it["state"] = "ready"
    return items


def main(argv):
    if "--help" in argv or "-h" in argv:
        print(__doc__)
        return 0
    opt = lambda name: argv[argv.index(name) + 1] if name in argv and argv.index(name) + 1 < len(argv) else None
    backlog = Path(opt("--backlog") or r"C:\Users\YOURNAME\Vault\_backlog")
    board = _board_dir(opt("--board"))
    limit = opt("--limit")
    if limit is not None and not limit.isdigit():
        sys.exit(f"--limit needs a number, got {limit!r}. Run `orch.py board ready --limit 10`")
    items, malformed = parse_backlog(backlog)
    items = classify(items, _load(board / "state.json"), _load(board / "holds.json"))
    show_all = "--all" in argv
    rows = items if show_all else [i for i in items if i["state"] == "ready"]
    total = len(rows)
    rows = rows[:int(limit)] if limit else rows
    if "--json" in argv:
        print(json.dumps(dict(count=len(rows), total=total, malformed=malformed, rows=rows), indent=1))
    else:
        print(to_toon(rows, ALL_FIELDS if show_all else FIELDS))
        print(count_line(len(rows), total))
        if malformed:
            print(f"skipped: {malformed} backlog line(s) start like an item but have no status word")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
