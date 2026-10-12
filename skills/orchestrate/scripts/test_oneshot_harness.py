"""grok and cursor panes are one-shot: the pane runs one headless command that tees a JSON stream to
`<report>.ndjson` / `<report>.json`. Done = a `"type":"result"` line. These tests cover the file
reader (UTF-16 under Windows PowerShell 5.1, UTF-8 under pwsh 7), the status, and the verdict
`classify_pane` takes from the result line instead of the raw-JSON screen.
"""
import json, os, sys, tempfile, unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import orchlib as L
import orch

OK = {"type": "result", "subtype": "success", "is_error": False, "result": "PLUMB-I3\nDONE"}
BAD = {"type": "result", "subtype": "error_during_execution", "is_error": True,
       "errors": ["You reached your free Grok Build usage limit for now."]}


def stream(result, encoding):
    lines = [json.dumps({"type": "system", "subtype": "init"}), json.dumps(result)]
    body = "\r\n".join(lines) + "\r\n"
    return (b"\xff\xfe" + body.encode("utf-16-le")) if encoding == "utf-16" else body.encode("utf-8")


class OneshotStream(unittest.TestCase):
    def entry(self, kind, result, encoding):
        d = tempfile.mkdtemp()
        report = os.path.join(d, "report-x.md")
        with open(report + L.ONESHOT_SUFFIX[kind], "wb") as fh:
            fh.write(stream(result, encoding))
        return {"kind": kind, "report": report}

    def test_result_line_is_read_from_both_encodings(self):
        for kind in ("grok", "cursor"):
            for enc in ("utf-16", "utf-8"):
                e = self.entry(kind, OK, enc)
                self.assertEqual(L.oneshot_result(e)["result"], "PLUMB-I3\nDONE", (kind, enc))
                self.assertEqual(L.oneshot_status(e), "done")

    def test_an_is_error_result_is_failed_not_done(self):
        e = self.entry("grok", BAD, "utf-16")
        self.assertEqual(L.oneshot_status(e), "failed")

    def test_no_file_or_no_result_line_is_working(self):
        self.assertEqual(L.oneshot_status({"kind": "grok", "report": os.path.join(tempfile.gettempdir(), "nope-x")}), "working")
        e = self.entry("grok", OK, "utf-8")
        with open(e["report"] + ".ndjson", "w", encoding="utf-8") as fh:
            fh.write(json.dumps({"type": "system"}) + "\n")
        self.assertEqual(L.oneshot_status(e), "working")

    def test_classify_pane_uses_the_result_line_not_the_screen(self):
        e = self.entry("cursor", OK, "utf-16")
        with mock.patch.object(L, "sidecar_load", return_value={"w1:p1": e}), \
                mock.patch.object(L, "pi_state", return_value=None):
            self.assertEqual(L.classify_pane("w1:p1")[0], "DONE")
        e = self.entry("grok", BAD, "utf-16")
        with mock.patch.object(L, "sidecar_load", return_value={"w1:p1": e}), \
                mock.patch.object(L, "pi_state", return_value=None):
            kind, said = L.classify_pane("w1:p1")
        self.assertEqual(kind, "ERROR")
        self.assertIn("usage limit", said)


class OneshotOnlyForOneshotKinds(unittest.TestCase):
    """A codex or pi pane's `report` is the agent's own markdown, not a stream file."""
    QUOTED = json.dumps({"type": "result", "is_error": True, "errors": ["quota"]})

    def codex_entry(self):
        report = os.path.join(tempfile.mkdtemp(), "report-codex.md")
        with open(report, "w", encoding="utf-8") as fh:
            fh.write("The grok stream ended with this line:\n" + self.QUOTED + "\nall fine here\n")
        return {"kind": "codex", "report": report}

    def test_oneshot_result_is_none_for_a_kind_with_no_stream_file(self):
        self.assertIsNone(L.oneshot_result(self.codex_entry()))
        self.assertEqual(L.oneshot_status(self.codex_entry()), "working")

    def test_a_codex_pane_quoting_a_result_line_is_not_error(self):
        with mock.patch.object(L, "sidecar_load", return_value={"w1:p1": self.codex_entry()}), \
                mock.patch.object(L, "pi_state", return_value=None), \
                mock.patch.object(L, "pane_read", return_value="Everything shipped.\nDONE"):
            self.assertEqual(L.classify_pane("w1:p1")[0], "DONE")


class FailedOneshotReachesTheWatchers(unittest.TestCase):
    """`oneshot_status` returns "failed"; both watcher gates must let that pane through to
    classify_pane, or the usage-limit failure is never posted."""
    PANE = "w1:p9"
    ROW = {"label": "grok-1", "owner": "pi-mate", "kind": "grok"}

    def test_the_shared_finished_set_includes_failed(self):
        self.assertIn("failed", L.FINISHED_STATUS)
        self.assertNotIn("working", L.FINISHED_STATUS)

    def test_other_panes_hands_the_failed_status_to_its_callers(self):
        e = OneshotStream().entry("grok", BAD, "utf-16")
        with mock.patch.object(L, "panes", return_value=[{"pane_id": self.PANE}]), \
                mock.patch.object(L, "acp_pane_ids", return_value=set()), \
                mock.patch.object(L, "sidecar_load", return_value={self.PANE: e}):
            self.assertEqual(L.other_panes()[self.PANE]["agent_status"], "failed")

    def test_mate_watch_classifies_a_failed_crew_pane(self):
        import contextlib, io
        import mate_watch as M
        import watch_sessions as W
        rows = {"w3:pM9": {"kind": "pi", "label": "pi-mate", "domain": "the pi harness", "owner": "vault-6c"},
                self.PANE: self.ROW}
        put = mock.Mock(return_value=r"C:\inbox\x.md")
        with contextlib.redirect_stdout(io.StringIO()), \
                mock.patch.object(W, "start_heartbeat"), \
                mock.patch.object(W, "READY_HOLD", 0), \
                mock.patch.object(W, "seen_load", return_value={}), \
                mock.patch.object(W, "seen_save", side_effect=lambda d, *a, **k: d), \
                mock.patch.object(L, "sidecar_load", return_value=rows), \
                mock.patch.object(L, "live_pane_ids", return_value=set(rows)), \
                mock.patch.object(L, "sessions", return_value={}), \
                mock.patch.object(L, "panes_by_sid", return_value={}), \
                mock.patch.object(L, "pi_state", return_value={"state": "idle"}), \
                mock.patch.object(L, "other_panes", return_value={self.PANE: {"agent": "grok", "agent_status": "failed"}}), \
                mock.patch.object(L, "classify_pane", return_value=("ERROR", "usage limit")) as cp, \
                mock.patch.object(L, "pi_inbox_put", put):
            M.main(["pi-mate", "--once"])
        cp.assert_called_once_with(self.PANE)
        self.assertIn("grok-1 ERROR | usage limit", put.call_args[0][1])

    def test_watch_sessions_classifies_a_failed_pane_and_posts_the_error(self):
        import contextlib, io
        import watch_sessions as W

        class Stop(BaseException):
            pass

        passes = []

        def stop(_):                    # the pane block only runs from the second pass (`not first`)
            passes.append(1)
            if len(passes) == 2:
                raise Stop
        out = io.StringIO()
        with contextlib.redirect_stdout(out), \
                mock.patch.object(W, "start_heartbeat"), \
                mock.patch.object(W, "READY_HOLD", 0), \
                mock.patch.object(W, "PANE_EVERY", 1), \
                mock.patch.object(W, "seen_load", return_value={}), \
                mock.patch.object(W, "seen_save", side_effect=lambda d, *a, **k: d), \
                mock.patch.object(W, "nudge_load", return_value={}), \
                mock.patch.object(W, "holds_load", return_value={}), \
                mock.patch.object(W, "to_board") as to_board, \
                mock.patch.object(W, "board_update"), \
                mock.patch.object(W.B, "sync_board"), \
                mock.patch.object(W.time, "sleep", stop), \
                mock.patch.object(L, "orchestrator_sid", return_value="me"), \
                mock.patch.object(L, "sessions", return_value={}), \
                mock.patch.object(L, "panes_by_sid", return_value={}), \
                mock.patch.object(L, "sidecar_load", return_value={self.PANE: self.ROW}), \
                mock.patch.object(L, "other_panes", return_value={self.PANE: {"agent": "grok", "agent_status": "failed"}}), \
                mock.patch.object(L, "classify_pane", return_value=("ERROR", "usage limit")) as cp, \
                mock.patch.object(L.ledger, "write"):
            with self.assertRaises(Stop):
                W.main([])
        cp.assert_called_once_with(self.PANE)
        self.assertIn("grok-1 ERROR | usage limit", out.getvalue())
        to_board.assert_called_once_with("ERROR", "grok-1", "usage limit")


class OneshotCommands(unittest.TestCase):
    def test_grok_uses_the_full_exe_path_and_verbatim(self):
        cmd = orch.grok_cmd("C:/h/t.md", "C:/w", "sonnet", "medium", "C:/h/report-x.md")
        self.assertIn(L.GROK_EXE, cmd)
        self.assertIn("--verbatim", cmd)
        self.assertIn("--model grok-4.7", cmd)          # a claude tier means "not given"
        self.assertIn("--reasoning-effort medium", cmd)
        self.assertTrue(cmd.endswith('report-x.md.ndjson"'))

    def test_cursor_never_uses_the_bare_agent_name(self):
        cmd = orch.cursor_cmd("C:/h/t.md", "C:/w", "sonnet", "C:/h/report-x.md")
        self.assertIn(L.CURSOR_EXE, cmd)
        self.assertIn("agent.cmd", cmd)
        self.assertIn("--trust", cmd)
        self.assertIn("--model 'auto'", cmd)
        self.assertNotIn("--cwd", cmd)

    def test_agy_uses_the_full_exe_path_and_the_default_model(self):
        cmd = orch.agy_cmd(model="sonnet", effort="low")
        self.assertIn(orch.AGY_EXE, cmd)
        self.assertIn("--model gemini-3.8-flash", cmd)
        self.assertIn("--effort low", cmd)

    def test_new_kinds_are_registered(self):
        for k in ("grok", "agy", "cursor"):
            self.assertIn(k, L.KINDS)


if __name__ == "__main__":
    unittest.main()
