"""A stub for `pi --mode rpc` for tests: pi's own records on stdio (not JSON-RPC), no model, no network.

The prompt `message` picks the behaviour (first word):
  ECHO <text>          stream <text>, settle
  TOOL                 a bash tool_execution_start/end pair, settle
  SELECT <title>|<a>|<b>   extension select dialog, then say which value came back
  CONFIRM <title>      extension confirm dialog, then say what came back
  INPUT                extension input dialog (free text), then say what came back
  WAIT                 run until `abort`, then end aborted
  ERR                  the model call fails: agent_end with a stopReason error
  HANDLED              the prompt response says disposition "handled" and no run starts
  SILENT               accept the prompt and never start a run (a /command that does nothing)
  EXIT                 exit at once, mid-turn
FAKE_PI_PROVIDER (default "openai-codex") is the provider get_state reports.
FAKE_LOG=<path> appends every line it receives.
"""
import json
import os
import queue
import sys
import threading

INBOX = queue.Queue()
LOG = os.environ.get("FAKE_LOG", "")
PROVIDER = os.environ.get("FAKE_PI_PROVIDER", "openai-codex")
STATE = {"name": "", "level": "medium", "session_file": os.environ.get("FAKE_PI_SESSION", "")}


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
            send({"type": "response", "command": "parse", "success": False, "error": "bad json"})
    INBOX.put(None)


def ok(rid, command, data=None):
    out = {"id": rid, "type": "response", "command": command, "success": True}
    if data is not None:
        out["data"] = data
    send(out)


def assistant(text, stop="stop", error=""):
    m = {"role": "assistant", "content": [{"type": "text", "text": text}], "model": "fake-pi-model",
         "usage": {"input": 40, "output": 2, "totalTokens": 42000}, "stopReason": stop}
    if error:
        m["errorMessage"] = error
    return m


def say(text):
    send({"type": "message_start", "message": {"role": "assistant"}})
    send({"type": "message_update", "assistantMessageEvent": {"type": "text_delta", "contentIndex": 0, "delta": text}})
    send({"type": "message_end", "message": assistant(text)})


def settle(messages):
    send({"type": "agent_end", "messages": messages, "willRetry": False})
    send({"type": "agent_settled"})


def dialog(method, rid="ui-1", **fields):
    send({"type": "extension_ui_request", "id": rid, "method": method, **fields})
    while True:
        msg = INBOX.get()
        if msg is None:
            sys.exit(0)
        if msg.get("type") == "extension_ui_response" and msg.get("id") == rid:
            return msg


def run(text):
    word, _, rest = text.partition(" ")
    send({"type": "agent_start"})
    if word == "ECHO":
        say(rest)
    elif word == "TOOL":
        send({"type": "tool_execution_start", "toolCallId": "t1", "toolName": "bash", "args": {"command": "ls"}})
        send({"type": "tool_execution_end", "toolCallId": "t1", "toolName": "bash", "isError": False,
              "result": {"content": [{"type": "text", "text": "a.txt"}]}})
        say("listed")
    elif word == "SELECT":
        title, *opts = rest.split("|")
        r = dialog("select", title=title, options=opts)
        say("cancelled" if r.get("cancelled") else f"picked {r.get('value')}")
    elif word == "CONFIRM":
        r = dialog("confirm", title=rest, message="sure?")
        say("cancelled" if r.get("cancelled") else f"confirmed {r.get('confirmed')}")
    elif word == "INPUT":
        r = dialog("input", title="Enter a value")
        say("cancelled" if r.get("cancelled") else f"typed {r.get('value')}")
    elif word == "WAIT":
        while True:
            msg = INBOX.get()
            if msg is None:
                sys.exit(0)
            if msg.get("type") == "abort":
                settle([assistant("", stop="aborted")])
                return
    elif word == "ERR":
        settle([assistant("", stop="error", error="boom: provider said no")])
        return
    elif word == "EXIT":
        sys.exit(7)
    else:
        say(f"unknown prompt {word}")
    settle([assistant(rest or word)])


def state():
    return {"model": {"id": "fake-pi-model", "provider": PROVIDER, "contextWindow": 200000},
            "thinkingLevel": STATE["level"], "isStreaming": False, "sessionFile": STATE["session_file"] or "/tmp/fake.jsonl",
            "sessionId": "fake-pi-session-1", "sessionName": STATE["name"]}


def main():
    threading.Thread(target=reader, daemon=True).start()
    send({"type": "extension_ui_request", "id": "n0", "method": "setStatus", "statusKey": "hide"})
    while True:
        msg = INBOX.get()
        if msg is None:
            return
        kind, rid = msg.get("type"), msg.get("id")
        if kind == "get_state":
            ok(rid, kind, state())
        elif kind == "get_available_thinking_levels":
            ok(rid, kind, {"levels": ["minimal", "low", "medium", "high", "xhigh"]})
        elif kind == "set_session_name":
            STATE["name"] = msg.get("name", "")
            ok(rid, kind)
        elif kind == "set_thinking_level":
            STATE["level"] = msg.get("level", "")
            ok(rid, kind)
        elif kind == "switch_session":
            STATE["session_file"] = msg.get("sessionPath", "")
            ok(rid, kind, {"cancelled": False})
        elif kind == "prompt":
            text = msg.get("message", "")
            word = text.partition(" ")[0]
            if word == "HANDLED":
                ok(rid, kind, {"disposition": "handled"})
                continue
            ok(rid, kind, {"disposition": "started"} if word != "SILENT" else {})
            if word == "SILENT":
                continue
            run(text)
        elif kind == "abort":
            ok(rid, kind)
        elif rid is not None:
            send({"id": rid, "type": "response", "command": kind, "success": False, "error": f"no {kind}"})


if __name__ == "__main__":
    main()
