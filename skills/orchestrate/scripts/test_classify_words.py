"""classify_words: a clean report that SAYS 'error' is not an ERROR.

2026-09-09: the health routine nudged the orchestrator four times in one afternoon for a lead
whose last line was "typecheck 0, lint 0 errors, 444 files / 5597 tests". Every nudge cost a
human-equivalent look. A real failure still classifies as ERROR.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import orchlib as L  # noqa: E402

CLEAN = [
    "Numbers: typecheck 0, lint 0 errors, 444 files / 5597 tests. +8, all mine. Next: the 390px pass.",
    "Mutation: putting 2 back fails six tests including the round trip. Shipped as 9abae33.",
    "Stopping the page rendering the card fails three AskClient tests, so it is not decorative.",
    "Full suite green: 1077 passed, 0 failed, 2 skipped. Both worktrees released.",
    "errors: 0, warnings: 3. Deployed dr9spvq7i Ready.",
    "Ran it without errors on plain main too, so it is pre-existing. Next step.",
    "breaking the fast-forward kills its own test only. Merged.",
    "/ask had no error.tsx, so any throw gave a blank page. Fixed, plus a test that fails if a "
    "primary route loses its boundary. Three mutations run, all killed. Starting step 3.",
    "Added an error boundary and an error page for /plan. Main green.",
]
REAL = [
    "Traceback (most recent call last):\n  File x.py line 3\nKeyError: 'foo'",
    "npm test exited 1: 3 failed, 440 passed. Stopping here.",
    "Error: ENOENT no such file or directory, open 'web/.env'",
    "could not connect to 127.0.0.1:8787 after 5 tries",
    "Deploy failed: build exceeded the 45 minute limit.",
]


def test_clean_reports_are_done():
    for t in CLEAN:
        assert L.classify_words(t) == "DONE", t


def test_real_failures_are_error():
    for t in REAL:
        assert L.classify_words(t) == "ERROR", t


def test_tool_error_flag_still_wins():
    assert L.classify_words("all good, 0 errors", tool_error=True) == "ERROR"


def test_question_outranks_error_mention():
    assert L.classify_words("the build failed twice. Which branch do you want me on?") == "QUESTION"


if __name__ == "__main__":
    test_clean_reports_are_done()
    test_real_failures_are_error()
    test_tool_error_flag_still_wins()
    test_question_outranks_error_mention()
    print("ok: 4 tests")
