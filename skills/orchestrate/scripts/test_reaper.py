"""py test_reaper.py - the reaper's rules: orphan ancestry, keeper pages, pane gates, pacing.

The command lines and parent chains below are verbatim from the 2026-10-05 box (probe of 108 live
npx/MCP helpers, all under a live claude.exe, and the real poll_keeper launch with a "Fall 2026" path).
"""
import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import reaper as R  # noqa: E402

NOW = 1_000_000.0
PW = r'"C:\Program Files\nodejs\\node.exe" "C:\Program Files\nodejs\\node_modules\npm\bin\npx-cli.js" @playwright/mcp@latest'
CTX7 = r"C:\Windows\system32\cmd.exe /d /s /c context7-mcp"
KEEPER_ARGV = [r"C:\Program Files\WindowsApps\Microsoft.PowerShell_7.6.6.0_x64__8wekyb3d8bbwe\pwsh.exe",
               "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
               r"C:\Users\YOURNAME\Vault\.claude\skills\learn\tools\poll_keeper.ps1",
               "-Page", r"C:\Users\YOURNAME\Vault\Personal\School\Fall 2026\CS-549\Learning\exam1-phase1-review-podium.html",
               "-Log", r"C:\Users\YOURNAME\Vault\Personal\School\Fall 2026\CS-549\Learning\.review-keeper\poll.log",
               "-NoExtend", "-PulseSeconds", "3"]


def proc(ppid, name, age_min=60):
    return {"ppid": ppid, "name": name, "ctime": NOW - age_min * 60, "rss": 50 * 2 ** 20}


class Orphans(unittest.TestCase):
    def test_a_helper_under_a_live_claude_is_never_an_orphan(self):
        # the real chain: node <- cmd <- claude.exe <- powershell <- herdr <- WindowsTerminal(parent gone)
        t = {10: proc(9, "WindowsTerminal.exe", 500), 11: proc(10, "herdr.exe", 400),
             12: proc(11, "powershell.exe", 300), 13: proc(12, "claude.exe", 249),
             14: proc(13, "cmd.exe", 249), 15: proc(14, "node.exe", 249)}
        self.assertFalse(R.is_orphan_helper(t, 15, PW, NOW)[0])
        self.assertFalse(R.is_orphan_helper(t, 14, CTX7, NOW)[0])

    def test_a_helper_whose_claude_is_gone_is_an_orphan(self):
        t = {14: proc(13, "cmd.exe", 249), 15: proc(14, "node.exe", 249)}   # pid 13 (claude) is gone
        ok, why = R.is_orphan_helper(t, 15, PW, NOW)
        self.assertTrue(ok)
        self.assertIn("gone", why)

    def test_a_reused_parent_pid_counts_as_gone(self):
        t = {13: proc(1, "explorer.exe", 5), 14: proc(13, "cmd.exe", 249)}   # pid 13 born after its "child"
        self.assertTrue(R.is_orphan_helper(t, 14, CTX7, NOW)[0])

    def test_any_non_helper_in_the_chain_protects_it(self):
        for owner in ("claude.exe", "explorer.exe", "chrome.exe", "herdr.exe", "python.exe", "pwsh.exe"):
            t = {13: proc(2, owner, 300), 14: proc(13, "cmd.exe", 249), 15: proc(14, "node.exe", 249)}
            self.assertFalse(R.is_orphan_helper(t, 15, PW, NOW)[0], owner)

    def test_a_young_helper_is_left_alone(self):
        t = {15: proc(99, "node.exe", 3)}
        self.assertFalse(R.is_orphan_helper(t, 15, PW, NOW)[0])

    def test_only_helper_command_lines_match(self):
        t = {15: proc(99, "node.exe", 300)}
        self.assertFalse(R.is_orphan_helper(t, 15, "node C:\\Users\\YOURNAME\\app\\server.js", NOW)[0])
        self.assertTrue(R.is_orphan_helper(t, 15, PW, NOW)[0])

    def test_shared_daemons_and_browsers_are_never_helpers(self):
        t = {15: proc(99, "node.exe", 300), 16: proc(99, "chrome.exe", 300)}
        for c in (r"node chrome-devtools-axi.js mcp", r"node lavish-axi mcp serve",
                  r"node podium_watch mcp", r"node x --remote-debugging-port=9333 mcp"):
            self.assertFalse(R.is_orphan_helper(t, 15, c, NOW)[0], c)
        self.assertFalse(R.is_orphan_helper(t, 16, "chrome.exe mcp", NOW)[0])

    def test_victims_are_the_root_and_helper_children_never_a_browser(self):
        t = {15: proc(99, "cmd.exe", 300), 16: proc(15, "node.exe", 299), 17: proc(16, "chrome.exe", 299),
             18: proc(16, "conhost.exe", 299)}
        rows = R.plan_procs(t, {15: CTX7, 16: "", 17: "", 18: ""}, NOW)
        self.assertEqual(len(rows), 1)
        self.assertEqual(sorted(rows[0][1]), [15, 16, 18])


class Keepers(unittest.TestCase):
    def test_page_with_spaces_survives_the_command_line_round_trip(self):
        page = R.keeper_page(subprocess.list2cmdline(KEEPER_ARGV))
        self.assertTrue(page.endswith("exam1-phase1-review-podium.html"))
        self.assertIn("Fall 2026", page)

    def test_page_closed(self):
        with tempfile.TemporaryDirectory() as d:
            page = os.path.join(d, "a.html")
            open(page, "w").close()
            sess = {"k": {"file": page, "status": "open"}}
            self.assertEqual(R.page_closed(page, sess, 99999), "")
            self.assertEqual(R.page_closed(page, {"k": {"file": page, "status": "ended"}}, 0), "Bridge session ended")
            # one ended + one open session for the same file: still open
            both = {"a": {"file": page, "status": "ended"}, "b": {"file": page, "status": "feedback"}}
            self.assertEqual(R.page_closed(page, both, 0), "")
            self.assertEqual(R.page_closed(page, {}, 60), "")                      # keeper just started
            self.assertTrue(R.page_closed(page, {}, R.KEEPER_NO_SESSION_AGE + 1))
            self.assertEqual(R.page_closed(os.path.join(d, "gone.html"), sess, 0), "page file is gone")


class Panes(unittest.TestCase):
    def facts(self, **kw):
        f = {"status": "idle", "idle_s": 700, "older_s": 600, "kind": "DONE", "said": "Done: x Proof: y Next: none"}
        f.update(kw)
        return f

    def test_a_finished_pane_is_reaped(self):
        self.assertTrue(R.pane_verdict(self.facts())[0])

    def test_owner_crew_report_and_dirty_files_no_longer_block_a_close(self):
        ok, _ = R.pane_verdict(self.facts(crew=True, report_delivered=False, worktree_clean=False))
        self.assertTrue(ok)

    def test_ten_minute_rule(self):
        self.assertEqual(R.parse_age(R.OLDER), 600)
        self.assertFalse(R.pane_verdict(self.facts(idle_s=500))[0])

    def test_a_report_that_waits_on_ryan_is_kept(self):
        for said in ["The change is wide, so it waits for his yes.", "Waiting on Ryan to pick A or B.",
                     "Needs your answer before I go on.", "I sent the poll status; the Bridge page is open."]:
            self.assertFalse(R.pane_verdict(self.facts(said=said))[0], said)

    def test_queue_runner_is_kept_only_while_jobs_are_queued(self):
        self.assertFalse(R.pane_verdict(self.facts(said="3 jobs left in the queue, next starts at 9."))[0])
        self.assertTrue(R.pane_verdict(self.facts(said="The queue is finished, and I have nothing left to do."))[0])

    def test_grill_tutor_keepers_are_kept(self):
        self.assertTrue("grill-tutor-3".startswith(R.KEEP_PREFIXES))

    def test_empty_tabs_and_workspaces_are_found(self):
        rows = {("tab", "list"): {"result": {"tabs": [{"tab_id": "t1", "pane_count": 0}, {"tab_id": "t2", "pane_count": 1}]}},
                ("workspace", "list"): {"result": {"workspaces": [{"workspace_id": "w9", "pane_count": 0}]}}}
        old = R.L.herdr
        R.L.herdr = lambda *a, **k: rows[tuple(a[:2])]
        try:
            self.assertEqual(R.empty_containers(), [("tab", "t1"), ("workspace", "w9")])
        finally:
            R.L.herdr = old

    def test_every_gate_refuses_by_name(self):
        for key, val, word in [("protected", True, "protected"), ("held", True, "held"),
                               ("board_named", True, "board question"),
                               ("keeper_needs", True, "keeper"), ("draft", True, "draft"),
                               ("prompt", "Allow?", "prompt"), ("status", "busy", "not idle"),
                               ("idle_s", 60, "long enough"), ("kind", "QUESTION", "finished clean"),
                               ("said", "  ", "finished clean"), ("bg", 2, "background")]:
            ok, why = R.pane_verdict(self.facts(**{key: val}))
            self.assertFalse(ok, key)
            self.assertIn(word, why, key)

    def test_dead_process_pane_is_reaped_but_still_respects_a_draft_and_the_seat(self):
        dead = self.facts(dead=True, status="busy", kind="?", report_delivered=False)
        self.assertTrue(R.pane_verdict(dead)[0])
        self.assertFalse(R.pane_verdict({**dead, "draft": True})[0])
        self.assertFalse(R.pane_verdict({**dead, "protected": True})[0])
        self.assertFalse(R.pane_verdict({**dead, "idle_s": 30})[0])   # a restart's stale pid


class Chrome(unittest.TestCase):
    def test_report_groups_instances_and_flags_old_portless_only(self):
        pipe = (r'chrome.exe --user-data-dir=C:\Users\YOURNAME\.playwright-profiles\stale '
                r'--remote-debugging-pipe')
        t = {1: proc(0, "node.exe", 300), 2: proc(1, "chrome.exe", 235), 3: proc(2, "chrome.exe", 235),
             4: proc(0, "chrome.exe", 251), 5: proc(4, "chrome.exe", 251), 6: proc(0, "chrome.exe", 30)}
        c = {2: pipe, 3: pipe + " --type=renderer",
             4: r"chrome.exe --remote-debugging-port=9333 --user-data-dir=C:\Users\YOURNAME\.playwright-profiles\personal",
             5: "chrome.exe --type=gpu-process", 6: pipe.replace("stale", "fresh")}
        rows = {r["profile"]: r for r in R.chrome_instances(t, c, NOW)}
        self.assertEqual(set(rows), {"stale", "personal", "fresh"})
        self.assertEqual((rows["stale"]["procs"], rows["stale"]["owner"]), (2, "node.exe"))
        self.assertTrue(rows["stale"]["flag"])
        self.assertFalse(rows["personal"]["flag"])      # has a port
        self.assertFalse(rows["fresh"]["flag"])         # portless but young
        self.assertEqual(rows["personal"]["port"], "9333")

    def test_long_lived_portless_chromes_are_not_flagged(self):
        """2026-10-07: channela, channelb (cc-strike-watch) and default (Ryan's own Chrome)
        were flagged 795 times in 7 days. They are meant to be there."""
        pipe = r'chrome.exe --user-data-dir=C:\Users\YOURNAME\.playwright-profiles\%s --remote-debugging-pipe'
        t = {1: proc(0, "node.exe", 300), 2: proc(1, "chrome.exe", 235), 3: proc(0, "chrome.exe", 235),
             4: proc(0, "chrome.exe", 235)}
        c = {2: pipe % "channela", 3: pipe % "channelb", 4: "chrome.exe --single-argument http://x"}
        rows = {r["profile"]: r for r in R.chrome_instances(t, c, NOW)}
        self.assertEqual(set(rows), {"channela", "channelb", "default"})
        self.assertFalse(any(r["flag"] for r in rows.values()))

    def test_a_flag_is_logged_once_per_instance(self):
        rows = [{"pid": 7, "flag": True}, {"pid": 8, "flag": True}, {"pid": 9, "flag": False}]
        self.assertEqual([r["pid"] for r in R.chrome_new_flags(rows, set())], [7, 8])
        self.assertEqual([r["pid"] for r in R.chrome_new_flags(rows, {"7"})], [8])
        self.assertEqual(R.chrome_new_flags(rows, {"7", "8"}), [])

    def test_a_flag_prints_on_the_first_pass_only(self):
        row = {"pid": 7, "profile": "stale", "port": "", "procs": 3, "mb": 200, "age_s": 9999,
               "owner": "", "flag": True}
        self.assertIn("FLAG:", "\n".join(R.chrome_lines([row])))
        again = "\n".join(R.chrome_lines([row], {"7"}))
        self.assertNotIn("FLAG:", again)
        self.assertIn("flagged earlier", again)

    def test_the_ram_hog_browser_is_named_and_logged_once(self):
        big = 17 * 1024
        row = {"pid": 5, "profile": "agent-chrome", "port": "9334", "procs": 80, "mb": big,
               "age_s": 9999, "owner": "", "flag": False, "heavy": True, "tabs": 54}
        text = "\n".join(R.chrome_lines([row], {"5:heavy"}))
        self.assertIn("HEAVY: 17 GB in 54 tabs", text)          # always shown: it explains low RAM
        self.assertEqual(R.chrome_new_flags([row], set()), [row])
        self.assertEqual(R.chrome_new_flags([row], {"5:heavy"}), [])

    def test_heavy_means_over_the_limit(self):
        t = {2: {**proc(0, "chrome.exe"), "rss": 9 * 1024 * 2 ** 20}, 3: proc(0, "chrome.exe")}
        c = {2: "chrome.exe --remote-debugging-port=9334 --user-data-dir=C:\\p\\agent-chrome",
             3: "chrome.exe --remote-debugging-port=9333 --user-data-dir=C:\\p\\personal"}
        rows = {r["profile"]: r for r in R.chrome_instances(t, c, NOW)}
        self.assertTrue(rows["agent-chrome"]["heavy"])
        self.assertFalse(rows["personal"]["heavy"])


class Pacing(unittest.TestCase):
    def test_two_minutes_normally(self):
        self.assertEqual(R.should_run({"last_run": NOW - 60}, NOW, 20), (False, False))
        self.assertEqual(R.should_run({"last_run": NOW - 130}, NOW, 20), (True, False))

    def test_two_minutes_when_ram_is_low(self):
        self.assertEqual(R.should_run({"last_run": NOW - 60}, NOW, 4), (False, True))
        self.assertEqual(R.should_run({"last_run": NOW - 130}, NOW, 4), (True, True))

    def test_manual_run_always_goes(self):
        self.assertTrue(R.should_run({"last_run": NOW}, NOW, 20, force=True)[0])

    def test_parse_age(self):
        self.assertEqual(R.parse_age("20m"), 1200)
        self.assertEqual(R.parse_age("1h"), 3600)
        with self.assertRaises(ValueError):
            R.parse_age("soon")


if __name__ == "__main__":
    unittest.main()
