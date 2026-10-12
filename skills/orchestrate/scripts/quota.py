"""One table of how much subscription quota is left, across every harness on this box.

  py orch.py quota [--json] [--all] [--no-refresh]
  py orch.py quota --tui [--once] [--no-refresh]

Two sources, because neither one sees everything:

- **quota-axi** (`npm i -g quota-axi`) covers codex, cursor, copilot, grok, kimi, zai and more. It
  reads each vendor's own credential file, and for codex/grok/kimi it reads **pi's** auth.json - so
  the moment pi has a Kimi or xAI key, quota-axi starts reporting that plan with no work here.
- **`accounts/ledger.json` + `claude-account.ps1 status`** for Claude, because quota-axi cannot see
  this box's Claude accounts at all. Measured 2026-09-14: quota-axi reads `~/.claude/.credentials.json`
  and reports `auth_required / credentials_invalid`, because on this machine that file holds only MCP
  OAuth - the two real logins live in `accounts/*.profile.json` + `accounts/*.token` and their usage
  in `accounts/ledger.json`. So Claude rows here never come from quota-axi, by design.

This command only *reports*. `claude-account.ps1` stays the thing that moves Claude accounts; the
orchestrator picks a harness by hand off this table (see the routing table in SKILL.md).
"""
import datetime, json, os, re, shutil, subprocess, sys, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import orchlib as L

ACCOUNTS = os.path.dirname(L.ACCOUNTS_TOOL) if L.ACCOUNTS_TOOL else ""
LEDGER = os.path.join(ACCOUNTS, "ledger.json") if ACCOUNTS else ""
AMBIENT = os.path.join(ACCOUNTS, "usage", "ambient.json") if ACCOUNTS else ""

# What each provider costs to run, so the table says whether spending it is free or billed.
# pi + anthropic is the one that surprises: pi's own providers.md says third-party harness usage of
# an Anthropic subscription "draws from extra usage ... and is billed per token, not against Claude
# plan limits". That is real money, so it is never a default route - see PI_ANTHROPIC_WARNING.
BILLING = {
    "claude": "sub (Max plan windows)",
    "codex": "sub (ChatGPT plan)",
    "grok": "sub / api key",
    "kimi": "sub / api key",
}

PI_ANTHROPIC_WARNING = (
    "pi + anthropic bills per token as Anthropic EXTRA USAGE, not against a Max plan window. "
    "Anthropic models belong on the claude harness."
)

ESC = "\x1b["
RESET = ESC + "0m"
BG = ESC + "48;2;21;18;32m"       # Herdr overlay #151220
WHITE = ESC + "38;2;203;201;226m" # Rose Pine Moon text #CBC9E2
MUTED = ESC + "38;2;144;140;170m"
CYAN = ESC + "38;2;156;207;216m"
GREEN = ESC + "38;2;62;143;176m"
YELLOW = ESC + "38;2;246;193;119m" # Rose Pine gold #F6C177
RED = ESC + "38;2;235;111;146m"
BRIGHT = ESC + "1;38;2;235;232;255m"
ACTIVE = ESC + "1;38;2;21;18;32;48;2;156;207;216m"
TERMINAL_ESCAPE = re.compile(
    r"\x1b\][\s\S]*?(?:\x07|\x1b\\|$)|\x1b\[[0-?]*[ -/]*[@-~]|\x1b[@-_]")


def _run(cmd, timeout=90, shell=False):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=timeout, shell=shell)
    except Exception:
        return ""
    return r.stdout or ""


def _pct(v):
    return "-" if v is None else f"{v:.0f}%"


def _when(ts):
    """A reset time as `Mon 14:00` / `Sep-21 14:00`, from epoch seconds or an ISO string."""
    if ts is None or ts == "":
        return "-"
    try:
        if isinstance(ts, str):
            dt = datetime.datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone()
        else:
            dt = datetime.datetime.fromtimestamp(ts)
    except (TypeError, ValueError, OverflowError, OSError):
        return "-"
    now = datetime.datetime.now(dt.tzinfo) if dt.tzinfo else datetime.datetime.now()
    same_day = dt.date() == now.date()
    return dt.strftime("%H:%M") if same_day else dt.strftime("%b-%d %H:%M")


def _reset_passed(ts, now=None):
    """Whether a usable reset timestamp is at or before now."""
    if ts is None or ts == "":
        return False
    try:
        if isinstance(ts, str):
            try:
                reset = float(ts)
            except ValueError:
                reset = datetime.datetime.fromisoformat(
                    ts.replace("Z", "+00:00")).timestamp()
        else:
            reset = float(ts)
        current = time.time() if now is None else float(now)
        return reset <= current
    except (TypeError, ValueError, OverflowError, OSError):
        return False


def _age(ts, now=None):
    if not ts:
        return "age unknown"
    try:
        if isinstance(ts, str):
            then = datetime.datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
        else:
            then = float(ts)
    except (TypeError, ValueError, OverflowError):
        return "age unknown"
    seconds = max(0, int((time.time() if now is None else now) - then))
    if seconds < 60:
        return f"observed {seconds}s ago"
    if seconds < 3600:
        return f"observed {seconds // 60}m ago"
    if seconds < 86400:
        return f"observed {seconds // 3600}h ago"
    return f"observed {seconds // 86400}d ago"


def _clamp(v):
    if v is None:
        return None
    try:
        return max(0.0, min(100.0, float(v)))
    except (TypeError, ValueError):
        return None


def _bar(v, size=12):
    v = _clamp(v)
    if v is None:
        return "[" + "?" * size + "]   -"
    filled = round(v * size / 100)
    return f"[{'#' * filled}{'-' * (size - filled)}] {v:3.0f}%"


def _plain_line(text, width):
    return text[:width].ljust(width)


def _ansi_line(text, width, colour):
    return BG + colour + _plain_line(text, width) + RESET


def _span_line(spans, width, colour=True):
    """Render trusted styles around clipped text without counting ANSI bytes as columns."""
    remaining = width
    clipped = []
    for style, value in spans:
        if remaining <= 0:
            break
        value = str(value)[:remaining]
        clipped.append((style, value))
        remaining -= len(value)
    if not colour:
        return "".join(value for _style, value in clipped).ljust(width)
    painted = "".join(RESET + BG + style + value for style, value in clipped)
    return painted + RESET + BG + (" " * remaining) + RESET


def _quota_colour(value):
    value = _clamp(value)
    if value is None:
        return MUTED
    if value <= 0:
        return RED
    if value <= 25:
        return YELLOW
    return CYAN


def _quota_spans(label, value, resets, width, unicode_symbols=True, now=None):
    expired = _reset_passed(resets, now)
    if expired:
        value = None
    value = _clamp(value)
    colour = _quota_colour(value)
    bar_size = 24 if width >= 86 else 16 if width >= 64 else max(4, min(10, width - 28))
    filled = 0 if value is None else round(value * bar_size / 100)
    percent = "unknown" if value is None else f"{value:3.0f}%"
    fill_char, track_char = ("█", "░") if unicode_symbols else ("#", ".")
    spans = [
        (WHITE, f"    {label:<8}"),
        (colour, fill_char * filled),
        (MUTED, track_char * (bar_size - filled)),
    ]
    if width >= 64:
        spans.extend([
            (colour, f"  {percent}" + ("" if value is None else " remaining")),
            (MUTED, "    reset passed" if expired else f"    resets {_when(resets)}"),
        ])
    else:
        spans.extend([
            (colour, f"  {percent}"),
            (MUTED, "  reset passed" if expired else f"  reset {_when(resets)}"),
        ])
    return spans


def _compact_window(label, value, resets, now=None):
    if _reset_passed(resets, now):
        return f"{label} unknown (reset passed)"
    value = _clamp(value)
    return f"{label} unknown" if value is None else f"{label} {value:.0f}% remaining"


def _provider_state(payload, provider):
    for item in (payload or {}).get("providers", []):
        if item.get("provider") == provider:
            return item.get("state") or {}
    return {}


def _safe_label(value):
    value = TERMINAL_ESCAPE.sub("", str(value or ""))
    value = re.sub(r"[\r\n\t]+", " ", value)
    value = re.sub(r"[\x00-\x1f\x7f-\x9f]", "", value)
    return re.sub(r"\b[^\s@]+@[^\s@]+\b", "[account]", value)


def render_tui(rows, payload, width=None, height=None, page=0,
               refresh=True, colour=True, now=None, unicode_symbols=True):
    """Return one terminal-sized frame. This never mutates or refreshes the collected values."""
    width = max(1, width or shutil.get_terminal_size((96, 30)).columns)
    lines = []

    def add(text="", shade=WHITE):
        lines.append(_span_line([(shade, text)], width, colour))

    def add_spans(spans):
        lines.append(_span_line(spans, width, colour))

    add("  HERDR USAGE", BRIGHT)
    add("  Remaining subscription quota; Claude observations are cached", MUTED)
    rule = "─" if unicode_symbols else "-"
    add("  " + rule * max(0, width - 4), MUTED)
    header = list(lines)
    lines.clear()

    ambient = next((r for r in rows if r["provider"] == "claude/ambient"), None)
    plans = [r for r in rows if r is not ambient and
             (r["provider"].startswith("claude/") or
              r["five_hour_left"] is not None or r["weekly_left"] is not None or
              _provider_state(payload, r["provider"]).get("status") == "fresh")]
    if not plans:
        add("  No readable subscribed accounts", MUTED)
    for index, r in enumerate(plans):
        provider = r["provider"]
        state = _provider_state(payload, provider)
        seen = r.get("seen") if provider.startswith("claude/") else state.get("refreshedAt")
        freshness = _age(seen, now)
        stale = bool(state.get("stale"))
        if stale:
            freshness = "STALE - " + freshness
        elif provider.startswith("claude/"):
            freshness = "cached " + freshness
        if index:
            add("")
        title = f"  {_safe_label(provider)}"
        title_spans = [(BRIGHT, title)]
        if r.get("active"):
            title_spans.extend([(WHITE, "  "), (ACTIVE, " ACTIVE ")])
        title_spans.extend([
            (MUTED, f"  {_safe_label(r['plan'])} plan  "),
            (RED if stale else MUTED, freshness),
        ])
        add_spans(title_spans)
        five = None if stale else r["five_hour_left"]
        week = None if stale else r["weekly_left"]
        add_spans(_quota_spans("5-hour", five, r["five_hour_resets"], width,
                               unicode_symbols, now))
        add_spans(_quota_spans("Weekly", week, r["weekly_resets"], width,
                               unicode_symbols, now))

    if ambient:
        add("")
        add_spans([
            (WHITE, "Shared Claude feed (account unverified)"),
            (MUTED, f"  {_age(ambient.get('seen'), now)}"),
        ])
        add_spans([
            (CYAN, "  " + _compact_window("5-hour", ambient.get("five_hour_left"),
                                           ambient.get("five_hour_resets"), now)),
            (MUTED, "  " + _compact_window("Weekly", ambient.get("weekly_left"),
                                            ambient.get("weekly_resets"), now)),
        ])

    present = {r["provider"].split("/", 1)[0] for r in plans}
    inactive = []
    for p in (payload or {}).get("providers", []):
        name = p.get("provider")
        status = (p.get("state") or {}).get("status", "unknown")
        if name in WATCHED and name != "claude" and name not in present:
            inactive.append((name, status.replace("_", " ")))
    if inactive:
        add("")
        summary = ", ".join(f"{_safe_label(name)}: {_safe_label(status)}"
                            for name, status in inactive)
        add_spans([(MUTED, "  Unavailable  "), (RED, summary)])
    elif payload is None:
        add("")
        add("  quota-axi did not answer; local Claude observations are still shown.", RED)

    body = lines
    footer = []

    def foot(text="", shade=WHITE):
        footer.append(_span_line([(shade, text)], width, colour))

    foot("")
    footer_size = 2
    page_size = max(1, (height or 1000000) - len(header) - footer_size)
    pages = max(1, (len(body) + page_size - 1) // page_size)
    page %= pages
    controls = "  r reload | q close | reload 60s"
    if pages > 1:
        controls += f" | j/k page {page + 1}/{pages}"
    foot(controls, CYAN)
    visible = body[page * page_size:(page + 1) * page_size]
    return "\n".join(header + visible + footer)


def _read_key(timeout):
    if os.name == "nt":
        import msvcrt
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if msvcrt.kbhit():
                key = msvcrt.getwch()
                if key in ("\x00", "\xe0"):
                    return {"H": "up", "P": "down"}.get(msvcrt.getwch(), "")
                return key.lower()
            time.sleep(0.05)
        return ""
    import select
    ready, _, _ = select.select([sys.stdin], [], [], timeout)
    return sys.stdin.read(1).lower() if ready else ""


def _supports_symbols(stream):
    encoding = getattr(stream, "encoding", None) or "utf-8"
    try:
        "█░─".encode(encoding)
        return True
    except (LookupError, UnicodeEncodeError):
        return False


def run_tui(refresh=True, once=False, stream=None, key_reader=None, clock=None):
    stream = stream or sys.stdout
    key_reader = key_reader or _read_key
    clock = clock or time.monotonic
    rows, payload = collect(refresh, ())
    if once:
        stream.write(render_tui(rows, payload, refresh=refresh,
                                colour=bool(getattr(stream, "isatty", lambda: False)()),
                                unicode_symbols=_supports_symbols(stream)) + "\n")
        stream.flush()
        return

    stream.write(ESC + "?1049h" + ESC + "?25l")
    try:
        page = 0
        next_refresh = clock() + 60
        while True:
            size = shutil.get_terminal_size((96, 25))
            stream.write(ESC + "H" + ESC + "2J")
            stream.write(render_tui(rows, payload, width=size.columns, height=size.lines,
                                    page=page, refresh=refresh,
                                    colour=bool(getattr(stream, "isatty", lambda: False)()),
                                    unicode_symbols=_supports_symbols(stream)))
            stream.flush()
            while True:
                key = key_reader(max(0, min(0.25, next_refresh - clock())))
                if key in ("q", "\x03"):
                    return
                if key in ("j", "down"):
                    page += 1
                    break
                if key in ("k", "up"):
                    page -= 1
                    break
                if key == "r" or clock() >= next_refresh:
                    rows, payload = collect(refresh, ())
                    page = 0
                    next_refresh = clock() + 60
                    break
    finally:
        stream.write(RESET + ESC + "?25h" + ESC + "?1049l")
        stream.flush()


# ------------------------------------------------------------------ claude (local accounts)

def claude_rows():
    """One row per Claude account, from the accounts ledger, with the active one marked.

    `ledger.json` stores **used** percentages per weekly-window fingerprint; this prints what is
    LEFT, so the whole table reads one way. `claude-account.ps1 status` is asked only for the email
    and the `*` active marker - if PowerShell is slow or the tool is off, the ledger alone still
    produces every row.
    """
    if not (LEDGER and os.path.exists(LEDGER)):
        return []
    try:
        with open(LEDGER, encoding="utf-8") as fh:
            led = json.load(fh)
    except (OSError, ValueError):
        return []

    active, emails = "", {}
    out = _run(["powershell", "-NoProfile", "-File", L.ACCOUNTS_TOOL, "status"], timeout=60)
    for line in out.splitlines():
        m = re.match(r"^(\*|\s)\s+(\S+)\s+(\S+@\S+)", line)
        if m:
            emails[m.group(2)] = m.group(3)
            if m.group(1) == "*":
                active = m.group(2)

    rows = []
    for name, e in sorted(led.items()):
        if not isinstance(e, dict):
            continue
        fh_, wk = e.get("five_hour") or {}, e.get("seven_day") or {}
        rows.append({
            "provider": f"claude/{name}",
            "plan": "max",
            "five_hour_left": 100 - fh_["used_percentage"] if fh_.get("used_percentage") is not None else None,
            "weekly_left": 100 - wk["used_percentage"] if wk.get("used_percentage") is not None else None,
            "five_hour_resets": fh_.get("resets_at"),
            "weekly_resets": wk.get("resets_at"),
            "active": name == active,
            "seen": e.get("ts"),
            "billing": BILLING["claude"],
            "source": "accounts/ledger.json",
            "note": emails.get(name, ""),
        })
    return rows


def ambient_row():
    """The live shared-login numbers the autoswitch actually ticks on, newer than the ledger."""
    if not (AMBIENT and os.path.exists(AMBIENT)):
        return None
    try:
        with open(AMBIENT, encoding="utf-8") as fh:
            d = json.load(fh)
    except (OSError, ValueError):
        return None
    fh_, wk = d.get("five_hour") or {}, d.get("seven_day") or {}
    return {
        "provider": "claude/ambient",
        "plan": "max",
        "five_hour_left": 100 - fh_["used_percentage"] if "used_percentage" in fh_ else None,
        "weekly_left": 100 - wk["used_percentage"] if "used_percentage" in wk else None,
        "five_hour_resets": fh_.get("resets_at"),
        "weekly_resets": wk.get("resets_at"),
        "active": False,
        "seen": d.get("ts"),
        "billing": BILLING["claude"],
        "source": "accounts/usage/ambient.json",
        "note": "live shared-login feed",
    }


# ------------------------------------------------------------------ everything else (quota-axi)

def axi_payload(refresh=True):
    """quota-axi's JSON, or None when it is not installed. `refresh=False` keeps the read strictly
    read-only - quota-axi otherwise lets a vendor CLI renew an expired session mid-read."""
    cmd = ["quota-axi", "--json"] + ([] if refresh else ["--no-credential-refresh"])
    out = _run(cmd, timeout=120, shell=L.WINDOWS)   # npm shim on Windows needs a shell
    try:
        return json.loads(out[out.index("{"):])
    except (ValueError, TypeError):
        return None


# quota-axi reports ten providers; these are the ones this box either uses or might buy. The rest
# (cursor, copilot, zai, agy, alibaba, opencode-go) only ever print "sign-in required" here, and a
# table nobody can read is a table nobody checks. Add a name here the day a plan for it exists.
WATCHED = ("claude", "codex", "grok", "kimi")


def axi_rows(payload, watched=WATCHED):
    """One row per provider quota-axi knows about, Claude excluded - see the module docstring."""
    rows = []
    for p in (payload or {}).get("providers", []):
        name = p.get("provider")
        if not name or name == "claude" or (watched and name not in watched):
            continue
        wins = {w.get("kind") or w.get("id"): w for w in p.get("windows") or []}
        by_id = {w.get("id"): w for w in p.get("windows") or []}
        five, week = wins.get("session") or wins.get("five_hour"), wins.get("weekly")
        # Grok reports no session/weekly window at all - its plan is a credit pool that refills on a
        # date. Show that pool in the weekly column, which is the one routing reads. Key it by id:
        # every grok window is kind "credits", so a kind lookup returns whichever sorted last
        # (product:chat, always 100%) instead of the aggregate pool that coding actually spends.
        if not week:
            week = by_id.get("credits") or by_id.get("product:grok_build")
        st = (p.get("state") or {}).get("status", "unknown")
        credits = (p.get("credits") or {}).get("remaining")
        note = ""
        if st != "fresh":
            note = (p.get("state") or {}).get("error") or st
        elif credits is not None:
            note = f"{credits:,.0f} credits"
        rows.append({
            "provider": name,
            "plan": p.get("plan") or "-",
            "five_hour_left": (five or {}).get("percentRemaining"),
            "weekly_left": (week or {}).get("percentRemaining"),
            "five_hour_resets": (five or {}).get("resetsAt"),
            "weekly_resets": (week or {}).get("resetsAt"),
            "active": False,
            "seen": None,
            "billing": BILLING.get(name, "-"),
            "source": "quota-axi",
            "note": note,
        })
    return rows


def axi_auth_gaps():
    """Providers quota-axi has a slot for but no credential - what a new plan would light up."""
    out = _run(["quota-axi", "auth"], timeout=90, shell=L.WINDOWS)
    gaps, ok = {}, set()
    for line in out.splitlines():
        parts = [x.strip().strip('"') for x in line.strip().split(",")]
        if len(parts) < 4:
            continue
        if parts[3] == "available":
            ok.add(parts[0])
        elif parts[3] in ("missing", "invalid", "expired"):
            gaps.setdefault(parts[0], []).append(f"{parts[1]}={parts[3]}")
    # A provider lists one slot per credential FILE it knows about. Grok has two (~/.grok/auth.json
    # and pi's auth.json) and only ever needs one, so a provider with any available slot is not a gap.
    return {k: v for k, v in gaps.items() if k not in ok}


# ------------------------------------------------------------------ the table

def collect(refresh=True, watched=WATCHED):
    payload = axi_payload(refresh)
    rows = claude_rows() + axi_rows(payload, watched)
    amb = ambient_row()
    if amb:
        rows.append(amb)
    # Most weekly headroom first - that is the number the routing rule picks on, because the 5h
    # window refills all day and the weekly one is what actually runs out (see SKILL.md).
    rows.sort(key=lambda r: (-(r["weekly_left"] if r["weekly_left"] is not None else -1),
                             r["provider"]))
    return rows, payload


def cmd_quota(as_json=False, refresh=True, all_providers=False, tui=False, once=False):
    if tui:
        run_tui(refresh, once)
        return
    rows, payload = collect(refresh, () if all_providers else WATCHED)
    if as_json:
        print(json.dumps({"rows": rows, "gaps": axi_auth_gaps(),
                          "axi": bool(payload)}, indent=2, default=str))
        return

    if payload is None:
        print("quota-axi is not installed or did not answer - "
              "`npm i -g quota-axi`. Claude rows below still work.")
    print(f"{'PROVIDER':18} {'PLAN':7} {'5H LEFT':>8} {'WEEK LEFT':>10}  "
          f"{'5H RESETS':12} {'WEEK RESETS':12} {'BILLING':22} NOTE")
    for r in rows:
        mark = "*" if r["active"] else " "
        print(f"{mark}{r['provider']:17} {r['plan']:7} {_pct(r['five_hour_left']):>8} "
              f"{_pct(r['weekly_left']):>10}  {_when(r['five_hour_resets']):12} "
              f"{_when(r['weekly_resets']):12} {r['billing']:22} {r['note']}")
    if not rows:
        print("  (nothing readable - is quota-axi installed, and is accounts_tool set in config.json?)")

    gaps = axi_auth_gaps()
    dormant = [p for p in ("grok", "kimi") if p in gaps]
    if dormant:
        print(f"\ndormant, no key yet: {', '.join(dormant)}  "
              f"(add one in ~/.pi/agent/models.json and quota-axi picks it up)")
    print(f"\n{PI_ANTHROPIC_WARNING}")
    best = next((r for r in rows if r["weekly_left"] is not None
                 and not r["provider"].startswith("claude/ambient")), None)
    if best:
        print(f"most weekly headroom: {best['provider']} ({_pct(best['weekly_left'])} left)")


if __name__ == "__main__":
    a = sys.argv[1:]
    cmd_quota("--json" in a, "--no-refresh" not in a, "--all" in a,
              "--tui" in a, "--once" in a)
