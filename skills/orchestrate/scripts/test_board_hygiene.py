"""Regression test for board hygiene (2026-09-13, board item improve-20260913 #2).

Part 1: a group can only carry one open question at a time.

Part 2: aged-question handling, corrected same day after the first cut auto-withdrew on age alone
and took four live asks off the real board (q95 dash review, bts-first-reel-970 dash approve,
catalog-saves-0910, q97 internship tabs). The rule now: WITHDRAWN-AGED only when the asking pane
is gone AND the text has no action-item shape (dash, review, submit, save, sign in, book, pay);
everything else past 72h gets a once-a-day AGED-STILL-OPEN line and moves to the top instead.
withdraw_aged_enabled defaults off (mirrors autopick_enabled) - the first cut had no such gate and
fired on its very first heartbeat tick.

Runs against an isolated board dir (ORCH_BOARD_DIR) and with the Slack mirror off (PODIUM_SLACK=0),
so it never touches the real board.
    py test_board_hygiene.py
"""
import datetime, json, os, subprocess, sys, tempfile, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
BOARD_PY = os.path.join(HERE, "board.py")
sys.path.insert(0, HERE)

failures = []


def check(label, cond):
    print(("ok   " if cond else "FAIL ") + label)
    if not cond:
        failures.append(label)


def run(tmpdir, *args, enabled=False, expect_fail=False):
    env = dict(os.environ, ORCH_BOARD_DIR=tmpdir, PODIUM_SLACK="0",
               ORCH_WITHDRAW_AGED_ENABLED="1" if enabled else "0")
    r = subprocess.run([sys.executable, BOARD_PY, *args], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", env=env, timeout=60)
    if expect_fail:
        if r.returncode == 0:
            raise SystemExit(f"board.py {args} was supposed to fail\n{r.stdout}")
        return (r.stdout or "") + (r.stderr or "")
    if r.returncode != 0:
        raise SystemExit(f"board.py {args} exited {r.returncode}\n{r.stdout}\n{r.stderr}")
    return r.stdout


def state_path(tmpdir):
    return os.path.join(tmpdir, "state.json")


def state(tmpdir):
    return json.load(open(state_path(tmpdir), encoding="utf-8"))


def save_state(tmpdir, s):
    json.dump(s, open(state_path(tmpdir), "w", encoding="utf-8"))


def q(s, qid):
    return next(x for x in s["questions"] if x["id"] == qid)


def age_hours(tmpdir, qid, hours):
    """Back-date a question's `created` so an aged check sees it as `hours` old."""
    s = state(tmpdir)
    stamp = (datetime.datetime.now().astimezone() - datetime.timedelta(hours=hours)).isoformat()
    q(s, qid)["created"] = stamp
    save_state(tmpdir, s)


# ---------------------------------------------------------------- one open question per group

with tempfile.TemporaryDirectory() as tmp:
    run(tmp, "add-question", "--id", "g1", "--title", "first ask", "--context", "c",
        "--option", "A=yes", "--group", "orch")
    out = run(tmp, "add-question", "--id", "g2", "--title", "second ask", "--context", "c",
              "--option", "A=yes", "--group", "orch", expect_fail=True)
    check("a second open question in the same group is refused", "refused" in out)
    check("the refusal names the open question it collided with", "g1" in out)
    check("the refused question was never written", not any(
        x["id"] == "g2" for x in state(tmp)["questions"]))

    run(tmp, "add-question", "--id", "g3", "--title", "other tab", "--context", "c",
        "--option", "A=yes", "--group", "webapp")
    check("a different group is not blocked", any(
        x["id"] == "g3" for x in state(tmp)["questions"]))

    run(tmp, "answer", "g1", "A")
    run(tmp, "add-question", "--id", "g4", "--title", "third ask", "--context", "c",
        "--option", "A=yes", "--group", "orch")
    check("a group frees up once its open question is answered", any(
        x["id"] == "g4" for x in state(tmp)["questions"]))

    run(tmp, "add-question", "--id", "g5", "--title", "no group a", "--context", "c",
        "--option", "A=yes")
    out = run(tmp, "add-question", "--id", "g6", "--title", "no group b", "--context", "c",
              "--option", "A=yes")
    check("ungrouped questions are never deduped against each other", "added g6" in out)

# ---------------------------------------------------------------- withdraw-aged is off by default

with tempfile.TemporaryDirectory() as tmp:
    run(tmp, "add-question", "--id", "w1", "--title", "plain ask, dead pane", "--context", "c",
        "--option", "A=yes", "--group", "orch", "--from", "ghost-pane-nonexistent")
    age_hours(tmp, "w1", 80)
    out = run(tmp, "withdraw-aged", enabled=False, expect_fail=True)
    check("withdraw-aged refuses while the flag is off", "withdraw-aged is off" in out)
    check("a refused run touched nothing", q(state(tmp), "w1")["answered"] is None)

    out = run(tmp, "withdraw-aged", "--dry-run", enabled=False)
    check("--dry-run works even with the gate off", "w1 WITHDRAWN-AGED" in out)
    check("--dry-run answered nothing", q(state(tmp), "w1")["answered"] is None)

# ---------------------------------------------------------------- action-item shape is protected

with tempfile.TemporaryDirectory() as tmp:
    # all four use a --from pane that cannot possibly be live, isolating the action-item check
    run(tmp, "add-question", "--id", "w2", "--title", "dash review: 7 reels waiting", "--context",
        "c", "--option", "A=yes", "--group", "channela", "--from", "ghost-pane-nonexistent")
    run(tmp, "add-question", "--id", "w3", "--title", "internships: submit each tab", "--context",
        "c", "--option", "A=yes", "--group", "career", "--from", "ghost-pane-nonexistent")
    run(tmp, "add-question", "--id", "w4", "--title", "plain status update", "--context", "c",
        "--option", "A=yes", "--group", "routines", "--from", "ghost-pane-nonexistent")
    for qid in ("w2", "w3", "w4"):
        age_hours(tmp, qid, 80)

    out = run(tmp, "withdraw-aged", "--dry-run", enabled=False)
    check("a dash-review item is never withdrawn", "w2 WITHDRAWN-AGED" not in out)
    check("a submit-shaped item is never withdrawn", "w3 WITHDRAWN-AGED" not in out)
    check("a plain aged item with a dead asker is withdrawn", "w4 WITHDRAWN-AGED" in out)
    check("the action-item ones resurface instead", "w2 AGED-STILL-OPEN" in out
          and "w3 AGED-STILL-OPEN" in out)

    out = run(tmp, "withdraw-aged", enabled=True)
    s = state(tmp)
    check("the real run withdraws only the plain one", q(s, "w4")["answer"] == "WITHDRAWN-AGED")
    check("the dash-review item stays open", q(s, "w2")["answered"] is None)
    check("the submit-shaped item stays open", q(s, "w3")["answered"] is None)
    check("a resurfaced question moves to the top of the list", s["questions"][0]["id"] in ("w2", "w3"))
    check("a resurfaced question is stamped so it does not resurface again today",
          bool(q(s, "w2").get("aged_notice_at")))

    # running again same-day: the plain one is gone, the resurfaced ones don't re-fire within 24h
    out = run(tmp, "withdraw-aged", "--dry-run", enabled=False)
    check("a second same-day run does not re-notice a freshly resurfaced question",
          "w2 AGED-STILL-OPEN" not in out and "w3 AGED-STILL-OPEN" not in out)

if failures:
    print(f"\n{len(failures)} FAILED: " + ", ".join(failures))
    sys.exit(1)
print("\nall CLI-level board hygiene checks passed")


# ---------------------------------------------------------------- pure-function unit tests

import board as B


class TestActionItemShape(unittest.TestCase):

    def test_dash_and_review_count(self):
        self.assertTrue(B.has_action_item_shape({"title": "Review on the dash", "context_md": ""}))

    def test_submit_save_signin_book_pay_count(self):
        for word in ("submit", "save the file", "sign in", "book a room", "pay the invoice"):
            self.assertTrue(B.has_action_item_shape({"title": word, "context_md": ""}), word)

    def test_plain_status_does_not_count(self):
        self.assertFalse(B.has_action_item_shape({"title": "pipeline ran clean overnight",
                                                   "context_md": "nothing to do"}))

    def test_word_boundary_does_not_false_positive(self):
        # "book" inside "Facebook", "pay" inside "display" must not trip the gate
        self.assertFalse(B.has_action_item_shape({"title": "Facebook API display bug",
                                                   "context_md": ""}))


class TestWithdrawableAged(unittest.TestCase):

    def now(self):
        return datetime.datetime.now().astimezone()

    def old_q(self, **over):
        base = {"title": "plain aged ask", "context_md": "", "from": "gone-pane", "answered": None,
                "created": (self.now() - datetime.timedelta(hours=80)).isoformat()}
        base.update(over)
        return base

    def test_dead_pane_no_action_shape_is_withdrawable(self):
        self.assertTrue(B.withdrawable_aged(self.old_q(), self.now(), live_labels=set()))

    def test_live_pane_is_never_withdrawn(self):
        q = self.old_q(**{"from": "still-here"})
        self.assertFalse(B.withdrawable_aged(q, self.now(), live_labels={"still-here"}))

    def test_action_item_shape_is_never_withdrawn_even_with_dead_pane(self):
        q = self.old_q(title="dash review: approve or reject")
        self.assertFalse(B.withdrawable_aged(q, self.now(), live_labels=set()))

    def test_too_fresh_is_never_withdrawn(self):
        q = self.old_q(created=self.now().isoformat())
        self.assertFalse(B.withdrawable_aged(q, self.now(), live_labels=set()))


if __name__ == "__main__":
    unittest.main()
