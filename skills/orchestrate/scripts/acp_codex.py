"""codex-acp: the ACP adapter for Codex panes (A2, 2026-10-09). Stdlib only, no orchestrator imports.

`orch.py spawn|task --kind codex --acp` runs `acp_pane.py --harness codex`, which starts this adapter
and talks to it with the same JSON-RPC client the claude panes use (acp_client.AcpClient). Codex is
an ACP agent like claude-agent-acp, so the wire, the policy and the board asks are shared; this
file holds only what differs: where the adapter and the codex binary are, which variables must
never reach the child, and how the session is configured.

Pins and rollback: references/acp.md. Adapter: `@agentclientprotocol/codex-acp` 2.1.1 (Apache-2.0),
pinned in acp/codex/package.json. It is NOT in the claude adapter's package.json because the claude
install runs `npm ci --omit=optional` and codex's native binary is an optional dependency.

Login is Ryan's ChatGPT subscription only (D2). `child_env` removes the API key variables, the
pane refuses to start if one is set, and `acp_client.require_subscription` stops unless the adapter
itself reports `kind == "account"`. The client never calls `authenticate`: with no ChatGPT login
that call would open a browser, and a login is Ryan's click, not this script's.
"""
import glob
import os
import re
import shutil

import acp_client as C

ADAPTER_REL = os.path.join("node_modules", "@agentclientprotocol", "codex-acp", "dist", "index.js")
API_KEY_VARS = ("OPENAI_API_KEY", "CODEX_API_KEY")
# The mode that asks (codex-acp 2.1.1): edits the workspace, asks before writing outside it or
# using the network. `agent-full-access` would never ask, so there would be nothing to route.
DEFAULT_MODE = "workspace-write"
CHATGPT_METHOD = "chat-gpt"          # codex-acp's auth method id for the ChatGPT subscription login
MODEL_IDS = ("model",)
EFFORT_IDS = ("reasoning_effort", "effort")


def adapter_path(override=None):
    """The pinned codex-acp index.js: $ACP_CODEX_ADAPTER, else this skill's acp/codex, else the
    live tree's. Never `npx`: npx would resolve whatever version is newest on a bad day."""
    cands = [override or os.environ.get("ACP_CODEX_ADAPTER", ""),
             os.path.join(C.SKILL, "acp", "codex", ADAPTER_REL),
             os.path.join(C.LIVE_ACP, "codex", ADAPTER_REL)]
    return next((c for c in cands if c and os.path.isfile(c)), "")


def codex_exe():
    """Ryan's installed native codex.exe (the global npm install), so the pane runs the same binary
    as a TUI pane and `npm ci --omit=optional` need not fetch a second copy. "" when it cannot be
    found: the adapter then uses the copy bundled with its own install."""
    env = os.environ.get("CODEX_PATH", "")
    if env and os.path.isfile(env):
        return env
    shim = shutil.which("codex")
    if not shim:
        return ""
    root = os.path.join(os.path.dirname(shim), "node_modules", "@openai", "codex")
    hits = glob.glob(os.path.join(root, "node_modules", "@openai", "codex-*", "vendor", "*", "bin",
                                  "codex.exe" if os.name == "nt" else "codex"))
    return hits[0] if hits else ""


def child_env(base=None, log_dir=""):
    """The adapter's environment: no API key (D2), no claude nested-session markers, Ryan's codex."""
    env = {k: v for k, v in (base if base is not None else os.environ).items()
           if k not in API_KEY_VARS and k not in C.API_KEY_VARS and k not in C.NESTED_MARKERS}
    exe = codex_exe()
    if exe:
        env["CODEX_PATH"] = exe
    if log_dir:
        env["APP_SERVER_LOGS"] = log_dir
    env["INITIAL_AGENT_MODE"] = DEFAULT_MODE
    return env


def is_auth_error(err):
    """True for a codex-acp answer that means "not authenticated yet" (ACP auth_required, -32000)."""
    return err.code == -32000 and re.search(r"auth|log ?in|sign ?in", err.message, re.I) is not None


def configure(client, sid, result, model, effort, policy, say=lambda text: None):
    """Set the session's mode, model and reasoning effort from the `session/new` result.
    Every dial is optional: one the session does not offer is reported through `say` and left as is."""
    want = C.harness_policy(policy, "codex").get("mode") or DEFAULT_MODE
    offered = C.mode_ids(result)
    if want in offered:
        client.set_mode(sid, want)
    elif offered:
        say(f"note: mode {want!r} is not offered by codex-acp (has {', '.join(offered)}); left as is")
    opts = result.get("configOptions") or []
    bare = (model or "").split("/")[-1]
    # codex-acp names a model "<id>[<effort>]" in some listings and offers the effort apart in others.
    wanted = [model, bare, f"{bare}[{effort}]" if effort else ""]
    cid, val = C.pick_config(opts, MODEL_IDS, wanted)
    if val:
        client.set_config_option(sid, cid, val)
    elif model:
        say(f"note: model {model!r} is not offered by this session; left as is")
    if effort:
        cid, val = C.pick_config(opts, EFFORT_IDS, [effort])
        if val:
            client.set_config_option(sid, cid, val)
        else:
            say(f"note: effort {effort!r} is not offered by this session; left as is")
