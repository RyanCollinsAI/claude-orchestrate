"""Phone alerts: a few real pings to Ryan's phone, everything else in a digest.

    py phone_alerts.py status            # on/off, muted until, sent today, digest queue
    py phone_alerts.py off | on          # the off switch (board/phone-alerts.off)
    py phone_alerts.py mute [2h|30m]     # silence pings; default 2h
    py phone_alerts.py unmute
    py phone_alerts.py poll              # read "mute 2h" / "off" / "on" replies in the channel
    py phone_alerts.py test "text"       # send one real test alert (counts toward the cap)

Channel: Slack, posted by the ci bot to the board's `podium_slack_channel`, with an @mention of
Ryan so his phone buzzes. The bot token has no DM scope (conversations.open -> missing_scope), so a
channel mention is the phone-visible path. Telegram stays cut (see telegram-alert-trim-2026-07).

Rules (Ryan: "I don't want to get spammed"):
- PING now only for: (a) blocked on Ryan 10+ min, (b) a job he started finishing, (c) money, a
  deadline under 24 h, or an error that stops uploads.
- Everything else queues into ONE digest, at most one per 3 h.
- Quiet hours 23:00-08:00: nothing sends, except a deadline under 2 h. Queued items wait.
- The same alert never repeats (key kept 7 days). Hard cap 8 sends per day, pings and digests.
- Off switch file, `mute`, and replies `mute 2h` / `off` / `on` in the channel.
Fails open: a dead Slack costs a stderr line, never the watchdog pass.
"""
import argparse, datetime, hashlib, json, os, re, sys, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import orchlib as L

STATE_PATH = os.path.join(L.BOARD_DIR, "phone-alerts-state.json")
OFF_PATH = os.path.join(L.BOARD_DIR, "phone-alerts.off")
BLOCKED_SECS = 10 * 60
DEADLINE_PING = 24 * 3600
DEADLINE_QUIET = 2 * 3600
DIGEST_GAP = 3 * 3600
DAILY_CAP = 8
QUIET_START, QUIET_END = 23, 8
SEEN_KEEP = 7 * 24 * 3600
RYAN_ID = "U0000000000"
MONEY_RE = re.compile(r"\$\s?\d|\bpayment\b|\binvoice\b|\bcharge[ds]?\b|\brefund\b|\bbilling\b", re.I)
UPLOAD_ERR_RE = re.compile(r"upload", re.I)


def _cfg(key, default=""):
    return L.cfg(key, default)


# ---------------------------------------------------------------- pure rules (tested)

def is_quiet(now):
    h = datetime.datetime.fromtimestamp(now).hour
    return h >= QUIET_START or h < QUIET_END


def classify(ev):
    """ev: {kind, key, text, blocked_secs, mine, deadline_secs, error_stops_uploads, loud}.
    -> "ping" or "digest"."""
    text = ev.get("text", "")
    if ev.get("deadline_secs") is not None and ev["deadline_secs"] < DEADLINE_PING:
        return "ping"
    if ev.get("error_stops_uploads") or (ev.get("kind") == "ERROR" and UPLOAD_ERR_RE.search(text)):
        return "ping"
    if MONEY_RE.search(text):
        return "ping"
    if ev.get("blocked_secs", 0) >= BLOCKED_SECS:
        return "ping"
    if ev.get("kind") == "DONE" and ev.get("mine"):
        return "ping"
    return "digest"


def _fp(ev):
    raw = ev.get("key") or f"{ev.get('kind', '')}|{ev.get('text', '')}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def _day(now):
    return datetime.datetime.fromtimestamp(now).strftime("%Y-%m-%d")


def _blank():
    return {"seen": {}, "queue": [], "last_digest": 0, "day": "", "sent_today": 0, "mute_until": 0}


def decide(ev, st, now, off=False):
    """Pure. Mutates st. -> ("send", text) | ("queued", "") | ("drop", reason)."""
    if st.get("day") != _day(now):
        st["day"], st["sent_today"] = _day(now), 0
    st["seen"] = {k: t for k, t in st.get("seen", {}).items() if now - t < SEEN_KEEP}
    fp = _fp(ev)
    if fp in st["seen"]:
        return "drop", "repeat"
    if off:
        return "drop", "off"
    lane = classify(ev)
    muted = now < st.get("mute_until", 0)
    # `loud` is Ryan's own opt-in (afk.json "loud": true, read by the watchdog's wedge alarm): the
    # one event kind allowed to break quiet hours besides a deadline under 2 h.
    urgent_quiet = bool(ev.get("loud")) or (
        ev.get("deadline_secs") is not None and ev["deadline_secs"] < DEADLINE_QUIET)
    if lane == "ping" and not muted and (not is_quiet(now) or urgent_quiet):
        if st["sent_today"] >= DAILY_CAP:
            return "drop", "cap"
        st["seen"][fp] = now
        st["sent_today"] += 1
        return "send", ev.get("text", "")
    st["seen"][fp] = now
    st["queue"].append({"t": now, "text": ev.get("text", "")})
    return "queued", ""


def due_digest(st, now, off=False):
    """Pure. Mutates st. -> digest text, or "" when none is due."""
    if st.get("day") != _day(now):
        st["day"], st["sent_today"] = _day(now), 0
    if (off or not st.get("queue") or is_quiet(now) or now < st.get("mute_until", 0)
            or now - st.get("last_digest", 0) < DIGEST_GAP or st["sent_today"] >= DAILY_CAP):
        return ""
    lines = [f"- {q['text']}" for q in st["queue"][:15]]
    more = len(st["queue"]) - len(lines)
    text = f"Digest ({len(st['queue'])} items):\n" + "\n".join(lines) + (f"\n...and {more} more" if more > 0 else "")
    st["queue"], st["last_digest"] = [], now
    st["sent_today"] += 1
    return text


def parse_reply(text):
    """'mute 2h' -> ('mute', 7200); 'off'; 'on'; else None."""
    t = (text or "").strip().lower()
    m = re.match(r"^mute(?:\s+(\d+)\s*(m|min|h|hr|hours?)?)?$", t)
    if m:
        n = int(m.group(1) or 2)
        unit = (m.group(2) or "h")
        return "mute", n * (60 if unit.startswith("m") else 3600)
    if t in ("off", "on", "unmute"):
        return t, 0
    return None


# ---------------------------------------------------------------- disk + Slack

def load():
    try:
        with open(STATE_PATH, encoding="utf-8") as fh:
            d = json.load(fh)
        return {**_blank(), **d} if isinstance(d, dict) else _blank()
    except (OSError, ValueError):
        return _blank()


def save(st):
    os.makedirs(L.BOARD_DIR, exist_ok=True)
    with open(STATE_PATH + ".tmp", "w", encoding="utf-8") as fh:
        json.dump(st, fh, indent=1)
    os.replace(STATE_PATH + ".tmp", STATE_PATH)


def is_off():
    return os.path.exists(OFF_PATH)


def _slack():
    lib = _cfg("slack_lib", "")
    if lib and lib not in sys.path:
        sys.path.insert(0, lib)
    import slack
    return slack


_last = {}


def send_slack(text):
    """Post with an @mention so the phone buzzes. Returns ts or ''. Never raises."""
    ch = _cfg("podium_slack_channel", "")
    if not ch or os.environ.get("PODIUM_SLACK", "1") == "0":
        return ""
    try:
        cid, ts = _slack().post_where(ch, f"<@{RYAN_ID}> {text}", timeout=8)
        _last["cid"] = cid              # poll needs the id; the bot cannot list this channel by name
        return ts or ""
    except Exception as e:
        print("phone_alerts: " + str(e).replace("\n", " ")[:160], file=sys.stderr)
        return ""


def handle(ev, now=None, sender=None):
    """Entry point for hooks. Returns "sent" | "queued" | "drop:<why>". Never raises."""
    try:
        now = now or time.time()
        sender = sender or send_slack
        st = load()
        verdict, out = decide(ev, st, now, is_off())
        if verdict == "send" and not sender(out):
            st["sent_today"] -= 1                    # the post did not go; let it retry once
            st["seen"].pop(_fp(ev), None)
            verdict, out = "drop", "slack-failed"
        digest = due_digest(st, now, is_off())
        if digest and not sender(digest):
            st["sent_today"] -= 1
            st["last_digest"] = 0
        if _last.get("cid"):
            st["channel_id"] = _last["cid"]
        save(st)
        return {"send": "sent", "queued": "queued"}.get(verdict, f"drop:{out}")
    except Exception as e:
        print(f"phone_alerts.handle failed ({type(e).__name__}: {e})", file=sys.stderr)
        return "drop:error"


def tick(flags):
    """Once per watchdog pass: apply Ryan's replies, queue this pass's flags, flush a due digest."""
    try:
        poll_replies()
        from_watchdog(flags)
        st = load()
        digest = due_digest(st, time.time(), is_off())
        if digest and not send_slack(digest):
            st["sent_today"] -= 1
            st["last_digest"] = 0
        if _last.get("cid"):
            st["channel_id"] = _last["cid"]
        save(st)
    except Exception as e:
        print(f"phone_alerts.tick failed ({type(e).__name__}: {e})", file=sys.stderr)


def from_watchdog(flags):
    """Queue or ping for the flags the watchdog just raised."""
    for f in flags:
        row = f.get("row", {})
        text = f"[{f['name']}] {'+'.join(f['cond'])}: " + "; ".join(f["detail"])[:160]
        blocked = row.get("idle_secs", 0) if set(f["cond"]) & {"PROMPT", "STALLED"} else 0
        handle({"kind": "ERROR" if "ERROR" in f["cond"] else "WATCHDOG",
                "key": f"wd|{f['name']}|{'+'.join(f['cond'])}|{int(time.time() // 3600)}",
                "text": text, "blocked_secs": blocked})


def from_done(session, outcome, task=""):
    """Called by board_done.record after a real done record is added."""
    try:
        meta = next((m for m in L.sessions().values() if m.get("name") == session), None)
        mine = bool(meta) and not meta.get("owner")      # no owner = Ryan started it himself
    except Exception:
        mine = False
    handle({"kind": "DONE", "key": f"done|{session}|{task or outcome}", "mine": mine,
            "text": f"Done - {session}: {outcome}"[:200]})


def poll_replies(sender_id=RYAN_ID):
    """Read recent channel messages from Ryan; apply 'mute 2h' / 'off' / 'on'. -> list of actions."""
    done = []
    if os.environ.get("PODIUM_SLACK", "1") == "0":
        return done
    try:
        sl = _slack()
        st = load()
        cid = st.get("channel_id") or sl.channel_id(_cfg("podium_slack_channel", ""))
        res = sl._call("conversations.history", {"channel": cid, "limit": 20,
                                                 "oldest": str(st.get("poll_ts", 0))}, timeout=10)
        for m in sorted(res.get("messages", []), key=lambda m: float(m["ts"])):
            st["poll_ts"] = max(float(st.get("poll_ts", 0)), float(m["ts"]))
            if m.get("user") != sender_id:
                continue
            cmd = parse_reply(re.sub(r"<@\w+>", "", m.get("text", "")))
            if cmd:
                done.append(apply(cmd[0], cmd[1], st))
        save(st)
    except Exception as e:
        print(f"phone_alerts.poll failed ({type(e).__name__}: {e})", file=sys.stderr)
    return done


def apply(action, secs, st):
    if action == "mute":
        st["mute_until"] = time.time() + secs
        return f"muted {secs // 60} min"
    if action == "unmute":
        st["mute_until"] = 0
        return "unmuted"
    if action == "off":
        open(OFF_PATH, "w").close()
        return "off"
    if os.path.exists(OFF_PATH):
        os.remove(OFF_PATH)
    return "on"


def main(argv=None):
    p = argparse.ArgumentParser(prog="phone_alerts.py")
    p.add_argument("cmd", choices=["status", "off", "on", "mute", "unmute", "poll", "test"])
    p.add_argument("arg", nargs="?", default="")
    a = p.parse_args(argv)
    st = load()
    if a.cmd == "status":
        left = max(0, int(st.get("mute_until", 0) - time.time()) // 60)
        print(f"{'OFF' if is_off() else 'on'}; muted {left} min; sent today {st['sent_today']}/{DAILY_CAP}; "
              f"queue {len(st['queue'])}; quiet now: {is_quiet(time.time())}")
    elif a.cmd == "poll":
        print(poll_replies() or "no commands")
    elif a.cmd == "test":
        ok = send_slack(a.arg or "Test alert from phone_alerts.py. Reply `mute 2h` to silence me.")
        if _last.get("cid"):
            st["channel_id"] = _last["cid"]
            save(st)
        print("sent ts=" + ok if ok else "FAILED (see stderr)")
    else:
        cmd = parse_reply(f"mute {a.arg}".strip()) if a.cmd == "mute" else (a.cmd, 0)
        print(apply(cmd[0], cmd[1], st))
        save(st)


if __name__ == "__main__":
    main()
