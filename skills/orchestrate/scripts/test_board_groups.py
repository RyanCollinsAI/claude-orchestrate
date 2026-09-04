"""Regression test for the board's group/tab logic (board-tabs, 2026-09-04).

No herdr needs to be running: every case below either passes --group explicitly or relies on
inheritance from a question/session already on the board, so `group_for_who()`'s live pane lookup
is exercised on its empty-result path only (no live panes to find -> "").

Runs against an isolated board dir (ORCH_BOARD_DIR) so it never touches the real board/state.json.
    py test_board_groups.py
"""
import json, os, subprocess, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
BOARD_PY = os.path.join(HERE, "board.py")

failures = []


def check(label, cond):
    print(("ok   " if cond else "FAIL ") + label)
    if not cond:
        failures.append(label)


def run(tmpdir, *args):
    env = dict(os.environ, ORCH_BOARD_DIR=tmpdir)
    r = subprocess.run([sys.executable, BOARD_PY, *args], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", env=env, timeout=60)
    if r.returncode != 0:
        raise SystemExit(f"board.py {args} exited {r.returncode}\n{r.stdout}\n{r.stderr}")
    return r.stdout


def state(tmpdir):
    return json.load(open(os.path.join(tmpdir, "state.json"), encoding="utf-8"))


with tempfile.TemporaryDirectory() as tmp:
    # explicit --group on add-question
    run(tmp, "add-question", "--id", "q1", "--title", "t1", "--context", "c1",
        "--group", "web-app")
    s = state(tmp)
    check("add-question --group is stored verbatim",
          next(q for q in s["questions"] if q["id"] == "q1")["group"] == "web-app")

    # a question with no --group and no live pane resolves to "" (Needs-you strip, no tab)
    run(tmp, "add-question", "--id", "q2", "--title", "t2", "--context", "c2",
        "--from", "no-such-live-pane")
    s = state(tmp)
    check("unresolvable --from falls back to '' (All only)",
          next(q for q in s["questions"] if q["id"] == "q2")["group"] == "")

    # show inherits its group from --for's question when no --group is given
    run(tmp, "show", "--caption", "cap1", "--text", "body1", "--for", "q1")
    s = state(tmp)
    check("show --for inherits the question's group",
          s["show"][0]["group"] == "web-app")

    # show's own --group wins over inheritance
    run(tmp, "show", "--caption", "cap2", "--text", "body2", "--for", "q1", "--group", "ideas-pipeline")
    s = state(tmp)
    check("show --group overrides --for inheritance",
          s["show"][0]["group"] == "ideas-pipeline")

    # done with --group
    run(tmp, "done", "shipped it", "--group", "routines")
    s = state(tmp)
    check("done --group is stored",
          s["done"][0]["group"] == "routines")

    # answering a question appends a done line that inherits the question's group
    run(tmp, "answer", "q1", "A")
    s = state(tmp)
    ans_line = next(d for d in s["done"] if d["text"].startswith("Q1 "))
    check("answer's done line inherits the question's group",
          ans_line["group"] == "web-app")

    # session rows carry a group key (empty without a live pane), never a KeyError
    run(tmp, "session", "some-pane", "--doing", "x", "--state", "working")
    s = state(tmp)
    check("session row always has a group key", "group" in s["sessions"][0])

    # legacy state (no group keys at all) loads clean and gets backfilled, not crashed
    legacy = {
        "updated": "2026-01-01T00:00:00-08:00", "header": {"usage": "", "note": ""},
        "questions": [{"id": "qx", "title": "old", "from": "", "context_md": "", "options": [],
                       "pick": "", "pick_why": "", "inputs": [], "created": "2026-01-01T00:00:00-08:00",
                       "answered": None, "answer": None}],
        "show": [], "sessions": [], "done": [],
    }
    json.dump(legacy, open(os.path.join(tmp, "state.json"), "w", encoding="utf-8"))
    run(tmp, "render")
    s = state(tmp)
    check("a legacy question with no group key renders without crashing and is backfilled",
          s["questions"][0].get("group") == "")

    # rendered board.html carries the tab scaffolding
    html = open(os.path.join(tmp, "board.html"), encoding="utf-8").read()
    check("board.html has the tab bar mount point", 'id="tabs"' in html)
    check("board.html defines renderTabs()", "function renderTabs" in html)

    # next_qid never reuses an id `prune` has already dropped from questions[] (2026-09-04:
    # two unrelated auto-posted questions both landing on "Q1" read as the same question)
    fresh = os.path.join(tmp, "fresh")
    os.makedirs(fresh, exist_ok=True)
    run(fresh, "add-question", "--id", "q1", "--title", "first", "--context", "c")
    run(fresh, "answer", "q1", "A")
    run(fresh, "prune", "--days", "0")
    s = state(fresh)
    check("prune actually dropped the answered question", not s["questions"])
    run(fresh, "add-question", "--title", "second, id picked automatically", "--context", "c")
    s = state(fresh)
    check("next auto-id does not reuse the pruned q1",
          s["questions"][0]["id"] != "q1")

    # sync_board: one call merges session rows by pane and prepends+caps the updates feed
    sys.path.insert(0, HERE)
    os.environ["ORCH_BOARD_DIR"] = fresh
    import importlib
    import board as B
    importlib.reload(B)
    B.sync_board(session_rows=[{"pane": "p1", "doing": "x", "model": "sonnet",
                                 "state": "working", "note": "", "group": "g1", "updated": ""}])
    B.sync_board(session_rows=[{"pane": "p1", "state": "done"}])
    s = state(fresh)
    row = next(r for r in s["sessions"] if r["pane"] == "p1")
    check("sync_board merges a second call into the same pane's row instead of duplicating it",
          len([r for r in s["sessions"] if r["pane"] == "p1"]) == 1 and row["state"] == "done"
          and row["doing"] == "x")
    B.sync_board(new_updates=[{"ts": "t2", "pane": "p1", "kind": "DONE", "text": "second", "group": ""}])
    s = state(fresh)
    check("sync_board prepends new updates (newest first)", s["updates"][0]["text"] == "second")
    del os.environ["ORCH_BOARD_DIR"]

    # looks_like_a_real_question: the auto-post gate, narrower than classify_words' QUESTION
    import orchlib as L
    check("a routine status update is not a real question",
          not L.looks_like_a_real_question("Nothing to do right now, waiting on your next instruction."))
    check("holding-for-go is not a real question",
          not L.looks_like_a_real_question("I am holding for go."))
    check("a literal question mark is a real question",
          L.looks_like_a_real_question("Should I ship this to prod?"))
    check("an explicit decision ask is a real question with no question mark",
          L.looks_like_a_real_question("Your call on which option to take."))

if failures:
    print(f"\n{len(failures)} FAILED: " + ", ".join(failures))
    sys.exit(1)
print("\nall group/tab checks passed")
