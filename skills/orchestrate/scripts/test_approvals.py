import os, sys, tempfile, unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import approvals as A

ACTION = "submit BA 323 Quiz 4 on Canvas"


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        p = mock.patch.object(A, "APPROVALS_FILE", os.path.join(self.tmp, "approvals.json"))
        p.start()
        self.addCleanup(p.stop)
        self.seat = True
        for target, val in (("_is_minter", lambda: self.seat),
                            ("_caller_is", lambda n: True)):
            q = mock.patch.object(A, target, val)
            q.start()
            self.addCleanup(q.stop)
        q = mock.patch.object(A.L, "caller_name", return_value="orchestrator")
        q.start()
        self.addCleanup(q.stop)

    def mint(self, **kw):
        a = dict(target="school-mate", action=ACTION, words="submit the BA 323")
        a.update(kw)
        return A.mint(a["target"], a["action"], a["words"])


class TestMint(Base):
    def test_seat_mints_a_record(self):
        r = self.mint()
        self.assertTrue(r["id"].startswith("ap-"))
        self.assertEqual(r["expires"] - r["ts"], 1800)
        self.assertEqual(A.active()[0]["id"], r["id"])

    def test_peer_cannot_mint(self):
        self.seat = False
        with self.assertRaises(SystemExit) as cm:
            self.mint()
        self.assertIn("REFUSED", str(cm.exception))
        self.assertEqual(A._load(), [])

    def test_needs_words_and_action(self):
        with self.assertRaises(SystemExit):
            self.mint(words="  ")
        with self.assertRaises(SystemExit):
            self.mint(action="")


class TestCheck(Base):
    def test_valid_then_single_use(self):
        r = self.mint()
        ok, why = A.check(r["id"], "school-mate", ACTION)
        self.assertTrue(ok, why)
        ok, why = A.check(r["id"], "school-mate", ACTION)
        self.assertFalse(ok)
        self.assertIn("already used", why)

    def test_forged_and_unknown_id_refused(self):
        self.mint()
        ok, why = A.check("ap-0000000000", "school-mate", ACTION)
        self.assertFalse(ok)
        self.assertIn("no such", why)

    def test_wrong_target_refused_and_not_consumed(self):
        r = self.mint()
        self.assertFalse(A.check(r["id"], "app-mate", ACTION)[0])
        self.assertTrue(A.check(r["id"], "school-mate", ACTION)[0])

    def test_different_action_refused(self):
        r = self.mint()
        ok, why = A.check(r["id"], "school-mate", "submit BA 370 Quiz 1")
        self.assertFalse(ok)
        self.assertIn("covers", why)

    def test_whitespace_and_case_in_action_ignored(self):
        r = self.mint()
        self.assertTrue(A.check(r["id"], "School-Mate", "  Submit  BA 323 quiz 4 on canvas ")[0])

    def test_expired_refused(self):
        r = self.mint()
        ok, why = A.check(r["id"], "school-mate", ACTION, now=r["ts"] + 1801)
        self.assertFalse(ok)
        self.assertEqual(why, "expired")

    def test_other_pane_cannot_spend_it(self):
        r = self.mint()
        with mock.patch.object(A, "_caller_is", lambda n: False):
            ok, why = A.check(r["id"], "school-mate", ACTION)
        self.assertFalse(ok)
        self.assertIn("not the pane", why)
        self.assertEqual(len(A.active()), 1)

    def test_prompt_suffix_names_id_and_action_and_refuses_dead_id(self):
        r = self.mint()
        s = A.approval_suffix(r["id"])
        self.assertIn(r["id"], s)
        self.assertIn(ACTION, s)
        A.check(r["id"], "school-mate", ACTION)
        with self.assertRaises(SystemExit):
            A.approval_suffix(r["id"])


if __name__ == "__main__":
    unittest.main()
