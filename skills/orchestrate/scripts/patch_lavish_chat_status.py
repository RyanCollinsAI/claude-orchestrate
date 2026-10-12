"""Patch the lavish-axi chrome so the Conversation column shows live progress.

Ryan, 2026-10-01: the right-hand column showed a spinner and "Working..." and then one final
reply, so he could not tell what the agent was doing. This adds, to the installed lavish-axi:

  1. cli.mjs      POST /api/:key/agent-status  -> broadcasts an `agent-status` live event (text
                  only, never stored in the chat history). The last status is kept in memory and
                  replayed to a tab that (re)connects its WebSocket.
  2. chrome-client.js  the Working bubble becomes: pulsing dot + latest status text + an elapsed
                  timer (m:ss). Every bubble gets a clock stamp and a clearer AGENT / YOU label.
  3. chrome.css   tighter spacing, label pills, and a muted dashed Working bubble, all inside the
                  Bridge palette that patch_lavish_brand.py sets (brass accent on cream).

The CLI side is `py podium.py status --note <note> --text <text>` in the learn skill.

Ported to lavish-axi 0.1.84 (task F6). Since 0.1.65 the live stream is a WebSocket, not SSE, so the
event goes out through broadcastLiveEvent (the same helper agent-reply and chat-sync use) and the
browser listens with events.set("agent-status", ...) in the connectLiveEvents handler table; the
replay on reconnect sits in sendInitialLiveEventState. 0.1.84 has no equivalent of the status line
(its per-note receipts say Seen / Working / Done but carry no text from the agent), so all of it stays.

Run order matters: patch_lavish_brand.py first (this anchors on lines the brand patches leave
alone, and its CSS uses the variables the brand palette defines). patch_lavish_brand.py's main()
calls this at the end, so the usual `py patch_lavish_brand.py` after `npm i -g lavish-axi` applies
everything.

cli.mjs is loaded once per server process. After patching, `lavish-axi stop` and reopen a page
to get the route. chrome-client.js and chrome.css are read per request: an open tab needs one reload.

    py patch_lavish_chat_status.py            # patch, or say "already patched"
    py patch_lavish_chat_status.py --check    # exit 0 if all three are patched, 1 if not
"""
import os
import sys

CLI = os.environ.get("LAVISH_CLI_MJS") or os.path.expanduser(
    r"~\AppData\Roaming\npm\node_modules\lavish-axi\dist\cli.mjs")   # env override: patch a scratch copy
DIST =os.path.dirname(CLI)
CLIENT = os.path.join(DIST, "chrome-client.js")
CSS = os.path.join(DIST, "chrome.css")

SRV_MARK = "/* patched: chat status route + live event */"
CLIENT_MARK = "/* patched: chat status line, elapsed timer, bubble stamps */"
CSS_MARK = "/* patched: chat column look - labels, stamps, tight spacing, muted working bubble */"

# ---------------------------------------------------------------------------
# cli.mjs
# ---------------------------------------------------------------------------

ROUTE_ANCHOR = '  app.post("/api/end", async (req, res, next) => {\n'
ROUTE_NEW = (
    "  " + SRV_MARK + "\n"
    r"""  const podiumLastStatus = new Map();
  events.on("agent-reply", (key) => podiumLastStatus.delete(key));
  events.on("agent-status", (key, payload) => broadcastLiveEvent(
    "agent-status", key, payload && typeof payload === "object" ? payload : { text: String(payload || "") }));
  app.post("/api/:key/agent-status", async (req, res, next) => {
    try {
      const text = String(req.body?.text || "").replace(/\s+/g, " ").trim().slice(0, 160);
      const session = await store.findByKey(req.params.key);
      if (!session) {
        res.status(404).json({ error: "session not found" });
        return;
      }
      if (text) podiumLastStatus.set(req.params.key, { text, at: Date.now() });
      else podiumLastStatus.delete(req.params.key);
      events.emit("agent-status", req.params.key, text);
      res.json({ status: "sent" });
    } catch (error) {
      next(error);
    }
  });
"""
) + ROUTE_ANCHOR

# sendInitialLiveEventState runs for every new WebSocket (a reload, or a background tab coming back).
REPLAY_ANCHOR = (
    '    client.sendEvent("agent-presence", presenceEventData(key, presence, activePolls, deliveredFeedback));\n'
)
REPLAY_NEW = REPLAY_ANCHOR + (
    "    const lastStatus = podiumLastStatus.get(key);\n"
    '    if (lastStatus && Date.now() - lastStatus.at < 900000) client.sendEvent("agent-status", { text: lastStatus.text });\n'
)

CLI_EDITS = [
    (ROUTE_ANCHOR, ROUTE_NEW),
    (REPLAY_ANCHOR, REPLAY_NEW),
]

# ---------------------------------------------------------------------------
# chrome-client.js
# ---------------------------------------------------------------------------

STATE_ANCHOR = "let workingBubble = null;\n"
STATE_NEW = STATE_ANCHOR + CLIENT_MARK + "\n" + r"""let workStartedAt = 0;
let workTimer;
let agentStatusText = "";
function fmtElapsed(ms) {
  const s = Math.max(0, Math.floor(ms / 1000));
  return Math.floor(s / 60) + ":" + String(s % 60).padStart(2, "0");
}
function fmtClock(at) {
  const d = at ? new Date(at) : new Date();
  return (isNaN(d.getTime()) ? new Date() : d).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
}
function bubbleHeadHtml(who, at) {
  return '<small class="bubble-head"><span class="who">' + who + '</span><span class="when">' + fmtClock(at) + "</span></small>";
}
function tickWorkTimer() {
  const t = workingBubble && workingBubble.querySelector(".work-time");
  if (t) t.textContent = fmtElapsed(Date.now() - workStartedAt);
}
function setAgentStatus(text) {
  agentStatusText = String(text || "").trim();
  const t = workingBubble && workingBubble.querySelector(".work-text");
  if (t) t.textContent = agentStatusText || "Working...";
}
function stopWorkTimer() {
  clearInterval(workTimer);
  workTimer = undefined;
  agentStatusText = "";
}
"""

# chatBubbleHtml(entry) is the one place a transcript entry becomes a bubble (addChat, syncChat and the
# agent-reply handler all go through it), so the label and the clock stamp are added there.
AGENT_HEAD_ANCHOR = '      "<small>Agent</small>" +\n'
AGENT_HEAD_NEW = '      bubbleHeadHtml("Agent", entry.at) +\n'
USER_HEAD_ANCHOR = '    "<small>You</small>" +\n'
USER_HEAD_NEW = '    bubbleHeadHtml("You", entry.at) +\n'

CREATE_ANCHOR = (
    "    workingBubble.innerHTML = '<span class=\"spinner\"></span><span>Working...</span>';\n"
)
CREATE_NEW = (
    "    workingBubble.innerHTML = '<span class=\"work-dot\"></span><span class=\"work-text\"></span>"
    "<span class=\"work-time\">0:00</span>';\n"
    "    workStartedAt = Date.now();\n"
    "    setAgentStatus(agentStatusText);\n"
    "    clearInterval(workTimer);\n"
    "    workTimer = setInterval(tickWorkTimer, 1000);\n"
)

REMOVE_ANCHOR = "    if (workingBubble) workingBubble.remove();\n    workingBubble = null;\n"
REMOVE_NEW = REMOVE_ANCHOR + "    stopWorkTimer();\n"

LISTEN_ANCHOR = (
    'events.set("agent-presence", (data) => setAgentPresence(data.mode === "external-listener" ? "external" : data.state));\n'
)
LISTEN_NEW = LISTEN_ANCHOR + (
    'events.set("agent-status", (data) => setAgentStatus(data.text));\n'
)

CLIENT_EDITS = [
    (STATE_ANCHOR, STATE_NEW),
    (AGENT_HEAD_ANCHOR, AGENT_HEAD_NEW),
    (USER_HEAD_ANCHOR, USER_HEAD_NEW),
    (CREATE_ANCHOR, CREATE_NEW),
    (REMOVE_ANCHOR, REMOVE_NEW),
    (LISTEN_ANCHOR, LISTEN_NEW),
]

# ---------------------------------------------------------------------------
# chrome.css  (values are the variables patch_lavish_brand.py defines)
# ---------------------------------------------------------------------------

CSS_BLOCK = CSS_MARK + "\n" + """.chat {
  gap: 6px;
  padding: 0 14px 10px;
}
.bubble {
  max-width: 92%;
  padding: 7px 10px 8px;
  border-radius: 10px;
  font-size: 13px;
  line-height: 1.4;
}
.bubble .bubble-text {
  overflow-wrap: anywhere;
}
.bubble small.bubble-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  margin-bottom: 4px;
}
.bubble.user small.bubble-head {
  flex-direction: row-reverse;
}
.bubble .who {
  padding: 1px 7px;
  border-radius: 999px;
  font-weight: 700;
}
.bubble.agent .who {
  background: var(--accent);
  color: var(--accent-ink);
}
.bubble.user .who {
  background: var(--border);
  color: var(--fg);
}
.bubble .when {
  font-size: 10px;
  font-weight: 500;
  letter-spacing: 0;
  text-transform: none;
  color: var(--fg-faint);
  font-variant-numeric: tabular-nums;
}
.bubble.agent {
  background: #ffffff;
  border-left: 3px solid var(--accent);
}
.bubble.agent.agent-working {
  display: flex;
  align-items: center;
  gap: 8px;
  background: transparent;
  border: 1px dashed var(--border-strong);
  color: var(--fg-muted);
  font-size: 12.5px;
}
.work-dot {
  flex: 0 0 auto;
  width: 8px;
  height: 8px;
  border-radius: 999px;
  background: var(--accent);
  animation: work-pulse 1.2s ease-in-out infinite;
}
.work-text {
  flex: 1 1 auto;
  min-width: 0;
  overflow-wrap: anywhere;
}
.work-time {
  flex: 0 0 auto;
  font-size: 11px;
  color: var(--fg-faint);
  font-variant-numeric: tabular-nums;
}
@keyframes work-pulse {
  0%, 100% {
    opacity: 0.35;
    transform: scale(0.8);
  }
  50% {
    opacity: 1;
    transform: scale(1);
  }
}
@media (prefers-reduced-motion: reduce) {
  .work-dot {
    animation: none;
  }
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
                  "(run patch_lavish_brand.py first, or this lavish-axi version moved the code)")
            return 1
    for anchor, new in edits:
        src = src.replace(anchor, new)
    if mark not in src:                      # the marker must land, or --check would lie
        print(f"{label}: marker did not land; refusing to write")
        return 1
    _write(path, src)
    print(f"patched {label} in {path}")
    return 0


def patch_server(check):
    return _apply("cli.mjs chat-status", CLI, SRV_MARK, CLI_EDITS, check)


def patch_client(check):
    return _apply("chrome-client.js chat-status", CLIENT, CLIENT_MARK, CLIENT_EDITS, check)


def patch_css(check):
    if not os.path.exists(CSS):
        print(f"not found: {CSS}")
        return 1
    src = _read(CSS)
    patched = CSS_MARK in src
    if check:
        print("chrome.css chat-look: " + ("patched" if patched else "not patched"))
        return 0 if patched else 1
    if patched:
        print("chrome.css chat-look: already patched")
        return 0
    _write(CSS, src.rstrip("\n") + "\n\n" + CSS_BLOCK)
    print(f"patched chat-look in {CSS}")
    return 0


def main():
    check = "--check" in sys.argv
    r1 = patch_server(check)
    r2 = patch_client(check)
    r3 = patch_css(check)
    return r1 or r2 or r3


if __name__ == "__main__":
    sys.exit(main())
