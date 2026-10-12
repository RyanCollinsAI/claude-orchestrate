"""Declared wait: a pane says "I am waiting on something outside me until T" and the zero-token
watchers leave it alone until then.

A pane writes one line in its own reply:

    paused: render of ep 14 until 2026-10-09T03:40Z

`watch_sessions.py` (STALE ping, idle-nudge) and `orch_watchdog.py` (STALLED) read it with
`find_in_entries`. A holds.json row with an `until` is the same thing. `orch.py hold NAME reason
--until T` writes that row, but only after the G7 patch for orch.py lands (orch.py was F9's this
wave); `parse_until_arg` is the parser that patch calls.
The idea and the rules below come from Kun's First Mate (fm-classify-lib.sh, `paused:` verb and
`status_paused_until`, MIT); this is a Python rewrite, no code copied.

Rules, each one tested:
  - only a `paused:` line that names `until <UTC ISO>` counts. A line with no time, or a bad time,
    is not a declaration, so a typo can never mute a pane.
  - an EXPIRED wait stops counting. A pane cannot mute itself forever.
  - a wait older than RESURFACE_SECS (4 h) is `stale`: the watcher says so once per 4 h instead of
    staying silent, so a forgotten wait cannot rot unseen.
  - no declaration: the pane is untouched, exactly as before.

Pure helpers only: no herdr, no disk besides what the caller passes in.
"""
import datetime
import json
import re

RESURFACE_SECS = 4 * 3600

_LINE = re.compile(r"^[\s>*_`\-]*paused\s*:\s*(?P<rest>\S.*)$", re.I)
_UNTIL = re.compile(r"\buntil\s+(?P<t>\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?Z)", re.I)
_REL = re.compile(r"^\+?(\d+)\s*(m|min|h|hr|hours?)$", re.I)


def parse_utc(token):
    """'2026-10-09T03:40Z' (or with seconds) -> epoch seconds, or None when it is not a real time."""
    try:
        return datetime.datetime.fromisoformat(
            token.strip().upper().replace("Z", "+00:00")).timestamp()
    except (ValueError, AttributeError):
        return None


def iso_utc(epoch):
    return datetime.datetime.fromtimestamp(epoch, datetime.timezone.utc).strftime("%Y-%m-%dT%H:%MZ")


def parse_until_arg(arg, now):
    """What `orch.py hold --until X` accepts: a UTC ISO time ('2026-10-09T03:40Z') or a length of
    time from now ('45m', '2h', '+30min'). -> ISO UTC string, or None when X is neither."""
    arg = (arg or "").strip()
    m = _REL.match(arg)
    if m:
        secs = int(m.group(1)) * (60 if m.group(2).lower().startswith("m") else 3600)
        return iso_utc(now + secs)
    t = parse_utc(arg) if arg.upper().endswith("Z") else None
    return iso_utc(t) if t is not None else None


def parse_line(line):
    """One line of text -> {reason, until (epoch)} when it is a valid declared wait, else None."""
    m = _LINE.match(line or "")
    if not m:
        return None
    rest = m.group("rest")
    u = _UNTIL.search(rest)
    if not u:
        return None
    until = parse_utc(u.group("t"))
    if until is None:
        return None
    reason = rest[:u.start()].strip(" -:,;") or "waiting"
    return {"kind": "paused", "reason": reason[:200], "until": until}


def find_in_text(text, written=0):
    """Newest-first is the caller's job; this takes one message and returns its LAST declaration."""
    found = None
    for line in (text or "").splitlines():
        d = parse_line(line)
        if d:
            found = d
    if found:
        found["written"] = written
    return found


def _assistant_text(entry):
    c = (entry.get("message") or {}).get("content")
    if isinstance(c, str):
        return c
    if isinstance(c, list):
        return "\n".join(b.get("text", "") for b in c
                         if isinstance(b, dict) and b.get("type") == "text")
    return ""


def _ts(entry):
    try:
        return datetime.datetime.fromisoformat(
            (entry.get("timestamp") or "").replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0


def find_in_entries(entries):
    """The declared wait a claude pane is under right now, from the tail of its transcript (raw
    jsonl lines, oldest first), or None. It is the declaration in the pane's NEWEST message that
    has any text: once the pane says something else, the wait is over. `written` is that message's
    time, which starts the 4 h resurface clock. Tool-call-only messages carry no text and are
    skipped, so a pane that declares a wait and then sits in a sleep loop keeps it."""
    for l in reversed(entries or []):
        if '"assistant"' not in l:
            continue
        try:
            e = json.loads(l)
        except ValueError:
            continue
        if e.get("type") != "assistant":
            continue
        text = _assistant_text(e)
        if not text.strip():
            continue
        return find_in_text(text, _ts(e))
    return None


def hold_decl(entry):
    """A holds.json row with an `until` is a declared wait too (kind 'hold': it blocks on Ryan).
    A row without one is the old forever-hold and is NOT handled here - callers keep their old
    `name in holds` check for it, so no existing hold changes meaning."""
    if not isinstance(entry, dict) or not entry.get("until"):
        return None
    u = entry["until"]
    until = float(u) if isinstance(u, (int, float)) else parse_utc(str(u))
    if until is None:
        return None
    return {"kind": "hold", "reason": str(entry.get("reason") or "waiting on Ryan")[:200],
            "until": until, "written": float(entry.get("ts") or 0)}


def state(decl, now):
    """'none' | 'active' | 'stale' | 'expired'.
    active: valid and under 4 h old - stay silent.
    stale: valid but declared over 4 h ago - say so (resurface), do not nag.
    expired: the time passed - it no longer counts, the normal checks run again."""
    if not decl:
        return "none"
    if decl["until"] <= now:
        return "expired"
    written = decl.get("written") or 0
    if written and now - written >= RESURFACE_SECS:
        return "stale"
    return "active"


def resurface_slot(decl, now):
    """Which 4 h slot a stale wait is in (1, 2, ...). The watcher says one line per new slot."""
    written = decl.get("written") or 0
    return int((now - written) // RESURFACE_SECS) if written else 0


def hold_expired(entry, now):
    """True only for a hold that names an `until` which has passed. Old holds never expire."""
    d = hold_decl(entry)
    return bool(d) and d["until"] <= now


def active_holds(holds, now):
    """holds.json minus the holds whose `until` has passed. Old holds (no `until`) all stay."""
    return {k: v for k, v in (holds or {}).items() if not hold_expired(v, now)}


def waiting(name, entries, holds, now):
    """(state, decl) for one pane. A hold with an unexpired `until` wins over the pane's own line
    because Ryan set it; otherwise the pane's newest `paused:` line decides."""
    h = hold_decl((holds or {}).get(name))
    if h and state(h, now) != "expired":
        return state(h, now), h
    d = find_in_entries(entries)
    return state(d, now), d


def describe(decl):
    who = "held by Ryan" if decl["kind"] == "hold" else "external wait"
    return f"{who}: {decl['reason']} until {iso_utc(decl['until'])}"
