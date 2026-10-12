"""Which herdr workspace a spawned pane lands in, as a TAB.

Before this, a spawn with no mate owner made herdr open a brand-new workspace per pane, so the
sidebar grew to 25+ entries. Now each such pane becomes a tab in one of a few domain workspaces.

`pick(label, cwd, domain, owner)` -> a rule dict, first match wins, else the fallback rule.
`place(...)` -> (workspace_id, reuse_pane) - the workspace is created on first use.

The table is `RULES` below. `config.json` key `workspace_map` (same shape) replaces it, so Ryan can
retune it without a code change. A rule matches when any of its `match` words is a substring of the
lowercased label, domain, owner or cwd. `aliases` are existing workspace labels reused before a new
one is created.
"""
import orchlib as L

FALLBACK = {"workspace": "Vault", "aliases": []}

RULES = [
    {"workspace": "Mods-and-Tooling", "aliases": [],
     "match": ["orchestrate", "orch", "mod-", "plugin", "hook", "herdr", "hx-", "skills",
               ".claude", "pi-", "bridge", "tooling"]},
    {"workspace": "School", "aliases": [],
     "match": ["school", "learn", "cs4", "cs5", "cs3", "midterm", "homework", "school"]},
    {"workspace": "ChannelA", "aliases": [],
     "match": ["channela", "sc-", "channelb", "youtube"]},
    {"workspace": "Slack-and-WebApp", "aliases": ["Slack", "WebApp"],
     "match": ["slack", "webapp"]},
    {"workspace": "Life", "aliases": ["Debt", "Ventures"],
     "match": ["jcu", "email", "intern", "debt", "venture", "family", "life", "visa", "parking"]},
]


def rules():
    return L.CONFIG.get("workspace_map") or RULES


def pick(label, cwd, domain="", owner=""):
    hay = " ".join(str(x or "") for x in (label, domain, owner, cwd)).lower().replace("/", "\\")
    for r in rules():
        if any(w.lower() in hay for w in r.get("match", [])):
            return r
    return FALLBACK


def place(label, cwd, domain="", owner="", env_args=()):
    """(workspace_id, reuse_pane_or_None). A workspace made here hands back its root pane so the
    caller runs the agent in it; that pane's tab is renamed after the pane."""
    rule = pick(label, cwd, domain, owner)
    for ref in [rule["workspace"], *rule.get("aliases", [])]:
        w = L.find_workspace(ref)
        if w:
            return w["workspace_id"], None
    ws_id, root = L.ensure_workspace(rule["workspace"], cwd, env_args)
    if root:
        tab = next((p["tab_id"] for p in L.panes() if p["pane_id"] == root), None)
        if tab:
            L.herdr("tab", "rename", tab, label, soft=True)
    return ws_id, root
