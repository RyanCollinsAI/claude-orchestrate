"""
seat-pair-log.py - Stop hook for Claude Code.

Applies ONLY to the Haiku seat of a seat pair (orchestrate/scripts/seat_pair.py). When a turn
ends, every prompt the seat has read since the last turn (seat-pair/seat-prompts.jsonl, written by
seat-pair-route.py) is matched to its own entry in the transcript: a user entry, or a
queued_command attachment for a message sent mid-turn. The assistant text after an entry, up to
the next entry of any kind, is that entry's segment; assign() turns segments into answers. So:
  - each message from Ryan gets its own logged answer in seat-pair/decisions.jsonl (kind
    `answer`, or `answer-from-opus` when its forwarded ask was shown to him), which the watcher
    reviews whether or not Haiku remembered to log it;
  - a `[hotkey id=X]` message gets replies/X.json with only its own reply;
  - while one of Ryan's forwarded asks has not been shown to him, the turn is held open ONCE.
A prompt recorded but not yet in the transcript was sent while the seat was busy and belongs to a
later turn: it is left for that turn (live 2026-10-11, "is the dashboard up or down?" was logged
against the reply to the message before it).
Any other session, no pair, or any exception -> allow (fail open).
Kill switch: C:\\Users\\YOURNAME\\.claude\\hooks\\.seat-pair-log-off
"""

import datetime
import json
import os
import sys
import time

HOOKS_DIR = os.path.dirname(os.path.abspath(__file__))
KILL_SWITCH = os.path.join(HOOKS_DIR, ".seat-pair-log-off")
sys.path.insert(0, HOOKS_DIR)
sys.path.insert(0, os.path.join(os.path.dirname(HOOKS_DIR), "skills", "orchestrate", "scripts"))
TAIL_BYTES = 600_000
STALE = 600          # a recorded prompt never seen in the transcript for this long was dropped
WAIT = 2.0           # for the newest user entry to land; the closing reply comes from the payload


def _epoch(iso):
    try:
        return datetime.datetime.fromisoformat(str(iso).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


def _norm(s):
    return " ".join(str(s or "").split()).lower()


def events(path, since=0.0):
    """[(role, text)] from the transcript tail, oldest first: real user input (typed, peer, or a
    queued_command attachment for a message sent mid-turn; not tool results and not hook
    feedback) and assistant text blocks, at or after `since`."""
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as fh:
            fh.seek(max(0, size - TAIL_BYTES))
            lines = fh.read().decode("utf-8", "replace").splitlines()
    except OSError:
        return []
    out = []
    for raw in lines:
        try:
            e = json.loads(raw)
        except ValueError:
            continue
        if since and _epoch(e.get("timestamp")) < since:
            continue
        att = e.get("attachment") if e.get("type") == "attachment" else None
        if isinstance(att, dict) and att.get("type") == "queued_command":
            # A message sent mid-turn is not a user entry: it lands as this attachment when the
            # running turn reads it (live tester r4: the mid-turn hotkey t2 never matched).
            if str(att.get("prompt") or "").strip():
                out.append(("queued", str(att["prompt"]).strip()))
            continue
        if e.get("type") not in ("user", "assistant"):
            continue
        content = (e.get("message") or {}).get("content")
        if isinstance(content, str):
            text = content
        else:
            blocks = [c for c in content or [] if isinstance(c, dict)]
            if any(c.get("type") == "tool_result" for c in blocks):
                continue
            text = " ".join(c.get("text", "") for c in blocks if c.get("type") == "text")
        if not text.strip() or (e["type"] == "user" and e.get("isMeta") and not e.get("origin")):
            continue
        if e["type"] == "user":
            # The same message as a later real entry: keep one, the real one. Only queued events
            # after the last reply can be that message; an older one is a message of its own.
            key = _norm(text)
            last_reply = max((k for k, ev in enumerate(out) if ev[0] == "assistant"), default=-1)
            out = [ev for k, ev in enumerate(out)
                   if not (ev[0] == "queued" and k > last_reply and _norm(ev[1]) and _norm(ev[1]) in key)]
        out.append((e["type"], text.strip()))
    return [("user", t) if role == "queued" else (role, t) for role, t in out]


def match(rows, evs):
    """[(row, end offset, event index)] for the rows the transcript shows the seat has read.
    Several rows can share one user entry (queued messages arrive joined); within one entry each
    row must be found AFTER the previous one, so a short message inside a longer one ("status" in
    a watcher note) is not taken as read."""
    found, pos, at, now = [], 0, 0, time.time()
    idx_of = []
    for row, end in rows:
        key = _norm(row.get("prompt"))[:100]
        idx = None
        for i in range(pos, len(evs)):
            if evs[i][0] != "user" or not key:
                continue
            j = _norm(evs[i][1]).find(key, at if i == pos else 0)
            if j >= 0:
                idx, at = i, j + len(_norm(row.get("prompt")))   # the whole message, not the 100-char key
                break
        idx_of.append(idx)
        if idx is not None:
            pos = idx
    for k, (row, end) in enumerate(rows):
        idx = idx_of[k]
        later_seen = any(i is not None for i in idx_of[k + 1:])
        if idx is None and not later_seen and now - row.get("ts", now) < STALE:
            break                    # not read yet: it belongs to a later turn
        found.append((row, end, idx))
    return found


def _words(s):
    return {w for w in _norm(s).split() if len(w) >= 5}


def join_texts(texts):
    """The texts joined, but a text that restates the one before it (most of the long words of
    the LONGER one shared, so a short closing line never wipes a long answer) replaces it: live 2026-10-11 the seat answered the OpenWhispr question, ran a tool,
    then said it again reworded, and the log held both."""
    kept = []
    for t in texts:
        if kept:
            a, b = _words(kept[-1]), _words(t)
            if a and b and len(a & b) >= 0.6 * max(len(a), len(b)):
                kept[-1] = t
                continue
        kept.append(t)
    return "\n\n".join(kept)


def segments(evs):
    """{index of every user entry: the assistant text after it, up to the next user entry}.
    Every entry ends the one before, recorded or not (live tester r3, bug B); assign() decides
    which segments a reply to Ryan may borrow back."""
    users = [i for i, (role, _t) in enumerate(evs) if role == "user"]
    out = {}
    for n, i in enumerate(users):
        stop = users[n + 1] if n + 1 < len(users) else len(evs)
        out[i] = join_texts([t for role, t in evs[i + 1:stop] if role == "assistant"])
    return out


def read_turn(payload, rows):
    """(found, segments). The turn's closing message reaches the transcript only after Stop hooks
    run, so it comes from the payload's last_assistant_message and belongs to the newest user
    entry (live tester r3, bug A: waiting for it in the transcript logged every last answer one
    turn late)."""
    since = min(r.get("ts", 0) for r, _ in rows) - 5
    last = str(payload.get("last_assistant_message") or "").strip()
    end = time.time() + WAIT
    while True:
        evs = events(payload.get("transcript_path", ""), since)
        found = match(rows, evs)
        segs = segments(evs)
        if segs and any(i is not None for _r, _e, i in found):
            if last:
                newest = max(segs)
                if _norm(last)[:200] not in _norm(segs[newest]):
                    segs[newest] = join_texts([t for t in (segs[newest], last) if t])
            return found, segs
        if time.time() >= end:
            return found, segs
        time.sleep(0.3)


def assign(found, segs, SP):
    """[(row, its own answer)] in `found` order.
    - Each `[hotkey id=X]` part goes to hotkey X wherever it sits in the turn.
    - A plain message from Ryan owns its segment. While it is still OWED an answer (its text is
      empty, or its forwarded ask is not shown in it), it also takes the untagged text of the
      entries after it, up to his next plain message: a peer message or a hotkey that landed
      while the seat waited on Opus cut his segment short (review r3 must-fix 1; live tester r4,
      "[hotkey id=t2] Clicking: ..." then the Opus answer).
    - Untagged text after a TAGGED hotkey always goes back to his earlier message.
    - An untagged hotkey reply counts as its own only when no plain message from Ryan came before
      it in the turn; otherwise it can hold that message's answer (review r3 must-fix 2: the VOO
      answer in the title-blue bubble), so the bubble gets its one Clicking line or the pane
      pointer."""
    tagged, rest = {}, {}
    for i in sorted(segs):
        parts, rest[i] = SP.split_tagged(segs[i])
        tagged.update(parts)
    by_idx = {}
    for row, _end, idx in found:
        by_idx.setdefault(idx, []).append(row)

    def owed(i, text):
        rows = by_idx.get(i, [])
        return not text or any(r.get("ask") and not SP.relay_seen(r["ask"], text) for r in rows)

    own, after_plain, plain = dict(rest), set(), None
    for i in sorted(segs):
        rows = by_idx.get(i, [])
        hot = bool(rows) and all(r.get("reply") for r in rows)
        if rows and not hot and any(r.get("from_ryan") for r in rows):
            plain = i
            continue
        if plain is None:
            continue
        after_plain.add(i)
        if not rest.get(i):
            continue
        all_tagged = hot and all(r["reply"] in tagged for r in rows)
        if all_tagged or owed(plain, own[plain]):
            own[plain] = (own[plain] + "\n\n" + rest[i]).strip()
            own[i] = ""
    out = []
    for row, _end, idx in found:
        rid = row.get("reply")
        if not rid:
            out.append((row, own.get(idx, "") if idx is not None else ""))
            continue
        text = rest.get(idx, "") if idx is not None else ""
        # Queued messages can arrive joined in one entry; then they share one reply text.
        alone = len(by_idx.get(idx, [])) == 1 and idx not in after_plain
        out.append((row, tagged.get(rid) or (SP.reply_part(text, rid, alone=alone) if text else None) or ""))
    return out


def main():
    if os.path.exists(KILL_SWITCH):
        return
    payload = json.loads(__import__("_hook_stdin").read_stdin() or "{}")
    import seat_pair as SP
    pair = SP.load_pair()
    if not pair or SP.role_of(payload.get("session_id", ""), os.environ.get("HERDR_PANE_ID", ""), pair) != "seat":
        return
    rows = SP.pending_prompts()
    if not rows:
        return
    found, segs = read_turn(payload, rows)
    # The last entry the seat read but has no reply text yet stays pending for the next Stop:
    # committing it now would log it with no answer and lose the one that follows.
    last_idx = found[-1][2] if found else None
    if last_idx is not None and not any(t for i, t in segs.items() if i >= last_idx):
        found = [f for f in found if f[2] != last_idx or time.time() - f[0].get("ts", 0) > STALE]
    if not found:
        return
    answers = assign(found, segs, SP)
    ryan = [(row, ans) for row, ans in answers if row.get("from_ryan")]
    # "Relayed" means Ryan SAW Opus's answer this turn, not that Haiku fetched it: live, a message
    # that landed mid-wait got the reply and the Opus answer was never shown.
    shown = "\n\n".join(segs[i] for i in sorted(segs))
    unseen = [row["ask"] for row, _ans in ryan if row.get("ask") and not SP.relay_seen(row["ask"], shown)]
    if unseen and not payload.get("stop_hook_active"):
        # A hotkey already answered gets its bubble now, not after the held turn ends.
        for row, ans in ryan:
            if row.get("reply") and ans:
                SP.write_reply(row["reply"], ans, "answer")
        aid = unseen[-1]
        got = SP.ask_answer(aid)
        if got:
            reason = (f"Ryan has not seen Opus's answer to his question ({SP._cut(SP.ask_question(aid), 200)}). "
                      f"Show it to him now in your own short words, saying it came from Opus: {got}")
        else:
            orch = SP.ORCH.replace("\\", "/")
            reason = (f"Opus is still answering ask {aid} for Ryan. Run: py \"{orch}\" ask-opus --wait "
                      f"{aid} (Bash timeout 600000), then show him the answer before you end the turn.")
        print(json.dumps({"decision": "block", "reason": reason}))
        return
    # A plain message with no text of its own was answered together with a later one (two sent
    # back to back, one reply): that later row names both, so neither drops out of the review.
    carry = []
    read = {id(r) for r, _e, idx in found if idx is not None}
    for row, ans in ryan:
        joined = [row] if row.get("reply") else carry + [row]     # a hotkey reply never covers others
        asks = [r["ask"] for r in joined if r.get("ask")]
        kind = "answer-from-opus" if asks and all(SP.relay_seen(a, shown) for a in asks) else "answer"
        if row.get("reply"):
            # Always a reply file, so the bubble never waits out its timeout for a turn that is over.
            SP.write_reply(row["reply"], ans or "Answered in the seat-haiku pane.", kind if ans else "unread")
        elif not ans:
            if id(row) in read:          # never reached the seat: nothing answered it
                carry.append(row)
            continue
        if ans:
            asked = " | ".join(SP._cut(r.get("prompt"), 300) for r in joined)
            if not row.get("reply"):
                carry = []
            SP._append(SP._p("decisions.jsonl"), {
                "ts": time.time(), "iso": SP._now_iso(), "kind": kind,
                "what": f"Ryan: {asked} -> seat: {SP._cut(ans, 900)}",
                "why": "", "rule": "", "by": "auto-stop"})
    SP.commit_turn(found[-1][1])


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
