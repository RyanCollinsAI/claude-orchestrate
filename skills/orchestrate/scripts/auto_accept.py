"""Auto-accept permission prompts in named panes (a deny-rule collision still prompts inside bypass).

  py auto_accept.py <pane_id> [...]

Emits one line for the first accepted prompt on a pane, then at most one line per pane every
AUTO_ACCEPT_REPORT_SEC seconds (default 600) carrying the count since the last line - a busy pane
hits a deny-rule prompt every few seconds and one Monitor event per press drowned the orchestrator
on 2026-09-07. Runs until killed. watch_sessions.py only REPORTS a stuck pane (`<name> PROMPT | ...`)
- use this when you want the pressing done for you.

Self-approval gate (2026-09-13, references/approval-policy.md "Self-approval requires proof"):
a prompt is only auto-accepted when the pane's own recent output carries verify evidence - a test
count, a proof-screenshot path, or a "verified on <surface>" line. Without one the press is
refused and reported instead, so a pane cannot sail an unverified change through on autopilot.
Set AUTO_ACCEPT_REQUIRE_VERIFY=0 to fall back to the old unconditional behavior. Every decision
(accepted or refused) is appended to board/self-approvals.jsonl with an id, so a later revert can
be logged against the approval it undoes.
"""
import json, os, re, sys, time, uuid

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import orchlib as L

REPORT_SEC = float(os.environ.get("AUTO_ACCEPT_REPORT_SEC", "600"))
REQUIRE_VERIFY = os.environ.get("AUTO_ACCEPT_REQUIRE_VERIFY", "1") != "0"
LOG_PATH = os.path.join(L.BOARD_DIR, "self-approvals.jsonl")

# The three shapes approval-policy.md accepts as proof: a test count ("14 passed", "3 tests"), a
# screenshot/proof file path, or an explicit "verified on <surface>" line.
VERIFY_PATTERNS = [
    re.compile(r"\b\d+\s+(?:tests?|passed|failed|specs?)\b", re.I),
    re.compile(r"[\w./\\-]+\.(?:png|jpg|jpeg|gif)\b", re.I),
    re.compile(r"verified on\s+\S+", re.I),
]


def has_verify_evidence(text):
    """True if `text` (a pane's recent visible output) carries proof of a verify step."""
    return any(p.search(text or "") for p in VERIFY_PATTERNS)


def verify_snippet(text):
    """The matched proof line, trimmed, or '' - kept in the log so a revert can be read against it."""
    for line in (text or "").splitlines():
        if has_verify_evidence(line):
            return line.strip()[:160]
    return ""


def log_decision(pane, decision, question, snippet):
    """Append one entry to board/self-approvals.jsonl. Never raises - a logging failure must not
    block the accept/refuse decision itself."""
    entry = {
        "id": uuid.uuid4().hex[:12],
        "ts": time.time(),
        "pane": pane,
        "decision": decision,
        "question": (question or "")[:160],
        "verify_snippet": snippet,
    }
    try:
        os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
    except OSError:
        pass
    return entry["id"]


def run(panes):
    last_report = {p: 0.0 for p in panes}
    pending = {p: 0 for p in panes}
    refused = {p: 0 for p in panes}
    last_q = {p: "" for p in panes}
    last_refusal = {p: "" for p in panes}

    while True:
        now = time.time()
        for pane in panes:
            try:
                text = L.pane_read(pane, 30)
                q = L.pending_prompt(text)
            except Exception:
                continue
            if q:
                snippet = verify_snippet(text)
                if REQUIRE_VERIFY and not snippet:
                    log_decision(pane, "refused", q, "")
                    refused[pane] += 1
                    last_refusal[pane] = q[:160]
                else:
                    L.send_keys(pane, "enter")
                    log_decision(pane, "accepted", q, snippet)
                    pending[pane] += 1
                    last_q[pane] = q[:160]
                    time.sleep(2)
            if pending[pane] and now - last_report[pane] >= REPORT_SEC:
                n = pending[pane]
                suffix = "" if n == 1 else f" (+{n - 1} more since last line)"
                print(f"{pane} ACCEPTED | {last_q[pane]}{suffix}", flush=True)
                pending[pane] = 0
                last_report[pane] = now
            if refused[pane] and now - last_report[pane] >= REPORT_SEC:
                n = refused[pane]
                suffix = "" if n == 1 else f" (+{n - 1} more since last line)"
                print(f"{pane} SELF-APPROVAL REFUSED | {last_refusal[pane]}{suffix} "
                      f"(no verify evidence)", flush=True)
                refused[pane] = 0
                last_report[pane] = now
        time.sleep(5)


if __name__ == "__main__":
    run(sys.argv[1:])
