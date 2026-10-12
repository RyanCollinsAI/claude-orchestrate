"""Verifiable approvals: a pane may submit or pay only with an id the orchestrator seat minted.

Why this exists (2026-10-02): Ryan told the orchestrator "submit the BA 323". school-mate refused,
correctly, because a relayed quote cannot be verified, and Ryan had to wait. A record the pane can
check replaces the quote.

    orch.py approve <target> --action "<one exact action>" --words "<Ryan's exact words>"
    orch.py approval check <id> --as <target> --action "<the exact action>"
    orch.py approval ls
    orch.py prompt <target> --approval <id> <text>        (appends the id to the message)

Rules, all enforced here:
- Only the orchestrator seat may mint. A peer is refused, so a peer cannot mint.
- One record = one action, one target, 30 minutes, single use.
- `check` consumes the record. A second check of the same id fails.
- `check` fails on a wrong target, a different action, an expired id, a used id, or an unknown id.
- `check` must run IN the target pane (or as its name). One pane cannot spend another's approval.

Limit: this is a file. A peer that edits board/approvals.json by hand can forge a record. The check
defends against a pane being TALKED into acting, not against a pane with a shell turning hostile.
"""
import datetime
import json
import os
import secrets
import time

import orchlib as L

APPROVALS_FILE = os.path.join(L.BOARD_DIR, "approvals.json")
TTL_SECONDS = 30 * 60
KEEP_SECONDS = 24 * 3600          # used and expired records stay a day, for the audit trail
_LOCK_STALE = 10


def _norm(s):
    return " ".join((s or "").split()).casefold()


def _load():
    try:
        with open(APPROVALS_FILE, encoding="utf-8") as fh:
            d = json.load(fh)
    except (OSError, ValueError):
        return []
    return d if isinstance(d, list) else []


def _save(rows):
    os.makedirs(os.path.dirname(APPROVALS_FILE), exist_ok=True)
    tmp = APPROVALS_FILE + f".{os.getpid()}.tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(rows, fh, indent=1)
    os.replace(tmp, APPROVALS_FILE)


class _Lock:
    """Cross-process lock so two checks of one id cannot both succeed."""
    def __enter__(self):
        self.path = APPROVALS_FILE + ".lock"
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        end = time.time() + 5
        while True:
            try:
                self.fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                return self
            except FileExistsError:
                try:
                    if time.time() - os.path.getmtime(self.path) > _LOCK_STALE:
                        os.remove(self.path)
                        continue
                except OSError:
                    pass
                if time.time() > end:
                    raise SystemExit("approvals file is locked; try again")
                time.sleep(0.05)

    def __exit__(self, *exc):
        os.close(self.fd)
        try:
            os.remove(self.path)
        except OSError:
            pass


def _is_minter():
    """The explicit seat only. The no-seat fallback in `caller_is_seat` would let any unowned pane
    mint; an approval is worth more than that."""
    return bool(L.orchestrator_seat()) and L.caller_is_seat()


def mint(target, action, words, now=None):
    if not _is_minter():
        raise SystemExit("approve REFUSED: only the orchestrator seat may write an approval, and "
                         "this session is not the seat (`py orch.py seat` shows who is). A peer "
                         "cannot mint one.")
    if not (target and action and (words or "").strip()):
        raise SystemExit('usage: orch.py approve <target-pane> --action "<one exact action>" '
                         '--words "<Ryan\'s exact words>"')
    now = now if now is not None else time.time()
    rec = {"id": "ap-" + secrets.token_hex(5), "target": target, "action": action.strip(),
           "words": words.strip(), "ts": now, "expires": now + TTL_SECONDS, "used": None,
           "written_by": L.caller_name(), "written_by_sid": os.environ.get("CLAUDE_CODE_SESSION_ID", ""),
           "written_by_pane": os.environ.get("HERDR_PANE_ID", "")}
    with _Lock():
        rows = [r for r in _load() if now - r.get("expires", 0) < KEEP_SECONDS]
        rows.append(rec)
        _save(rows)
    return rec


def _caller_is(as_name):
    """True when the running session is the pane called `as_name` (name or pane id). A caller
    outside any managed pane cannot be matched, so it is trusted only by its --as claim."""
    kind, _row = L.caller_row()
    pane = os.environ.get("HERDR_PANE_ID", "")
    if not kind and not pane:
        return True
    return _norm(L.caller_name()) == _norm(as_name) or (pane and _norm(pane) == _norm(as_name))


def check(aid, as_pane, action, now=None):
    """Verify and consume. Returns (ok, reason)."""
    now = now if now is not None else time.time()
    if not (aid and as_pane and action):
        return False, 'usage: orch.py approval check <id> --as <pane> --action "<exact action>"'
    with _Lock():
        rows = _load()
        rec = next((r for r in rows if r.get("id") == aid), None)
        if not rec:
            return False, "no such approval id"
        if rec.get("used"):
            return False, f"already used at {rec['used']}"
        if now >= rec.get("expires", 0):
            return False, "expired"
        if _norm(rec["target"]) != _norm(as_pane):
            return False, f"this approval is for {rec['target']!r}, not {as_pane!r}"
        if not _caller_is(as_pane):
            return False, f"--as {as_pane!r} is not the pane running this check"
        if _norm(rec["action"]) != _norm(action):
            return False, f"this approval covers {rec['action']!r}, not that action"
        rec["used"] = datetime.datetime.fromtimestamp(now).isoformat(timespec="seconds")
        _save(rows)
    return True, f"VALID: {rec['action']} (Ryan said: {rec['words']!r}; minted by {rec['written_by']})"


def active(now=None):
    now = now if now is not None else time.time()
    return [r for r in _load() if not r.get("used") and r.get("expires", 0) > now]


def _opt(a, flag, default=None):
    if flag in a:
        i = a.index(flag)
        if i + 1 < len(a):
            return a[i + 1]
    return default


def cmd_approve(a):
    rec = mint(a[1] if len(a) > 1 and not a[1].startswith("--") else "", _opt(a, "--action", ""),
               _opt(a, "--words", ""))
    mins = int(TTL_SECONDS / 60)
    print(f"approval {rec['id']} for {rec['target']}: {rec['action']}")
    print(f"  single use, expires in {mins} min. Send it with: py orch.py prompt {rec['target']} "
          f"--approval {rec['id']} \"<text>\"")


def cmd_approval(a):
    sub = a[1] if len(a) > 1 else "ls"
    if sub == "check":
        ok, why = check(a[2] if len(a) > 2 else "", _opt(a, "--as", ""), _opt(a, "--action", ""))
        print(why)
        raise SystemExit(0 if ok else 1)
    if sub == "ls":
        rows = active()
        for r in rows:
            left = int((r["expires"] - time.time()) / 60)
            print(f"{r['id']}  {r['target']}  {left}m left  {r['action']}")
        if not rows:
            print("no active approvals")
        return
    raise SystemExit("usage: orch.py approval check <id> --as <pane> --action \"...\" | ls")


def approval_suffix(aid):
    """Text appended to a prompt by `orch.py prompt --approval <id>`. Refuses an id that is not a
    live record, so a typo is caught here and not by the pane."""
    rec = next((r for r in _load() if r.get("id") == aid), None)
    if not rec or rec.get("used") or time.time() >= rec.get("expires", 0):
        raise SystemExit(f"--approval {aid}: not a live approval (unknown, used, or expired). "
                         f"Nothing was sent.")
    orch = r"C:\Users\YOURNAME\.claude\skills\orchestrate\scripts\orch.py"
    return (f"\n[approval {aid}: covers ONLY \"{rec['action']}\". Run `py \"{orch}\" approval "
            f"check {aid} --as {rec['target']} --action \"{rec['action']}\"` and act only on "
            f"VALID.]")
