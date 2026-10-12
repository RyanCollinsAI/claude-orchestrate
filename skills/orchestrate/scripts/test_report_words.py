"""A finished report must not flag ERROR, and `checkout` meaning a git checkout is not a purchase.

2026-10-04: delegate-gate-fix ended its turn with the text below. The watchdog flagged it ERROR
(the adjective in "One failed send does not stop the rest") and unstick-mate escalated it as "stuck
on a decision" (`checkout`, from "the live checkout"). Verbatim report text.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import orchlib as L  # noqa: E402
import unstick as U  # noqa: E402

REPORT = (
    "Both are built and on main. I sent the report to vault-ac.\n\n"
    "- **Check first:** neither existed. Spawn prompts had the seat name baked in, and there was no "
    "announce or lookup command.\n"
    "- **Announce:** `py orch.py seat <label> --announce` sets the seat. Then it messages every live "
    "session once. One failed send does not stop the rest.\n"
    "- **Lookup:** `py orch.py seat --who` prints only the seat name. Every spawned prompt now says to "
    "run it if the report target is gone. I built a command, not a hook.\n"
    "- **Did it work:** yes. All 331 tests pass. `seat --who` prints `vault-ac` on the live checkout. "
    "I did not run `--announce` on the live fleet.\n"
    "- **What you do now:** nothing. Next time the seat moves, run `seat <label> --announce`."
)


def test_the_finished_report_is_done():
    assert L.classify_words(" ".join(REPORT.split())) == "DONE"


def test_the_finished_report_is_not_escalated():
    assert U.classify(REPORT)[0] != "ESCALATE"


def test_git_checkout_words_are_not_a_purchase():
    for said in ("It is on the live checkout.", "Ran git checkout main in the worktree.",
                 "The shared checkout is clean.", "Check out the diff when you can."):
        assert U.classify(said)[0] != "ESCALATE", said


def test_a_real_purchase_still_escalates():
    for said in ("Ready to proceed to checkout - go?", "I am at the checkout page, card form is open.",
                 "Should I place the order?", "Add to cart done, now the checkout button."):
        assert U.classify(said)[0] == "ESCALATE", said


def test_real_failures_still_error():
    for t in ("The build failed.", "tests failed: 3, stopping", "it failed to connect to the server",
              "Deploy failed: build exceeded the limit."):
        assert L.classify_words(t) == "ERROR", t


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
    print("ok")
