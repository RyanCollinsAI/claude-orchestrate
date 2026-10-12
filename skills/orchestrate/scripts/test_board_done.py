"""Tests for board_done.py: done[] records, dedupe, auto-close of resolved questions.

Runs against an isolated board dir (ORCH_BOARD_DIR) with Slack off (PODIUM_SLACK=0).
    py test_board_done.py
"""
import datetime, json, os, subprocess, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

failures = []


def check(label, cond):
    print(("ok   " if cond else "FAIL ") + label)
    if not cond:
        failures.append(label)


def run(tmp, script, *args, expect_fail=False):
    env = dict(os.environ, ORCH_BOARD_DIR=tmp, PODIUM_SLACK="0")
    r = subprocess.run([sys.executable, os.path.join(HERE, script), *args], capture_output=True,
                       text=True, encoding="utf-8", errors="replace", env=env, timeout=60)
    if expect_fail != (r.returncode != 0):
        raise SystemExit(f"{script} {args} exit {r.returncode}\n{r.stdout}\n{r.stderr}")
    return (r.stdout or "") + (r.stderr or "")


def state(tmp):
    return json.load(open(os.path.join(tmp, "state.json"), encoding="utf-8"))


def stamp(**delta):
    return (datetime.datetime.now().astimezone() + datetime.timedelta(**delta)).isoformat()


def add_q(tmp, qid):
    run(tmp, "board.py", "add-question", "--id", qid, "--title", f"ask {qid}", "--context", "c",
        "--option", "A=yes", "--group", f"g-{qid}")


with tempfile.TemporaryDirectory() as tmp:
    run(tmp, "board_done.py", "record", "--session", "pane-a", "--outcome", "shipped the thing",
        "--task", "task-pane-a-1", "--group", "g")
    s = state(tmp)
    d = s["done"][0]
    check("record writes a done entry", len(s["done"]) == 1)
    check("entry carries session, task, outcome", (d["session"], d["task"], d["outcome"]) ==
          ("pane-a", "task-pane-a-1", "shipped the thing"))
    check("entry text is what the Done tab shows", d["text"] == "pane-a: shipped the thing")
    check("task id lands in done_tasks", "task-pane-a-1" in s["done_tasks"])

    out = run(tmp, "board_done.py", "record", "--session", "pane-a", "--outcome", "reworded",
              "--task", "task-pane-a-1", "--group", "g")
    check("same session+task is deduped", "already recorded" in out and len(state(tmp)["done"]) == 1)

    run(tmp, "board_done.py", "record", "--session", "pane-b", "--outcome", "Fixed  the BUG",
        "--task", "", "--group", "g")
    run(tmp, "board_done.py", "record", "--session", "pane-b", "--outcome", "fixed the bug",
        "--task", "", "--group", "g")
    check("no task id: same outcome text (case/space blind) is deduped",
          len([x for x in state(tmp)["done"] if x["session"] == "pane-b"]) == 1)

    out = run(tmp, "board_done.py", "record", "--session", "pane-c", "--outcome", "  ",
              "--task", "", "--group", "g")
    check("empty outcome is not recorded", len(state(tmp)["done"]) == 2)

    # auto-close by linked task
    add_q(tmp, "q1")
    run(tmp, "board_done.py", "link", "q1", "--task", "task-pane-a-1")
    check("link to an already-done task closes the question at once",
          state(tmp)["questions"][0]["answered"] is not None)
    q1 = state(tmp)["questions"][0]
    check("closed question records a reason, who, and answer",
          q1["answer"] == "CLOSED-TASK-DONE" and q1["answered_by"] == "auto"
          and "task-pane-a-1" in q1["answer_reason"])
    check("closing wrote a Done line naming the reason",
          any("q1" in x["text"].lower() and "closed by auto" in x["text"]
              for x in state(tmp)["done"]))

    # task finishes later -> record closes the linked question
    add_q(tmp, "q2")
    run(tmp, "board_done.py", "link", "q2", "--task", "task-pane-z-9")
    check("open while its task is not done", state(tmp)["questions"][1]["answered"] is None)
    run(tmp, "board_done.py", "record", "--session", "pane-z", "--outcome", "finished z",
        "--task", "task-pane-z-9", "--group", "g")
    check("recording the task done closes the question",
          [x for x in state(tmp)["questions"] if x["id"] == "q2"][0]["answer"] == "CLOSED-TASK-DONE")

    # expiry
    add_q(tmp, "q3")
    add_q(tmp, "q4")
    run(tmp, "board_done.py", "link", "q3", "--expires", stamp(hours=-1))
    run(tmp, "board_done.py", "link", "q4", "--expires", stamp(hours=+5))
    qs = {x["id"]: x for x in state(tmp)["questions"]}
    check("past expiry closes with a reason", qs["q3"]["answer"] == "CLOSED-EXPIRED"
          and qs["q3"]["answer_reason"].startswith("expired"))
    check("future expiry stays open", qs["q4"]["answered"] is None)
    run(tmp, "board_done.py", "link", "q4", "--expires", "not-a-date", expect_fail=True)

    # manual close needs a reason, is idempotent
    add_q(tmp, "q5")
    run(tmp, "board_done.py", "close", "q5", expect_fail=True)
    run(tmp, "board_done.py", "close", "q5", "--reason", "answered in chat")
    q5 = [x for x in state(tmp)["questions"] if x["id"] == "q5"][0]
    check("manual close stamps reason and closer", q5["answer_reason"] == "answered in chat"
          and q5["answered_by"] == "orch-board-done")
    n = len(state(tmp)["done"])
    out = run(tmp, "board_done.py", "close", "q5", "--reason", "again")
    check("closing a closed question changes nothing",
          "already closed" in out and len(state(tmp)["done"]) == n)

    # watcher path: sync_board turns a DONE update into a record, deduped on repeat
    import importlib
    os.environ["ORCH_BOARD_DIR"] = tmp
    os.environ["PODIUM_SLACK"] = "0"
    import board
    importlib.reload(board)
    upd = [{"ts": board.now_iso(), "pane": "pane-w", "kind": "DONE", "text": "watcher saw it done",
            "group": ""}, {"ts": board.now_iso(), "pane": "pane-w", "kind": "QUESTION",
                           "text": "not a completion", "group": ""}]
    board.sync_board(new_updates=upd)
    board.sync_board(new_updates=upd)
    w = [x for x in state(tmp)["done"] if x.get("session") == "pane-w"]
    check("a watcher DONE update becomes one done record (QUESTION ignored, repeat deduped)",
          len(w) == 1 and w[0]["source"] == "watcher")
    check("done[] and state.js stay valid JSON after all writes",
          json.loads(open(os.path.join(tmp, "state.js"), encoding="utf-8").read()
                     .split("=", 1)[1].rstrip().rstrip(";"))["done"] == state(tmp)["done"])

if failures:
    print(f"\n{len(failures)} FAILED")
    sys.exit(1)
print("\nall passed")
