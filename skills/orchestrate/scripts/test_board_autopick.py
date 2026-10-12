"""Regression test for auto-pick (2026-09-09): a question the asker marked `--auto` has its own
recommended pick applied for it once 24h pass with no human answer.

Runs against an isolated board dir (ORCH_BOARD_DIR) and with the Slack mirror off (PODIUM_SLACK=0),
so it never touches the real board or posts anything. The config gate is driven through
ORCH_AUTOPICK_ENABLED rather than by editing config.json.
    py test_board_autopick.py
"""
import json, os, subprocess, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
BOARD_PY = os.path.join(HERE, "board.py")

failures = []


def check(label, cond):
    print(("ok   " if cond else "FAIL ") + label)
    if not cond:
        failures.append(label)


def run(tmpdir, *args, enabled=False, expect_fail=False):
    env = dict(os.environ, ORCH_BOARD_DIR=tmpdir, PODIUM_SLACK="0",
               ORCH_AUTOPICK_ENABLED="1" if enabled else "0")
    r = subprocess.run([sys.executable, BOARD_PY, *args], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", env=env, timeout=60)
    if expect_fail:
        if r.returncode == 0:
            raise SystemExit(f"board.py {args} was supposed to fail\n{r.stdout}")
        return (r.stdout or "") + (r.stderr or "")
    if r.returncode != 0:
        raise SystemExit(f"board.py {args} exited {r.returncode}\n{r.stdout}\n{r.stderr}")
    return r.stdout


def state(tmpdir):
    return json.load(open(os.path.join(tmpdir, "state.json"), encoding="utf-8"))


def q(s, qid):
    return next(x for x in s["questions"] if x["id"] == qid)


with tempfile.TemporaryDirectory() as tmp:
    # --auto with no --pick has nothing to apply, so it is refused at add time
    out = run(tmp, "add-question", "--id", "qbad", "--title", "no pick", "--context", "c",
              "--auto", expect_fail=True)
    check("--auto without --pick is refused", "--auto needs a --pick" in out)
    check("the refused question was not written", not os.path.exists(os.path.join(tmp, "state.json")))

    # a fresh --auto question: marked, with a clock in the future
    run(tmp, "add-question", "--id", "q1", "--title", "not due yet", "--context", "c",
        "--option", "A=keep", "--option", "B=drop", "--pick", "A", "--auto", "--group", "routines")
    s = state(tmp)
    check("--auto is stored on the question", q(s, "q1")["auto"] is True)
    check("--auto sets a due timestamp", bool(q(s, "q1")["auto_after"]))

    # not due: the clock has 24h left, so autopick leaves it alone
    out = run(tmp, "autopick", enabled=True)
    check("a question that is not due prints 'nothing due'", "nothing due" in out)
    check("a not-due question stays open", q(state(tmp), "q1")["answered"] is None)

    # a question whose clock already ran out (negative --auto-hours puts it in the past)
    run(tmp, "add-question", "--id", "q2", "--title", "overdue", "--context", "c",
        "--option", "A=yes", "--option", "B=no", "--pick", "B", "--auto", "--auto-hours=-1",
        "--group", "webapp")
    # and one nobody marked auto, due or not - it must never be touched
    run(tmp, "add-question", "--id", "q3", "--title", "human only", "--context", "c",
        "--option", "A=yes", "--pick", "A")

    # config off: the real run refuses and says why, and changes nothing
    out = run(tmp, "autopick", enabled=False, expect_fail=True)
    check("autopick refuses while autopick_enabled is false", "autopick is off" in out)
    check("the refused run answered nothing", q(state(tmp), "q2")["answered"] is None)

    # --dry-run works even with the gate off, and still writes nothing
    out = run(tmp, "autopick", "--dry-run", enabled=False)
    check("--dry-run names the question it would fire", "would auto-picked q2: B" in out)
    check("--dry-run does not touch a non-auto question", "q3" not in out)
    check("--dry-run answered nothing", q(state(tmp), "q2")["answered"] is None)

    # the real thing
    out = run(tmp, "autopick", enabled=True)
    s = state(tmp)
    check("autopick prints the line it applied", "auto-picked q2: B" in out)
    check("the due question is answered with its own pick", q(s, "q2")["answer"] == "B")
    check("the applied answer is stamped answered_by=auto", q(s, "q2")["answered_by"] == "auto")
    check("the due question is marked answered", bool(q(s, "q2")["answered"]))
    done = s["done"][0]
    check("Done carries the auto-picked line", done["text"] == "auto-picked q2: B")
    check("the Done line inherits the question's group", done["group"] == "webapp")

    # a question nobody marked --auto is never picked for, and neither is a not-due one
    check("a non-auto question is untouched", q(s, "q3")["answered"] is None)
    check("the not-due auto question is still untouched", q(s, "q1")["answered"] is None)

    # running again is a no-op: an answered question is no longer a candidate
    out = run(tmp, "autopick", enabled=True)
    check("a second run has nothing left to do", "nothing due" in out)
    check("the second run did not stack a second Done line",
          len([d for d in state(tmp)["done"] if d["text"] == "auto-picked q2: B"]) == 1)

    # the page can draw the clock
    html = open(os.path.join(tmp, "board.html"), encoding="utf-8").read()
    check("board.html defines autoTag()", "function autoTag" in html)

if failures:
    print(f"\n{len(failures)} FAILED: " + ", ".join(failures))
    sys.exit(1)
print("\nall autopick checks passed")
