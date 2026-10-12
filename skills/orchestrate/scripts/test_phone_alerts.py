import datetime, os, sys, unittest

os.environ["PODIUM_SLACK"] = "0"
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import phone_alerts as P


def at(h, d=2):
    return datetime.datetime(2026, 10, d, h, 0).timestamp()


def ev(**k):
    return {"kind": "WATCHDOG", "key": k.pop("key", "k1"), "text": "x", **k}


class Rules(unittest.TestCase):
    def test_lanes(self):
        self.assertEqual(P.classify(ev(blocked_secs=700)), "ping")
        self.assertEqual(P.classify(ev(blocked_secs=300)), "digest")
        self.assertEqual(P.classify(ev(kind="DONE", mine=True)), "ping")
        self.assertEqual(P.classify(ev(kind="DONE", mine=False)), "digest")
        self.assertEqual(P.classify(ev(deadline_secs=3600 * 20)), "ping")
        self.assertEqual(P.classify(ev(deadline_secs=3600 * 30)), "digest")
        self.assertEqual(P.classify(ev(text="Stripe charged $40")), "ping")
        self.assertEqual(P.classify(ev(kind="ERROR", text="upload failed")), "ping")
        self.assertEqual(P.classify(ev(kind="ERROR", text="lint failed")), "digest")

    def test_ping_sends_then_repeat_dropped(self):
        st = P._blank()
        self.assertEqual(P.decide(ev(blocked_secs=900), st, at(12))[0], "send")
        self.assertEqual(P.decide(ev(blocked_secs=900), st, at(13)), ("drop", "repeat"))

    def test_quiet_hours_queue_except_deadline_under_2h(self):
        st = P._blank()
        self.assertEqual(P.decide(ev(key="a", blocked_secs=900), st, at(23))[0], "queued")
        self.assertEqual(P.decide(ev(key="b", deadline_secs=3 * 3600), st, at(2))[0], "queued")
        self.assertEqual(P.decide(ev(key="c", deadline_secs=3600), st, at(2))[0], "send")

    def test_loud_breaks_quiet_hours_and_quiet_does_not(self):
        st = P._blank()
        self.assertEqual(P.decide(ev(key="q", blocked_secs=900), st, at(2))[0], "queued")
        self.assertEqual(P.decide(ev(key="l", blocked_secs=900, loud=True), st, at(2))[0], "send")
        st["mute_until"] = at(3)
        self.assertEqual(P.decide(ev(key="m", blocked_secs=900, loud=True), st, at(2))[0], "queued",
                         "loud must not override a mute Ryan set himself")

    def test_daily_cap(self):
        st = P._blank()
        res = [P.decide(ev(key=f"k{i}", blocked_secs=900), st, at(12))[0] for i in range(10)]
        self.assertEqual(res.count("send"), 8)
        self.assertEqual(P.decide(ev(key="n", blocked_secs=900), st, at(12, 3))[0], "send")

    def test_mute_and_off(self):
        st = P._blank()
        st["mute_until"] = at(14)
        self.assertEqual(P.decide(ev(blocked_secs=900), st, at(13))[0], "queued")
        self.assertEqual(P.decide(ev(key="z", blocked_secs=900), P._blank(), at(13), off=True),
                         ("drop", "off"))

    def test_digest_once_per_3h(self):
        st = P._blank()
        P.decide(ev(key="a"), st, at(9))
        self.assertIn("Digest (1 items)", P.due_digest(st, at(9)))
        P.decide(ev(key="b"), st, at(10))
        self.assertEqual(P.due_digest(st, at(10)), "")
        self.assertIn("Digest", P.due_digest(st, at(13)))

    def test_digest_waits_for_quiet_end(self):
        st = P._blank()
        P.decide(ev(key="a"), st, at(23))
        self.assertEqual(P.due_digest(st, at(3)), "")
        self.assertIn("Digest", P.due_digest(st, at(8)))

    def test_parse_reply(self):
        self.assertEqual(P.parse_reply("mute 2h"), ("mute", 7200))
        self.assertEqual(P.parse_reply("<@x> Mute 30m".replace("<@x> ", "")), ("mute", 1800))
        self.assertEqual(P.parse_reply("mute"), ("mute", 7200))
        self.assertEqual(P.parse_reply("off"), ("off", 0))
        self.assertIsNone(P.parse_reply("hello there"))


class Handle(unittest.TestCase):
    def setUp(self):
        self.sent = []
        self._s, self._l, self._o = P.save, P.load, P.is_off
        self.st = P._blank()
        P.save = lambda s: None
        P.load = lambda: self.st
        P.is_off = lambda: False

    def tearDown(self):
        P.save, P.load, P.is_off = self._s, self._l, self._o

    def test_failed_send_does_not_burn_cap_or_dedupe(self):
        self.assertEqual(P.handle(ev(blocked_secs=900), at(12), sender=lambda t: ""), "drop:slack-failed")
        self.assertEqual(self.st["sent_today"], 0)
        self.assertEqual(P.handle(ev(blocked_secs=900), at(12), sender=lambda t: "1.2"), "sent")

    def test_digest_rides_after_ping(self):
        P.handle(ev(key="q"), at(9), sender=self.sent.append)
        self.assertTrue(any(t.startswith("Digest") for t in self.sent))


if __name__ == "__main__":
    unittest.main()
