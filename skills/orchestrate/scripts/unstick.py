"""unstick.py - the guard rails for `unstick-mate`, the mate that un-sticks stalled panes.

  py unstick.py check <pane> [--flag COND]        gate + classify + the pane's last lines. READ ONLY.
  py unstick.py did <pane> --flag F --did "..." --why "..."   log an action and start the 10 min clock
  py unstick.py escalate <pane> --flag F --text "..."   log it and tell the owner; decides nothing

The watchdog (orch_watchdog.py) wakes the mate with one line per flagged pane. The mate is an LLM
and chooses the words; THIS file holds every rule that must hold whatever the LLM thinks:

  gate      never the orchestrator seat, never itself, never a domain mate, and one action per pane
            per 10 minutes (the grok loop guard, memory grok-orchestrator-seat-2026-09-21: a seat
            woken by its own output loops, so a pane is never acted on twice in a row)
  classify  what the pane is stuck on: ESCALATE | PERMISSION | HOOK_DENIED | QUESTION | STALL
  log       board/unstick.log and one line on the Bridge Updates feed

ESCALATE wins over everything: submit, pay, send, publish, delete, a wide ship, or a pick between
options go to a human (through the owner and the orchestrator), never answered here.
A PERMISSION prompt is never answered by this mate; hooks/answer-parked-permissions.py already
answers the safe file prompts, so a prompt still parked goes to the owner.
"""
import argparse
import json
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import orchlib as L

SCRIPTS = os.path.dirname(os.path.abspath(__file__))
MATE_NAME = "unstick-mate"
LOG_PATH = os.path.join(L.BOARD_DIR, "unstick.log")
STATE_PATH = os.path.join(L.BOARD_DIR, "unstick-state.json")
RATE_SECS = 10 * 60

# Actions only Ryan may take or choose. Matched on the pane's last message, case-insensitive.
ESCALATE_RE = re.compile(
    # `checkout` is a git word here ("the live checkout", "a worktree checkout"), so it only counts
    # as a purchase in a shopping phrase (2026-10-04: a finished report was escalated as a buy).
    r"\b(submit|submission|turn(?:ing)? in|hand(?:ing)? in|pay|payment|purchase|"
    r"(?:proceed(?:ing)?|go(?:ing)?) to check-?out|(?:at|complete|finish) (?:the )?check-?out|"
    r"check-?out (?:page|button|form|flow|cart|screen)|place (?:the |an? )?order|add to cart|"
    r"buy|send (?:an? |the )?(?:e-?mail|message|dm|reply|slack)|e-?mail (?:him|her|them|the)|"
    r"publish|go live|upload to youtube|post (?:it |this )?(?:to|on)|delete|remove permanently|"
    r"wipe|drop (?:the )?(?:table|database)|force[- ]push|reset --hard|merge (?:it )?to main|"
    r"ship (?:it )?to (?:main|prod)|deploy to prod|wide (?:ship|blast)|blast radius|"
    r"which (?:option|approach|do you prefer|would you prefer)|option [ab]\b|your call|up to you|"
    r"do you want me to (?:choose|pick))\b", re.I)
# A PreToolUse refusal only. "Stop hook error" is a different thing (it pushes a pane to keep going).
HOOK_RE = re.compile(r"PreToolUse:\w+ hook error|blocked by .{0,40}hook|hook (?:denied|blocked)|"
                     r"^\s*[⎿ ]*Blocked:", re.I | re.M)
QUESTION_RE = re.compile(r"\?\s*$|\b(should i|do you want|would you like|which|can you confirm|"
                         r"please confirm|let me know|waiting for)\b", re.I)


# ---------------------------------------------------------------- pure rules (tested)

def last_message(text, n=1200):
    """The tail of a pane's screen that holds the stuck message. Drops the empty box and footer."""
    lines = [l.rstrip() for l in text.splitlines()]
    while lines and (not lines[-1].strip() or re.match(r"^[\s│╭╰─>❯⏵\-]*$", lines[-1])
                     or "bypass permissions" in lines[-1].lower() or "? for shortcuts" in lines[-1]):
        lines.pop()
    return "\n".join(lines)[-n:]


NOISE_RE = re.compile(r"^[\s─│╭╰>❯✢✻·\-]*$|bypass permissions|\? for shortcuts|ctrl\+o|Tip:|"
                      r"^(calm|hide) (on|off)|shift\+tab|^\s*\S+ ·\s+\S+ \*?\d* .*ctx|Ran \d+ stop hooks?")


def clean_screen(text, n=14):
    """The last `n` meaningful lines of a pane screen: no blank rows, rules, status bar or tips."""
    keep = [l.rstrip() for l in text.splitlines() if l.strip() and not NOISE_RE.search(l)]
    return "\n".join(keep[-n:])


def classify(msg, screen=None):
    """-> (kind, why). `msg` is the pane's last assistant message (from its transcript); `screen`
    is its cleaned screen. The human-decision check reads `msg` ONLY - the screen also holds the
    pane's own spawn prompt, which says "never pay" and would escalate every pane.
    Order matters: a human decision beats a prompt beats a hook message beats a question."""
    screen = msg if screen is None else screen
    tail = last_message(msg)
    if ESCALATE_RE.search(tail):
        return "ESCALATE", f"asks for a human decision: {ESCALATE_RE.search(tail).group(0)!r}"
    if L.pending_prompt(screen):
        return "PERMISSION", "parked on a permission prompt - never answered here"
    if HOOK_RE.search(screen):
        return "HOOK_DENIED", "a hook refused the command - relay the allowed form"
    if QUESTION_RE.search(tail):
        return "QUESTION", "asks something - answer only if the task file or rules already decide it"
    return "STALL", "idle with no question - nudge once with the next concrete step"


def gate(name, now, state, protected=(), domain_mate=False):
    """-> (ok, reason). `protected` is every name this mate must never act on."""
    low = (name or "").lower()
    if not name:
        return False, "no pane named"
    if low == MATE_NAME or low in {p.lower() for p in protected if p}:
        return False, f"{name} is the orchestrator seat or this mate - never acted on"
    if domain_mate:
        return False, f"{name} is a domain mate - its owner handles it, not this mate"
    last = (state.get("acted") or {}).get(low, 0)
    if now - last < RATE_SECS:
        return False, f"acted on {name} {int(now - last)}s ago - one action per pane per 10 min"
    return True, "ok"


# ---------------------------------------------------------------- state + log

def state_load():
    try:
        with open(STATE_PATH, encoding="utf-8") as fh:
            d = json.load(fh)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def state_save(d):
    os.makedirs(L.BOARD_DIR, exist_ok=True)
    with open(STATE_PATH + ".tmp", "w", encoding="utf-8") as fh:
        json.dump(d, fh, indent=1)
    os.replace(STATE_PATH + ".tmp", STATE_PATH)


def log_action(pane, flag, did, why, now=None, log=None):
    log = log or LOG_PATH
    os.makedirs(os.path.dirname(log), exist_ok=True)
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(now or time.time()))}  " \
           f"pane={pane}  flag={flag}  did={' '.join(did.split())}  why={' '.join(why.split())}"
    with open(log, "a", encoding="utf-8") as fh:
        fh.write(line + "\n")
    return line


def board_update(pane, flag, did):
    try:
        import board as B
        B.sync_board(new_updates=[{"ts": B.now_iso(), "pane": pane, "kind": "UNSTICK",
                                    "text": f"{flag}: {' '.join(did.split())}"[:200],
                                    "group": B.group_for_who(pane)}])
        return True
    except Exception as e:
        print(f"  -> board write failed ({type(e).__name__}: {e})", flush=True)
        return False


def record(pane, flag, did, why, now=None):
    """Log the action, post it to Bridge, and start the pane's 10 minute clock."""
    now = now or time.time()
    st = state_load()
    st.setdefault("acted", {})[pane.lower()] = now
    state_save(st)
    line = log_action(pane, flag, did, why, now)
    board_update(pane, flag, did)
    return line


# ---------------------------------------------------------------- commands

def _protected():
    names = {L.orchestrator_name() or ""}
    seat = L.orchestrator_seat() or {}
    names.add(seat.get("label", ""))
    return names - {""}


def cmd_check(name, flag):
    m = L.resolve(name)
    pane = (L.panes_by_sid().get((m or {}).get("sessionId", "")) or {}).get("pane_id", "") \
        if m else name
    ok, why = gate((m or {}).get("name", name), time.time(), state_load(), _protected(),
                   bool((m or {}).get("domain")))
    screen = clean_screen(L.pane_read(pane, 80, "recent")) if pane else ""
    msg = L.classify(m["sessionId"])[1] if m else ""
    kind, kwhy = classify(msg, screen)
    print(f"GATE: {'OK' if ok else 'REFUSE'} - {why}")
    print(f"KIND: {kind} - {kwhy}")
    print(f"FLAG: {flag or '-'}   OWNER: {(m or {}).get('owner') or '-'}   PANE: {pane or '-'}")
    print("---- its last message ----")
    print(msg or "(none)")
    print("---- its screen ----")
    print(screen)
    return 0 if ok else 3


def cmd_did(name, flag, did, why):
    print(record(name, flag, did, why))
    return 0


def cmd_escalate(name, flag, text):
    import subprocess
    m = L.resolve(name)
    owner = (m or {}).get("owner") or L.orchestrator_name()
    line = record(name, flag, "ESCALATED: " + text, "needs a human decision, not answered here")
    print(line)
    if owner and owner.lower() not in (MATE_NAME, name.lower()):
        env = dict(os.environ, ORCH_SENDER=MATE_NAME)
        r = subprocess.run([sys.executable, os.path.join(SCRIPTS, "orch.py"), "prompt", owner,
                            f"[unstick] {name} is stuck on a decision only a human makes: {text} "
                            f"(flag {flag}). I did NOT answer it. Decide, or put it on Bridge."],
                           capture_output=True, text=True, timeout=90, env=env)
        print(f"  -> {owner}: {'sent' if r.returncode == 0 else 'FAILED ' + r.stderr[-150:]}")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check")
    c.add_argument("pane")
    c.add_argument("--flag", default="")
    d = sub.add_parser("did")
    d.add_argument("pane")
    d.add_argument("--flag", required=True)
    d.add_argument("--did", required=True)
    d.add_argument("--why", required=True)
    e = sub.add_parser("escalate")
    e.add_argument("pane")
    e.add_argument("--flag", required=True)
    e.add_argument("--text", required=True)
    a = ap.parse_args(argv)
    if a.cmd == "check":
        return cmd_check(a.pane, a.flag)
    if a.cmd == "did":
        return cmd_did(a.pane, a.flag, a.did, a.why)
    return cmd_escalate(a.pane, a.flag, a.text)


if __name__ == "__main__":
    sys.exit(main())
