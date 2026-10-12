"""Tests for auto_accept.py's self-approval gate (2026-09-13, board item improve-20260913 #1).

  AA-001  a test count in the pane's recent output counts as verify evidence
  AA-002  a screenshot/proof path counts as verify evidence
  AA-003  an explicit "verified on <surface>" line counts as verify evidence
  AA-004  plain chatter with no proof does not count
  AA-005  verify_snippet returns the matched line, trimmed, for the log
  AA-006  log_decision appends one JSON line per call, newest last

Plain unittest, no dependencies, no herdr, no real panes. Everything touching disk goes to a temp
dir (auto_accept never opens a real pane in this file).

  py test_auto_accept.py
"""
import json, os, sys, tempfile, unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import auto_accept as A


class TestHasVerifyEvidence(unittest.TestCase):

    def test_test_count_counts(self):
        self.assertTrue(A.has_verify_evidence("ran the suite: 14 tests passed"))

    def test_screenshot_path_counts(self):
        self.assertTrue(A.has_verify_evidence(r"proof: C:\tmp\render-check.png"))

    def test_verified_on_line_counts(self):
        self.assertTrue(A.has_verify_evidence("verified on the Bridge page"))

    def test_plain_chatter_does_not_count(self):
        self.assertFalse(A.has_verify_evidence("looks good, shipping now"))
        self.assertFalse(A.has_verify_evidence(""))


class TestVerifySnippet(unittest.TestCase):

    def test_returns_the_matched_line_trimmed(self):
        text = "building...\n  3 tests passed  \nDo you want to proceed?"
        self.assertEqual(A.verify_snippet(text), "3 tests passed")

    def test_empty_when_nothing_matches(self):
        self.assertEqual(A.verify_snippet("no proof here"), "")


class TestLogDecision(unittest.TestCase):

    def test_appends_one_line_per_call(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "self-approvals.jsonl")
            old = A.LOG_PATH
            A.LOG_PATH = path
            try:
                id1 = A.log_decision("app-lead", "refused", "Do you want to proceed?", "")
                id2 = A.log_decision("app-lead", "accepted", "Do you want to proceed?",
                                     "5 tests passed")
            finally:
                A.LOG_PATH = old
            with open(path, encoding="utf-8") as f:
                lines = [json.loads(l) for l in f]
        self.assertEqual(len(lines), 2)
        self.assertEqual([l["decision"] for l in lines], ["refused", "accepted"])
        self.assertEqual([l["id"] for l in lines], [id1, id2])
        self.assertEqual(lines[1]["verify_snippet"], "5 tests passed")
        self.assertNotEqual(id1, id2, "each decision needs its own id so a revert can name one")


if __name__ == "__main__":
    unittest.main()
