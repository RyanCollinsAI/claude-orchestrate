"""afk.py - one switch for "Ryan is away" (afk) and one for "Ryan is here, batch the noise" (quiet).

    orch.py afk on --words "<his words>" [--until 07:00] [--auto-minutes 20] [--auto-all] [--wedge-minutes 30] [--loud]
    orch.py afk off            print the return brief, end the away window
    orch.py afk status         one screen: what is on, since when, how much is parked
    orch.py afk tick           one pass: route and auto-pick board questions (the watchdog calls it)
    orch.py quiet on | off     same digest, but questions still reach the seat

State is board/afk.json (schema: board/schema.md, "board/afk.json"). The G7 wedge alarm reads the
same file, so the field names `afk`, `since`, `loud`, `wedge_minutes`, `ack_ts` are shared.

While AFK, `tick` does two things to open board questions:
  - A question its asker marked `--auto` (free and reversible) gets its clock cut to N minutes
    (`auto_minutes`, default 20). With `--auto-all` (off by default, asked for every window) any
    question with a recommended `pick` gets the same clock. Either way, never one on the
    Ryan-only list. When the clock runs out the pick is applied, `answered_by` is "auto", and the
    asking pane is told.
  - Anything on the Ryan-only list (money, deletes, sends, publishing, security, plans), a question
    with no pick, or one needing typed input, is never auto-picked. It waits, shows in the return
    brief, and the G7 wedge alarm pings the phone if it stays open.
Watchdog lines go to board/afk-digest.md instead of the seat (`digest_flag`), AFK or quiet.
The words Ryan gives are stored exactly and shown back at the return. Nothing here reads them.

Idea and return-brief shape from Kun's First Mate (.agents/skills/afk, bin/fm-afk-contract.sh,
bin/fm-afk-return.sh; MIT, Copyright (c) 2026 Kun Chen). No code is copied; the code is ours.
"""
import datetime
import json
import os
import re
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import orchlib as L

AFK_PATH = os.path.join(L.BOARD_DIR, "afk.json")
DIGEST_PATH = os.path.join(L.BOARD_DIR, "afk-digest.md")
ARCHIVE_DIR = os.path.join(L.BOARD_DIR, "afk-archive")
HOLDS_PATH = os.path.join(L.BOARD_DIR, "holds.json")

AUTO_MINUTES = 20
WEDGE_MINUTES = 30
HOLD_OFF = "auto-pick is off for this window"
HOLD_NOT_FREE = "the asker did not mark it free (--auto); `afk on --auto-all` allows these"
HOLD_BY_SETTING = (HOLD_OFF, HOLD_NOT_FREE)     # holds that a changed `afk on` setting can lift
DIGEST_HEAD ="# AFK digest\n\nOne line per parked note. Newest last. `orch.py afk off` reads it back.\n\n"

# The Ryan-only list. A hit means "never auto-pick": money, deletes, anything sent to a person,
# anything published, security, and anything that changes Ryan's plans. It only ever blocks; it can
# never grant. Matching is on the whole question (title, context, option text), so a harmless word
# can park a question. That is the safe direction: a parked question costs a wait, a wrong
# auto-pick can cost money.
RYAN_ONLY = (
    ("money", re.compile(
        r"\$\s?\d|\b(pay\w*|paid|purchas\w*|buy\w*|bought|invoice\w*|charg\w+|refund\w*|billing|"
        r"subscri\w+|upgrad\w+|renew\w*|credit\w*|card|wire\w*|venmo|paypal|tuition|donat\w*|"
        r"checkout|quote|spend\w*|spent|budget|dollars?|cents?|usd|fees?|orders?|ordering|"
        r"price[sd]?|pricing|tier|plan upgrade|cost[s]? (me|you|us|\d))\b", re.I)),
    ("delete", re.compile(
        r"\b(delet\w+|remov\w+|rm|rmdir|drop\w*|wipe\w*|purg\w+|destroy\w*|eras\w+|uninstall\w*|"
        r"truncat\w+|nuke\w*|force[- ]push\w*|reset --hard|kill\w*|clean ?up|clear\w*|overwrit\w+|"
        r"revert\w*|discard\w*|abandon\w*|close[sd]?|rotat\w+|revoke\w*|transfer\w*|prune\w*|"
        r"reap\w*|rollback|roll back|undo|git clean|del|rd|format\w*|reboot\w*|restart\w*|shut ?down|"
        r"unlink\w*|trash\w*|recycl\w+)\b", re.I)),
    ("send", re.compile(
        r"\b(send\w*|sent|e-?mail\w*|mail\w*|dm|tweet\w*|publish\w*|announce\w*|slack|telegram|"
        r"whatsapp|submit\w*|upload\w*|invit\w+|repl(y|ies)|text him|text her|post\w*|messag\w+|"
        r"notif\w+|share[sd]?|sharing|public|ship\w*|releas\w+|merg\w+|push\w*|tell\w*|ping\w*|"
        r"comment\w*|apply|applying|regist\w+|sign\w*|enrol\w*|rsvp|forward\w*|broadcast\w*)\b", re.I)),
    ("youtube-or-plans", re.compile(
        r"\b(youtube|channela|channelb|monetiz\w+|canvas|grades?|professor|deadline|"
        r"reschedul\w+|cancel\w*|calendar|flight|book(ing)?|reserv\w+|visa|invest\w*|"
        r"portfolio|stocks?|trade|deploy\w*|production|prod|live|launch\w*|appointment|"
        r"internship|class|semester|schedule[sd]?)\b", re.I)),
    ("security", re.compile(
        r"\b(passwords?|credentials?|secrets?|tokens?|keys?|api[- ]key\w*|2fa|oauth|ssh|login|"
        r"permissions?|chmod|sudo|admin|\.env)\b", re.I)),
)


# ---------------------------------------------------------------- small helpers

def now_dt():
    return datetime.datetime.now().astimezone().replace(microsecond=0)


def _iso(dt):
    return dt.isoformat()


def _epoch(v):
    """Epoch number, or ISO time (Z, offset, or local) -> epoch seconds, else None."""
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return float(v)
    if isinstance(v, str) and v.strip():
        try:
            d = datetime.datetime.fromisoformat(v.strip().replace("Z", "+00:00"))
            return d.timestamp()
        except ValueError:
            return None
    return None


def _opt(a, flag, default=None):
    if flag in a:
        i = a.index(flag)
        if i + 1 < len(a):
            return a[i + 1]
    return default


def _replace(src, dst, tries=8):
    """os.replace with a short retry: on Windows a file another process has open (the watchdog
    reading afk.json) cannot be replaced for a moment. Same shape as board._replace."""
    for i in range(tries):
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            if i == tries - 1:
                raise
            time.sleep(0.05 * (i + 1))


def _atomic_write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + f".{os.getpid()}.tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(text)
    _replace(tmp, path)


def _clock(dt):
    return dt.strftime("%Y-%m-%d %H:%M")


def _span(secs):
    m = max(0, int(secs // 60))
    return f"{m // 60}h{m % 60:02d}m" if m >= 60 else f"{m}m"


def parse_until(text, now=None):
    """"07:00", "7am", "7:30pm", "6h", "+90m" or an ISO time -> aware datetime. A clock time with
    no date means its next occurrence."""
    now = now or now_dt()
    s = (text or "").strip().lower()
    if not s:
        raise SystemExit("--until needs a time, like 07:00, 7am, 6h or 2026-10-09T07:00")
    m = re.fullmatch(r"\+?(\d+(?:\.\d+)?)\s*(h|hr|hrs|hours?|m|min|mins|minutes?)", s)
    if m:
        n = float(m.group(1))
        return now + datetime.timedelta(minutes=n * (60 if m.group(2).startswith("h") else 1))
    m = re.fullmatch(r"(\d{1,2})(?::(\d{2}))?\s*(am|pm)?", s)
    if m:
        hh, mm, ap = int(m.group(1)), int(m.group(2) or 0), m.group(3)
        if ap == "pm" and hh < 12:
            hh += 12
        if ap == "am" and hh == 12:
            hh = 0
        if hh > 23 or mm > 59:
            raise SystemExit(f"--until {text!r} is not a clock time")
        naive = datetime.datetime.combine(now.date(), datetime.time(hh, mm))
        t = naive.astimezone()                       # local zone rules, so a DST night lands right
        return t if t > now else (naive + datetime.timedelta(days=1)).astimezone()
    try:
        d = datetime.datetime.fromisoformat(text.strip().replace("Z", "+00:00"))
    except ValueError:
        raise SystemExit(f"--until {text!r} is not a time. Use 07:00, 7am, 6h or an ISO time.")
    return d if d.tzinfo else d.astimezone()


# ---------------------------------------------------------------- the contract file

def load(path=None):
    """board/afk.json -> dict; {} when absent, empty or unreadable (which means not AFK)."""
    try:
        with open(path or AFK_PATH, encoding="utf-8") as fh:
            d = json.load(fh)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def save(d, path=None):
    _atomic_write(path or AFK_PATH, json.dumps(d, indent=1, ensure_ascii=False) + "\n")


def is_afk(d=None):
    return bool((load() if d is None else d).get("afk"))


def is_quiet(d=None):
    return bool((load() if d is None else d).get("quiet"))


def _minutes(d, key, default):
    try:
        v = float(d.get(key, default))
    except (TypeError, ValueError):
        return float(default)
    return v if v >= 0 else float(default)


# ---------------------------------------------------------------- the Ryan-only list

def ryan_only_reason(q, afk=None):
    """Why this question must wait for Ryan, or "" when it may be auto-picked."""
    bits = [q.get("title", ""), q.get("context_md", ""), q.get("pick_why", "")]
    for o in q.get("options") or []:
        bits += [str(o.get("label", "")), str(o.get("value", ""))]
    text = "\n".join(str(b) for b in bits).replace("_", " ")     # `_` is a word character: do_delete slipped past \b
    for label, rx in RYAN_ONLY:
        if rx.search(text):
            return f"Ryan-only ({label})"
    for pat in (afk or {}).get("ryan_only_extra") or []:
        try:
            if re.search(pat, text, re.I):
                return "Ryan-only (your extra rule)"
        except re.error:
            continue
    return ""


def hold_reason(q, afk):
    """Why this question may not be auto-picked while AFK, or "" when it may."""
    if _minutes(afk, "auto_minutes", AUTO_MINUTES) <= 0:
        return HOLD_OFF
    if q.get("inputs"):
        return "needs typed input"
    if q.get("multi"):
        return "multi-select"
    if not q.get("pick") or not (q.get("options") or []):
        return "no recommended option"
    if str(q["pick"]) not in {str(o.get("value")) for o in q["options"]}:
        return "the pick is not one of the options"
    if not _asker_auto(q) and not afk.get("auto_all"):
        return HOLD_NOT_FREE
    return ryan_only_reason(q, afk)


# ---------------------------------------------------------------- the digest

def digest_add(kind, text, now=None, path=None):
    """Append one line to board/afk-digest.md. Never raises: a digest problem must not stop a pass."""
    try:
        path = path or DIGEST_PATH
        now = now or now_dt()
        line = "- " + _iso(now) + " | " + kind + " | " + " ".join(str(text).split())[:300] + "\n"
        os.makedirs(os.path.dirname(path), exist_ok=True)
        new = not os.path.exists(path) or os.path.getsize(path) == 0
        with open(path, "a", encoding="utf-8") as fh:
            fh.write((DIGEST_HEAD if new else "") + line)
        return True
    except Exception as e:
        print(f"afk digest: {type(e).__name__}: {e}", file=sys.stderr)
        return False


def digest_lines(since=None, path=None):
    """[(epoch, kind, text)] oldest first, only lines at or after `since` (epoch)."""
    out = []
    try:
        with open(path or DIGEST_PATH, encoding="utf-8") as fh:
            for raw in fh:
                m = re.match(r"- (\S+) \| ([A-Z-]+) \| (.*)$", raw.rstrip("\n"))
                ts = _epoch(m.group(1)) if m else None
                if ts is not None and (since is None or ts >= since):
                    out.append((ts, m.group(2), m.group(3)))
    except OSError:
        pass
    return out


def digest_flag(name, cond, detail, now=None):
    """The watchdog's hook. True = the line was parked in the digest, so do not prompt the seat.
    False = not AFK and not quiet, so send it as before. Reads afk.json fresh on every call."""
    d = load()
    if not (d.get("afk") or d.get("quiet")):
        return False
    digest_add("WATCHDOG", f"{name}: {cond} - {detail}", now)
    return True


# ---------------------------------------------------------------- the board

def _board_load():
    import board as B
    return B.load()


def _board_save(s):
    import board as B
    B.save(s)


def _board_slack(line, group):
    try:
        import board as B
        B.slack_done(line, group)
    except Exception:
        pass


def _send(name, text):
    env = dict(os.environ, ORCH_SENDER="afk")
    r = subprocess.run([sys.executable, os.path.join(os.path.dirname(os.path.abspath(__file__)), "orch.py"),
                        "prompt", name, text], capture_output=True, text=True, timeout=90, env=env)
    return r.returncode == 0


def _asker_auto(q):
    """True when the asking pane itself marked the question free and reversible (`--auto`)."""
    return bool(q.get("auto")) and q.get("afk_orig_auto") is not False


def _hold(q, why, now, actions):
    """Park a question for Ryan: no auto clock, stamped with the reason. The asker's own --auto
    and clock are remembered so `afk off` can give them back."""
    if "afk_orig_auto" not in q and q.get("auto"):
        q["afk_orig_auto"] = True
        q.setdefault("afk_orig_after", q.get("auto_after", ""))
    q["auto"], q["auto_after"] = False, ""
    q["afk_hold"] = why
    q.pop("afk_auto", None)
    digest_add("WAITS", f"{q.get('id', '').upper()} {q.get('title', '')} waits for you ({why})", now)
    actions.append(f"hold {q.get('id')}: {why}")


def _release(q):
    """Take the AFK stamps and clock off one question, giving the asker's own values back."""
    if q.get("afk_orig_auto") is True:
        q["auto"], q["auto_after"] = True, q.get("afk_orig_after", "")
    elif q.get("afk_orig_auto") is False:
        q["auto"], q["auto_after"] = False, ""
    elif "afk_orig_after" in q:
        q["auto_after"] = q["afk_orig_after"]
    for k in ("afk_seen", "afk_auto", "afk_hold", "afk_orig_auto", "afk_orig_after"):
        q.pop(k, None)


def tick(now=None, load_board=None, save_board=None, send=None, afk=None):
    """One pass while AFK -> list of what it did (strings). Quiet or off: does nothing.

    Pure of the clock and the board when the callers are injected. Never raises."""
    now = now or now_dt()
    afk = load() if afk is None else afk
    if not afk.get("afk"):
        return []
    try:
        s = (load_board or _board_load)()
    except (Exception, SystemExit) as e:
        print(f"afk tick: board unreadable ({type(e).__name__})", file=sys.stderr)
        return []
    actions, changed, fired = [], False, []
    window = str(afk.get("since"))
    minutes = _minutes(afk, "auto_minutes", AUTO_MINUTES)
    since = _epoch(afk.get("since")) or 0
    for q in s.get("questions", []):
        if q.get("answered"):
            continue
        if q.get("afk_seen") != window:
            if q.get("afk_seen") is not None:
                _release(q)                  # a stamp from an older window that never got released
            q["afk_seen"] = window
            changed = True
            why = hold_reason(q, afk)
            if why:
                _hold(q, why, now, actions)
            elif q.get("auto"):
                # The asker already marked it free and reversible. Keep their rule, shorten the clock.
                due = max(_epoch(q.get("created")) or now.timestamp(), since) + minutes * 60
                old = _epoch(q.get("auto_after"))
                if old is None or due < old:
                    q["afk_orig_after"] = q.get("auto_after", "")
                    q["auto_after"] = _iso(datetime.datetime.fromtimestamp(due).astimezone().replace(microsecond=0))
                q["afk_auto"] = True
                actions.append(f"auto {q.get('id')}: due {q['auto_after']}")
            else:
                due = max(_epoch(q.get("created")) or now.timestamp(), since) + minutes * 60
                q["auto"], q["afk_orig_auto"] = True, False
                q["auto_after"] = _iso(datetime.datetime.fromtimestamp(due).astimezone().replace(microsecond=0))
                q["afk_auto"] = True
                actions.append(f"auto {q.get('id')}: due {q['auto_after']}")
        elif q.get("afk_auto"):
            # The rules may have tightened since it was clocked (--auto-minutes 0, a new extra rule).
            why = hold_reason(q, afk)
            if why:
                _hold(q, why, now, actions)
                changed = True
        if q.get("afk_auto") and not q.get("answered"):
            due = _epoch(q.get("auto_after"))
            if due is not None and due <= now.timestamp():
                line = f"auto-picked {q['id']}: {q['pick']} (AFK, recommended option)"
                q["answered"] = _iso(now)
                q["answer"] = q["pick"]
                q["answered_by"] = "auto"
                q["answer_reason"] = f"AFK auto-pick after {minutes:g} min"
                s.setdefault("done", []).insert(0, {"ts": _iso(now), "text": line, "group": q.get("group", "")})
                fired.append((q, line))
                changed = True
    if changed:
        try:
            (save_board or _board_save)(s)
        except (Exception, SystemExit) as e:
            print(f"afk tick: board not saved ({type(e).__name__}); nothing was changed", file=sys.stderr)
            return []
    send = send or _send
    for q, line in fired:
        _board_slack(line, q.get("group", ""))
        label = next((o.get("label") for o in q.get("options", []) if str(o.get("value")) == str(q["pick"])), "")
        digest_add("AUTO-PICK", f"{q['id'].upper()} {q.get('title', '')}: picked {q['pick']}"
                   + (f" ({label})" if label else "") + f", asked by {q.get('from') or 'the orchestrator'}", now)
        actions.append(line)
        if q.get("from"):
            try:
                told = send(q["from"], f'[answer to {q["id"]}] Ryan is AFK. The board auto-picked "{q["pick"]}"'
                            f'{" (" + label + ")" if label else ""} for "{q.get("title", "")}". '
                            "It was the recommended option. If it was wrong, tell the orchestrator.")
                why = "" if told else "the prompt failed (pane gone or bad name)"
            except Exception as e:
                why = type(e).__name__
            if why:
                print(f"afk tick: could not tell {q['from']} ({why})", file=sys.stderr)
                digest_add("WAITS", f"{q['id'].upper()} was auto-picked but {q['from']} was not told ({why}); "
                           "tell it yourself", now)
    return actions


def _reroute_held():
    """`afk on --auto-all` or `--auto-minutes` changed while already on: a question held only
    because of the old setting is released, so the next `tick` looks at it again. Returns how many."""
    try:
        s = _board_load()
        n = 0
        for q in s.get("questions", []):
            if not q.get("answered") and str(q.get("afk_hold", "")).startswith(HOLD_BY_SETTING):
                _release(q)
                n += 1
        if n:
            _board_save(s)
        return n
    except (Exception, SystemExit) as e:
        print(f"afk on: could not re-look at held questions ({type(e).__name__})", file=sys.stderr)
        return 0


def _restore_unfired(s):
    """AFK is over: take the AFK clock off every question it did not fire. Returns how many."""
    n = 0
    for q in s.get("questions", []):
        if q.get("answered") or not q.get("afk_seen"):
            continue
        n += bool(q.get("afk_auto"))
        _release(q)
    return n


# ---------------------------------------------------------------- the return brief

def _ledger_summary(since, records):
    by = {"done": [], "pr_ready": [], "merged": [], "cleaned_up": [], "attention": []}
    for r in records:
        if (r.get("ts") or 0) < since:
            continue
        ev, task = r.get("event", ""), r.get("task", "?")
        if ev == "task.status":
            st = r.get("state")
            bucket = "done" if st == "done" else "attention" if st in ("question", "blocked", "error", "offer") else None
        else:
            bucket = {"task.pr_ready": "pr_ready", "task.merged": "merged", "task.cleaned_up": "cleaned_up"}.get(ev)
        if bucket and task not in by[bucket]:
            by[bucket].append(task)
    return by


def build_brief(afk, now, questions, digest, ledger, asks=None, holds=None, title="Return brief",
                digest_only=False):
    """The text `afk off` prints. Pure: every input is passed in.

    afk: the contract dict. questions: board questions. digest: [(epoch, kind, text)].
    ledger: fleet-ledger records. asks: open `orch.py ask` rows. holds: {pane: {reason}}.
    digest_only: the quiet digest. Quiet reads no board, no ledger and decides nothing, so it
    shows only the parked watchdog notes; "Waiting (0)" or "0 done" there would be false."""
    since = _epoch(afk.get("since")) or now.timestamp()
    out = [f"=== {title}: {_clock(datetime.datetime.fromtimestamp(since))} -> {_clock(now)} "
           f"({_span(now.timestamp() - since)}) ==="]
    if digest_only:
        return "\n".join(out + _notes_lines(digest))
    words =(afk.get("words") or "").strip("\n")
    if words:
        out.append("Your words (kept exactly):")
        out += ["  " + ln for ln in words.splitlines()]
    else:
        out.append("Your words: none recorded.")
    for sup in afk.get("superseded") or []:
        out.append(f"  (replaced at {sup.get('ts', '?')}: {' '.join(str(sup.get('words', '')).split())})")
    back = _epoch(afk.get("return_at"))
    if back:
        late = now.timestamp() - back
        out.append("Expected back " + _clock(datetime.datetime.fromtimestamp(back))
                   + (f"; you are {_span(late)} past that." if late > 60 else
                      f"; {_span(-late)} early." if late < -60 else "; right on time."))

    # From the digest, not the board: the hourly prune deletes answered questions, the digest stays.
    picks = [d for d in digest if d[1] == "AUTO-PICK"]
    out.append(f"Decided for you ({len(picks)}):")
    for ts, _kind, text in picks:
        out.append(f"  - {_clock(datetime.datetime.fromtimestamp(ts))} {text}")
    if not picks:
        out.append("  (nothing)")

    waits = [q for q in questions if not q.get("answered")]
    n_waits = len(waits) + len(asks or []) + len(holds or {})
    out.append(f"Waiting on you ({n_waits}):")
    for q in waits:
        why = q.get("afk_hold") or ("clock running; auto-picks at " + str(q.get("auto_after")) if q.get("afk_auto") else "")
        age = _span(now.timestamp() - (_epoch(q.get("created")) or now.timestamp()))
        out.append(f"  - {q['id'].upper()} {q.get('title', '')} [{why or 'open'}] open {age}")
    for r in asks or []:
        out.append(f"  - ask {r.get('id')} from {r.get('from')}: {r.get('question')} [{' | '.join(r.get('options', []))}]")
    for name, h in (holds or {}).items():
        out.append(f"  - {name} is held: {(h or {}).get('reason') or 'waiting on Ryan'}")
    if not n_waits:
        out.append("  (nothing)")

    by = _ledger_summary(since, ledger)
    out.append("Fleet while you were away: "
               + ", ".join(f"{len(by[k])} {k.replace('_', ' ')}" for k in ("done", "pr_ready", "merged", "cleaned_up", "attention")))
    for k, label in (("done", "finished"), ("pr_ready", "PR ready"), ("merged", "merged"), ("attention", "need a look")):
        if by[k]:
            out.append(f"  {label}: " + ", ".join(by[k][:12]) + (f" +{len(by[k]) - 12} more" if len(by[k]) > 12 else ""))

    return "\n".join(out + _notes_lines(digest))


def _notes_lines(digest):
    notes = [d for d in digest if d[1] == "WATCHDOG"]
    out = [f"Watchdog notes parked ({len(notes)}):"]
    for ts, _kind, text in notes[-8:]:
        out.append(f"  - {_clock(datetime.datetime.fromtimestamp(ts))} {text}")
    if len(notes) > 8:
        out.append(f"  (+{len(notes) - 8} older ones in board/afk-digest.md)")
    if not notes:
        out.append("  (nothing)")
    return out


def _gather(afk, now):
    """Read the live sources for the brief. Each one fails to empty, never loudly."""
    try:
        s = _board_load()
        questions = s.get("questions", [])
    except (Exception, SystemExit):
        s, questions = None, []
    try:
        import asks as K
        rows = K.open_rows(now.timestamp())
    except Exception:
        rows = []
    try:
        with open(HOLDS_PATH, encoding="utf-8") as fh:
            holds = json.load(fh)
        holds = holds if isinstance(holds, dict) else {}
    except (OSError, ValueError):
        holds = {}
    since = _epoch(afk.get("since")) or now.timestamp()
    return s, questions, digest_lines(since), L.ledger.read(), rows, holds


def _archive(afk, brief, now, digest_too):
    try:
        os.makedirs(ARCHIVE_DIR, exist_ok=True)
        stamp = int(_epoch(afk.get("since")) or now.timestamp())
        _atomic_write(os.path.join(ARCHIVE_DIR, f"{stamp}-brief.md"), brief + "\n")
        _atomic_write(os.path.join(ARCHIVE_DIR, f"{stamp}-afk.json"), json.dumps(afk, indent=1) + "\n")
        if digest_too and os.path.exists(DIGEST_PATH):
            _replace(DIGEST_PATH, os.path.join(ARCHIVE_DIR, f"{stamp}-digest.md"))
    except OSError as e:
        print(f"afk: could not archive ({e})", file=sys.stderr)


# ---------------------------------------------------------------- commands

def cmd_on(a, now=None):
    now = now or now_dt()
    d = load()
    words = _opt(a, "--words")
    until = _opt(a, "--until")
    for flag in ("--auto-minutes", "--wedge-minutes"):
        v = _opt(a, flag)
        if v is not None:
            try:
                if float(v) < 0:
                    raise ValueError
            except ValueError:
                raise SystemExit(f"{flag} needs a number of minutes, 0 or more")
    was_on = bool(d.get("afk"))
    before = (d.get("auto_all"), d.get("auto_minutes"))
    if was_on:
        if words is None and until is None and not any(f in a for f in ("--auto-minutes", "--wedge-minutes", "--loud", "--auto-all")):
            print(f"afk is already on since {d.get('since')}. Nothing changed. Send --words to replace your words.")
            return d
        if words is not None and words != d.get("words"):
            d.setdefault("superseded", []).append({"ts": _iso(now), "words": d.get("words", "")})
    else:
        # `loud` and `wedge_minutes` are per window, like `auto_all`: one --loud must not arm the
        # phone through quiet hours on every later night.
        d = {k: v for k, v in d.items() if k in ("quiet", "quiet_since", "ack_ts", "ryan_only_extra")}
        d["since"] = _iso(now)
        d["superseded"] = []
        d["words"] = ""
    d["afk"] = True
    d.pop("ended", None)
    if words is not None:
        d["words"] = words
    if until is not None:
        d["return_at"] = _iso(parse_until(until, now))
    d.setdefault("return_at", "")
    if "--auto-minutes" in a:
        d["auto_minutes"] = float(_opt(a, "--auto-minutes"))
    d.setdefault("auto_minutes", AUTO_MINUTES)
    if "--wedge-minutes" in a:
        d["wedge_minutes"] = float(_opt(a, "--wedge-minutes"))
    d.setdefault("wedge_minutes", WEDGE_MINUTES)
    if "--loud" in a:
        d["loud"] = True
    d.setdefault("loud", False)
    if "--auto-all" in a:
        d["auto_all"] = True
    d.setdefault("auto_all", False)
    save(d)
    if was_on and before != (d["auto_all"], d["auto_minutes"]):
        _reroute_held()
    n = d["auto_minutes"]
    print(f"afk is ON since {_clock(datetime.datetime.fromisoformat(d['since']))}."
          + (f" You said you are back by {_clock(datetime.datetime.fromisoformat(d['return_at']))}." if d["return_at"] else ""))
    print("Your words, kept exactly: " + (d["words"] if d["words"] else "(none)"))
    scope = "any question with a recommended option" if d["auto_all"] else "a question its asker marked --auto"
    print(f"- {scope[0].upper() + scope[1:]} is auto-picked after {n:g} min." if n > 0
          else "- Auto-pick is OFF. Every question waits for you.")
    if n > 0 and not d["auto_all"]:
        print("  Other questions wait. Add --auto-all to let any question with a pick auto-pick.")
    print("- Money, delete, send, publish, security, plans and no-pick questions never auto-pick. They wait.")
    print(f"- The phone alarm pings (quiet channel{', loud' if d['loud'] else ''}) after {d['wedge_minutes']:g} min of waiting.")
    print("- Watchdog notes go to board/afk-digest.md, not the seat.")
    print("Run `orch.py afk off` for the return brief.")
    return d


def cmd_off(now=None):
    now = now or now_dt()
    d = load()
    if not d.get("afk"):
        print("afk is not on. Nothing to end.")
        return None
    s, questions, digest, ledger, rows, holds = _gather(d, now)
    brief = build_brief(d, now, questions, digest, ledger, rows, holds)
    if s is not None:
        try:
            _restore_unfired(s)
            _board_save(s)
        except (Exception, SystemExit) as e:
            print(f"afk off: could not release the auto clocks ({type(e).__name__})", file=sys.stderr)
    d["afk"] = False
    d["ended"] = _iso(now)
    d["ack_ts"] = _iso(now)
    save(d)
    _archive(d, brief, now, digest_too=not d.get("quiet"))
    print(brief)
    return brief


def cmd_quiet(on, now=None):
    now = now or now_dt()
    d = load()
    if on:
        if d.get("quiet"):
            print("quiet is already on. Nothing changed.")
            return d
        d["quiet"], d["quiet_since"] = True, _iso(now)
        d.setdefault("afk", False)
        save(d)
        print("quiet is ON. Watchdog notes go to board/afk-digest.md. Questions still reach the seat.")
        return d
    if not d.get("quiet"):
        print("quiet is not on. Nothing to end.")
        return None
    since = _epoch(d.get("quiet_since")) or now.timestamp()
    lines = digest_lines(since)
    brief = build_brief({"since": d.get("quiet_since"), "words": ""}, now, [], lines, [], title="Quiet digest", digest_only=True)
    d["quiet"] = False
    save(d)
    if not d.get("afk"):
        _archive({"since": d.get("quiet_since")}, brief, now, digest_too=True)
        print(brief)
    else:
        print("quiet is OFF. afk is still on, so the digest keeps filling for the return brief.")
    return brief


def cmd_status(now=None):
    now = now or now_dt()
    d = load()
    if not (d.get("afk") or d.get("quiet")):
        print("afk: off. quiet: off.")
        return
    if d.get("afk"):
        print(f"afk: ON since {d.get('since')}, auto-pick {d.get('auto_minutes', AUTO_MINUTES)} min, "
              f"wedge {d.get('wedge_minutes', WEDGE_MINUTES)} min" + (f", back {d['return_at']}" if d.get("return_at") else ""))
        print("words: " + (d.get("words") or "(none)"))
    else:
        print("afk: off.")
    print("quiet: " + (f"ON since {d.get('quiet_since')}" if d.get("quiet") else "off"))
    since = min(filter(None, (_epoch(d.get("since")) if d.get("afk") else None,
                              _epoch(d.get("quiet_since")) if d.get("quiet") else None)), default=0)
    print(f"digest: {len(digest_lines(since))} line(s) in board/afk-digest.md")


def main(a):
    cmd = a[0]
    if cmd == "quiet":
        if len(a) < 2 or a[1] not in ("on", "off"):
            raise SystemExit("usage: orch.py quiet on|off")
        cmd_quiet(a[1] == "on")
        return
    sub = a[1] if len(a) > 1 else ""
    if sub == "on":
        cmd_on(a[2:])
    elif sub == "off":
        cmd_off()
    elif sub == "status":
        cmd_status()
    elif sub == "tick":
        acts = tick()
        print("\n".join(acts) if acts else "afk tick: nothing to do")
    else:
        raise SystemExit('usage: orch.py afk on --words "<your words>" [--until 07:00] [--auto-minutes N] '
                         "[--wedge-minutes N] [--loud] | off | status | tick")


if __name__ == "__main__":
    main(["afk"] + sys.argv[1:])
