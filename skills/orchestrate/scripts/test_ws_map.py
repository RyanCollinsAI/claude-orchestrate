"""Tests for ws_map: spawns land as tabs in a few domain workspaces, never a workspace per pane."""
import os, sys, unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import orchlib as L
import orch
import ws_map as WS


class TestPick(unittest.TestCase):
    def label(self, label, cwd="C:\\x", domain="", owner=""):
        return WS.pick(label, cwd, domain, owner)["workspace"]

    def test_domains(self):
        self.assertEqual(self.label("mod-dictation"), "Mods-and-Tooling")
        self.assertEqual(self.label("hx-workspaces", "C:\\Users\\YOURNAME\\wt\\.claude\\hx"), "Mods-and-Tooling")
        self.assertEqual(self.label("cs450-midterm", "C:\\Vault\\Personal\\School\\CS450"), "School")
        self.assertEqual(self.label("sc-crop", "C:\\Vault\\Business\\ChannelA"), "ChannelA")
        self.assertEqual(self.label("slack-email"), "Slack-and-WebApp")
        self.assertEqual(self.label("jcu-forms"), "Life")

    def test_unknown_falls_back_to_ryan_os(self):
        self.assertEqual(self.label("zzz", "C:\\tmp"), "Vault")

    def test_config_table_replaces_default(self):
        custom = [{"workspace": "Only", "aliases": [], "match": ["zzz"]}]
        with mock.patch.dict(L.CONFIG, {"workspace_map": custom}):
            self.assertEqual(self.label("zzz"), "Only")


class TestPlace(unittest.TestCase):
    def test_existing_workspace_reused_no_create(self):
        with mock.patch.object(L, "find_workspace", side_effect=lambda r: {"workspace_id": "wF"} if r == "School" else None), \
             mock.patch.object(L, "ensure_workspace") as ens:
            self.assertEqual(WS.place("cs450", "C:\\School"), ("wF", None))
            ens.assert_not_called()

    def test_alias_reused(self):
        with mock.patch.object(L, "find_workspace", side_effect=lambda r: {"workspace_id": "wJ"} if r == "Slack" else None), \
             mock.patch.object(L, "ensure_workspace") as ens:
            self.assertEqual(WS.place("slack-x", "C:\\x"), ("wJ", None))
            ens.assert_not_called()

    def test_first_use_creates_and_renames_tab(self):
        with mock.patch.object(L, "find_workspace", return_value=None), \
             mock.patch.object(L, "ensure_workspace", return_value=("w99", "w99:p1")) as ens, \
             mock.patch.object(L, "panes", return_value=[{"pane_id": "w99:p1", "tab_id": "w99:t1"}]), \
             mock.patch.object(L, "herdr") as h:
            self.assertEqual(WS.place("mod-x", "C:\\x"), ("w99", "w99:p1"))
            ens.assert_called_once()
            h.assert_called_with("tab", "rename", "w99:t1", "mod-x", soft=True)


class TestSpawnWorkspace(unittest.TestCase):
    def test_mate_owner_workspace_still_wins(self):
        with mock.patch.object(L, "session_domain", return_value="x"), \
             mock.patch.object(L, "mate_workspace_id", return_value="w5"), \
             mock.patch.object(WS, "place") as pl:
            self.assertEqual(orch._spawn_workspace("a", None, "", "mate", None, "C:\\x"), ("w5", None))
            pl.assert_not_called()

    def test_plain_spawn_goes_through_map(self):
        with mock.patch.object(L, "session_domain", return_value=""), \
             mock.patch.object(WS, "place", return_value=("wF", None)) as pl:
            self.assertEqual(orch._spawn_workspace("a", None, "", "seat", None, "C:\\x"), ("wF", None))
            pl.assert_called_once()

    def test_live_mate_guard_still_refuses(self):
        with mock.patch.object(L, "ensure_workspace", return_value=("w7", None)), \
             mock.patch.object(L, "workspace_claimed_by", return_value="other"):
            with self.assertRaises(SystemExit):
                orch._spawn_workspace("a", None, "subject", "seat", None, "C:\\x")


if __name__ == "__main__":
    unittest.main()
