"""py test_watch_sessions_wait.py - the real watch_sessions.main() loop for a few passes against a
fake fleet, to prove a declared wait silences the STALE ping and the idle-nudge, and that an
expired or missing one does not. Only herdr, the registry and the board are faked; transcripts are
real files' worth of jsonl lines fed through the real readers.

Also checks the ledger line a stop-reason writes.
"""
import contextlib
import datetime
import io
import json
import os
import sys
import tempfile
import time
import unittest
from unittest import mock

os.environ["PODIUM_SLACK"] = "0"
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import declared_wait as DW  # noqa: E402
import watch_sessions as W  # noqa: E402

L = W.L


class Stop(BaseException):
    pass


def line(text, ts):
    return json.dumps({"type": "assistant",
                       "timestamp": datetime.datetime.fromtimestamp(ts, datetime.timezone.utc)
                       .isoformat().replace("+00:00", "Z"),
                       "message": {"role": "assistant", "content": [{"type": "text", "text": text}]}})


class Loop(unittest.TestCase):
    def run_loop(self, panes, passes=6):
        """panes: {name: (status, agent_status, [transcript lines])} -> stdout of the loop."""
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        sids = {n: "sid-" + n for n in panes}
        sess = {"sid-orch": {"sessionId": "sid-orch", "name": "orchestrator", "status": "idle"}}
        by_sid = {}
        ents = {}
        for i, (n, (st, ast, lines)) in enumerate(panes.items()):
            sess[sids[n]] = {"sessionId": sids[n], "name": n, "status": st, "owner": "orchestrator"}
            by_sid[sids[n]] = {"pane_id": f"w1:p{i}", "agent_status": ast, "cwd": "C:/x"}
            ents[sids[n]] = lines
        briefs = {n: os.path.join(tmp.name, f"task-{n}.md") for n in panes}
        for p in briefs.values():
            with open(p, "w", encoding="utf-8") as fh:
                fh.write("1. do the thing\n2. report\n")
        calls = {"n": 0}

        def sleep(_):
            calls["n"] += 1
            if calls["n"] >= passes:
                raise Stop

        prompts = []
        patches = [
            mock.patch.object(L, "BOARD_DIR", tmp.name),
            # the *_load/*_save helpers bind their default path at import, so patching the path
            # constants is not enough: stub them, or the loop reads and writes the real board dir
            mock.patch.object(W, "seen_load", lambda *a, **k: {}),
            mock.patch.object(W, "seen_save", lambda seen, *a, **k: seen),
            mock.patch.object(W, "nudge_load", lambda *a, **k: {}),
            mock.patch.object(W, "nudge_save", lambda *a, **k: None),
            mock.patch.object(W, "holds_load", lambda *a, **k: {}),
            mock.patch.object(W, "start_heartbeat"),
            mock.patch.object(W, "READY_HOLD", 0),
            mock.patch.object(W, "NUDGE_IDLE_SECS", 0),
            mock.patch.object(W, "POLL", 0),
            mock.patch.object(W, "latest_brief", lambda n, d=None: briefs.get(n)),
            mock.patch.object(W, "reap_candidates", return_value=[]),
            mock.patch.object(W, "board_update"),
            mock.patch.object(W, "to_board"),
            mock.patch.object(W.B, "sync_board"),
            mock.patch.object(W.B, "live_sessions", return_value=[]),
            mock.patch.object(L, "orchestrator_sid", return_value="sid-orch"),
            mock.patch.object(L, "orchestrator_name", return_value="orchestrator"),
            mock.patch.object(L, "sessions", return_value=sess),
            mock.patch.object(L, "panes_by_sid", return_value=by_sid),
            mock.patch.object(L, "agent_names", return_value={p["pane_id"]: 1 for p in by_sid.values()}),
            mock.patch.object(L, "owners_load", return_value={s: {"owner": "orchestrator"} for s in sess}),
            mock.patch.object(L, "owner_is_live", return_value=False),
            mock.patch.object(L, "tail_entries", lambda sid, nbytes=0: ents.get(sid, [])),
            mock.patch.object(L, "last_usage", return_value=(100, "m")),
            mock.patch.object(L, "classify", return_value=("DONE", "all finished")),
            mock.patch.object(L, "pane_read", return_value=""),
            mock.patch.object(L, "pane_mode", return_value=""),
            mock.patch.object(L, "pending_prompt", return_value=""),
            mock.patch.object(L, "other_panes", return_value={}),
            mock.patch.object(L, "sidecar_load", return_value={}),
            mock.patch.object(L, "herdr", lambda *a, **k: prompts.append(a) or {}),
            mock.patch.object(W.time, "sleep", sleep),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf), self.assertRaises(Stop):
            W.main(["--stale-min", "1", "--dry-run"])
        self.ledger = L.ledger.read(os.path.join(tmp.name, "fleet-ledger.jsonl"))
        self.prompts = prompts
        return buf.getvalue()

    def test_stale_ping_and_nudge_honor_the_wait(self):
        now = time.time()
        hour_ago = now - 3600
        panes = {
            # busy, last message an hour old: STALE unless it declared a wait
            "waits": ("busy", "working", [line("paused: render until " + DW.iso_utc(now + 1800), hour_ago)]),
            "plain": ("busy", "working", [line("working on it", hour_ago)]),
            "expired": ("busy", "working", [line("paused: render until " + DW.iso_utc(now - 600), hour_ago)]),
            "notime": ("busy", "working", [line("paused: render, back later", hour_ago)]),
            # idle, brief has steps: nudged unless it declared a wait
            "idle-waits": ("idle", "idle", [line("paused: gpu job until " + DW.iso_utc(now + 1800), hour_ago)]),
            "idle-plain": ("idle", "idle", [line("done with step 1", hour_ago)]),
        }
        out = self.run_loop(panes)
        said = [l for l in out.splitlines() if " STALE 1m" in l]
        self.assertEqual(sorted(l.split()[0] for l in said), ["expired", "notime", "plain"], out)
        nudged = sorted(l.split()[1].rstrip(":") for l in out.splitlines() if l.startswith("NUDGE "))
        self.assertEqual(nudged, ["idle-plain"], out)

    def test_a_wait_over_4_hours_old_is_resurfaced_once_per_slot_not_pinged(self):
        now = time.time()
        panes = {"forgot": ("busy", "working",
                            [line("paused: render until " + DW.iso_utc(now + 7200), now - 5 * 3600)])}
        out = self.run_loop(panes, passes=10)
        rechecks = [l for l in out.splitlines() if "WAIT-RECHECK" in l]
        self.assertEqual(len(rechecks), 1, out)
        self.assertTrue(rechecks[0].startswith("forgot WAIT-RECHECK 4h | external wait: render until"), out)
        self.assertNotIn("STALE", out)

    def test_a_young_wait_says_nothing(self):
        now = time.time()
        panes = {"young": ("busy", "working",
                           [line("paused: render until " + DW.iso_utc(now + 7200), now - 3600)])}
        out = self.run_loop(panes, passes=10)
        self.assertNotIn("WAIT-RECHECK", out)
        self.assertNotIn("STALE", out)

    def test_a_stop_reason_writes_a_ledger_status_line(self):
        panes = {"idle-plain": ("idle", "idle", [line("done with step 1", time.time() - 3600)])}
        out = self.run_loop(panes)
        self.assertIn("idle-plain DONE | all finished", out)
        got = [r for r in self.ledger if r["event"] == "task.status" and r["task"] == "idle-plain"]
        self.assertEqual((got[0]["state"], got[0]["text"]), ("done", "all finished"))


if __name__ == "__main__":
    unittest.main()
