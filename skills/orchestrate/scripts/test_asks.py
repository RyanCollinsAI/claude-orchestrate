import os, sys, tempfile, unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import asks as K
import approvals as A

ACTION = "submit BA 323 Quiz 4 on Canvas"


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        for mod, attr, name in ((K, "ASKS_FILE", "asks.json"), (A, "APPROVALS_FILE", "approvals.json")):
            p = mock.patch.object(mod, attr, os.path.join(self.tmp, name))
            p.start()
            self.addCleanup(p.stop)
        self.seat = True
        for obj, attr, fn in ((K.L, "caller_is_seat", lambda: self.seat),
                              (A, "_is_minter", lambda: self.seat),
                              (K.L, "caller_name", lambda: "school-mate")):
            p = mock.patch.object(obj, attr, fn)
            p.start()
            self.addCleanup(p.stop)
        self.sent = []

    def send(self, name, text):
        self.sent.append((name, text))


class TestAsk(Base):
    def test_add_and_list(self):
        r = K.add("Use A or B?", ["A", "B"])
        self.assertTrue(r["id"].startswith("q-"))
        self.assertEqual(r["from"], "school-mate")
        self.assertEqual([x["id"] for x in K.open_rows()], [r["id"]])

    def test_needs_two_options_and_a_question(self):
        with self.assertRaises(SystemExit):
            K.add("Q?", ["only one"])
        with self.assertRaises(SystemExit):
            K.add("  ", ["A", "B"])

    def test_stale_ask_is_not_open(self):
        K.add("Old?", ["A", "B"], now=1000)
        self.assertEqual(K.open_rows(now=1000 + K.OPEN_SECONDS + 1), [])

    def test_cli_parses_flags_and_question(self):
        with mock.patch("builtins.print"):
            K.cmd_ask(["ask", "--options", "Red|Blue", "Which", "colour?"])
        r = K.open_rows()[0]
        self.assertEqual((r["question"], r["options"]), ("Which colour?", ["Red", "Blue"]))

    def test_cli_defaults_to_yes_no(self):
        with mock.patch("builtins.print"):
            K.cmd_ask(["ask", "Ship", "it?"])
        self.assertEqual(K.open_rows()[0]["options"], ["Yes", "No"])


class TestAnswer(Base):
    def test_answer_by_number_and_text_goes_to_asker(self):
        r = K.add("A or B?", ["A", "B"])
        _rec, opt = K.answer(r["id"], "2", self.send)
        self.assertEqual(opt, "B")
        self.assertEqual(self.sent[0][0], "school-mate")
        self.assertIn('picked "B"', self.sent[0][1])
        self.assertEqual(K.open_rows(), [])
        r2 = K.add("again?", ["A", "B"])
        self.assertEqual(K.answer(r2["id"], "a", self.send)[1], "A")

    def test_answer_twice_refused(self):
        r = K.add("A or B?", ["A", "B"])
        K.answer(r["id"], "1", self.send)
        with self.assertRaises(SystemExit):
            K.answer(r["id"], "2", self.send)
        self.assertEqual(len(self.sent), 1)

    def test_bad_option_refused_and_stays_open(self):
        r = K.add("A or B?", ["A", "B"])
        with self.assertRaises(SystemExit):
            K.answer(r["id"], "C", self.send)
        self.assertEqual(len(K.open_rows()), 1)
        self.assertEqual(self.sent, [])

    def test_peer_cannot_answer(self):
        r = K.add("A or B?", ["A", "B"])
        self.seat = False
        with self.assertRaises(SystemExit) as cm:
            K.answer(r["id"], "1", self.send)
        self.assertIn("REFUSED", str(cm.exception))
        self.assertEqual(len(K.open_rows()), 1)

    def test_action_go_ahead_mints_approval(self):
        r = K.add("Submit?", ["Yes", "No"], action=ACTION)
        K.answer(r["id"], "1", self.send)
        self.assertIn("approval ap-", self.sent[0][1])
        ap = A.active()[0]
        self.assertEqual((ap["target"], ap["action"]), ("school-mate", ACTION))

    def test_action_other_pick_mints_nothing(self):
        r = K.add("Submit?", ["Yes", "No"], action=ACTION)
        K.answer(r["id"], "2", self.send)
        self.assertEqual(A.active(), [])
        self.assertIn("Do NOT", self.sent[0][1])
        self.assertNotIn("approval ap-", self.sent[0][1])

    def test_plain_ask_mints_nothing(self):
        r = K.add("A or B?", ["A", "B"])
        K.answer(r["id"], "1", self.send)
        self.assertEqual(A.active(), [])


if __name__ == "__main__":
    unittest.main()
