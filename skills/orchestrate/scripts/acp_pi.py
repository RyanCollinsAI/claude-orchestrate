"""pi --mode rpc: the client for pi panes (A2, 2026-10-09). Stdlib only, no orchestrator imports.

`orch.py spawn|task --kind pi --acp` runs `acp_pane.py --harness pi`, which starts `pi --mode rpc`
and talks to it through PiRpcClient. Pi's RPC is not ACP (commands like {"type":"prompt"} in,
events like {"type":"message_update"} out, one JSON object per LF-terminated line), so this class
translates it to the surface AcpPane already uses for claude and codex: `prompt` blocks until the
run settles, `session/update`-shaped notifications carry the text, tools and context size, and a
question becomes a `session/request_permission` call that the pane answers by policy or a board ask.

Pi has no permission prompts: it is permissive by default and nothing asks before a tool runs, so
the protocol cannot gate a tool call and this client does not pretend to. The only questions pi can
raise are extension dialogs (`extension_ui_request` with method select or confirm). Those go
through the same policy and the same board ask as a claude permission request. A dialog that asks
for free text (input, editor) is cancelled: nobody is at that pane to type.

Docs: pi-coding-agent/docs/rpc.md. Framing rule from there: split on LF only (not Node readline,
not a Unicode-aware reader) and strip a trailing CR. AcpClient's reader already does that.
"""
import json
import os
import shutil
import threading
import time

import acp_client as C

DIALOGS = ("select", "confirm", "input", "editor")
START_GRACE_S = 15          # a prompt that starts no run in this long was handled in place (a /command)
TOOL_KINDS = {"bash": "execute", "read": "read", "edit": "edit", "write": "edit", "grep": "search",
              "find": "search", "ls": "search"}


def pi_command(extra=()):
    """argv for `pi --mode rpc ...`. Node plus pi's own bundle, found next to the `pi` shim, so no
    .cmd wrapper sits between this client and the process it must be able to kill. Falls back to
    the shim itself when the bundle is not where the global npm install puts it."""
    shim = shutil.which("pi")
    if not shim:
        return []
    cli = os.path.join(os.path.dirname(shim), "node_modules", "@earendil-works", "pi-coding-agent",
                       "dist", "bundle", "cli.js")
    node = shutil.which("node")
    base = [node, cli] if node and os.path.isfile(cli) else [shim]
    return base + ["--mode", "rpc", *extra]


def pi_args(model="", provider="", effort="", name=""):
    """The pi flags that carry the pane's identity: provider, model, thinking level, session name."""
    return ([*(["--provider", provider] if provider else []), *(["--model", model] if model else []),
             *(["--thinking", effort] if effort else []), *(["--name", name] if name else [])])


def child_env(base=None):
    """Pi's environment: no claude nested-session markers; everything else (pi's own login, the
    HERDR_* variables) passes through untouched."""
    return {k: v for k, v in (base if base is not None else os.environ).items()
            if k not in C.NESTED_MARKERS}


def _text_of(result):
    parts = (result or {}).get("content") or []
    return " ".join(p.get("text", "") for p in parts if isinstance(p, dict) and p.get("type") == "text")


class PiRpcClient(C.AcpClient):
    """AcpClient's process, reader thread and job object, with pi's records in place of JSON-RPC."""

    def __init__(self, cmd, cwd, env, stderr_path=None, on_notification=None, on_request=None,
                 wire_log=None, job=True, policy=None):
        super().__init__(cmd, cwd, env, stderr_path, on_notification, on_request, wire_log, job)
        self.hp = C.harness_policy(policy, "pi")
        self.state, self.ctx_size = {}, 0
        self._settled = threading.Event()
        self._turn = {"started": False, "err": None, "cancelled": False}

    # ------------------------------------------------------------ records in
    def _dispatch(self, msg):
        kind = msg.get("type")
        if kind == "response":
            with self._lock:
                slot = self._pending.pop(msg.get("id"), None)
            if slot:
                slot["msg"] = msg
                slot["event"].set()
        elif kind == "extension_ui_request":
            if msg.get("method") in DIALOGS:
                threading.Thread(target=self._serve_dialog, args=(msg,), daemon=True, name="pi-dialog").start()
            elif msg.get("method") == "notify":
                self._note({"sessionUpdate": "agent_notice", "text": str(msg.get("message", ""))})
        elif kind:
            try:
                self._event(msg)
            except Exception as e:                 # a translation bug must never kill the reader
                self.noise.append(f"pi event {kind} raised {type(e).__name__}: {e}")

    def _note(self, update):
        if self.on_notification:
            self.on_notification("session/update", {"update": update})

    def _event(self, msg):
        kind, turn = msg["type"], self._turn
        if kind == "agent_start":
            turn["started"], turn["err"] = True, None
        elif kind == "message_update":
            ev = msg.get("assistantMessageEvent") or {}
            if ev.get("type") == "text_delta":
                self._note({"sessionUpdate": "agent_message_chunk",
                            "content": {"type": "text", "text": ev.get("delta", "")}})
        elif kind == "message_end":
            m = msg.get("message") or {}
            if m.get("role") == "assistant":
                self._note({"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": "\n"}})
                use = m.get("usage") or {}
                used = use.get("totalTokens") or sum(int(use.get(k) or 0) for k in
                                                     ("input", "output", "cacheRead", "cacheWrite"))
                if used:
                    self._note({"sessionUpdate": "usage_update", "used": used, "size": self.ctx_size,
                                "_meta": {"_claude/model": m.get("model", "")}})
        elif kind == "tool_execution_start":
            name = msg.get("toolName") or "?"
            self._note({"sessionUpdate": "tool_call", "toolCallId": msg.get("toolCallId"), "title": name,
                        "kind": TOOL_KINDS.get(name, "other"), "rawInput": msg.get("args") or {}})
        elif kind == "tool_execution_end":
            self._note({"sessionUpdate": "tool_call_update", "toolCallId": msg.get("toolCallId"),
                        "status": "failed" if msg.get("isError") else "completed",
                        "content": [{"type": "content",
                                     "content": {"type": "text", "text": _text_of(msg.get("result"))}}]})
        elif kind == "agent_end":
            last = next((m for m in reversed(msg.get("messages") or []) if m.get("role") == "assistant"), {})
            if last.get("stopReason") == "aborted":
                turn["cancelled"] = True
            elif last.get("stopReason") == "error" and not msg.get("willRetry"):
                turn["err"] = last.get("errorMessage") or "the model call failed"
        elif kind == "auto_retry_end" and msg.get("success") is False:
            turn["err"] = msg.get("finalError") or "retries exhausted"
        elif kind == "extension_error":
            self._note({"sessionUpdate": "agent_notice", "text": f"extension error: {msg.get('error', '')}"})
        elif kind == "agent_settled":
            self._settled.set()

    # ------------------------------------------------------------ extension dialogs
    def _ui_reply(self, rid, **fields):
        try:
            self._send({"type": "extension_ui_response", "id": rid, **fields})
        except C.AcpClosed:
            pass

    def _serve_dialog(self, msg):
        rid, method = msg["id"], msg["method"]
        title, text = str(msg.get("title") or method), str(msg.get("message") or "")
        if method in ("input", "editor"):
            self._note({"sessionUpdate": "agent_notice",
                        "text": f"extension asked for text ({title}); cancelled, nobody types at this pane"})
            return self._ui_reply(rid, cancelled=True)
        if method == "select":
            options = [{"optionId": str(o), "name": str(o), "kind": C.dialog_option_kind(o, self.hp)}
                       for o in msg.get("options") or []]
        else:
            options = [{"optionId": "yes", "name": "Yes", "kind": "allow_once"},
                       {"optionId": "no", "name": "No", "kind": "reject_once"}]
        if method == "select" and not any(o["kind"] == "allow_once" for o in options):
            self._note({"sessionUpdate": "agent_notice",
                        "text": f"extension dialog ({title}) has no option that means yes; cancelled"})
            return self._ui_reply(rid, cancelled=True)
        call = {"toolCallId": rid, "title": title, "kind": "execute", "_dialog": True,
                "rawInput": {"command": f"{title} {text}".strip()}}
        try:
            res = self.on_request("session/request_permission", {"toolCall": call, "options": options}) \
                if self.on_request else None
        except Exception:
            res = None
        out = (res or {}).get("outcome") or {}
        if out.get("outcome") != "selected":
            return self._ui_reply(rid, cancelled=True)
        if method == "select":
            return self._ui_reply(rid, value=out.get("optionId"))
        return self._ui_reply(rid, confirmed=out.get("optionId") == "yes")

    # ------------------------------------------------------------ commands out
    def command(self, kind, timeout=60, **fields):
        """Send one pi command and block for its response. -> the response's `data` dict. Raises
        AcpError when pi answers success false, AcpClosed if pi exits first, TimeoutError."""
        with self._lock:
            self._next_id += 1
            rid = f"c{self._next_id}"
            slot = {"event": threading.Event(), "msg": None, "error": None}
            self._pending[rid] = slot
        try:
            self._send({"id": rid, "type": kind, **fields})
        except C.AcpClosed:
            with self._lock:
                self._pending.pop(rid, None)
            raise
        if not slot["event"].wait(timeout):
            with self._lock:
                self._pending.pop(rid, None)
            raise TimeoutError(f"pi {kind} had no answer in {timeout}s")
        if slot["error"]:
            raise slot["error"]
        res = slot["msg"]
        if not res.get("success"):
            raise C.AcpError(-32000, str(res.get("error") or f"pi {kind} failed"))
        return res.get("data") or {}

    # ------------------------------------------------------------ the AcpClient surface
    def _absorb(self, st):
        self.state = st
        self.ctx_size = (st.get("model") or {}).get("contextWindow") or self.ctx_size

    def initialize(self, name="", version="", timeout=60):
        self._absorb(self.command("get_state", timeout=timeout))
        return {}

    def wait_auth(self, timeout=10):
        """Pi has no auth status to read; it uses whatever login `pi` is configured with. The one
        thing refused here is an Anthropic provider: that is per-token extra usage, not a plan."""
        provider = (self.state.get("model") or {}).get("provider", "")
        self.auth = {"kind": "api_key" if provider == "anthropic" else "account", "label": f"pi/{provider or '?'}"}
        return self.auth

    def _session_result(self, st):
        self._absorb(st)
        model = st.get("model") or {}
        levels = self.command("get_available_thinking_levels").get("levels") or []
        return {"sessionId": st.get("sessionId", ""), "sessionFile": st.get("sessionFile", ""),
                "configOptions": [
                    {"id": "model", "options": [{"value": model.get("id", "")}]},
                    {"id": "effort", "options": [{"value": v} for v in levels]}]}

    def new_session(self, cwd, name="", timeout=60):
        if name:
            self.command("set_session_name", timeout=timeout, name=name)
        return self._session_result(self.command("get_state", timeout=timeout))

    def load_session(self, session_file, cwd, timeout=60):
        """Pi resumes by session FILE (the state file's `sessionFile`), not by id."""
        if not os.path.isfile(session_file):
            raise C.AcpError(-32602, f"pi resumes from a session file; {session_file!r} is not one "
                                     f"(use the state file's sessionFile)")
        if self.command("switch_session", timeout=timeout, sessionPath=session_file).get("cancelled"):
            raise C.AcpError(-32000, "an extension cancelled the session switch")
        return self._session_result(self.command("get_state", timeout=timeout))

    def set_mode(self, session_id, mode_id, timeout=30):
        return {}

    def set_config_option(self, session_id, config_id, value, timeout=30):
        if config_id == "effort":
            self.command("set_thinking_level", timeout=timeout, level=value)
        return {}

    def prompt(self, session_id, text):
        """One run. Blocks until pi settles (`agent_settled`: no retry, compaction or queued
        follow-up left), which is later than `agent_end`. -> end_turn or cancelled; an error that
        pi reports after accepting the prompt raises AcpError, like a failed `session/prompt`."""
        turn = self._turn = {"started": False, "err": None, "cancelled": False}
        self._settled.clear()                      # subscribe before sending, so a fast run is not missed
        data = self.command("prompt", message=text)
        if data.get("disposition") == "handled":
            return "end_turn"
        sent = time.time()
        while not self._settled.wait(0.25):
            if self.closed:
                raise C.AcpClosed("pi exited")
            if not turn["started"] and data.get("disposition") != "started" and time.time() - sent > START_GRACE_S:
                return "end_turn"                   # an extension /command: handled in place, no run
        if turn["err"] and not turn["cancelled"]:
            raise C.AcpError(-32000, turn["err"])
        return "cancelled" if turn["cancelled"] else "end_turn"

    def cancel(self, session_id):
        """The Escape key. No wait: `abort` answers only once pi is idle."""
        self._turn["cancelled"] = True
        self._send({"id": "abort", "type": "abort"})
