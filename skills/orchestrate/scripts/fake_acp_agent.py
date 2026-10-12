"""A stub ACP agent for tests: speaks JSON-RPC 2.0 on stdio like claude-agent-acp, no model, no network.

The prompt text picks the behaviour (first word):
  ECHO <text>        stream <text> as agent_message_chunk, end_turn
  TOOL               tool_call + tool_call_update(completed), end_turn
  PERM <command>     session/request_permission for an execute call; reply says allowed/rejected/cancelled
  PEREDIT <path>     session/request_permission for an edit call
  WAIT               block until session/cancel, then stopReason cancelled
  ASKQ               answer ends with a question mark
  EXIT               exit at once, mid-turn
  USAGE              send a usage_update (used 42000 of 200000)
FAKE_AUTH_KIND (default "account") sets the auth kind it pushes at initialize.
FAKE_NEED_AUTH=1 makes session/new answer "Authentication required" until `authenticate` has been called.
FAKE_FLAVOR=codex makes it look like codex-acp: ChatGPT auth methods, session modes, a
reasoning_effort dial, and codex's permission option ids (allow_once / allow_for_session / decline).
FAKE_LOG=<path> appends every line it receives (so a test can see what the client sent).
"""
import json
import os
import queue
import sys
import threading

INBOX = queue.Queue()
LOG = os.environ.get("FAKE_LOG", "")
SESSION = "fake-session-0001"
CODEX = os.environ.get("FAKE_FLAVOR") == "codex"
AUTHED = []


def send(msg):
    sys.stdout.write(json.dumps(msg, separators=(",", ":")) + "\n")
    sys.stdout.flush()


def reader():
    for raw in sys.stdin:
        if LOG:
            with open(LOG, "a", encoding="utf-8") as fh:
                fh.write(raw)
        try:
            INBOX.put(json.loads(raw))
        except ValueError:
            pass
    INBOX.put(None)


def note(update):
    send({"jsonrpc": "2.0", "method": "session/update", "params": {"sessionId": SESSION, "update": update}})


def chunk(text):
    note({"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": text}})


CONFIG = [
    {"id": "mode", "type": "select", "currentValue": "bypassPermissions",
     "options": [{"value": v} for v in ("default", "acceptEdits", "plan", "bypassPermissions")]},
    {"id": "model", "type": "select", "currentValue": "opus",
     "options": [{"value": v} for v in ("default", "opus", "sonnet", "haiku", "fable")]},
    {"id": "effort", "type": "select", "currentValue": "high",
     "options": [{"value": v} for v in ("default", "low", "medium", "high", "xhigh", "max")]},
]


MODE_IDS = ("read-only", "workspace-write", "agent", "agent-full-access")
CODEX_CONFIG = [
    {"id": "mode", "type": "select", "category": "mode", "currentValue": "agent",
     "options": [{"value": v} for v in MODE_IDS]},
    {"id": "model", "type": "select", "category": "model", "currentValue": "gpt-5",
     "options": [{"value": v} for v in ("gpt-5", "gpt-6-astra")]},
    {"id": "reasoning_effort", "type": "select", "category": "thought_level", "currentValue": "medium",
     "options": [{"value": v} for v in ("low", "medium", "high", "xhigh")]},
]
CODEX_MODES = {"currentModeId": "agent", "availableModes": [{"id": v, "name": v} for v in MODE_IDS]}


def permission_options():
    if CODEX:
        return [{"optionId": "allow_once", "name": "Yes, proceed", "kind": "allow_once"},
                {"optionId": "allow_for_session", "name": "Yes, for this session", "kind": "allow_always"},
                {"optionId": "decline", "name": "No", "kind": "reject_once"},
                {"optionId": "cancel", "name": "No, and stop", "kind": "reject_once"}]
    return [{"optionId": "allow-once", "name": "Yes", "kind": "allow_once"},
            {"optionId": "allow-with-updates", "name": "Always", "kind": "allow_always"},
            {"optionId": "reject", "name": "No", "kind": "reject_once"}]


def permission(call):
    """Ask the client and wait for the matching answer. -> the outcome dict."""
    send({"jsonrpc": "2.0", "id": 9001, "method": "session/request_permission", "params": {
        "sessionId": SESSION, "toolCall": call, "options": permission_options()}})
    while True:
        msg = INBOX.get()
        if msg is None:
            sys.exit(0)
        if msg.get("id") == 9001:
            return (msg.get("result") or {}).get("outcome") or {}


def handle_prompt(rid, text):
    word, _, rest = text.partition(" ")
    stop = "end_turn"
    if word == "ECHO":
        chunk(rest + "\n")
    elif word == "TOOL":
        note({"sessionUpdate": "tool_call", "toolCallId": "t1", "title": "Read a.txt", "kind": "read"})
        note({"sessionUpdate": "tool_call_update", "toolCallId": "t1", "status": "completed",
              "content": [{"type": "content", "content": {"type": "text", "text": "file text"}}]})
        chunk("read it\n")
    elif word in ("PERM", "PEREDIT"):
        if word == "PERM":
            call = {"toolCallId": "t2", "title": rest, "kind": "execute", "rawInput": {"command": rest}}
        else:
            call = {"toolCallId": "t2", "title": f"Edit {rest}", "kind": "edit",
                    "rawInput": {"file_path": rest}}
        out = permission(call)
        if out.get("outcome") == "cancelled":
            chunk("cancelled\n")
        else:
            kind = next((o["kind"] for o in permission_options() if o["optionId"] == out.get("optionId")), "")
            chunk(("allowed" if kind == "allow_once" else "rejected") + f" {out.get('optionId')}\n")
    elif word == "WAIT":
        while True:
            msg = INBOX.get()
            if msg is None:
                sys.exit(0)
            if msg.get("method") == "session/cancel":
                stop = "cancelled"
                break
    elif word == "ASKQ":
        chunk("Which one do you want?")
    elif word == "EXIT":
        sys.exit(7)
    elif word == "USAGE":
        note({"sessionUpdate": "usage_update", "used": 42000, "size": 200000,
              "_meta": {"_claude/model": "claude-fake"}})
        chunk("ok\n")
    else:
        chunk(f"unknown prompt {word}\n")
    send({"jsonrpc": "2.0", "id": rid, "result": {"stopReason": stop}})


def main():
    threading.Thread(target=reader, daemon=True).start()
    while True:
        msg = INBOX.get()
        if msg is None:
            return
        method, rid, p = msg.get("method"), msg.get("id"), msg.get("params") or {}
        if method == "initialize":
            send({"jsonrpc": "2.0", "id": rid, "result": {
                "protocolVersion": 1, "agentInfo": {"name": "fake-acp", "version": "0"},
                "authMethods": [{"id": "api-key"}, {"id": "chat-gpt"}] if CODEX else [],
                "agentCapabilities": {"loadSession": True}}})
            send({"jsonrpc": "2.0", "method": "_auth/status_update", "params": {"authStatus": {
                "kind": os.environ.get("FAKE_AUTH_KIND", "account"), "label": "Fake Max"}}})
        elif method == "authenticate":
            AUTHED.append(p.get("methodId"))
            send({"jsonrpc": "2.0", "id": rid, "result": {}})
        elif method == "session/new" and os.environ.get("FAKE_NEED_AUTH") and not AUTHED:
            send({"jsonrpc": "2.0", "id": rid, "error": {"code": -32000, "message": "Authentication required"}})
        elif method == "session/new":
            res = {"sessionId": SESSION, "configOptions": CODEX_CONFIG if CODEX else CONFIG}
            if CODEX:
                res["modes"] = CODEX_MODES
            send({"jsonrpc": "2.0", "id": rid, "result": res})
        elif method == "session/load":
            note({"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": "OLD HISTORY\n"}})
            send({"jsonrpc": "2.0", "id": rid, "result": {"sessionId": p.get("sessionId"), "configOptions": CONFIG}})
        elif method in ("session/set_mode", "session/set_config_option"):
            send({"jsonrpc": "2.0", "id": rid, "result": {}})
        elif method == "session/prompt":
            text = "".join(b.get("text", "") for b in p.get("prompt") or [])
            handle_prompt(rid, text)
        elif rid is not None:
            send({"jsonrpc": "2.0", "id": rid, "error": {"code": -32601, "message": f"no {method}"}})


if __name__ == "__main__":
    main()
