"""Two-tier seat: a fast Haiku seat Ryan talks to, an Opus watcher thinking behind it.

  orch.py seat-pair up [--cwd DIR]            start both panes (Haiku left, Opus right) + the feeder
  orch.py seat-pair down [--seat-to NAME]     close both, stop the feeder, hand the seat to NAME
  orch.py seat-pair status
  orch.py seat-pair fix "<text>"              watcher -> seat: a correction (logged)
  orch.py seat-pair answer <id> "<text>"      watcher -> seat: the answer to an ask
  orch.py decide --kind K --what "..." [--why "..."] [--rule N]    seat: log one decision
  orch.py ask-opus "<question>" [--timeout S] | ask-opus --wait <id> [--timeout S]
  py seat_pair.py feed                        (started detached by `up`) wakes the watcher
  py seat_pair.py send-ask <id>               (started detached by the route hook) prompts it

Ryan 2026-10-11: "So you got the quick thinking versus deeper thinking in the background."
His rulings (memory haiku-seat-opus-watcher): Opus fixes a bad call THROUGH the Haiku seat and
never prompts a work pane itself; hard asks go Haiku -> Opus -> Haiku; Opus reviews the seat's
decisions AND the panes' DONE reports; no usage pause, the account rule covers it.

Nothing here relies on Haiku remembering an instruction. The first live review (2026-10-11) typed
"Should we make the pending queue unlimited or keep the 50-item cap?" into Haiku; it answered
alone, never asked Opus and logged nothing, and Opus's answer was the opposite. So:
  - hooks/seat-pair-route.py (UserPromptSubmit) forwards a hard message from Ryan to Opus BEFORE
    Haiku starts, and tells Haiku the ask id to wait on;
  - hooks/seat-pair-log.py (Stop) logs every answer Haiku gives Ryan, and holds the turn open once
    while a forwarded ask is unrelayed;
  - cmd_spawn and cmd_kill log the seat's spawns and kills themselves;
  - orch.py refuses the watcher every command off WATCHER_COMMANDS, whatever shell spelling it
    used (the hook alone was bypassed with `ORCH=x; py "$ORCH" prompt ...`).

Self-wake guard: the feeder never feeds the watcher a line the watcher caused. Its own and the
seat's ledger rows are skipped by label, and `fix-applied` / `answer-from-opus` decisions are not
fed back, so nothing bounces between the two panes. seat_watch.py's about_the_seat() exists
because that exact loop burned a Grok week on 2026-09-21.
"""
import json
import os
import re
import subprocess
import sys
import time
import uuid

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import orchlib as L

SCRIPTS = os.path.dirname(os.path.abspath(__file__))
ORCH = os.path.join(SCRIPTS, "orch.py")
REFS = os.path.join(os.path.dirname(SCRIPTS), "references")
SEAT_LABEL, WATCHER_LABEL, GROUP = "seat-haiku", "seat-watcher", "seat-pair"
POLL = 3            # seconds between feeder passes
QUIET = 120         # seconds with no new item before a batch goes to the watcher
MAX_AGE = 300       # ...or once the oldest pending item is this old, so a steady trickle still goes
RESET_K = 120       # watcher context (k tokens) past which it hands off and restarts fresh
HANDOFF_WAIT = 600  # longest wait for the watcher's handoff notes before restarting anyway
RESTART_WAIT = 300  # longest a restart may take before the feeder gives up on it
GRACE = 120         # a watcher pane mid-restart may be missing from herdr briefly
BATCH_CHARS = 3500  # one prompt; what does not fit waits for the next batch
ITEM_CHARS = 400
ASK_TIMEOUT = 540   # under the Bash tool's 10-minute ceiling
SKIP_KINDS = {"fix-applied", "answer-from-opus"}
# What the watcher may run through orch.py. Anything else is refused in main().
WATCHER_COMMANDS = {"ls", "idle", "asks", "dispatch", "quota", "needs-me", "doctor", "seat-pair"}
WATCHER_PAIR_SUBS = {"fix", "answer", "status"}


def pair_dir():
    return os.path.join(L.BOARD_DIR, "seat-pair")


def _p(name):
    return os.path.join(pair_dir(), name)


def _now_iso():
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def _append(path, row):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def _read_json(path, default=None):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return default


def _write_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.{os.getpid()}.tmp"      # per process: two hooks writing at once shared one .tmp (review r2)
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=1, ensure_ascii=False)
    os.replace(tmp, path)


def load_pair():
    return _read_json(_p("pair.json"), {}) or {}


def _ledger_path():
    return os.path.join(L.BOARD_DIR, "fleet-ledger.jsonl")


# ---------------------------------------------------------------- who is calling

def role_of(session_id=None, pane=None, pair=None):
    """'seat', 'watcher' or None for the session running this. Matched by session id or by herdr
    pane id, so a watcher whose session id changed (a restart) is still the watcher."""
    pair = load_pair() if pair is None else pair
    sid = session_id if session_id is not None else os.environ.get("CLAUDE_CODE_SESSION_ID", "")
    pane = pane if pane is not None else os.environ.get("HERDR_PANE_ID", "")
    for role in ("watcher", "seat"):
        r = pair.get(role) or {}
        if (sid and r.get("sid") == sid) or (pane and r.get("pane") == pane):
            return role
    return None


def watcher_refusal(cmd, args):
    """The refusal text when the watcher runs an orch.py command it may not, else None."""
    if role_of() != "watcher":
        return None
    seat = (load_pair().get("seat") or {}).get("label", SEAT_LABEL)
    how = (f"You reach only the seat ({seat}): `seat-pair fix \"<text>\"` for a correction, "
           f"`seat-pair answer <id> \"<text>\"` for an ask.")
    if cmd == "seat-pair" and (args[:1] and args[0] not in WATCHER_PAIR_SUBS):
        return f"refused: the watcher may not run seat-pair {args[0]}. " + how
    if cmd == "prompt" and args[:1] == [seat]:
        return None
    if cmd not in WATCHER_COMMANDS:
        return f"refused: the watcher may not run orch.py {cmd}; the seat gives the orders. " + how
    return None


# ---------------------------------------------------------------- hard questions

# A message from Ryan that wants judgment, not a lookup. Over-calling costs him 12-60 s of waiting;
# under-calling is caught later, because every Haiku answer is logged and reviewed. Tuned on the
# live tester's lists (2026-10-11): it missed "thoughts on", "honest take", "im torn ... whats the
# move", and forwarded lookups like "is the dashboard up or down?" and "why is X still working".
HARD_RES = [re.compile(p, re.I) for p in (
    r"\bshould (i|we|you|it|this|they)\b", r"(^|[.!?]\s+)should\b", r"\bshould\b[^.!?]*\?",
    r"\bpick (one|between|a|an|the)\b", r"\b(choose|decide) (between|which|whether)\b",
    r"\bwhich (one )?(should|would|is better|option|approach|way)\b", r"\bwhich do (i|we)\b",
    r"\bwhat do you think\b", r"\bwhat would you\b", r"\bdo you think\b", r"\bhow would you\b",
    r"\bthoughts on\b", r"\btake on\b", r"\bthink about how\b", r"\bwhichever you think\b",
    r"\bwhat'?s the (move|play|call)\b", r"\bwhat is the (move|play|call)\b", r"\bi'?m torn\b",
    r"\bis it (worth|better|smart|a good idea)\b", r"\bworth it\b", r"\bdo i need to worry\b",
    r"\b(better|best|smarter|right) (way|approach|option|move|call|plan)\b",
    r"\bpros and cons\b", r"\btrade-?offs?\b", r"\brecommend",
    r"\b(plan|design|architect\w*|strategy|approach)\b.*\?",
    r"\bplan (out|this|it|a|the)\b", r"\bhow should\b", r"\bwhy (would|not)\b",
)]
# An "A or B?" question is a choice unless it opens like a yes/no lookup or a command.
LOOKUP_START = re.compile(r"^(is|are|did|does|do|has|have|was|were|can|kill|close|open|stop|start)\b", re.I)


def _choice_question(t):
    for s in re.split(r"(?<=[.!?])\s+", t):
        s = s.strip()
        if s.endswith("?") and re.search(r"\bor\b", s, re.I) and not re.search(r"\bor not\b", s, re.I) \
                and not LOOKUP_START.match(s):
            return True
    return False


# A message from a tool acting for Ryan (the CapsLock hotkey) carries an id, and the seat's answer
# to that turn is written to replies/<id>.json for the tool to show. Typed with `herdr agent
# prompt` (no [from] stamp), so the hooks treat it as Ryan.
HOTKEY_RE = re.compile(r"^\s*\[hotkey id=([A-Za-z0-9_-]{1,40})\]")


def reply_id(prompt):
    m = HOTKEY_RE.match(prompt or "")
    return m.group(1) if m else None


def write_reply(rid, text, kind):
    _write_json(_p(f"replies/{rid}.json"), {"text": text, "kind": kind, "ts": time.time()})


CLICK_RE = re.compile(r"^\s*Clicking:.*$", re.M)
TAGGED_RE = re.compile(r"\[hotkey id=([A-Za-z0-9_-]{1,40})\](.*?)(?=\[hotkey id=|\n\s*\n|\Z)", re.S)


def split_tagged(text):
    """({hotkey id: its tagged part}, the text with those parts taken out). A tagged part runs to
    the next tag or paragraph break, the same bounds reply_part uses."""
    parts = {m.group(1): m.group(2).strip() for m in TAGGED_RE.finditer(text or "")}
    return parts, TAGGED_RE.sub("", text or "").strip()


def reply_part(answer, rid, alone=True):
    """The part of a turn's answer that belongs to hotkey `rid`, or None when it cannot be told
    apart. Claude Code merges a message sent mid-turn into the running turn, so one answer can
    cover several messages; the route hook tells the seat to open each hotkey's part with its
    `[hotkey id=X]` tag. Untagged: the whole answer only when the hotkey was the turn's sole
    message from Ryan, else its `Clicking:` line if there is exactly one. Live 2026-10-11 an
    untagged merged turn put a VOO/QQQ answer in the title-blue bubble."""
    tag = f"[hotkey id={rid}]"
    i = answer.find(tag)
    if i >= 0:
        # A tagged part runs to the next tag or the next paragraph break: a bubble reply is a few
        # short lines, and untagged text after it may answer another message.
        rest = answer[i + len(tag):]
        ends = [j for j in (rest.find("[hotkey id="), rest.find("\n\n")) if j >= 0]
        return (rest[:min(ends)] if ends else rest).strip()
    if alone:
        return answer
    clicks = CLICK_RE.findall(answer)
    return clicks[0].strip() if len(clicks) == 1 else None


# ---------------------------------------------------------------- the seat's turns

# One line per prompt the seat receives, appended by the route hook and consumed by the Stop hook
# from a byte cursor. A single "current turn" file was overwritten whenever a second message
# arrived mid-turn (review r2 and the hotkey tool, 2026-10-11), which lost the hotkey's reply.

def record_prompt(prompt, from_ryan, ask=None):
    _append(_p("seat-prompts.jsonl"), {"ts": time.time(), "prompt": prompt[:2000], "from_ryan": from_ryan,
                                       "ask": ask, "reply": reply_id(prompt)})


def turn_prompts():
    """(prompts received since the last logged turn, the offset to commit once they are logged)."""
    cur = (_read_json(_p("seat-prompts.cursor.json")) or {}).get("offset", 0)
    return _read_new(_p("seat-prompts.jsonl"), cur)


def pending_prompts():
    """[(prompt row, byte offset just past it)] since the last committed one. The Stop hook commits
    only up to the last prompt the seat has actually read: a prompt sent while the seat is busy is
    recorded at submit time but read in a LATER turn (live 2026-10-11: 'is the dashboard up or
    down?' was logged against the reply to the message before it)."""
    cur = (_read_json(_p("seat-prompts.cursor.json")) or {}).get("offset", 0)
    try:
        with open(_p("seat-prompts.jsonl"), "rb") as fh:
            fh.seek(cur)
            data = fh.read()
    except OSError:
        return []
    out, pos = [], cur
    for raw in data.split(b"\n")[:-1]:      # the last piece is "" or a half-written line
        pos += len(raw) + 1
        try:
            out.append((json.loads(raw.decode("utf-8")), pos))
        except ValueError:
            continue
    return out


def commit_turn(offset):
    _write_json(_p("seat-prompts.cursor.json"), {"offset": offset})


def ask_question(aid):
    return (_read_json(_p(f"asks/{aid}.json")) or {}).get("q", "")


def ask_answer(aid):
    return (_read_json(_p(f"asks/{aid}.answer.json")) or {}).get("text", "")


def relay_seen(aid, shown):
    """True when the text Ryan saw this turn carries Opus's answer to `aid`. Haiku relays in its own
    words, so this checks that a quarter of the answer's longer words appear, not the exact text."""
    ans = ask_answer(aid)
    if not ans:
        return False
    words = set(re.findall(r"[a-z0-9]{5,}", ans.lower()))
    shown = (shown or "").lower()
    return not words or sum(w in shown for w in words) / len(words) >= 0.25


def is_hard(text):
    t = HOTKEY_RE.sub("", (text or "")).strip()
    if not t or t.startswith(("[from ", "[fix from watcher]", "/", "<")):
        return False
    t = t.split(" | screenshot:")[0]       # a hotkey line's tail is tool context, not Ryan's words
    return _choice_question(t) or any(r.search(t) for r in HARD_RES)


# ---------------------------------------------------------------- decide / fix / ask

def decide(kind, what, why="", rule="", by=None):
    if not (kind and what and what.strip()):
        sys.exit('decide needs --kind and --what: orch.py decide --kind spawn --what "..." --why "..."')
    _append(_p("decisions.jsonl"), {"ts": time.time(), "iso": _now_iso(), "kind": kind.strip(),
                                    "what": what.strip(), "why": (why or "").strip(),
                                    "rule": (rule or "").strip(), "by": by or L.caller_name()})
    print(f"logged {kind}: {what.strip()[:80]}")


def auto_log(kind, what, why=""):
    """Log a seat decision from inside orch.py (spawn, kill) when the caller IS the pair's seat.
    Never fails the command it rides on."""
    try:
        if role_of() == "seat":
            decide(kind, what, why, by="auto")
    except Exception as e:
        print(f"  (seat-pair auto-log skipped: {type(e).__name__}: {e})")


BOX_RE = re.compile(r"^\s*❯\s?(.*)$")


SGR_RE = re.compile(r"\x1b\[([0-9;]*)m")
ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")


def drop_dim(line):
    """`line` without its dim (SGR 2) text or escape codes. The input box shows a dim suggestion
    when it is empty; read as plain text it looks like a draft (live tester r4)."""
    out, dim, pos = [], False, 0
    for m in SGR_RE.finditer(line):
        if not dim:
            out.append(line[pos:m.start()])
        params = [int(p) if p else 0 for p in m.group(1).split(";")]
        k = 0
        while k < len(params):
            p = params[k]
            if p in (38, 48, 58):                       # colour: skip its own arguments
                k += 5 if k + 1 < len(params) and params[k + 1] == 2 else 3
                continue
            if p == 2:
                dim = True
            elif p in (0, 22):
                dim = False
            k += 1
        pos = m.end()
    if not dim:
        out.append(line[pos:])
    return ANSI_RE.sub("", "".join(out))


def box_draft(pane):
    """Unsent text in a claude pane's input box, or ''. Dim suggestion text does not count."""
    for ln in reversed((L.pane_read(pane, 12, fmt="ansi") or "").splitlines()):
        m = BOX_RE.match(drop_dim(ln))
        if m:
            return m.group(1).strip()
    return ""


def wait_box_clear(pane, timeout=90):
    """Typing into the seat while Ryan has a draft in its box glues the two together (live tester,
    2026-10-11: 'restart the agent chrome' sat unsent for 5 minutes). Wait for him to send it."""
    deadline = time.time() + timeout
    while box_draft(pane):
        if time.time() >= deadline:
            return False
        time.sleep(3)
    return True


def is_pair_seat(pane):
    return bool(pane) and (load_pair().get("seat") or {}).get("pane") == pane


def _prompt(name, text):
    # orch.py prompt into the seat can wait up to 90 s for Ryan's draft, then verifies delivery.
    try:
        r = subprocess.run([sys.executable, ORCH, "prompt", name, text], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=150)
    except subprocess.TimeoutExpired:
        return False, f"orch.py prompt {name} timed out after 150 s"
    return r.returncode == 0, (r.stdout + r.stderr).strip()


def _need_pair():
    pair = load_pair()
    if not pair:
        sys.exit("no seat pair is up - start one with: py orch.py seat-pair up")
    return pair


def fix(text):
    pair = _need_pair()
    _append(_p("fixes.jsonl"), {"ts": time.time(), "iso": _now_iso(), "text": text.strip()})
    ok, out = _prompt(pair["seat"]["label"],
                      f"[fix from watcher] {text.strip()}\n"
                      f"Apply it now. Then log it: py \"{ORCH}\" decide --kind fix-applied --what \"<what you changed>\". "
                      f"Tell Ryan only if it changes something he will see.")
    print(out)
    if not ok:
        sys.exit(1)


def new_ask(question, route="seat"):
    aid = "a" + uuid.uuid4().hex[:6]
    _write_json(_p(f"asks/{aid}.json"), {"q": question.strip(), "ts": time.time(), "route": route,
                                         "relayed": False})
    return aid


def send_ask(aid):
    """Put an open ask in front of the watcher. Returns (ok, output)."""
    pair = _need_pair()
    ask = _read_json(_p(f"asks/{aid}.json"))
    if not ask:
        return False, f"no ask {aid}"
    return _prompt(pair["watcher"]["label"],
                   f"[hard ask {aid}] {ask['q']}\n"
                   f"Ryan is waiting on this; answer it before any review batch. Reply with: "
                   f"py \"{ORCH}\" seat-pair answer {aid} \"<your answer>\"")


def ask_opus(question, timeout=ASK_TIMEOUT):
    _need_pair()
    if not question.strip():
        sys.exit('ask-opus needs the question: orch.py ask-opus "<question>"')
    aid = new_ask(question)
    ok, out = send_ask(aid)
    if not ok:
        sys.exit(f"could not reach the watcher: {out}")
    return wait_answer(aid, timeout)


def wait_answer(aid, timeout=ASK_TIMEOUT):
    if not _read_json(_p(f"asks/{aid}.json")):
        sys.exit(f"no ask {aid!r} in {_p('asks')}")
    path = _p(f"asks/{aid}.answer.json")
    deadline = time.time() + timeout
    while time.time() < deadline:
        ans = _read_json(path)
        if ans:
            print(ans.get("text", ""))
            mark_relayed(aid)
            return 0
        failed = (_read_json(_p(f"asks/{aid}.json")) or {}).get("send_failed")
        if failed:
            print(f"the watcher could not be reached for ask {aid} ({failed}). Answer Ryan yourself, "
                  f"say Opus was unreachable, and tell him to check the seat-watcher pane.")
            return 4
        time.sleep(2)
    print(f"no answer from the watcher yet (ask {aid}). Keep waiting with: "
          f"py \"{ORCH}\" ask-opus --wait {aid}")
    return 3


def mark_relayed(aid):
    ask = _read_json(_p(f"asks/{aid}.json"))
    if ask and not ask.get("relayed"):
        ask["relayed"] = True
        _write_json(_p(f"asks/{aid}.json"), ask)


def answer(aid, text):
    if not os.path.exists(_p(f"asks/{aid}.json")):
        sys.exit(f"no open ask {aid!r} in {_p('asks')}")
    _write_json(_p(f"asks/{aid}.answer.json"), {"text": text.strip(), "ts": time.time()})
    print(f"answered {aid}")


# ---------------------------------------------------------------- feeder

def _read_new(path, offset):
    """Complete lines added to `path` since byte `offset`, and the new offset. A file that shrank
    (rotated or rewritten) is read from the start."""
    try:
        size = os.path.getsize(path)
    except OSError:
        return [], 0
    if size < offset:
        offset = 0
    with open(path, "rb") as fh:
        fh.seek(offset)
        data = fh.read()
    end = data.rfind(b"\n")
    if end < 0:
        return [], offset
    out = []
    for raw in data[:end].split(b"\n"):
        try:
            out.append(json.loads(raw.decode("utf-8")))
        except ValueError:
            continue
    return out, offset + end + 1


def _cut(s, n=ITEM_CHARS):
    s = " ".join(str(s or "").split())
    return s if len(s) <= n else s[:n - 3] + "..."


PURE_CLICK_RE = re.compile(r"Ryan: \[hotkey id=[^\]]+\].* -> seat: Clicking: [^.!?]{1,80}\.?", re.S)


def decision_item(row):
    if row.get("kind") in SKIP_KINDS:
        return None
    if PURE_CLICK_RE.fullmatch(str(row.get("what") or "").strip()):
        return None          # a hotkey answered with just "Clicking: <control>" has nothing to judge
    # An answer carries Ryan's question and the seat's reply; the watcher needs both to judge it.
    s = f"DECISION {row.get('kind')} | {_cut(row.get('what'), 1200 if row.get('kind') == 'answer' else ITEM_CHARS)}"
    if row.get("why"):
        s += f" | why: {_cut(row.get('why'), 200)}"
    if row.get("rule"):
        s += f" | rule {row['rule']}"
    return s


SHA_RE = re.compile(r"\b[0-9a-f]{7,40}\b")
VERDICT_RE = re.compile(r"\b(SHIP|FIX-FIRST|PERFECT|ISSUES|PASS(?:ED)?|FAIL(?:ED)?|BLOCKED)\b")


def ledger_item(row, skip_names, seen=None):
    """A DONE line for the watcher, or None. `seen` ({task: {shas, verdict}}) drops a repeat DONE
    from the same pane that brings no new commit and no new verdict: the seat switch on
    2026-10-11 made about 15 panes resend the same report."""
    if row.get("event") != "task.status" or row.get("state") != "done":
        return None
    task = row.get("task") or ""
    if task in skip_names or task.startswith("vault"):
        return None
    text = str(row.get("text") or "")
    if seen is not None:
        shas = set(SHA_RE.findall(text))
        verdict = (VERDICT_RE.findall(text) or [""])[-1]
        prev = seen.get(task)
        if prev and shas <= set(prev["shas"]) and verdict == prev["verdict"]:
            return None
        seen[task] = {"shas": sorted(shas | set(prev["shas"] if prev else [])), "verdict": verdict}
    return f"DONE {task} | {_cut(text)}"


def start_cursor():
    """Begin at the end of both files: the watcher reviews what happens from now, not history."""
    def size(p):
        try:
            return os.path.getsize(p)
        except OSError:
            return 0
    return {"decisions": size(_p("decisions.jsonl")), "ledger": size(_ledger_path()), "pending": []}


def collect(cursor, pair):
    skip = {pair.get("seat", {}).get("label"), pair.get("watcher", {}).get("label")}
    items = []
    rows, cursor["decisions"] = _read_new(_p("decisions.jsonl"), cursor.get("decisions", 0))
    items += [i for i in map(decision_item, rows) if i]
    rows, cursor["ledger"] = _read_new(_ledger_path(), cursor.get("ledger", 0))
    seen = cursor.setdefault("done_seen", {})
    items += [i for i in (ledger_item(r, skip, seen) for r in rows) if i]
    return items, cursor


def batch_text(items):
    """(prompt text, how many items it carries). Whatever does not fit stays pending. The rules
    live in the watcher's brief; repeating them in every batch only costs tokens."""
    head = "[review batch] {n} item(s){more}.\n"
    body, used = [], len(head) + 40
    for it in items:
        line = "- " + it
        if body and used + len(line) + 1 > BATCH_CHARS:
            break
        body.append(line)
        used += len(line) + 1
    n, rest = len(body), len(items) - len(body)
    more = f", {rest} more next" if rest else ""
    return head.format(n=n, more=more) + "\n".join(body), n


def pane_state(label):
    """(alive, idle) for a pane, read from herdr."""
    p = next((p for p in L.panes() if p.get("label") == label), None)
    if not p:
        return False, False
    return True, p.get("agent_status") in ("idle", "done")


NOTES = "watcher-notes.md"


def batch_ready(now, last_new, first_pending):
    """QUIET seconds with no new item, or the oldest pending item MAX_AGE old: each new item
    resets the quiet clock, so without the age cap a steady trickle would never be sent."""
    return now - last_new >= QUIET or now - first_pending >= MAX_AGE


def open_asks():
    """Hard asks Ryan may still be waiting on: no answer, no failed send, not past the wait."""
    out = []
    try:
        names = os.listdir(_p("asks"))
    except OSError:
        return out
    for n in names:
        if not n.endswith(".json") or n.endswith(".answer.json"):
            continue
        aid = n[:-5]
        ask = _read_json(_p(f"asks/{n}")) or {}
        if os.path.exists(_p(f"asks/{aid}.answer.json")) or ask.get("send_failed"):
            continue
        try:
            age = time.time() - os.path.getmtime(_p(f"asks/{n}"))
        except OSError:
            continue
        if age < ASK_TIMEOUT + 60:
            out.append(aid)
    return out


def watcher_ctx():
    sid = (load_pair().get("watcher") or {}).get("sid")
    return L.last_usage(sid)[0] if sid else 0


def handoff_step(cursor, label, now, idle=True):
    """Advance a watcher reset by one feeder pass. True while a reset is in progress, so no
    review batch goes out. Ryan 2026-10-11 (via the seat): every review turn re-read the
    watcher's ~210k context, so past RESET_K it writes notes and a fresh watcher takes over.
    Never while a hard ask is open: Ryan is waiting on that answer."""
    h = cursor.get("handoff")
    if not h:
        return False
    if h["phase"] == "notes":
        try:
            done = os.path.getmtime(_p(NOTES)) >= h["ts"]
        except OSError:
            done = False
        if ((done and idle) or now - h["ts"] > HANDOFF_WAIT) and not open_asks():
            h.update(phase="restarting", since=now, old_sid=(load_pair().get("watcher") or {}).get("sid"))
            _detached([sys.executable, ORCH, "seat-pair", "restart-watcher"], _p("restart.log"))
            print(f"seat_pair feed: notes {'written' if done else 'timed out'}; restarting {label}", flush=True)
    elif h["phase"] == "restarting":
        if (load_pair().get("watcher") or {}).get("sid") != h.get("old_sid"):
            print(f"seat_pair feed: {label} restarted fresh", flush=True)
            cursor.pop("handoff")
        elif now - h["since"] > RESTART_WAIT:
            print(f"seat_pair feed: {label} restart did not finish in {RESTART_WAIT}s; see restart.log", flush=True)
            cursor.pop("handoff")
    return "handoff" in cursor


def feed():
    pair = load_pair()
    if not pair:
        print("seat_pair feed: no pair.json; exiting", flush=True)
        return 0
    label = pair["watcher"]["label"]
    # Pending items live in cursor.json beside the offsets, so a reloader restart cannot drop
    # items whose offsets were already advanced. No cap: every item reaches the watcher.
    cursor = _read_json(_p("cursor.json")) or start_cursor()
    pending, missing_since = list(cursor.get("pending", [])), None
    last_new = first_pending = time.time() if pending else 0.0
    print(f"seat_pair feed: started (pid {os.getpid()}) feeding {label}", flush=True)
    while True:
        if load_pair().get("watcher", {}).get("label") != label:
            print("seat_pair feed: the pair was taken down; exiting", flush=True)
            return 0
        now = time.time()
        items, cursor = collect(cursor, pair)
        if items:
            pending += items
            last_new = now
            first_pending = first_pending or now
        cursor["pending"] = pending
        alive, idle = pane_state(label)
        resetting = handoff_step(cursor, label, now, alive and idle)
        _write_json(_p("cursor.json"), cursor)
        _write_json(_p("heartbeat.json"), {"ts": now, "pid": os.getpid(), "pending": len(pending)})
        if not alive:
            missing_since = missing_since or now
            # A restart closes the pane on purpose; only an unexplained absence ends the feeder.
            if now - missing_since > GRACE and not resetting:
                print(f"seat_pair feed: {label} is gone; exiting", flush=True)
                return 0
        else:
            missing_since = None
        # Wait for the watcher to be idle: a busy watcher batches for free, and a review prompt
        # typed into a running turn would split its attention. Hard asks never wait here.
        if pending and alive and idle and batch_ready(now, last_new, first_pending) and not resetting:
            if watcher_ctx() >= RESET_K and not open_asks():
                ok, out = _prompt(label, f"[handoff] Write your open fixes and known bugs to "
                                         f"{_p(NOTES)}, then end the turn.")
                print(f"-> {label}: handoff {'sent' if ok else 'FAILED: ' + out[:200]}", flush=True)
                if ok:
                    cursor["handoff"] = {"phase": "notes", "ts": time.time()}
                    _write_json(_p("cursor.json"), cursor)
            else:
                text, n = batch_text(pending)
                ok, out = _prompt(label, text)
                print(f"-> {label}: {n} of {len(pending)} item(s) {'sent' if ok else 'FAILED: ' + out[:200]}",
                      flush=True)
                if ok:
                    pending = pending[n:]
                    first_pending = time.time() if pending else 0.0
                    cursor["pending"] = pending
                    _write_json(_p("cursor.json"), cursor)
        time.sleep(POLL)


def _detached(argv, log):
    """Hidden console, own process group: same shape and reasons as orch.start_seat_watch."""
    os.makedirs(pair_dir(), exist_ok=True)
    kw = {}
    if L.WINDOWS:
        kw["creationflags"] = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kw["start_new_session"] = True
    with open(log, "a", encoding="utf-8") as fh:
        return subprocess.Popen(argv, stdout=fh, stderr=subprocess.STDOUT,
                                stdin=subprocess.DEVNULL, close_fds=True, **kw).pid


def start_feed(quiet=True):
    log = _p("feed.log")
    pid = _detached([sys.executable, os.path.join(SCRIPTS, "reloader.py"), os.path.abspath(__file__), "feed"], log)
    if not quiet:      # a hook's stdout is its JSON answer, so only `up` prints
        print(f"  feeder started detached (pid {pid}); log {log}")
    return pid


def start_send_ask(aid):
    """The route hook must return in seconds; prompting the watcher can take 25 s to verify."""
    return _detached([sys.executable, os.path.abspath(__file__), "send-ask", aid], _p("send-ask.log"))


FEED_STALE = 30    # the feeder writes its heartbeat every POLL seconds


def feed_alive():
    hb = _read_json(_p("heartbeat.json"), {}) or {}
    return time.time() - float(hb.get("ts", 0) or 0) < FEED_STALE


def ensure_feed():
    """Restart the feeder when its heartbeat is stale. Killing the feeder child also ended its
    reloader (live test 2026-10-11), and nothing brought it back: decisions logged but never
    reached Opus. Called from the route hook and `status`, so the next use heals it."""
    pair = load_pair()
    if not pair or feed_alive():
        return None
    pair["feed_pid"] = start_feed()
    _write_json(_p("heartbeat.json"), {"ts": time.time(), "pid": pair["feed_pid"], "pending": 0})
    _write_json(_p("pair.json"), pair)
    return pair["feed_pid"]


def stop_feed(pid):
    if not pid:
        return
    if L.WINDOWS:
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True)
    else:
        try:
            os.kill(pid, 15)
        except OSError:
            pass


# ---------------------------------------------------------------- up / down / status

def _log_dispatch(model, why):
    import dispatch_rule as DR
    DR.gate("spawn", ["spawn", "--rule", "override", "--reason", why, "--harness", "claude",
                      "--model", model, "--effort", "high"], source="cli", caller=L.caller_name())


def up(cwd, spawn):
    pair = load_pair()
    live = [lb for lb in (SEAT_LABEL, WATCHER_LABEL) if pane_state(lb)[0]]
    if live:
        # Either label alive is enough: a second seat-haiku would make every label lookup a coin
        # flip (review r2).
        sys.exit(f"{' and '.join(live)} already running. Take the pair down first "
                 f"(py orch.py seat-pair down), or close the leftover pane with orch.py kill <label> --force.")
    cwd = cwd or L.DEFAULT_CWD
    # Haiku first so it takes the tab's left half; the watcher splits to its right.
    _log_dispatch("haiku", "seat-pair seat: fast front seat (Ryan 2026-10-11)")
    s_pane, s_sid = spawn(SEAT_LABEL, "@" + os.path.join(REFS, "seat-pair-seat.md"), "haiku", cwd,
                          group=GROUP, effort="high", color="red")
    _log_dispatch("opus", "seat-pair watcher: Opus reviews the Haiku seat (Ryan 2026-10-11)")
    try:
        w_pane, w_sid = spawn(WATCHER_LABEL, "@" + os.path.join(REFS, "seat-pair-watcher.md"), "opus",
                              cwd, group=GROUP, effort="high", color="purple",
                              expensive="seat-pair watcher, Ryan 2026-10-11")
    except SystemExit:
        print(f"the watcher did not start, so {SEAT_LABEL} (pane {s_pane}) is up alone. "
              f"Close it with: py \"{ORCH}\" kill {SEAT_LABEL} --force")
        raise
    os.makedirs(pair_dir(), exist_ok=True)
    _write_json(_p("cursor.json"), start_cursor())
    pair = {"seat": {"label": SEAT_LABEL, "pane": s_pane, "sid": s_sid},
            "watcher": {"label": WATCHER_LABEL, "pane": w_pane, "sid": w_sid}, "up": _now_iso(), "cwd": cwd}
    _write_json(_p("pair.json"), pair)
    commit_turn(_read_new(_p("seat-prompts.jsonl"), 0)[1])    # a new seat starts with no old turn
    pair["feed_pid"] = start_feed(quiet=False)
    _write_json(_p("pair.json"), pair)
    print(f"seat pair up: {SEAT_LABEL} (pane {pair['seat']['pane']}) + {WATCHER_LABEL} "
          f"(pane {pair['watcher']['pane']}).")
    print(f"To make Haiku the seat: py \"{ORCH}\" seat {SEAT_LABEL}")


def seat_tab_label(pair):
    """The label of the tab the seat sits in, so a restarted watcher splits in beside it. `seat`
    renames that tab to ORCHESTRATOR, so the `seat-pair` group name no longer finds it (live
    2026-10-11: the first restarted watcher opened in a tab of its own)."""
    tab_id = next((p.get("tab_id") for p in L.panes() if p.get("pane_id") == pair["seat"].get("pane")), None)
    return next((t.get("label") for t in L.tabs() if t.get("tab_id") == tab_id), None) if tab_id else None


def restart_watcher(spawn, kill):
    """Close the watcher and spawn a fresh one whose prompt is its brief plus the notes it wrote
    on `[handoff]`. Run detached by the feeder; pair.json gets the new pane and sid last, which
    is the feeder's signal that the restart is done."""
    pair = _need_pair()
    w = pair["watcher"]
    try:
        with open(os.path.join(REFS, "seat-pair-watcher.md"), encoding="utf-8") as fh:
            brief = fh.read()
    except OSError as e:
        sys.exit(f"cannot read the watcher brief: {e}")
    try:
        with open(_p(NOTES), encoding="utf-8") as fh:
            notes = fh.read().strip()
    except OSError:
        notes = ""
    prompt = _p("watcher-restart.md")
    with open(prompt, "w", encoding="utf-8") as fh:
        fh.write(brief.rstrip() + "\n\n## Notes from your previous run\n\n"
                 + (notes or "(none were written)") + "\n")
    if pane_state(w["label"])[0]:
        try:
            kill(w["label"], force=True)
        except SystemExit as e:
            print(f"  {w['label']}: {e}")
    if w.get("pane") and not _gone(w["pane"]):
        sys.exit(f"{w['label']} (pane {w['pane']}) did not close; the restart stopped.")
    _log_dispatch("opus", "seat-pair watcher restart: fresh context (Ryan 2026-10-11)")
    pane, sid = spawn(WATCHER_LABEL, "@" + prompt, "opus", pair.get("cwd") or L.DEFAULT_CWD,
                      group=seat_tab_label(pair) or GROUP, effort="high", color="purple",
                      expensive="seat-pair watcher restart, Ryan 2026-10-11")
    pair = load_pair()
    pair["watcher"].update(pane=pane, sid=sid, restarted=_now_iso())
    _write_json(_p("pair.json"), pair)
    print(f"{WATCHER_LABEL} restarted fresh: pane {pane}")


def _gone(pane, timeout=15):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if pane not in {p.get("pane_id") for p in L.panes()}:
            return True
        time.sleep(1)
    return False


def down(seat_to, kill, seat):
    """The watcher goes first and must be confirmed gone before pair.json (which arms the
    watcher's guards) is removed; a teardown that fails half way leaves the guards on."""
    pair = load_pair()
    if not pair:
        sys.exit("no seat pair is up")
    if (L.orchestrator_seat() or {}).get("label") == pair["seat"]["label"]:
        if not seat_to:
            sys.exit(f"{pair['seat']['label']} holds the seat. Name who takes it back: "
                     f"py orch.py seat-pair down --seat-to <pane-label>")
        seat(seat_to)
    w = pair["watcher"]
    if pane_state(w["label"])[0]:
        try:
            kill(w["label"], force=True)
        except SystemExit as e:
            print(f"  {w['label']}: {e}")
    if w.get("pane") and not _gone(w["pane"]):
        sys.exit(f"{w['label']} (pane {w['pane']}) is still up, so the pair stays recorded and its "
                 f"guards stay on. Close it, then run seat-pair down again.")
    stop_feed(pair.get("feed_pid"))
    try:
        kill(pair["seat"]["label"], force=True)
    except SystemExit as e:
        print(f"  {pair['seat']['label']}: {e}")
    try:
        os.remove(_p("pair.json"))
    except OSError:
        pass
    print("seat pair down")


def _count(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return sum(1 for _ in fh)
    except OSError:
        return 0


def status():
    pair = load_pair()
    if not pair:
        print("seat pair: down")
        return
    restarted = ensure_feed()
    pair = load_pair()
    hb = _read_json(_p("heartbeat.json"), {}) or {}
    age = time.time() - float(hb.get("ts", 0) or 0)
    seat_label = (L.orchestrator_seat() or {}).get("label", "")
    for role in ("seat", "watcher"):
        r = pair[role]
        alive, idle = pane_state(r["label"])
        print(f"{role}: {r['label']} pane={r['pane']} {'idle' if idle else 'busy' if alive else 'GONE'}"
              + ("  (holds the orchestrator seat)" if r["label"] == seat_label else ""))
    print(f"feeder: pid {pair.get('feed_pid')} heartbeat {int(age)}s ago, {hb.get('pending', 0)} pending"
          + (f"  (was dead; restarted as pid {restarted})" if restarted else ""))
    print(f"decisions logged: {_count(_p('decisions.jsonl'))}, fixes sent: {_count(_p('fixes.jsonl'))}")


if __name__ == "__main__":
    if sys.argv[1:2] == ["feed"]:
        sys.exit(feed())
    if sys.argv[1:2] == ["send-ask"] and len(sys.argv) > 2:
        ok, out = send_ask(sys.argv[2])
        if not ok:
            # wait_answer stops on this at once instead of waiting out ASK_TIMEOUT.
            ask = _read_json(_p(f"asks/{sys.argv[2]}.json"))
            if ask:
                ask["send_failed"] = _cut(out, 200) or "no output"
                _write_json(_p(f"asks/{sys.argv[2]}.json"), ask)
        print(f"{_now_iso()} send-ask {sys.argv[2]}: {'ok' if ok else 'FAILED'} {out[:200]}", flush=True)
        sys.exit(0 if ok else 1)
    sys.exit("usage: py seat_pair.py feed | send-ask <id>   (the rest goes through orch.py)")
