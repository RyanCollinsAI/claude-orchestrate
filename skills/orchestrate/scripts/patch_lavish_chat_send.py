"""Patch lavish-axi so Send to Agent returns fast and the chat column tells the truth about the agent.

Ryan, 2026-10-01: Send to Agent felt buggy, and nothing said "working" unless the agent remembered
to post a podium.py status. Root causes found in the lavish-axi 0.1.32 chrome-client.js / cli.mjs:

  1. Click latency. sendQueued asked the artifact iframe for a DOM snapshot and WAITED for it
     before the POST. A heavy or busy page (the learn page) made the button spin for seconds, and a
     dead iframe pinned it for 15 s.
  2. A dead button. While the agent was working, the button was disabled and sendQueued silently
     retried forever: the text stayed in the box, nothing appeared, nothing said why.
  3. No status without help. Presence only knew "listening" / "working"; the text came only from
     agents that called podium.py status.

Ported to lavish-axi 0.1.84 (task F6). What 0.1.84 now does itself, so those sub-patches are GONE:

  - (2) sendQueued (chrome-client.js, "function sendQueued") has no presence gate and updateSendState
    only disables the button for ended / terminal sends. A click always sends.
  - Instant feedback on click: the note renders as a dashed "Queued" / "Sending..." bubble before the
    box is cleared (render() then chatInput.value = ""), isPromptSending derives the label, and
    submitQueuedOnce drops prompts already delivered, so a second click cannot double-send.
  - The "Sent" / "Agent has not picked this up yet" lines: every sent note carries a Seen / Working /
    Done receipt row, and until Seen it says "No agent is listening" or "Delivering" (receiptNoteText).
  - Keeping the Working bubble last in the log: syncChat re-appends it after every transcript rebuild.
  - Ignoring a late snapshot: takeSnapshotRequest returns null once the request timed out.

What this still adds (on top of patch_lavish_chat_status.py, which must be applied first):

  cli.mjs
    - per-session auto status: when a poll delivers the message the status becomes "Agent got it -
      working" and its clock starts; cleared on agent reply, on an artifact reload, when the session
      ends, or when the agent polls again. The agent-status route now OVERRIDES the text but keeps
      the clock. The agent-status live event carries {text, since, phase} and a reconnecting tab
      gets it replayed.
  chrome-client.js
    - the Working bubble renders the server text with a live (m:ss) timer from the delivery moment.
    - SNAPSHOT_REQUEST_TIMEOUT_MS 5000 -> 700: 0.1.68 bounded the snapshot wait at 5 s, but a busy
      page still held the POST for that long; the words are the payload and the snapshot is context.
  chrome.css
    - pressed state for the send button.

cli.mjs is loaded once per server process: a running Bridge keeps the old code until it restarts.
chrome-client.js and chrome.css are read per request: an open tab needs one reload.

LAVISH_DIST points at another copy of the dist folder (tests, throwaway servers).

    py patch_lavish_chat_send.py            # patch, or say "already patched"
    py patch_lavish_chat_send.py --check    # exit 0 if all three are patched, 1 if not
"""
import os
import sys

DIST = os.environ.get("LAVISH_DIST") or os.path.expanduser(
    r"~\AppData\Roaming\npm\node_modules\lavish-axi\dist")
CLI = os.path.join(DIST, "cli.mjs")
CLIENT = os.path.join(DIST, "chrome-client.js")
CSS = os.path.join(DIST, "chrome.css")

SRV_MARK = "/* patched: chat auto status (working + timer) */"
CLIENT_MARK = "/* patched: chat send smooth (bounded snapshot wait, server status) */"
CSS_MARK = "/* patched: chat send smooth - pressed state */"

# ---------------------------------------------------------------------------
# cli.mjs
# ---------------------------------------------------------------------------

STATE_ANCHOR = '  app.post("/api/end", async (req, res, next) => {\n'
STATE_NEW = (
    "  " + SRV_MARK + "\n"
    r"""  const chatAuto = new Map();
  function chatPayload(key) {
    const s = chatAuto.get(key);
    if (!s) return { text: "" };
    return { text: s.own || "Agent got it - working", since: s.phase === "working" ? s.since : null, phase: s.phase };
  }
  function chatEmit(key) {
    events.emit("agent-status", key, chatPayload(key));
  }
  function chatClear(key) {
    if (!chatAuto.delete(key)) return;
    chatEmit(key);
  }
  function chatSetOwn(key, text) {
    let s = chatAuto.get(key);
    if (!s) {
      if (!text) return;
      s = { phase: "own", since: 0, own: "" };
      chatAuto.set(key, s);
    }
    s.own = text;
    if (!text && s.phase === "own") chatAuto.delete(key);
    chatEmit(key);
  }
  events.on("feedback-delivered", (key) => {
    const s = chatAuto.get(key);
    if (s && s.phase === "working") return;
    chatAuto.set(key, { phase: "working", since: Date.now(), own: s ? s.own : "" });
    chatEmit(key);
  });
  events.on("agent-reply", chatClear);
  events.on("reload", chatClear);
  events.on("ended", chatClear);
  events.on("agent-presence", (key, state) => {
    const s = chatAuto.get(key);
    if (s && state === "listening") chatClear(key);
  });
"""
) + STATE_ANCHOR

OWN_ANCHOR = '      events.emit("agent-status", req.params.key, text);\n'
OWN_NEW = "      chatSetOwn(req.params.key, text);\n"

REPLAY_ANCHOR = (
    '    if (lastStatus && Date.now() - lastStatus.at < 900000) client.sendEvent("agent-status", { text: lastStatus.text });\n'
)
REPLAY_NEW = (
    '    { const chatNow = chatPayload(key); if (chatNow.text) client.sendEvent("agent-status", chatNow); }\n'
)

# markFeedbackDelivered(key, activePolls, deliveredFeedback, events) is the one place a poll's delivery
# is recorded; it gets the server's EventEmitter as a parameter, so the event reaches the block above.
DELIVERED_ANCHOR = "  deliveredFeedback.add(key);\n"
DELIVERED_NEW = DELIVERED_ANCHOR + '  events.emit("feedback-delivered", key);\n'

CLI_EDITS = [
    (STATE_ANCHOR, STATE_NEW),
    (OWN_ANCHOR, OWN_NEW),
    (REPLAY_ANCHOR, REPLAY_NEW),
    (DELIVERED_ANCHOR, DELIVERED_NEW),
]

# ---------------------------------------------------------------------------
# chrome-client.js
# ---------------------------------------------------------------------------

CLIENT_STATE_ANCHOR = 'let agentStatusText = "";\n'
CLIENT_STATE_NEW = CLIENT_STATE_ANCHOR + CLIENT_MARK + "\nlet statusSince = 0;\n"

TICK_ANCHOR = (
    "function tickWorkTimer() {\n"
    '  const t = workingBubble && workingBubble.querySelector(".work-time");\n'
    "  if (t) t.textContent = fmtElapsed(Date.now() - workStartedAt);\n"
    "}\n"
)
TICK_NEW = (
    "function tickWorkTimer() {\n"
    '  const t = workingBubble && workingBubble.querySelector(".work-time");\n'
    '  if (t) t.textContent = "(" + fmtElapsed(Date.now() - (statusSince || workStartedAt)) + ")";\n'
    "}\n"
)

SETSTATUS_ANCHOR = (
    "function setAgentStatus(text) {\n"
    '  agentStatusText = String(text || "").trim();\n'
    '  const t = workingBubble && workingBubble.querySelector(".work-text");\n'
    '  if (t) t.textContent = agentStatusText || "Working...";\n'
    "}\n"
)
SETSTATUS_NEW = r"""function setAgentStatus(p) {
  if (p && typeof p === "object") {
    agentStatusText = String(p.text || "").trim();
    statusSince = Number(p.since) || 0;
  } else {
    agentStatusText = String(p || "").trim();
  }
  const t = workingBubble && workingBubble.querySelector(".work-text");
  if (t) t.textContent = agentStatusText || "Working...";
  tickWorkTimer();
}
"""

STOP_ANCHOR = "  workTimer = undefined;\n  agentStatusText = \"\";\n"
STOP_NEW = STOP_ANCHOR + "  statusSince = 0;\n"

LISTEN_ANCHOR = 'events.set("agent-status", (data) => setAgentStatus(data.text));\n'
LISTEN_NEW = 'events.set("agent-status", (data) => setAgentStatus(data));\n'

SNAP_ANCHOR = "const SNAPSHOT_REQUEST_TIMEOUT_MS = 5000;\n"
SNAP_NEW = "const SNAPSHOT_REQUEST_TIMEOUT_MS = 700;\n"

CLIENT_EDITS = [
    (CLIENT_STATE_ANCHOR, CLIENT_STATE_NEW),
    (TICK_ANCHOR, TICK_NEW),
    (SETSTATUS_ANCHOR, SETSTATUS_NEW),
    (STOP_ANCHOR, STOP_NEW),
    (LISTEN_ANCHOR, LISTEN_NEW),
    (SNAP_ANCHOR, SNAP_NEW),
]

# ---------------------------------------------------------------------------
# chrome.css
# ---------------------------------------------------------------------------

CSS_BLOCK = CSS_MARK + "\n" + """#send {
  transition: transform 90ms ease, filter 90ms ease, opacity 120ms ease-out;
}
#send:active:not(:disabled),
#send[data-sending="true"] {
  transform: scale(0.97);
  filter: brightness(0.92);
}
#send[data-sending="true"] {
  cursor: progress;
}
"""


def _read(path):
    return open(path, encoding="utf-8").read()


def _write(path, text):
    open(path, "w", encoding="utf-8", newline="\n").write(text)


def _apply(label, path, mark, edits, check):
    if not os.path.exists(path):
        print(f"not found: {path}")
        return 1
    src = _read(path)
    patched = mark in src
    if check:
        print(f"{label}: " + ("patched" if patched else "not patched"))
        return 0 if patched else 1
    if patched:
        print(f"{label}: already patched")
        return 0
    for anchor, _ in edits:
        n = src.count(anchor)
        if n != 1:
            print(f"{label}: anchor count {n} != 1 for {anchor[:70]!r} "
                  "(run patch_lavish_chat_status.py first, or this lavish-axi version moved the code)")
            return 1
    for anchor, new in edits:
        src = src.replace(anchor, new)
    if mark not in src:
        print(f"{label}: marker did not land; refusing to write")
        return 1
    _write(path, src)
    print(f"patched {label} in {path}")
    return 0


def patch_css(check):
    if not os.path.exists(CSS):
        print(f"not found: {CSS}")
        return 1
    src = _read(CSS)
    patched = CSS_MARK in src
    if check:
        print("chrome.css chat-send: " + ("patched" if patched else "not patched"))
        return 0 if patched else 1
    if patched:
        print("chrome.css chat-send: already patched")
        return 0
    _write(CSS, src.rstrip("\n") + "\n\n" + CSS_BLOCK)
    print(f"patched chat-send in {CSS}")
    return 0


def main():
    check = "--check" in sys.argv
    r1 = _apply("cli.mjs chat-send", CLI, SRV_MARK, CLI_EDITS, check)
    r2 = _apply("chrome-client.js chat-send", CLIENT, CLIENT_MARK, CLIENT_EDITS, check)
    r3 = patch_css(check)
    return r1 or r2 or r3


if __name__ == "__main__":
    sys.exit(main())
