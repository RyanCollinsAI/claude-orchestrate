"""Completion records and auto-closing of resolved questions on the Bridge board.

Why this exists (2026-10-02): /bearings said "no completions in 24 h" after a day of finished
work, and Bridge kept answered-in-real-life questions open. Nothing wrote done[] when a pane
reported to its owner, and nothing closed a question when its work finished or its time passed.

    py board_done.py record --session NAME --outcome "one line" [--task ID] [--group G]
    py board_done.py link QID [--task ID] [--expires ISO]
    py board_done.py close QID --reason "why" [--by NAME]
    py board_done.py sweep                 # close every question whose task is done or expired
    py board_done.py show                  # done[] newest first, plus done_tasks

Rules:
- A done record is {ts, text, group, session, task, outcome, source}. Deduped on (session, task)
  when a task id is known, else on (session, outcome with whitespace collapsed, lowercased).
- `task` defaults to the newest handoffs/task-<session>-*.md stem, the file `orch.py task` wrote.
- done[] is pruned at 24 h by `board.py prune`; `done_tasks` ({task: ts}, capped) keeps the ids
  so a question linked to a task finished days ago still closes.
- A question closes on its own only when `task` (set by `link`) is in done_tasks or `expires`
  (ISO, set by `link`) has passed. Closing always stamps answered_by, answer_reason and a Done
  line, so no question disappears without a recorded reason.
- Every write goes through board.load()/board.save(), the existing atomic tmp + os.replace path.
"""
import argparse, datetime, glob, os, re, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import orchlib as L

DONE_TASKS_CAP = 300
NOISE_OUTCOME = re.compile(r"^\s*(done\.?|ok\.?|nothing\.?)?\s*$", re.I)


def _b():
    # deferred: board.py imports this module back from sync_board
    import board
    return board


def _norm(text):
    return " ".join(str(text or "").split()).lower()


def default_task(session):
    """Stem of the newest handoffs/task-<session>-*.md, '' when there is none."""
    hits = glob.glob(os.path.join(L.HANDOFFS, f"task-{session}-*.md"))
    if not hits:
        return ""
    return os.path.splitext(os.path.basename(max(hits, key=os.path.getmtime)))[0]


def record(s, session, outcome, task="", group="", ts=None, source="report"):
    """Add one done record to state dict `s`. True when added, False when it was a duplicate or
    carried no outcome. Pure apart from the clock; the caller saves."""
    outcome = " ".join(str(outcome or "").split())
    if NOISE_OUTCOME.match(outcome):
        return False
    key_task = bool(task)
    for d in s["done"]:
        if d.get("session") != session:
            continue
        if key_task and d.get("task") == task:
            return False
        if not key_task and _norm(d.get("outcome")) == _norm(outcome):
            return False
    ts = ts or _b().now_iso()
    s["done"].insert(0, {"ts": ts, "text": f"{session}: {outcome}", "group": group,
                         "session": session, "task": task, "outcome": outcome,
                         "source": source})
    if task:
        dt = s.setdefault("done_tasks", {})
        dt[task] = ts
        if len(dt) > DONE_TASKS_CAP:
            for old in sorted(dt, key=dt.get)[:len(dt) - DONE_TASKS_CAP]:
                del dt[old]
    return True


def _close(s, q, answer, reason, by, now_s):
    q["answered"] = now_s
    q["answer"] = answer
    q["answered_by"] = by
    q["answer_reason"] = reason
    q["auto_closed"] = True
    s["done"].insert(0, {"ts": now_s, "text": f"{q['id'].upper()} {q.get('title', '')}: {answer} "
                         f"[closed by {by}: {reason}]", "group": q.get("group", ""),
                         "session": by, "task": q.get("task", ""), "outcome": reason,
                         "source": "auto-close"})


def close_resolved(s, now=None):
    """Close every open question whose linked task is done or whose expiry passed. -> [(qid, reason)].
    Pure apart from the clock; the caller saves."""
    now = now or datetime.datetime.now().astimezone()
    now_s = now.replace(microsecond=0).isoformat()
    done_tasks = s.get("done_tasks", {})
    closed = []
    for q in s["questions"]:
        if q.get("answered"):
            continue
        reason = answer = None
        if q.get("task") and q["task"] in done_tasks:
            answer, reason = "CLOSED-TASK-DONE", f"linked task {q['task']} finished {done_tasks[q['task']]}"
        elif q.get("expires"):
            try:
                if datetime.datetime.fromisoformat(q["expires"]) <= now:
                    answer, reason = "CLOSED-EXPIRED", f"expired {q['expires']}"
            except ValueError:
                pass                       # unparsable clock: a human still owns it
        if reason:
            _close(s, q, answer, reason, "auto", now_s)
            closed.append((q["id"], reason))
    return closed


def on_sync(s, new_updates=None):
    """Called by board.sync_board with the state it is about to save: a watcher DONE line becomes a
    done record, then resolved questions close. Never raises into the watcher."""
    try:
        for u in reversed(new_updates or []):
            if u.get("kind") == "DONE":
                record(s, u.get("pane", ""), u.get("text", ""), group=u.get("group", ""),
                       ts=u.get("ts"), source="watcher")
        close_resolved(s)
    except Exception as e:
        print(f"board_done.on_sync failed ({type(e).__name__}: {e})", file=sys.stderr)


# ---------------------------------------------------------------- CLI

def _find_q(s, qid):
    q = next((x for x in s["questions"] if x.get("id") == qid), None)
    if not q:
        sys.exit(f"no question {qid} on the board")
    return q


def cmd_record(a):
    B = _b()
    s = B.load()
    task = a.task if a.task is not None else default_task(a.session)
    group = a.group if a.group is not None else B.group_for_who(a.session)
    if record(s, a.session, a.outcome, task=task, group=group):
        close_resolved(s)
        B.save(s)
        L.ledger.write("task.status", a.session, state="done", text=a.outcome)
        pr = re.search(r"https://github\.com/\S+/pull/\d+", a.outcome)
        if pr:
            L.ledger.write("task.pr_ready", a.session, pr=pr.group(0))
        import phone_alerts
        phone_alerts.from_done(a.session, a.outcome, task)
        print(f"recorded: {a.session}: {a.outcome}" + (f"  (task {task})" if task else ""))
    else:
        print("already recorded (or empty outcome); nothing changed")


def cmd_link(a):
    B = _b()
    s = B.load()
    q = _find_q(s, a.qid)
    if a.task:
        q["task"] = a.task
    if a.expires:
        datetime.datetime.fromisoformat(a.expires)      # refuse a clock that can never parse
        q["expires"] = a.expires
    close_resolved(s)
    B.save(s)
    print(f"linked {a.qid}: task={q.get('task', '')} expires={q.get('expires', '')}")


def cmd_close(a):
    B = _b()
    s = B.load()
    q = _find_q(s, a.qid)
    if q.get("answered"):
        print(f"{a.qid} is already closed; nothing changed")
        return
    _close(s, q, "CLOSED-STALE", a.reason, a.by, B.now_iso())
    B.save(s)
    B.slack_done(s["done"][0]["text"], q.get("group", ""))
    print(f"closed {a.qid}: {a.reason}")


def cmd_sweep(a):
    B = _b()
    s = B.load()
    closed = close_resolved(s)
    if closed:
        B.save(s)
    for qid, reason in closed:
        print(f"closed {qid}: {reason}")
    print(f"sweep: {len(closed)} closed")


def cmd_show(a):
    s = _b().load()
    for d in s["done"]:
        print(f"{d['ts']}  {d.get('text', '')}" + (f"  [{d['task']}]" if d.get("task") else ""))
    print(f"done_tasks: {len(s.get('done_tasks', {}))}")


def main(argv=None):
    p = argparse.ArgumentParser(prog="board_done.py")
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("record")
    r.add_argument("--session", required=True)
    r.add_argument("--outcome", required=True)
    r.add_argument("--task")
    r.add_argument("--group")
    r.set_defaults(fn=cmd_record)
    k = sub.add_parser("link")
    k.add_argument("qid")
    k.add_argument("--task")
    k.add_argument("--expires")
    k.set_defaults(fn=cmd_link)
    c = sub.add_parser("close")
    c.add_argument("qid")
    c.add_argument("--reason", required=True)
    c.add_argument("--by", default="orch-board-done")
    c.set_defaults(fn=cmd_close)
    sub.add_parser("sweep").set_defaults(fn=cmd_sweep)
    sub.add_parser("show").set_defaults(fn=cmd_show)
    a = p.parse_args(argv)
    a.fn(a)


if __name__ == "__main__":
    main()
