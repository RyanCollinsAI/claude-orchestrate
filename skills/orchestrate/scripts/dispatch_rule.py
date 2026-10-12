"""Dispatch-rule gate: every `orch.py task|spawn|review` must name the crew-dispatch.json rule it
matched, and the harness/model/effort it passes must be one of that rule's USE entries.

The seat judges which rule fits (the `when` text is never parsed here, exactly as in firstmate).
This module only checks the answer is consistent with the table, and logs every decision to
state/dispatch-log.jsonl so Ryan can audit the picks. Built 2026-10-08 after the seat picked Sonnet
for a hard security/architecture call and nothing noticed.

Two callers, one rule set:
  - the orchestrator-guardrails G-10 hook runs `py dispatch_rule.py check --command "<segment>"`
    before the Bash call, so a bad pick is refused with the exact allowed combos;
  - `orch.py` calls `gate()` itself, so a spawn that never passed the hook (a script, a pane
    without the plugin) is held to the same rule and is the one that writes the allow log.

--rule <n>              n is the number `py orch.py dispatch` prints; `default` is the table's
                        default list; `override` skips the match and needs --reason "<why>".
Kill switch: ORCH_DISPATCH_HOOK=off in the environment, or as a prefix on the command itself.
Stdlib only: the hook runs it as a subprocess under `python.exe -S`-style isolation.
"""
import datetime
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL = os.path.dirname(HERE)
DISPATCH_FILE = os.path.join(SKILL, "crew-dispatch.json")
STATE_DIR = os.path.join(SKILL, "state")
LOG_FILE = os.path.join(STATE_DIR, "dispatch-log.jsonl")

GATED = ("task", "spawn", "review")
# What a verb falls back to when the flag is absent, copied from orch.py's own parsing:
# `_harness` defaults to claude, and `review` defaults --model to sonnet.
DEFAULT_HARNESS = "claude"
DEFAULT_MODEL = {"review": "sonnet"}
PYTHON = re.compile(r"^(?:.*[\\/])?(?:py|python3?)(?:\.exe)?$", re.I)
OFF_TOKEN =re.compile(r"\bORCH_DISPATCH_HOOK=off\b")
ORCH_TOKEN = re.compile(r"""^["']?(?:.*[\\/])?(?:orch\.py|\$ORCH|%ORCH%|\$\{ORCH\})["']?$""")


def is_off(command="", env=None):
    env = os.environ if env is None else env
    return (env.get("ORCH_DISPATCH_HOOK", "").strip().lower() == "off"
            or bool(OFF_TOKEN.search(command or "")))


# --- reading a command ------------------------------------------------------------------------

_TOK = re.compile(r"""(?:"[^"]*"|'[^']*'|[^\s"']+)+""")


def tokenize(text):
    """Whitespace split that keeps a quoted run together and strips the quotes. No backslash
    escapes: a Windows path stays whole, which shlex would mangle."""
    out = []
    for m in _TOK.finditer(text or ""):
        out.append(re.sub(r"\"([^\"]*)\"|'([^']*)'", lambda q: q.group(1) or q.group(2) or "",
                          m.group(0)))
    return out


def split_segments(command):
    """Shell segments, splitting on ; | & and newlines that sit outside quotes. A line continuation
    (bash `\\`, PowerShell backtick, before a newline) joins its two lines first."""
    segs, buf, quote = [], "", ""
    for ch in re.sub(r"[\\`]\r?\n", " ", command or ""):
        if quote:
            buf += ch
            if ch == quote:
                quote = ""
        elif ch in "\"'":
            quote = ch
            buf += ch
        elif ch in ";|&\n":
            segs.append(buf)
            buf = ""
        else:
            buf += ch
    segs.append(buf)
    return [s for s in segs if s.strip()]


def find_calls(command):
    """[(sub, argv_after_orch)] for every orch.py task|spawn|review call in the command. argv[0]
    is the verb, so it lines up with the `a` list orch.py's own main() parses."""
    calls = []
    for seg in split_segments(command):
        toks = tokenize(seg)
        # The call is what the shell RUNS: the command word, or the script after py/python. A
        # bare mention (`echo orch.py task x`) is not a spawn.
        toks = [t for t in toks if not re.match(r"^\w+=", t)] if re.match(r"\s*\w+=", seg) else toks
        for i, t in enumerate(toks):
            if ORCH_TOKEN.match(t) and i + 1 < len(toks) and toks[i + 1] in GATED \
                    and (i == 0 or PYTHON.match(toks[i - 1])):
                calls.append((toks[i + 1], toks[i + 1:]))
                break
    return calls


def opt(a, flag, default=None):
    """orch.py's `_opt`: a trailing flag with no value, or an empty one, is "not given"."""
    if flag not in a:
        return default
    i = a.index(flag) + 1
    v = a[i] if i < len(a) else None
    return v if v and v.strip() else default


# --- the table ---------------------------------------------------------------------------------

def load_table(path=None):
    path = path or DISPATCH_FILE
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _profiles(use):
    return use if isinstance(use, list) else [use]


def _tier(model):
    """claude-sonnet-5-5 -> sonnet; anything else lowercased. Tier names and full ids both pass."""
    m = (model or "").strip().lower()
    mm = re.match(r"claude-(opus|sonnet|haiku|fable)\b", m)
    return mm.group(1) if mm else m


def _combo(p):
    return (str(p.get("harness", "")).lower(), _tier(p.get("model")), str(p.get("effort", "")).lower())


def flags_of(p):
    s = f"--harness {p.get('harness')} --model {p.get('model')}"
    return s + (f" --effort {p['effort']}" if p.get("effort") else "")


def entries_for(table, rule):
    """The USE entries a rule allows, or None when `rule` names nothing. `default` is the table's
    default list."""
    if rule == "default":
        return _profiles(table.get("default", []))
    if rule and rule.isdigit() and 1 <= int(rule) <= len(table.get("rules", [])):
        return _profiles(table["rules"][int(rule) - 1].get("use", []))
    return None


# --- the quota pick: floor, spendPriority, explain ----------------------------------------------
# Same three steps as firstmate's quota-array-dispatch (MIT, kunchenguid/firstmate): gates first,
# then the highest spendPriority. Eligibility and reasoning class are the rule's own USE list
# (the seat already judged them when it chose the rule), so the gate left here is the runway floor.
# Ours is a plain weekly percent, and spendPriority is a number the table's author writes.

def _is_num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def runway_floor(table):
    """The top-level runwayFloor as a percent, or None when absent or not a number."""
    f = (table or {}).get("runwayFloor")
    return f if _is_num(f) else None


def schema_errors(table):
    """Problems in the optional fields: runwayFloor 0-100, spendPriority a number on every entry."""
    errs = []
    if "runwayFloor" in table and not (_is_num(table["runwayFloor"]) and 0 <= table["runwayFloor"] <= 100):
        errs.append("runwayFloor must be a number from 0 to 100")
    lists = [(f"rule {i}", r.get("use", [])) for i, r in enumerate(table.get("rules", []), 1)]
    for name, use in lists + [("default", table.get("default", []))]:
        for p in _profiles(use):
            if "spendPriority" in p and not _is_num(p["spendPriority"]):
                errs.append(f"{name}: spendPriority must be a number ({flags_of(p)})")
    return errs


def _sp(p):
    return p["spendPriority"] if _is_num(p.get("spendPriority")) else 0


def evaluate(profiles, headroom, floor=None):
    """(winner_index, rows). One row per profile, in written order, with a verdict and a reason.
    Floor: an entry whose harness has a KNOWN weekly reading under `floor` is out while another
    entry is not under it (an unreadable harness stays in, and still sorts last). Rank: highest
    spendPriority, then most weekly headroom, then the order written. If every entry is under the
    floor the floor lifts and the most headroom wins, spendPriority ignored: nothing has room,
    so spend the least-empty bucket."""
    def left(p):
        return headroom.get(p.get("harness"))

    def under(p):
        return floor is not None and left(p) is not None and left(p) < floor

    rows = [{"profile": p, "left": left(p), "sp": _sp(p), "under": under(p)} for p in profiles]
    if not rows:
        return None, rows
    ok = [i for i, r in enumerate(rows) if not r["under"]]
    lifted = not ok
    pool = list(range(len(rows))) if lifted else ok

    def key(i):
        h = -1 if rows[i]["left"] is None else rows[i]["left"]
        return (-h, i) if lifted else (-rows[i]["sp"], -h, i)

    win = min(pool, key=key)
    w = rows[win]
    fmt = lambda v: "no reading" if v is None else f"{v:g}%"
    for i, r in enumerate(rows):
        if i == win:
            rivals = [rows[j] for j in pool if j != i]
            tied = [x for x in rivals if x["sp"] == r["sp"]]
            if len(rows) == 1:
                why = "only entry"
            elif lifted:
                why = f"every entry is under the {floor:g}% floor, so the floor lifts: most weekly headroom"
            elif not rivals:
                why = "the only entry over the floor"
            elif not tied:
                why = f"highest spendPriority ({r['sp']:g}) among entries over the floor"
            elif all(x["left"] == r["left"] for x in tied):
                why = "ties on spendPriority and headroom: first written wins"
            else:
                why = f"spendPriority ties at {r['sp']:g}: most weekly headroom"
            r["status"], r["why"] = "WON", why
        elif r["under"] and not lifted:
            r["status"], r["why"] = "LOST", (f"under the runway floor ({fmt(r['left'])} < {floor:g}%) "
                                             f"while another entry has room")
        elif lifted:
            r["status"], r["why"] = "LOST", f"less weekly headroom ({fmt(r['left'])} vs {fmt(w['left'])})"
        elif r["sp"] != w["sp"]:
            r["status"], r["why"] = "LOST", f"lower spendPriority ({r['sp']:g} vs {w['sp']:g})"
        elif r["left"] != w["left"]:
            r["status"], r["why"] = "LOST", f"less weekly headroom ({fmt(r['left'])} vs {fmt(w['left'])})"
        else:
            r["status"], r["why"] = "LOST", "ties on spendPriority and headroom, written later"
    return win, rows


def resolve_profile(profiles, headroom, floor=None):
    """The winning profile (see evaluate), or None for an empty list."""
    win, rows = evaluate(profiles, headroom, floor)
    return None if win is None else rows[win]["profile"]


def explain(rule, table=None, headroom=None):
    """Plain text: why each USE entry of `rule` (a number or `default`) won or lost. `headroom` is
    {harness: weekly-percent-left or None}, the dict orch.quota_headroom returns. Raises ValueError
    for a rule that names nothing."""
    table = table if table is not None else load_table()
    rule = str(rule)
    use = entries_for(table, rule)
    if use is None:
        raise ValueError(f"{rule!r} is not a rule number or `default`")
    headroom = headroom or {}
    floor = runway_floor(table)
    win, rows = evaluate(use, headroom, floor)
    if rule == "default":
        title = "default - nothing else fits"
    else:
        when = " ".join(str(table["rules"][int(rule) - 1].get("when", "")).split())
        title = f"rule {rule} - " + (when if len(when) <= 100 else when[:97] + "...")
    seen = ", ".join(f"{k} {'-' if v is None else str(int(v)) + '%'}"
                     for k, v in headroom.items() if k != "codex") or "none read"
    out = [title,
           f"runway floor: {'none' if floor is None else f'{floor:g}%'}   weekly headroom: {seen}"]
    for r in rows:
        left = "no reading" if r["left"] is None else f"{r['left']:g}%"
        out.append(f"  {r['status']:<4}  {flags_of(r['profile'])}   left {left}  spendPriority {r['sp']:g}")
        out.append(f"        {r['why']}")
    out.append("pick: " + (flags_of(rows[win]["profile"]) if win is not None else "(no entries)"))
    return "\n".join(out)


def _parse_headroom(text):
    """`claude=40,pi=0` -> {"claude": 40.0, "pi": 0.0}. `x=?` or `x=-` is an unreadable harness."""
    out = {}
    for part in (text or "").split(","):
        if "=" in part:
            k, v = part.split("=", 1)
            try:
                out[k.strip()] = float(v.strip().rstrip("%"))
            except ValueError:
                out[k.strip()] = None
    return out


def _live_headroom():
    """The same reading `orch.py dispatch` prints: quota.collect rows through orch.quota_headroom.
    Imported late so the hook's `check` path never loads orch or the quota collectors."""
    import quota
    import orch
    rows, _ = quota.collect(False)
    return orch.quota_headroom(rows)


def rule_list(table):
    lines = ["crew-dispatch.json rules (pass the number of the one you matched):"]
    for i, r in enumerate(table.get("rules", []), 1):
        when = " ".join(str(r.get("when", "")).split())
        when = when if len(when) <= 110 else when[:107] + "..."
        lines.append(f"  {i}. {when}")
        lines.append("       allows: " + "  |  ".join(flags_of(p) for p in _profiles(r.get("use", []))))
    lines.append("  default. nothing above fits")
    lines.append("       allows: " + "  |  ".join(flags_of(p) for p in _profiles(table.get("default", []))))
    lines.append('  override. a deliberate off-table pick: --rule override --reason "<why>" (logged)')
    return "\n".join(lines)


# --- the decision ------------------------------------------------------------------------------

def decide(sub, a, table, env=None, command=""):
    """(decision, message, record). decision is allow | override | off | deny.
    `a` is the argv after orch.py, with the verb first. The record is the audit-log row."""
    label = a[1] if len(a) > 1 and not a[1].startswith("-") else ""
    kind, harness_flag = opt(a, "--kind"), opt(a, "--harness")
    harness = (kind or harness_flag or DEFAULT_HARNESS).lower()
    model = opt(a, "--model", DEFAULT_MODEL.get(sub))
    effort = opt(a, "--effort")
    rule = opt(a, "--rule")
    reason = opt(a, "--reason", "")
    rec = {"sub": sub, "label": label, "rule": rule, "harness": harness, "model": model,
           "effort": effort}
    if reason:
        rec["reason"] = reason
    if is_off(command, env):
        return "off", "", rec
    if sub == "task" and opt(a, "--host"):       # runs on that host's own login: no quota rule applies
        rec["why"] = "remote host"
        return "allow", "", rec

    def deny(msg, why):
        rec["why"] = why
        return "deny", msg, rec

    if not rule:
        return deny("refused: `" + sub + "` needs --rule <n>, the crew-dispatch.json rule you "
                    "matched (judge the `when` text yourself, then pass its number).\n"
                    + rule_list(table), "no --rule")
    if rule == "override":
        if not reason:
            return deny('refused: --rule override needs --reason "<why this pick is off-table>".',
                        "override without --reason")
        return "override", "", rec
    allowed = entries_for(table, rule)
    if allowed is None:
        return deny(f"refused: --rule {rule!r} is not a rule number, `default` or `override`.\n"
                    + rule_list(table), "unknown rule")
    if not model:
        return deny("refused: pass --model. Rule " + rule + " allows: "
                    + "  |  ".join(flags_of(p) for p in allowed), "no --model")
    if (harness, _tier(model), (effort or "").lower()) in {_combo(p) for p in allowed}:
        return "allow", "", rec
    given = f"--harness {harness} --model {model}" + (f" --effort {effort}" if effort else
                                                       " (no --effort)")
    return deny(f"refused: rule {rule} does not allow {given}.\nRule {rule} allows exactly: "
                + "  |  ".join(flags_of(p) for p in allowed)
                + '\nWrong rule? Re-read `py orch.py dispatch` and pass the right number. '
                  'Deliberately off-table? --rule override --reason "<why>".',
                f"combo not in rule {rule}")


def log_row(decision, rec, source, caller=""):
    """Append one audit row. Never raises: a full disk must not stop a spawn."""
    try:
        path = os.environ.get("ORCH_DISPATCH_LOG") or LOG_FILE   # tests point this at a temp file
        os.makedirs(os.path.dirname(path), exist_ok=True)
        row = {"ts": datetime.datetime.now().isoformat(timespec="seconds"), "decision": decision,
               "source": source, "caller": caller, **{k: v for k, v in rec.items() if v}}
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    except OSError:
        pass


def gate(sub, a, source="cli", caller="", env=None, command="", table_path=None):
    """The orch.py entry point: returns an error message to refuse with, or None to go ahead.
    Writes the audit row for every outcome except a hook-side allow (the CLI logs that one when
    the spawn actually runs, so each spawn is one row). A table that is missing or unreadable
    lets the call through: the table's own loader already exits loudly on a broken file."""
    try:
        table = load_table(table_path)
    except (OSError, ValueError):
        return None
    decision, msg, rec = decide(sub, a, table, env, command)
    if decision == "allow" and source == "hook":
        return None
    log_row(decision, rec, source, caller)
    return msg if decision == "deny" else None


def main(argv):
    """`check --command "<shell text>"`: prints the refusal text and exits 3 when a call in the
    command breaks the rule, else prints nothing and exits 0. Fails open on any error.
    `--explain <rule> [--headroom claude=40,pi=0]`: prints why each USE entry of the rule won or
    lost. Live quota unless --headroom gives the numbers."""
    if len(argv) >= 2 and argv[1] == "--explain":
        rule = argv[2] if len(argv) > 2 and not argv[2].startswith("-") else ""
        if not rule:
            print("usage: dispatch_rule.py --explain <rule number|default> [--headroom claude=40,pi=0]",
                  file=sys.stderr)
            return 2
        table = load_table()
        given = opt(argv, "--headroom")
        try:
            head = _parse_headroom(given) if given else _live_headroom()
        except Exception as e:
            print(f"dispatch_rule: no live quota ({e}); every harness reads as unknown", file=sys.stderr)
            head = {}
        try:
            print(explain(rule, table, head))
        except ValueError as e:
            print(f"{e}\n{rule_list(table)}", file=sys.stderr)
            return 2
        return 0
    if len(argv) < 3 or argv[1] != "check" or argv[2] != "--command":
        print("usage: dispatch_rule.py check --command <text> [--caller <name>]\n"
              "       dispatch_rule.py --explain <rule> [--headroom claude=40,pi=0]", file=sys.stderr)
        return 2
    command = argv[3] if len(argv) > 3 else ""
    caller = opt(argv, "--caller", "")
    try:
        for sub, a in find_calls(command):
            msg = gate(sub, a, source="hook", caller=caller, command=command)
            if msg:
                print(msg)
                return 3
    except Exception as e:  # a bug here must never block a spawn
        print(f"dispatch_rule: {e}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
