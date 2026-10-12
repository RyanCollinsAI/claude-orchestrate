"""
seat-pair-route.py - UserPromptSubmit hook for Claude Code.

Applies ONLY to the Haiku seat of a seat pair (orchestrate/scripts/seat_pair.py). When Ryan sends
it a hard message (seat_pair.is_hard: "should", "which", "plan", "X or Y?" ...), this forwards the
message to the Opus watcher BEFORE Haiku starts, and tells Haiku the ask id to wait on. Haiku then
only has to wait and relay.

Why a hook: the first live review (2026-10-11) asked Haiku "Should we make the pending queue
unlimited or keep the 50-item cap?" with the forwarding rule in its brief. Haiku answered alone,
and Opus's answer was the opposite. An instruction is not a guard.

Every prompt is appended to seat-pair/seat-prompts.jsonl (never overwritten: a message that
arrives mid-turn joins the running turn, and seat-pair-log.py answers all of them). Each call also
restarts a dead feeder (seat_pair.ensure_feed).
Any other session, no pair, or any exception -> nothing happens (fail open).
Kill switch: C:\\Users\\YOURNAME\\.claude\\hooks\\.seat-pair-route-off
"""

import json
import os
import sys
import time

HOOKS_DIR = os.path.dirname(os.path.abspath(__file__))
KILL_SWITCH = os.path.join(HOOKS_DIR, ".seat-pair-route-off")
sys.path.insert(0, os.path.join(os.path.dirname(HOOKS_DIR), "skills", "orchestrate", "scripts"))


def main():
    if os.path.exists(KILL_SWITCH):
        return
    payload = json.loads(__import__("_hook_stdin").read_stdin() or "{}")
    import seat_pair as SP
    pair = SP.load_pair()
    if not pair or SP.role_of(payload.get("session_id", ""), os.environ.get("HERDR_PANE_ID", ""), pair) != "seat":
        return
    prompt = str(payload.get("prompt", ""))
    if prompt.lstrip().startswith("/"):
        return              # a slash command is not a message, and never reaches the transcript as one
    from_ryan =not prompt.lstrip().startswith(("[from ", "[fix from watcher]", "<"))
    aid, ctx = None, None
    if from_ryan and SP.is_hard(prompt):
        aid = SP.new_ask(SP.HOTKEY_RE.sub("", prompt).strip(), route="hook")
        SP.start_send_ask(aid)
        orch = SP.ORCH.replace("\\", "/")
        ctx = (f"SEAT-PAIR: this message from Ryan reads as a hard question, so it was already "
               f"forwarded to the Opus watcher as ask {aid}. Do not answer it yourself. Say in one "
               f"line that Opus is thinking, then run: py \"{orch}\" ask-opus --wait {aid} "
               f"(Bash timeout 600000). Relay Opus's answer in your own short words and say it "
               f"came from Opus. Add facts from orch.py ls only if Opus asks for them.")
    rid = SP.reply_id(prompt)
    if rid:
        # Said at the moment it matters, not only in the brief: the seat that merged a hotkey into
        # another answer had been spawned before the brief carried the tag rule.
        tag = (f"SEAT-PAIR: this is a CapsLock hotkey message from Ryan. Start your reply to it with "
               f"the exact tag [hotkey id={rid}] - only the text after the tag reaches his bubble.")
        ctx = f"{ctx}\n{tag}" if ctx else tag
    if from_ryan and not rid:
        # Earlier messages from Ryan this turn: sent back to back, the seat answered only the
        # last one (live tester r6, "which panes are working" got no answer on screen). Only the
        # last minute: an older pending row is likely one he cancelled with Esc.
        now = time.time()
        earlier = [r["prompt"] for r, _ in SP.pending_prompts()
                   if r.get("from_ryan") and not r.get("reply") and now - r.get("ts", 0) < 60]
        if earlier:
            note = ("SEAT-PAIR: Ryan sent this before you finished replying to: "
                    + " | ".join(SP._cut(p, 150) for p in earlier[-3:])
                    + ". Answer every one of his messages, not only this one, unless you already did.")
            ctx = f"{ctx}\n{note}" if ctx else note
    SP.record_prompt(prompt, from_ryan, aid)
    try:
        SP.ensure_feed()
    except Exception:
        pass
    if ctx:
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "UserPromptSubmit",
                                                 "additionalContext": ctx}}))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
