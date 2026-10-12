"""Bridge (was Podium): the one page your human reads, and the only place they are asked anything. Opens in
`lavish-axi`.

State, not HTML, is the source of truth. `board/state.json` holds everything; `board render` turns
it into `board/board.html`, and the page re-reads `state.json` every 5 s so a change shows up
without reopening. Every command below renders at the end, so the HTML is never stale.

  py board.py add-question --id q5 --title "..." --context-file x.md
        [--context "..."] [--option A="..." --option B="..."] [--pick A --why "..."]
        [--input a="a =" --input b="b ="] [--from cg4-askbox] [--group webapp]
        [--auto [--auto-hours 24]]
  py board.py answer q5 "A"                     mark answered, move it to Done
  py board.py autopick [--dry-run]              apply the pick on every due --auto question
  py board.py show --caption "..." (--file x.md | --text "...") [--for q5] [--group webapp]
  py board.py session <pane> --doing "..." --model opus --state working
  py board.py sessions --from-ls                fill the table from the live herdr/registry board
  py board.py done "text" [--group webapp]  one line on the Done tab, and one line to Slack
  py board.py render                            rewrite board.html from state.json
  py board.py open                              lavish-axi board-calm.html (the Calm inbox, the default), print the URL
  py board.py old                               lavish-axi board.html (the previous board page)
  py board.py prune [--hours 36]                 drop stale Show/Done/sessions/answered questions
  py board.py digest                            morning "show" block: shipped, waiting, usage

Short by default, set 2026-09-07: the page opens on **Now** - open questions one line each (title
plus the pick) and one line per live session - and nothing else. Done and Show are their own tabs,
each item collapsed to one line until it is clicked. Every `done` and `show` also posts one line to
Slack (`podium_slack_channel` in config.json), so the record lives there and the page stays a page
about what is happening now. The Slack post always happens after the state write and never raises.

The rule, set 2026-09-03: a question carries the real thing - the code block, the picture,
the table, the diagram - never a one-line summary of it. `context_md` is markdown and may hold
```mermaid fences, $$math$$, ![img](relative.png), tables and code fences; all four render.

Auto-pick, set 2026-09-09: a question the asker marked `--auto` and that nobody answered inside 24h
has its own recommended `--pick` applied for it. Only the asker decides that, at add time; nothing
here ever reads a title and guesses. Money, deletes, mail, YouTube and plan changes are never
marked `--auto`. Off until `autopick_enabled` is true in config.json.

Tabs, set 2026-09-04: every item on the board carries a `group` - the herdr tab a pane spawned
into (`--group` on `orch.py spawn`). Bridge builds one tab per group plus All, and filters Show,
Sessions and Done by the selected tab; Needs You always shows every open question regardless of
tab. `--group` on add-question/show/done wins outright; otherwise it is looked up live from
`--from`/`--for`/the pane itself, falling back to `""` (All only). See `board/schema.md`.
"""
import argparse, contextlib, datetime, io, itertools, json, os, re, shutil, subprocess, sys, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from axi_sdk import AxiError, fast_version, run_axi_cli

VERSION = "1.0.0"
if __name__ == "__main__" and fast_version(sys.argv[1:], VERSION):
    sys.exit(0)             # answered before orchlib and scorecard load

import orchlib as L
import scorecard

BOARD = L.BOARD_DIR
STATE = os.path.join(BOARD, "state.json")
STATEJS = os.path.join(BOARD, "state.js")
HTML = os.path.join(BOARD, "board.html")
PLAIN = os.path.join(BOARD, "board-plain.html")

class BoardUsage(SystemExit):
    """A usage mistake (bad id, missing input). Still a SystemExit with the message as its code, so
    in-process callers that catch SystemExit see what they always saw. `main` turns it into exit 2."""

    def __init__(self, message, *suggestions):
        super().__init__(message)
        self.suggestions = list(suggestions)


IMG_RE = re.compile(r"!\[([^\]]*)\]\(([^)\s]+)\)")
# Repos `digest` reads this morning's commits from. Machine-specific, so it comes from config.json.
DIGEST_REPOS = L.CONFIG.get("digest_repos") or []


# ---------------------------------------------------------------- slack mirror

# Ryan, 2026-09-07: "I don't really need to see what we've already done ... maybe that should be
# something that goes to Slack." Done lines and Show blocks are the record, not the ask, so the
# page keeps them behind a tab and Slack gets the running feed. Both machine-specific, so both
# come from config.json (`podium_slack_channel`, `slack_lib`) and are simply off when unset.
SLACK_CHANNEL = L.cfg("podium_slack_channel", "")
SLACK_LIB = L.cfg("slack_lib", "")
URLFILE = os.path.join(BOARD, "podium-url.txt")


def podium_url():
    """The URL the last `board.py open` handed out, so a Show post can link to the real page.
    Falls back to the file path, which is still openable on this box."""
    try:
        u = open(URLFILE, encoding="utf-8").read().strip()
        if u:
            return u
    except OSError:
        pass
    return HTML


def slack_line(text):
    """Post one line to Slack and return its ts, or "" if it did not go.

    Fails open, always. Call it AFTER save() - the board write is the thing that must not be lost,
    and a dead token, a DNS blip or a missing sessionlib then costs a stderr line and nothing else.
    `PODIUM_SLACK=0` turns it off for a run (the tests set it)."""
    if os.environ.get("PODIUM_SLACK", "1") == "0" or not SLACK_CHANNEL or not SLACK_LIB:
        return ""
    try:
        if SLACK_LIB not in sys.path:
            sys.path.insert(0, SLACK_LIB)
        import slack
        return slack.post(SLACK_CHANNEL, text, timeout=8) or ""
    except Exception as e:
        print("slack: " + str(e).replace("\n", " ")[:160], file=sys.stderr)
        return ""


def slack_done(text, group=""):
    slack_line(":white_check_mark: *Done*" + (f"  `{group}`" if group else "") + "  " + str(text))


def slack_show(caption, group=""):
    """Caption plus a link, never the body and never an image - Slack is the notification, Bridge
    is where the thing itself is looked at."""
    slack_line(":eyes: *Show*" + (f"  `{group}`" if group else "") + "  " +
               (str(caption) or "(no caption)") + "\n" + podium_url())


# ---------------------------------------------------------------- state

def now_iso():
    return datetime.datetime.now().astimezone().replace(microsecond=0).isoformat()


# Hours a `--auto` question waits for a human before its own pick is applied for it.
AUTOPICK_HOURS = 24


def auto_after(hours=AUTOPICK_HOURS):
    """The timestamp an --auto question becomes due, `hours` from now."""
    return (datetime.datetime.now().astimezone().replace(microsecond=0)
            + datetime.timedelta(hours=float(hours))).isoformat()


def default_state():
    return {"updated": now_iso(), "header": {"usage": "", "note": ""}, "links": [],
            "questions": [], "show": [], "sessions": [], "done": [], "updates": [],
            "next_qid": 1, "mode": "list"}


def load():
    if not os.path.exists(STATE):
        return default_state()
    try:
        s = json.load(open(STATE, encoding="utf-8"))
    except Exception as e:
        sys.exit(f"{STATE} is not readable JSON ({e}); fix or delete it")
    had_counter = "next_qid" in s
    for k, v in default_state().items():
        s.setdefault(k, v)
    if not had_counter:
        # Upgrading a pre-counter state.json: start ahead of every id still on the board, so the
        # very next auto-post can't collide with one a human is currently looking at. It cannot
        # know about ids already pruned before this field existed - those are gone either way.
        nums = [int(m.group(1)) for q in s["questions"]
                for m in [re.match(r"q(\d+)$", q.get("id", ""))] if m]
        if nums:
            s["next_qid"] = max(nums) + 1
    for coll in ("questions", "show", "sessions", "done"):
        for item in s.get(coll, []):
            item.setdefault("group", "")
    return s


def _replace(tmp, dst, tries=8):
    """os.replace with a short retry. lavish-axi's server and the watcher read state.js / state.json
    on their own clocks, and on Windows a file that is open for reading cannot be replaced -
    WinError 32 killed one board sync on 2026-09-06 23:14. A few 50 ms waits ride it out."""
    for i in range(tries):
        try:
            os.replace(tmp, dst)
            return
        except PermissionError:
            if i == tries - 1:
                raise
            time.sleep(0.05 * (i + 1))


_LINKS_CACHE = {"at": 0.0, "rows": []}
LINKS_TTL = 20          # seconds; save() runs on every board command and board_watch's poll


def hub_links(cap=6):
    """The links that used to be the separate home page, as [{title, url, live}].

    Ryan, 2026-09-13: the hub and the board were two pages he had to bounce between. The links now
    live in Bridge's own top bar, so there is one page.

    podium_home owns the lookup, so this is a DEFERRED import - podium_home imports this module at
    its top, and importing it back at module level here would be a cycle. Called at save() time,
    long after both modules are loaded, so the cycle never actually forms.

    Deliberately does NOT call podium_home.live_pollers(): that shells out to `pwsh
    Get-CimInstance Win32_Process` with a 30 s timeout, and save() runs on every single board
    command and on board_watch's poll loop. A process scan on that path would turn a 5 ms write
    into a multi-second one. `live` is taken from the session's own status instead, which
    state.json already has in memory. The rest still reads each page's <title> off disk, so the
    whole result is cached for LINKS_TTL.

    Fails open to no links: a missing hub strip is a cosmetic loss, a board that will not save is
    not."""
    now = time.time()
    if now - _LINKS_CACHE["at"] < LINKS_TTL:
        return _LINKS_CACHE["rows"]
    rows = [{"title": "Review dashboard", "url": "http://127.0.0.1:8787", "live": False}]
    try:
        import podium_home as PH
        raw = PH._raw_sessions()
        classes_path = PH.classes_path()
        if classes_path:
            v = PH._session_for_path(raw, classes_path)
            rows.append({"title": PH.title_of(classes_path),
                         "url": PH._link({"url": v.get("url", ""), "path": classes_path}),
                         "live": v.get("status") == "open"})
        seen = {os.path.normcase(os.path.abspath(classes_path))} if classes_path else set()
        others = []
        for path, key in PH.open_sessions().items():
            if path in seen:
                continue
            v = raw.get(key, {})
            others.append((PH._epoch(v.get("updated_at", "")), path, v))
        others.sort(reverse=True)
        for _, path, v in others[:cap]:
            rows.append({"title": PH.title_of(path),
                         "url": PH._link({"url": v.get("url", ""), "path": path}),
                         "live": v.get("status") == "open"})
    except Exception:
        pass
    # Home is pinned to its own button in the bar, left of the tabs, so it is the one link that
    # can never fall into the More menu (Ryan, 2026-09-14). Marked here rather than matched on the
    # title in JS, and appended when home.html is not open in Bridge at all so the button still
    # has somewhere real to go - the same file:// fallback the chrome's own pill used to use.
    home = next((r for r in rows if r.get("title") == "Home"), None)
    if home is None:
        try:
            import podium_home as PH
            url = ""
            if os.path.exists(PH.HOME_URLFILE):
                url = open(PH.HOME_URLFILE, encoding="utf-8").read().strip()
            home = {"title": "Home", "live": False,
                    "url": url or "file:///" + os.path.abspath(PH.HOME).replace("\\", "/")}
            rows.append(home)
        except Exception:
            home = None
    if home is not None:
        home["home"] = True
    # The previous board page, reachable from the Calm page header (alt="old"). Only when it has a
    # real Bridge URL: a file:// link from an http page goes nowhere. Pages skip alt rows in their
    # own links bar; `board open` registers the session so this row exists after any open.
    try:
        import podium_home as PH
        v = PH._session_for_path(PH._raw_sessions(), HTML)
        if v.get("url"):
            rows.append({"title": "Old board", "url": v["url"], "live": v.get("status") == "open",
                         "alt": "old"})
    except Exception:
        pass
    _LINKS_CACHE.update(at=now, rows=rows)
    return rows


def save(s, render=True):
    s["updated"] = now_iso()
    s["links"] = hub_links()
    os.makedirs(BOARD, exist_ok=True)
    tmp = STATE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(s, fh, indent=2, ensure_ascii=False)
    _replace(tmp, STATE)            # the page polls this file; never let it read a half-write
    # lavish-axi renders the artifact in a sandboxed iframe whose origin is `null`, so a fetch of
    # state.json is refused for want of an Access-Control-Allow-Origin header even though the
    # server returns 200 (measured 2026-09-03). A <script> load is not CORS-checked, so the same
    # state also goes out as state.js and the page falls back to it. Both files, one write.
    tmp = STATEJS + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write("window.__boardState = " + json.dumps(s, ensure_ascii=False) + ";\n")
    _replace(tmp, STATEJS)
    if render:
        write_plain(s)
        write_html()
        _write_calm_quietly()           # board-calm.html: a second page beside the board, see below
    return s


def next_qid(s):
    """Monotonic, never reused - even after `prune` drops an old answered question from
    `questions[]`. Two auto-posted questions naming the same small id at different times read as
    the same question to a human who remembers Q1 was about something else entirely (2026-09-04)."""
    used = {q.get("id", "") for q in s["questions"]}
    n = max(s.get("next_qid", 1), 1)
    while f"q{n}" in used:
        n += 1
    s["next_qid"] = n + 1
    return f"q{n}"


def bump_qid_past(s, qid):
    """An explicit --id skips next_qid() entirely, so the counter has to be told about it by hand -
    otherwise the very next auto-picked id can still land back on a hand-picked one that's since
    been pruned. A no-op for anything that isn't the qN shape."""
    m = re.match(r"q(\d+)$", qid or "")
    if m:
        s["next_qid"] = max(s.get("next_qid", 1), int(m.group(1)) + 1)


# ---------------------------------------------------------------- markdown in, images alongside

def _same_file(a, b):
    """Same bytes, ignoring CRLF vs LF - a text-ish asset (SVG) checked out with CRLF is still the
    same picture as the LF copy the source tree holds."""
    if not os.path.exists(b):
        return False
    try:
        return (open(a, "rb").read().replace(b"\r\n", b"\n")
                == open(b, "rb").read().replace(b"\r\n", b"\n"))
    except OSError:
        return False


def absorb_images(md, base_dir):
    """Copy every local image the markdown points at into board/ and rewrite the link to the bare
    filename. lavish-axi serves the HTML's own directory, and a leading `/` never resolves there."""
    os.makedirs(BOARD, exist_ok=True)

    def one(m):
        alt, src = m.group(1), m.group(2)
        if src.startswith(("http://", "https://", "data:")):
            return m.group(0)
        p = src if os.path.isabs(src) else os.path.join(base_dir, src)
        if not os.path.exists(p):
            return m.group(0)                       # leave it; it may already sit in board/
        name = os.path.basename(p)
        dest = os.path.join(BOARD, name)
        # Skip a copy that would change nothing. Copying a tracked SVG over itself rewrites its
        # line endings and shows up as a whitespace-only diff for someone else to clean up.
        if os.path.abspath(p) != os.path.abspath(dest) and not _same_file(p, dest):
            shutil.copyfile(p, dest)
        return f"![{alt}]({name})"

    return IMG_RE.sub(one, md)


def read_md(text=None, file=None):
    """Markdown from --text or --context-file, with its images pulled next to the HTML."""
    if file:
        if not os.path.exists(file):
            raise BoardUsage(f"no such file: {file}", "Check the path, then run the command again")
        return absorb_images(open(file, encoding="utf-8").read(),
                             os.path.dirname(os.path.abspath(file)))
    return absorb_images(text or "", os.getcwd())


def kv(pairs, what):
    """['A=needs the $9 pass', ...] -> [('A', 'needs the $9 pass'), ...]"""
    out = []
    for p in pairs or []:
        if "=" not in p:
            raise BoardUsage(f'--{what} wants NAME=label, got {p!r}', f'Example: --{what} A="first choice"')
        k, v = p.split("=", 1)
        out.append((k.strip(), v.strip().strip('"')))
    return out


# ---------------------------------------------------------------- groups (board tabs)

def group_for_who(who):
    """The herdr tab label a pane or session display name lives in right now - the same string
    --group filed it under at spawn time. An overflow tab ('webapp-2', from orch.py's
    cap-3-panes-per-tab rule) folds back to its base group. '' when nothing live matches: the
    session already ended, or this is the orchestrator's own write with no session behind it.
    Board.py's callers pass this through --group when they already know it, so a live lookup is
    only the fallback, not the only path."""
    if not who:
        return ""
    p = next((pp for pp in L.panes() if pp.get("label") == who), None)
    if not p:
        sid = next((sid for sid, m in L.sessions().items() if m.get("name") == who), None)
        p = L.panes_by_sid().get(sid) if sid else None
    if not p:
        return ""
    tab = L.tab_label(p.get("tab_id"))
    return re.sub(r"-\d+$", "", tab) if tab else ""


def resolve_group(explicit, who=None, inherit=""):
    """--group wins outright; else a live pane/session lookup; else whatever the caller says to
    inherit (a show block takes its linked question's group); else '' (Needs-you strip only)."""
    if explicit:
        return explicit
    if who:
        g = group_for_who(who)
        if g:
            return g
    return inherit or ""


# ---------------------------------------------------------------- commands

def open_question_in_group(s, group):
    """The open (unanswered) question already sitting in `group`, or None. `group` falsy (the
    catch-all Needs-you strip) is never gated - only a real tab can already be "full"."""
    if not group:
        return None
    return next((x for x in s["questions"] if not x.get("answered")
                 and x.get("group") == group), None)


def cmd_add_question(a):
    s = load()
    qid = a.id or next_qid(s)
    if any(q.get("id") == qid for q in s["questions"]):
        sys.exit(f"{qid} already exists; pick another --id or answer that one first")
    from_ = getattr(a, "from_") or ""
    group = resolve_group(getattr(a, "group", None), from_)
    existing = open_question_in_group(s, group)
    if existing:
        sys.exit(f"refused: group '{group}' already has an open question {existing['id']}: "
                 f"{existing.get('title', '')} - answer that first")
    if a.id:
        bump_qid_past(s, a.id)
    auto = bool(getattr(a, "auto", False))
    if auto and not a.pick:
        raise BoardUsage("--auto needs a --pick: auto-pick applies the recommendation, so there must be one",
                         "Add --pick A, where A is one of your --option names")
    hours = getattr(a, "auto_hours", None) or AUTOPICK_HOURS
    q = {"id": qid, "title": a.title, "from": from_,
         "context_md": read_md(a.context, a.context_file),
         "options": [{"value": k, "label": v} for k, v in kv(a.option, "option")],
         "pick": a.pick or "", "pick_why": a.why or "",
         "inputs": [{"name": k, "label": v, "width": 70} for k, v in kv(a.input, "input")],
         "multi": bool(getattr(a, "multi", False)),
         "group": group,
         "auto": auto, "auto_after": auto_after(hours) if auto else "",
         "created": now_iso(), "answered": None, "answer": None}
    # Optional display fields for the calm page (board-calm.html); written only when given, so a
    # question added without them is byte-for-byte what it always was.
    for k in ("time", "due", "kind", "one"):
        v = getattr(a, k, None)
        if v:
            q[k] = v
    s["questions"].append(q)
    save(s)
    print(f"added {qid}: {a.title}")
    return qid


def add_question_dict(q):
    """Same as add-question, for callers already holding a dict (watch_sessions.py)."""
    s = load()
    if "id" in q:
        bump_qid_past(s, q["id"])
    else:
        q["id"] = next_qid(s)
    q.setdefault("created", now_iso())
    for k, v in (("from", ""), ("options", []), ("pick", ""), ("pick_why", ""),
                 ("inputs", []), ("answered", None), ("answer", None), ("context_md", ""),
                 ("multi", False), ("auto", False), ("auto_after", "")):
        q.setdefault(k, v)
    if q["auto"] and not q["pick"]:
        q["auto"] = False               # an auto question with no pick has nothing to apply
    if q["auto"] and not q["auto_after"]:
        q["auto_after"] = auto_after(AUTOPICK_HOURS)
    q.setdefault("group", group_for_who(q.get("from", "")))
    existing = open_question_in_group(s, q["group"])
    if existing:
        print(f"refused {q['id']} (group '{q['group']}' already open): "
              f"{existing['id']}: {existing.get('title', '')}")
        return existing["id"]
    s["questions"].append(q)
    save(s)
    return q["id"]


def cmd_answer(a):
    s = load()
    q = next((x for x in s["questions"] if x.get("id") == a.qid), None)
    if not q:
        raise BoardUsage(f"no question {a.qid} on the board",
                         "Run `py board.py --format toon` to list the open questions")
    # Idempotent on purpose: two board_watch processes polling the same Bridge session both
    # deliver the same answer, and they can press Send to Agent twice. Answering an already
    # answered question must not stack a second Done line (it did, measured 2026-09-03).
    if q.get("answered") and q.get("answer") == a.answer:
        print(f"{a.qid} was already answered {a.answer}; nothing changed")
        return
    line = f"{a.qid.upper()} {q.get('title', '')}: {a.answer}"
    if q.get("answered"):
        # startswith, not ==: a line written with `--by` carries an "[answered by ...]" suffix,
        # and an exact match would leave that older line behind as a duplicate.
        prev = f"{a.qid.upper()} {q.get('title', '')}: {q['answer']}"
        s["done"] = [d for d in s["done"] if not str(d.get("text", "")).startswith(prev)]
    q["answered"] = now_iso()
    q["answer"] = a.answer
    # Who wrote it. Ryan answering through board_watch passes neither flag and stays the default,
    # so a Done line with no `by` still means "he said it". A session answering for him has to
    # say so (`--by`/`--reason`, which orchestrator-guardrails G-7 requires), because an answer
    # written for him reads on the board exactly like his.
    by = getattr(a, "by", None)
    if by:
        q["answered_by"] = by
        q["answer_reason"] = getattr(a, "reason", "") or ""
        line += f"  [answered by {by}"
        line += f": {q['answer_reason']}]" if q["answer_reason"] else "]"
    s["done"].insert(0, {"ts": now_iso(), "text": line, "group": q.get("group", "")})
    save(s)
    slack_done(line, q.get("group", ""))
    print(f"answered {a.qid}: {a.answer}")


# ---------------------------------------------------------------- auto-pick

def autopick_enabled():
    """`autopick_enabled` in config.json, default off. Read through a bool gate of its own because
    L.cfg hands back the raw environment string for an ORCH_ override, and "0" is truthy."""
    v = L.cfg("autopick_enabled", False)
    if isinstance(v, str):
        return v.strip().lower() in ("1", "true", "yes", "on")
    return bool(v)


def autopick_due(q, now=None):
    """True when this open question is marked auto, has a pick, and its clock has run out."""
    if q.get("answered") or not q.get("auto") or not q.get("pick") or not q.get("auto_after"):
        return False
    try:
        due = datetime.datetime.fromisoformat(q["auto_after"])
    except Exception:
        return False                    # unparsable clock: never fire, a human still owns it
    return due <= (now or datetime.datetime.now().astimezone())


def cmd_autopick(a):
    """Apply the recommended pick to every question whose 24h has run out, and move it to Done.

    The whole safety of this sits in one place: a question is only ever a candidate because the
    session that asked it passed `--auto`, which it does only for a free, reversible choice. Text
    is never read to decide. Money, deletes, mail, YouTube and plan changes are never marked auto,
    so they can never arrive here."""
    dry = bool(getattr(a, "dry_run", False))
    if not autopick_enabled() and not dry:
        sys.exit("autopick is off: set \"autopick_enabled\": true in "
                 f"{L.CONFIG_PATH} (or run with --dry-run)")
    s = load()
    now = datetime.datetime.now().astimezone()
    due = [q for q in s["questions"] if autopick_due(q, now)]
    if not due:
        print("autopick: nothing due" + (" (dry run)" if dry else ""))
        return []
    lines = []
    for q in due:
        line = f"auto-picked {q['id']}: {q['pick']}"
        print(("would " if dry else "") + line + f"  ({q.get('title', '')})")
        lines.append(line)
        if dry:
            continue
        q["answered"] = now_iso()
        q["answer"] = q["pick"]
        q["answered_by"] = "auto"
        s["done"].insert(0, {"ts": now_iso(), "text": line, "group": q.get("group", "")})
    if dry:
        return lines
    save(s)
    for q, line in zip(due, lines):
        slack_done(line, q.get("group", ""))     # after save(); slack_line can never raise
    return lines


# ---------------------------------------------------------------- aged questions (2026-09-13)
#
# First cut of this (same day) auto-withdrew on age alone and took four live asks off the board
# with it - q95 (dash review), bts-first-reel-970 (dash approve), catalog-saves-0910 (app-ge-parser
# needed it), q97 (internship tabs). Ryan's board is a queue of things he clicks days later, not a
# staleness graveyard: age past 72h is never enough on its own. Corrected rule -
#
#   - WITHDRAWN-AGED only when BOTH hold: the asking pane is no longer live, AND the text has no
#     action-item shape (dash, review, submit, save, sign in, book, pay - the words that mean a
#     human still has to go click something).
#   - Everything else past 72h gets AGED_STILL_OPEN_EVERY_HOURS resurfacing instead: a once-a-day
#     AGED-STILL-OPEN line and a move to the top of the board, never an answer.

WITHDRAW_AGE_HOURS = 72
AGED_STILL_OPEN_EVERY_HOURS = 24
# Word-bounded so "book" doesn't fire on "Facebook" and "pay" doesn't fire on "display".
ACTION_ITEM_RE = re.compile(r"\b(dash|review|submit|saves?|saved|sign[\s-]?in|book|pay(?:s|ment|ing)?)\b",
                            re.I)


def withdraw_aged_enabled():
    """`withdraw_aged_enabled` in config.json, default off - same shape as autopick_enabled().
    Added after the first cut fired unconditionally on its very first heartbeat tick with no
    killswitch and took four live asks with it; --dry-run still works with the gate off."""
    v = L.cfg("withdraw_aged_enabled", False)
    if isinstance(v, str):
        return v.strip().lower() in ("1", "true", "yes", "on")
    return bool(v)


def auto_close_enabled():
    """`auto_close_enabled` in config.json, default off - same shape as autopick_enabled() and
    withdraw_aged_enabled(). Auto-close (orch.py's `auto_close_candidates`/`cmd_auto_close`) kills
    a finished pane with nobody watching, so it stays off until a dry run has been reviewed against
    the live fleet; `orch.py auto-close --dry-run` works with the gate off either way."""
    v = L.cfg("auto_close_enabled", False)
    if isinstance(v, str):
        return v.strip().lower() in ("1", "true", "yes", "on")
    return bool(v)


def has_action_item_shape(q):
    """True when the title or context reads as something a human still has to go click - the
    board's actual normal state, not a stale leftover."""
    blob = q.get("title", "") + " " + q.get("context_md", "")
    return bool(ACTION_ITEM_RE.search(blob))


def _age_hours(q, now):
    return round((now - datetime.datetime.fromisoformat(q["created"])).total_seconds() / 3600.0, 1)


def withdrawable_aged(q, now, live_labels, hours=WITHDRAW_AGE_HOURS):
    """True only when the asking pane is gone AND the text has no action-item shape - age alone
    is never sufficient (2026-09-13 incident)."""
    if q.get("answered"):
        return False
    try:
        created = datetime.datetime.fromisoformat(q["created"])
    except Exception:
        return False                    # unparsable clock: never silently drop it
    if now - created < datetime.timedelta(hours=hours):
        return False
    if q.get("from", "") in live_labels:
        return False                    # the asker is still around to be asked again
    return not has_action_item_shape(q)


def aged_still_open_due(q, now, hours=WITHDRAW_AGE_HOURS, every_hours=AGED_STILL_OPEN_EVERY_HOURS):
    """True when this open question is past `hours` and hasn't had an AGED-STILL-OPEN resurface
    in the last `every_hours` - the non-destructive path for everything withdrawable_aged refuses."""
    if q.get("answered"):
        return False
    try:
        created = datetime.datetime.fromisoformat(q["created"])
    except Exception:
        return False
    if now - created < datetime.timedelta(hours=hours):
        return False
    last = q.get("aged_notice_at")
    if last:
        try:
            if now - datetime.datetime.fromisoformat(last) < datetime.timedelta(hours=every_hours):
                return False
        except Exception:
            pass
    return True


def cmd_withdraw_aged(a):
    """Past --hours (default 72): withdraw only what has no asker left and no action-item shape;
    everything else gets a once-a-day AGED-STILL-OPEN line and moves to the top of the board."""
    dry = bool(getattr(a, "dry_run", False))
    if not withdraw_aged_enabled() and not dry:
        sys.exit("withdraw-aged is off: set \"withdraw_aged_enabled\": true in "
                 f"{L.CONFIG_PATH} (or run with --dry-run)")
    hours = getattr(a, "hours", None) or WITHDRAW_AGE_HOURS
    s = load()
    now = datetime.datetime.now().astimezone()
    live = _live_pane_labels()

    withdraw = [q for q in s["questions"] if withdrawable_aged(q, now, live, hours)]
    withdraw_ids = {q["id"] for q in withdraw}
    resurface = [q for q in s["questions"]
                 if q["id"] not in withdraw_ids and aged_still_open_due(q, now, hours)]

    if not withdraw and not resurface:
        print("withdraw-aged: nothing past " + f"{hours:g}h" + (" (dry run)" if dry else ""))
        return []

    lines = []
    for q in withdraw:
        line = f"{q['id']} WITHDRAWN-AGED | {q.get('title', '')} ({_age_hours(q, now)}h)"
        print(("would " if dry else "") + line)
        lines.append(line)
        if dry:
            continue
        q["answered"] = now_iso()
        q["answer"] = "WITHDRAWN-AGED"
        q["answered_by"] = "auto"
        q["answer_reason"] = f"aged past {hours:g}h, asker gone, no action-item shape"
        s["done"].insert(0, {"ts": now_iso(), "text": line, "group": q.get("group", "")})
    for q in resurface:
        line = f"{q['id']} AGED-STILL-OPEN | {q.get('title', '')} ({_age_hours(q, now)}h)"
        print(("would " if dry else "") + line)
        lines.append(line)
        if dry:
            continue
        q["aged_notice_at"] = now_iso()
        s["questions"].remove(q)
        s["questions"].insert(0, q)     # top of the board
    if dry:
        return lines
    save(s)
    for q, line in zip(withdraw, lines[:len(withdraw)]):
        slack_done(line, q.get("group", ""))
    return lines


def cmd_show(a):
    s = load()
    body = read_md(a.text, a.file)
    if not body.strip():
        raise BoardUsage("show needs --file or --text", 'Example: py board.py show --caption "x" --text "body"')
    for_ = getattr(a, "for_") or ""
    qref = next((x for x in s["questions"] if x.get("id") == for_), None) if for_ else None
    group = resolve_group(getattr(a, "group", None), inherit=qref.get("group", "") if qref else "")
    sid = "s" + str(len(s["show"]) + 1)
    s["show"].insert(0, {"id": sid, "caption": a.caption or "", "body_md": body,
                         "for": for_, "created": now_iso(), "group": group})
    save(s)
    slack_show(a.caption or "", group)
    print(f"show {sid}: {a.caption or '(no caption)'}")
    return sid


def add_show(caption, body_md, for_="", group=""):
    s = load()
    if not group and for_:
        qref = next((x for x in s["questions"] if x.get("id") == for_), None)
        if qref:
            group = qref.get("group", "")
    sid = "s" + str(len(s["show"]) + 1)
    s["show"].insert(0, {"id": sid, "caption": caption, "body_md": body_md,
                         "for": for_, "created": now_iso(), "group": group})
    save(s)
    slack_show(caption, group)
    return sid


def cmd_session(a):
    s = load()
    row = next((x for x in s["sessions"] if x.get("pane") == a.pane), None)
    if not row:
        row = {"pane": a.pane, "doing": "", "model": "", "state": "working", "note": "",
               "group": group_for_who(a.pane)}
        s["sessions"].append(row)
    for k, v in (("doing", a.doing), ("model", a.model), ("state", a.state), ("note", a.note)):
        if v is not None:
            row[k] = v
    row.setdefault("group", group_for_who(a.pane))
    save(s)
    print(f"session {a.pane}: {row['state']}")


# classify() says why a session stopped; the board only needs to know whether a human has to act.
KIND_STATE = {"QUESTION": "waiting", "BLOCKED": "waiting", "OFFER": "waiting",
              "ERROR": "error", "DONE": "done"}


def _entry_ts(e):
    """Epoch seconds of one log entry, 0 when it carries no usable timestamp."""
    ts = e.get("timestamp")
    if not ts:
        return 0
    try:
        return datetime.datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
    except (ValueError, AttributeError):
        return 0


def _last_assistant_lines(entries):
    """(newest assistant message with its line breaks intact, epoch).

    orchlib's `last_assistant_text` collapses the whole message onto one line, which is right for
    the classifier's word rules and wrong here: the sessions table wants the FIRST line."""
    for l in reversed(entries):
        if '"assistant"' not in l:
            continue
        e = L._load(l)
        if not e or e.get("type") != "assistant":
            continue
        c = (e.get("message") or {}).get("content")
        if isinstance(c, list):
            t = "\n".join(b.get("text", "") for b in c
                          if isinstance(b, dict) and b.get("type") == "text").strip()
        elif isinstance(c, str):
            t = c.strip()
        else:
            t = ""
        if t:
            return t, _entry_ts(e)
    return "", 0


def _last_peer_message(entries, to=""):
    """(newest cross-session message this pane sent, epoch), preferring one addressed to `to`.

    A pane's own report to the orchestrator names the work better than anything else in its log,
    because it was written to be read by someone else. Read from the SendMessage tool_use block in
    the sender's own log; `to` is matched as a prefix, since the address carries a session-id
    suffix (`app-lead-5-3 [cff28c]`)."""
    fallback = ("", 0)
    for l in reversed(entries):
        if '"SendMessage"' not in l:
            continue
        e = L._load(l)
        if not e or e.get("type") != "assistant":
            continue
        for b in (e.get("message") or {}).get("content") or []:
            if not isinstance(b, dict) or b.get("type") != "tool_use" or b.get("name") != "SendMessage":
                continue
            inp = b.get("input") or {}
            text = (inp.get("message") or inp.get("content") or inp.get("summary") or "").strip()
            if not text:
                continue
            if not to or str(inp.get("to") or inp.get("recipient") or "").startswith(to):
                return text, _entry_ts(e)
            if not fallback[0]:
                fallback = (text, _entry_ts(e))
    return fallback


def clip(text, cap=120):
    """One tidy line out of a message: first non-empty line, markdown stripped, cut at a sentence
    end at or under `cap`, and only hard-truncated when no sentence ends in range."""
    line = next((l.strip() for l in (text or "").splitlines() if l.strip()), "")
    line = " ".join(re.sub(r"[`*_#>]+", "", line).split())
    if len(line) <= cap:
        return line
    cut = max(line.rfind(m, 0, cap + 1) for m in (". ", "! ", "? "))
    if cut >= 40:
        return line[:cut + 1]
    return line[:cap - 3].rstrip() + "..."


def doing_line(entries, pane, cap=120):
    """What the `doing` column shows, in preference order: the pane's last message to the
    orchestrator, then its last assistant message, then the terminal-title scrape. Newest wins
    between the first two - both can come from the same turn, and ties go to the peer message.

    Until 2026-09-09 this was `classify()[1][:60]`: the first 60 characters of the LAST 300, so the
    board read "ed migration. It turned out to be" - a sentence cut open at both ends."""
    peer, pts = _last_peer_message(entries, L.orchestrator_name() or "")
    said, sts = _last_assistant_lines(entries)
    text = peer if (peer and pts >= sts) else (said or peer)
    return clip(text, cap) or clip(pane.get("terminal_title_stripped") or "", cap)


def live_sessions():
    """The same rows `orch.py ls` prints, as dicts - built from orchlib, not by parsing its text."""
    by_sid = L.panes_by_sid()
    me = L.orchestrator_sid()
    rows = []
    for sid, m in L.sessions().items():
        if sid == me:
            continue
        ents = L.tail_entries(sid)
        ctx, model = L.last_usage(sid, ents)
        kind, said = L.classify(sid, ents)
        p = by_sid.get(sid, {})
        state = "working" if m["status"] == "busy" else KIND_STATE.get(kind, "working")
        tab = L.tab_label(p.get("tab_id")) if p else ""
        ts = L.last_assistant_ts(sid, ents)
        rows.append({"pane": p.get("label") or m["name"],
                     # its own newest message first - a terminal title is often stale or generic
                     "doing": doing_line(ents, p),
                     "model": model.replace("-5", "").replace("-4-5-20251001", "") or "?",
                     "state": state,
                     "note": f"ctx {ctx}k",
                     "group": re.sub(r"-\d+$", "", tab) if tab else "",
                     "updated": (datetime.datetime.fromtimestamp(ts).astimezone()
                                 .replace(microsecond=0).isoformat() if ts else "")})
    return sorted(rows, key=lambda r: r["pane"])


def sync_board(session_rows=None, new_updates=None, cap_updates=50):
    """One load-modify-save for watch_sessions.py's poll loop: refresh every live session's row by
    pane, and prepend fresh entries to the Updates feed. Bulk on purpose - one call per poll beats
    one save per pane, since every `save()` is a full read-modify-write of the same file another
    process (board_watch.py, a human running `board.py answer`) could be writing at the same
    moment."""
    s = load()
    if session_rows:
        by_pane = {r["pane"]: r for r in s["sessions"]}
        for r in session_rows:
            by_pane[r["pane"]] = {**by_pane.get(r["pane"], {}), **r}
        s["sessions"] = sorted(by_pane.values(), key=lambda r: r["pane"])
    if new_updates:
        s["updates"] = (list(new_updates) + s.get("updates", []))[:cap_updates]
    import board_done
    board_done.on_sync(s, new_updates)      # DONE lines -> done[], resolved questions close
    save(s)


def cmd_sessions(a):
    s = load()
    if a.from_ls:
        s["sessions"] = live_sessions()
        save(s)
    if getattr(a, "format", "text") != "toon":
        for r in s["sessions"]:
            print(f"{r['pane']:16} {r['state']:8} {r['model']:8} {r['doing']}")
    return s["sessions"]


def cmd_done(a):
    s = load()
    group = getattr(a, "group", None) or ""
    s["done"].insert(0, {"ts": now_iso(), "text": a.text, "group": group})
    save(s)
    slack_done(a.text, group)
    print("done: " + a.text)


def _live_pane_labels():
    """Every name a live board session row could be keyed under: a herdr pane's label (covers a
    codex pane too - it has no registry entry), or a Claude registry session name for a pane
    started by hand with no herdr label."""
    labels = {p.get("label") for p in L.panes() if p.get("label")}
    labels |= {m.get("name") for m in L.sessions().values() if m.get("name")}
    return labels


def cmd_prune(a):
    """Keep Bridge short: Show blocks older than --hours (default 36) go, Done lines older than a
    fixed 24h go (that is Bridge's own "since your last look" window, not tunable per run), every
    session row for a pane that is no longer live goes, and every answered question goes outright -
    Done already carries the record of what it was answered.

    --days still works (`prune --days 0`, used by test_board_groups.py) and maps to hours * 24."""
    s = load()
    now = datetime.datetime.now().astimezone()
    hours = (a.days * 24) if a.days is not None else (a.hours if a.hours is not None else 36)
    show_cut = now - datetime.timedelta(hours=hours)
    done_cut = now - datetime.timedelta(hours=24)

    kept_done = []
    for d in s["done"]:
        try:
            stale = datetime.datetime.fromisoformat(d["ts"]) < done_cut
        except Exception:
            stale = False                          # unparsable timestamp: never silently drop it
        if not stale:
            kept_done.append(d)
    dropped_done = len(s["done"]) - len(kept_done)
    s["done"] = kept_done

    kept_show = []
    for b in s.get("show", []):
        try:
            stale = datetime.datetime.fromisoformat(b["created"]) < show_cut
        except Exception:
            stale = False
        if not stale:
            kept_show.append(b)
    dropped_show = len(s.get("show", [])) - len(kept_show)
    s["show"] = kept_show

    before_q = len(s["questions"])
    s["questions"] = [q for q in s["questions"] if not q.get("answered")]
    dropped_q = before_q - len(s["questions"])

    live = _live_pane_labels()
    before_sess = len(s.get("sessions", []))
    s["sessions"] = [r for r in s.get("sessions", []) if r.get("pane") in live]
    dropped_sess = before_sess - len(s["sessions"])

    save(s)
    print(f"pruned: {dropped_show} show block(s) (>{hours:g}h), {dropped_done} done line(s) (>24h), "
          f"{dropped_q} answered question(s), {dropped_sess} stale session row(s)")


def cmd_mode(a):
    """Which view a fresh load of the board opens in: `list` (the two columns) or `call` (the
    Captain's call card stack). It lives in state.json because the page cannot remember its own
    choice - lavish-axi sandboxes the artifact iframe and every storage accessor there throws."""
    s = load()
    if not a.mode:
        print(s.get("mode", "list"))
        return
    s["mode"] = a.mode
    save(s)
    print(f"board opens in {a.mode} mode")


def cmd_render(a=None):
    save(load())            # rewrites state.js, board-plain.html and (if it changed) board.html
    print(HTML)


def lavish_cmd(*args):
    """`lavish-axi` on this box is an npm .CMD shim; CreateProcess cannot launch one directly
    (WinError 193), so a .cmd/.bat has to go through cmd /c."""
    os.environ.setdefault("LAVISH_AXI_HOST", "127.0.0.1")  # lavish 0.1.85 binds the Tailscale address otherwise
    exe =shutil.which("lavish-axi") or "lavish-axi"
    if os.name == "nt" and exe.lower().endswith((".cmd", ".bat")):
        return ["cmd", "/c", exe, *args]
    return [exe, *args]


def ensure_lavish_brand():
    """Re-apply the Bridge-in-the-titlebar patch before every open, so an `npm i -g lavish-axi`
    update (which overwrites the patched file) never silently brings back "Lavish Editor". Never
    fatal: a missing or already-patched install just prints and moves on."""
    patcher = os.path.join(os.path.dirname(os.path.abspath(__file__)), "patch_lavish_brand.py")
    if not os.path.exists(patcher):
        return
    subprocess.run([sys.executable, patcher], capture_output=True, text=True, timeout=30)


def _open_page(path, remember):
    """lavish-axi <path>, print the URL. `remember` writes it to podium-url.txt."""
    save(load())
    ensure_lavish_brand()
    r = subprocess.run(lavish_cmd(path, "--no-gate"), capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=180)
    out = (r.stdout or "") + (r.stderr or "")
    print(out.strip())
    m = re.search(r"""https?://[^\s"']+""", out)
    if m:
        if remember:
            # Remembered so a Slack Show post can link to the page Ryan actually has open, rather
            # than a file path he cannot click. Its own small file, never state.json - the page polls
            # that one and this has nothing to do with board content.
            try:
                open(URLFILE, "w", encoding="utf-8").write(m.group(0) + "\n")
            except OSError:
                pass
        print("URL: " + m.group(0))
    return bool(m)


def _register_old_board_session():
    """Make sure Bridge has a session for board.html, without opening a tab, so the "Old board" link
    on the Calm page and on Home always has somewhere to go. A no-op when one is already open;
    an ended session stays ended (lavish-axi refuses to reopen it uninvited) and that is fine."""
    try:
        subprocess.run(lavish_cmd(HTML, "--no-open", "--no-gate"), capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=60)
    except Exception as e:
        print("old board session: " + str(e).replace("\n", " ")[:160], file=sys.stderr)


def cmd_open(a=None):
    """The default board is the Calm inbox (board-calm.html) since 2026-10-10. The old page is
    `board old`. Its URL is what podium-url.txt remembers, so Slack Show links land on Calm too."""
    _register_old_board_session()       # first, so the save inside _open_page already sees its link
    _open_page(calm_path(), remember=True)


def cmd_old(a=None):
    """Open the previous board page (board.html). Leaves podium-url.txt on the default page."""
    _open_page(HTML, remember=False)


# ---------------------------------------------------------------- digest

def git_since(repo, since):
    # `.git` is a FILE in any repo whose git dir was separated - the vault is one. Testing
    # `isdir` here silently reported zero vault commits in every digest until 2026-09-09.
    if not scorecard.is_repo(repo):
        return []
    r = subprocess.run(["git", "-C", repo, "log", "--since", since, "--pretty=%h %s"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    return [l for l in (r.stdout or "").splitlines() if l.strip()]


def account_status():
    tool = L.ACCOUNTS_TOOL
    if not tool or not os.path.exists(tool):
        return "(account switching is off)"
    r = subprocess.run(["pwsh", "-NoProfile", "-File", tool, "status"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace",
                       timeout=90)
    return (r.stdout or r.stderr or "").strip() or "(no output)"


def cmd_digest(a=None):
    s = load()
    since = (datetime.datetime.now() - datetime.timedelta(days=1)).strftime("%Y-%m-%d %H:%M")
    parts = [f"### Shipped since {since}"]
    any_commit = False
    for repo in DIGEST_REPOS:
        lines = git_since(repo, since)
        if lines:
            any_commit = True
            parts.append(f"**{os.path.basename(repo.rstrip(os.sep)) or repo}**")
            parts += [f"- `{l}`" for l in lines[:12]]
    if not any_commit:
        parts.append("- nothing committed in the last day")

    open_q = [q for q in s["questions"] if not q.get("answered")]
    parts.append("\n### Waiting on you")
    parts += ([f"- **{q['id'].upper()}** {q.get('title', '')}" for q in open_q]
              or ["- nothing; the board is clear"])

    parts.append("\n### Usage\n```\n" + account_status() + "\n```")

    # How the seat itself did over the last week, from `scorecard.py`. It answers its own question
    # ("am I doing a good job?") with counted numbers rather than a feeling, and it never raises -
    # a scorecard that cannot be built must not cost Ryan his morning digest.
    parts.append("\n" + scorecard.scorecard_block(days=getattr(a, "days", 7) or 7))

    sid = add_show("Morning digest, " + datetime.date.today().strftime("%a %b %d"),
                   "\n".join(parts))
    print(f"digest -> {sid} ({len(open_q)} question(s) waiting)")


# ---------------------------------------------------------------- html

def esc(t):
    return (str(t).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;"))


# board-plain.html's own styles. It used to borrow the board's CSS, which stopped working the day
# the board became a two-column app shell: that CSS sets `body{display:flex;height:100%;
# overflow:hidden}` for the fixed columns, which on a plain document clips everything below the
# fold with no way to scroll to it. A reading copy needs nothing more than this.
PLAIN_CSS = """
  :root{color-scheme:light dark;--bg:#ececea;--card:#fff;--ink:#1b1d1c;--mute:#5e6360;
        --line:#bcc2bf;--accent:#00695c;--code:#1f2937;--codeink:#f3f4f6}
  @media (prefers-color-scheme:dark){:root{--bg:#101312;--card:#171b1a;--ink:#dde3e1;
        --mute:#8b938f;--line:#2c3332;--accent:#4db6a4;--code:#0d1110;--codeink:#dde3e1}}
  *{box-sizing:border-box}
  body{margin:0;background:var(--bg);color:var(--ink);
       font:14px/1.5 "Segoe UI",system-ui,-apple-system,sans-serif}
  .wrap{max-width:900px;margin:0 auto;padding:18px 14px 40px}
  h1{font-size:19px;margin:0 0 4px}
  .small{color:var(--mute);font-size:12.5px}
  .card{background:var(--card);border:1px solid var(--line);padding:9px 11px;margin:0 0 8px;
        overflow-wrap:anywhere}
  .card.q{border-left:3px solid var(--accent)}
  .card.show{border-left:3px solid var(--mute)}
  pre.code{background:var(--code);color:var(--codeink);padding:9px 11px;overflow-x:auto;
           margin:7px 0 0;white-space:pre-wrap;font:12px/1.5 ui-monospace,Consolas,monospace}
"""


def write_plain(s):
    """board-plain.html: the script-free reading copy the <noscript> block links to.

    It lives in its own file for a reason. lavish-axi live-reloads the artifact whenever the HTML it is
    serving changes on disk, and a reload throws away whatever they were half-way through typing
    (measured 2026-09-03: a typed answer vanished 0.5 s after an unrelated `board done`). Keeping
    every changing byte out of board.html is what makes the page updatable without a reload.

    Deliberately plain: markdown stays as text in a <pre>, so this can never disagree with the real
    renderer about what a fence or a $$ block means."""
    out = []
    for q in s["questions"]:
        if q.get("answered"):
            continue
        auto = (f" <span class='small'>(auto-picks {esc(q.get('pick', ''))} after "
                f"{esc(q.get('auto_after', ''))})</span>") if q.get("auto") else ""
        out.append(f"<div class='card q'><b>{esc(q['id'].upper())}</b> {esc(q.get('title', ''))}{auto}"
                   f"<pre class='code'>{esc(q.get('context_md', ''))}</pre></div>")
    for b in s["show"]:
        out.append(f"<div class='card show'>{esc(b.get('caption', ''))}"
                   f"<pre class='code'>{esc(b.get('body_md', ''))}</pre></div>")
    for r in s["sessions"]:
        out.append(f"<div class='card'>{esc(r.get('pane'))} - {esc(r.get('state'))} - "
                   f"{esc(r.get('doing'))}</div>")
    body = "\n".join(out) or "<div class='card'>Bridge is empty.</div>"
    page = (f"<!doctype html><html lang='en'><head><meta charset='utf-8'>"
            f"<meta name='viewport' content='width=device-width, initial-scale=1'>"
            f"<title>Bridge (plain)</title><style>{PLAIN_CSS}</style></head><body>"
            f"<div class='wrap'><h1>Bridge</h1>"
            f"<p class='small'>Script-free copy, {esc(s['updated'])}.</p>{body}</div></body></html>")
    tmp = PLAIN + ".tmp"
    open(tmp, "w", encoding="utf-8").write(page)
    # Through _replace, not a bare os.replace: this file is read by lavish-axi's server and by
    # anyone who followed the <noscript> link, and on Windows an open file cannot be replaced.
    # The retry was added for state.js / state.json on 2026-09-06 and this write was missed, so it
    # kept failing the same way - WinError 5 killed a board session sync on 2026-09-16. A fix that
    # covers two of three call sites is the kind that looks done and is not.
    _replace(tmp, PLAIN)


# The Bridge look, set by Ryan 2026-09-13 after five themes and three tightenings of the one he
# marked ("4b Spine"). Two things he said drove every choice here:
#
#   "I don't like the orange really"     - the old --ask #b45309 is gone. Teal is the only accent;
#                                          red now means one thing, a question waiting over 48h.
#   "it's all just down one by one ...   - two columns, questions grouped by project, one line
#    more compact so I can see            each with the picks ON the row. Nothing to open to
#    everything at once"                  answer, and neither column scrolls the page.
#   "the ai bubbles look ai, look at      - no 999px pills, no pastel chips, no shadows, no card
#    some asian sites"                     radii. Square corners and 1px hairlines, the way
#                                          kakaku.com / Yahoo! JAPAN / nikkei.com do density.
#
# Each column scrolls inside itself, so a 1440x900 screen never scrolls the page; below 900 the
# whole thing becomes one plain column and the page scrolls normally.
CSS = """
  :root{color-scheme:light dark;
    --bg:#ececea;--card:#fff;--ink:#1b1d1c;--mute:#5e6360;--line:#bcc2bf;--rowline:#dfe3e1;
    --hover:#e8f1ef;--accent:#00695c;--accentbg:#d9ebe8;--accentink:#00564b;--ok:#2e7d32;
    --warn:#8d6e00;--danger:#b3261e;--blue:#1a5fb4;
    --barbg:#2f3a37;--barink:#f2f4f3;--barline:#4a5754;--countbg:#00695c;--countink:#fff;
    --headbg:#dfe3e1;--headink:#1b1d1c;--gbg:#eef1f0;--code:#1f2937;--codeink:#f3f4f6}
  @media (prefers-color-scheme:dark){:root{
    --bg:#101312;--card:#171b1a;--ink:#dde3e1;--mute:#8b938f;--line:#2c3332;--rowline:#202625;
    --hover:#182624;--accent:#4db6a4;--accentbg:#10312c;--accentink:#7fd3c4;--ok:#5cb45f;
    --warn:#c9a94a;--danger:#ef7a72;--blue:#6fa8e0;
    --barbg:#1c2221;--barink:#dde3e1;--barline:#2c3332;--countbg:#4db6a4;--countink:#101312;
    --headbg:#1e2524;--headink:#7fd3c4;--gbg:#141918;--code:#0d1110;--codeink:#dde3e1}}
  *{box-sizing:border-box;min-width:0}
  html,body{margin:0;height:100%}
  body{background:var(--bg);color:var(--ink);display:flex;flex-direction:column;overflow:hidden;
       font:13px/1.45 "Segoe UI",system-ui,-apple-system,Roboto,sans-serif}
  a{color:inherit;text-decoration:none}

  /* ---- top bar. ONE row, never two (Ryan, 2026-09-14: "see how there are like two nav bars,
     we want only one so we have more room to look at the screen"). Brand, Home, the page tabs,
     the waiting count and the Focus/List toggle all share this line. The tabs never wrap: .links
     is the only flexible item and it clips, and layoutTabs() folds whatever no longer fits into
     the More menu, so a busy hub costs the same one row as an empty one.

     Home lives here now instead of in the 40px strip the Bridge chrome used to reserve above the
     iframe for its own pill - board.html carries the `podium-own-home` marker (write_html) and
     patch_lavish_brand.py drops the strip for any page that does. The lavish-axi chrome's own bar
     (artifact title, Annotate) is a different document OUTSIDE this sandboxed iframe, so it is
     the one row that cannot be folded in from here. ---- */
  .bar{display:flex;align-items:center;gap:9px;flex-wrap:nowrap;flex:none;padding:3px 11px;
       background:var(--barbg);color:var(--barink);border-bottom:1px solid var(--barline)}
  .brand{font-weight:700;letter-spacing:-.01em;flex:none}
  .homelink{flex:none;display:inline-flex;align-items:center;gap:5px;padding:1px 8px;
       color:var(--barink);opacity:.86;border:1px solid var(--barline)}
  .homelink:hover{opacity:1;border-color:var(--barink)}
  .homelink svg{display:block}
  .count{font-weight:700;padding:1px 7px;flex:none;white-space:nowrap;margin-left:auto;
       background:var(--countbg);color:var(--countink)}
  .count i{font-style:normal}
  .count.zero{background:transparent;color:var(--barink);opacity:.6;padding-left:0}
  /* the only item that may shrink, and the only one that clips - everything else is flex:none.
     `flex:0 1 auto` (not 1 1) so the strip is exactly as wide as the tabs that fit: the More
     button then sits against the last tab rather than stranded mid-bar. */
  .links{display:flex;flex-wrap:nowrap;align-items:center;flex:0 1 auto;overflow:hidden}
  .links a{flex:none;white-space:nowrap;padding:2px 9px;color:var(--barink);opacity:.82;
       border-left:1px solid var(--barline)}
  .links a:first-child{border-left:none}
  .links a[hidden]{display:none}
  .links a:hover{opacity:1;text-decoration:underline}
  .links a.live::before{content:"";display:inline-block;width:5px;height:5px;background:var(--ok);
       margin-right:5px;vertical-align:2px}
  .tabmore{flex:none;position:relative}
  .tabmore[hidden]{display:none}
  .morebtn{font:inherit;font-weight:700;padding:1px 8px;cursor:pointer;color:var(--barink);
       opacity:.82;background:transparent;border:1px solid var(--barline);border-radius:0}
  .morebtn:hover,.tabmore.open .morebtn{opacity:1;border-color:var(--barink)}
  .moremenu{position:absolute;top:calc(100% + 3px);right:0;z-index:20;min-width:190px;
       max-height:60vh;overflow-y:auto;display:flex;flex-direction:column;
       background:var(--barbg);border:1px solid var(--barline)}
  .moremenu[hidden]{display:none}
  .moremenu a{padding:5px 10px;color:var(--barink);opacity:.86;white-space:nowrap;
       border-top:1px solid var(--barline)}
  .moremenu a:first-child{border-top:none}
  .moremenu a:hover{opacity:1;background:rgba(255,255,255,.09)}
  .stamp{flex:0 1 auto;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;
       padding-left:2px;opacity:.55;font-size:11.5px}

  /* ---- the two columns ---- */
  .grid{flex:1;display:grid;grid-template-columns:minmax(0,1.55fr) minmax(0,1fr);gap:5px;
        padding:5px;min-height:0}
  .col{display:flex;flex-direction:column;min-height:0;background:var(--card);
       border:1px solid var(--line)}
  .colhead{display:flex;align-items:center;gap:8px;padding:3px 9px;flex:none;
           background:var(--headbg);color:var(--headink);border-bottom:1px solid var(--line)}
  .colhead h2{margin:0;font-size:11px;font-weight:700;letter-spacing:.07em;text-transform:uppercase}
  .colhead .n{font-weight:700;opacity:.85;font-size:11px}
  .colhead .hint{margin-left:auto;opacity:.6;font-size:11px}
  .colbody{overflow-y:auto;overflow-x:hidden;flex:1;min-height:0}
  .colbody::-webkit-scrollbar{width:9px}
  .colbody::-webkit-scrollbar-thumb{background:var(--line)}

  /* ---- one project block ---- */
  .ghead{display:flex;align-items:center;gap:7px;padding:7px 8px 2px;position:sticky;top:0;z-index:1;
         background:var(--card);border-top:2px solid var(--accent);color:var(--accentink)}
  .ghead b{font-weight:700;letter-spacing:.05em;text-transform:uppercase;font-size:11px}
  .ghead .n{opacity:.6;font-size:11px}

  /* ---- one question. The spine is the grouping cue that replaced the zebra stripes. ---- */
  .qr{display:flex;align-items:center;gap:8px;padding:4px 8px 4px 10px;
      border-bottom:1px solid var(--rowline);border-left:3px solid var(--accentbg);cursor:pointer}
  .qr:hover{background:var(--hover);border-left-color:var(--accent)}
  .qr.open{background:var(--hover);border-left-color:var(--accent)}
  .qr .ix{opacity:.5;flex:none;width:20px;text-align:right;font-variant-numeric:tabular-nums}
  .qr .t{font-weight:600;flex:1 1 auto;
         display:-webkit-box;-webkit-box-orient:vertical;-webkit-line-clamp:1;overflow:hidden}
  .qr .why{opacity:.62;flex:1 1 36%;
         display:-webkit-box;-webkit-box-orient:vertical;-webkit-line-clamp:1;overflow:hidden}
  .qr .age{flex:none;opacity:.6;font-variant-numeric:tabular-nums}
  .qr .age.late{color:var(--danger);opacity:1;font-weight:700}
  /* "auto in 6h" - how long a --auto question waits before its own pick is applied for it. Quiet:
     it says "I will decide this for you unless you say otherwise", which is information, not an
     alarm. */
  .qr .auto{flex:none;opacity:.55;font-size:11px;border:1px solid var(--line);padding:0 5px}
  .qr .auto.due{color:var(--danger);border-color:var(--danger);opacity:1}
  .picks{display:flex;gap:3px;flex:none}
  .picks button{font:inherit;font-weight:700;line-height:1;padding:3px 7px;cursor:pointer;
     border:1px solid var(--line);background:var(--card);color:var(--mute);border-radius:2px;
     min-width:24px;text-align:center}
  .picks button.rec{border-color:var(--accent);background:var(--accentbg);color:var(--accentink)}
  .picks button:hover{border-color:var(--accent);color:var(--accentink)}
  .picks .more{padding:3px 7px;opacity:.55;border:1px dashed var(--line);border-radius:2px}

  /* ---- the expanded body of one question: context, inputs, free text ---- */
  .qd{border-bottom:1px solid var(--rowline);border-left:3px solid var(--accent);
      background:var(--hover);padding:8px 10px 10px}
  .qd .from{opacity:.6;font-size:11.5px;margin-bottom:4px}
  .qd .ctx{font-size:12.5px}
  .qd .ctx p{margin:5px 0}
  .qd .mdwrap{overflow-x:auto}
  .qd .row{display:flex;gap:7px;align-items:center;flex-wrap:wrap;margin-top:7px}
  .qd input[type=text],.qd textarea{font:inherit;border:1px solid var(--line);padding:4px 8px;
      background:var(--card);color:var(--ink);border-radius:0}
  .qd textarea{width:100%;min-height:58px}
  .qd button.send{font:inherit;font-weight:700;border:1px solid var(--accent);
      background:var(--accent);color:#fff;padding:4px 11px;cursor:pointer;border-radius:2px}
  .qd .queued{color:var(--ok);font-size:11.5px;display:none}
  .qd .optlist{display:flex;flex-direction:column;gap:4px;margin:7px 0 0}
  .qd .optlist label{display:flex;gap:7px;align-items:flex-start;border:1px solid var(--line);
      background:var(--card);padding:4px 8px;cursor:pointer}
  .qd .optlist label.pick{border-color:var(--accent)}

  /* ---- right column ---- */
  .sess{display:flex;align-items:center;gap:7px;padding:3px 8px;
        border-bottom:1px solid var(--rowline)}
  .sess .nm{font-weight:700;flex:none}
  .sess .dg{opacity:.62;flex:1 1 auto;
       display:-webkit-box;-webkit-box-orient:vertical;-webkit-line-clamp:1;overflow:hidden}
  .sess .ctx{opacity:.5;flex:none;font-variant-numeric:tabular-nums;font-size:11.5px}
  .flag{width:6px;height:6px;flex:none;background:var(--accent)}
  .flag.waiting{background:var(--warn)}
  .flag.done{background:var(--mute)}
  .flag.error{background:var(--danger)}
  .sub{margin:9px 0 0;padding:2px 8px;background:var(--gbg);color:var(--accentink);font-weight:700;
       letter-spacing:.05em;text-transform:uppercase;font-size:11px;
       border-top:1px solid var(--line);border-bottom:1px solid var(--line)}
  .srow{display:flex;gap:7px;align-items:center;padding:3px 8px;
        border-bottom:1px solid var(--rowline);cursor:pointer}
  .srow:hover{background:var(--hover)}
  .srow .cap{display:-webkit-box;-webkit-box-orient:vertical;-webkit-line-clamp:1;overflow:hidden}
  .srow .when{opacity:.5;flex:none;margin-left:auto;font-variant-numeric:tabular-nums;
              font-size:11.5px}
  .srow.open .cap{-webkit-line-clamp:unset;overflow:visible;overflow-wrap:anywhere}
  .sbody{padding:6px 10px 9px;border-bottom:1px solid var(--rowline);background:var(--hover);
         font-size:12.5px;overflow-x:auto}

  .empty{opacity:.55;padding:7px 9px;font-size:12px}
  pre.code{background:var(--code);color:var(--codeink);padding:9px 11px;overflow-x:auto;
     margin:7px 0;white-space:pre;font:12px/1.5 ui-monospace,Consolas,monospace}
  code{background:var(--gbg);padding:1px 4px;font:12px ui-monospace,Consolas,monospace}
  pre.mermaid{background:transparent;margin:0;padding:0;overflow-x:auto}
  .mathblock{margin:5px 0;overflow-x:auto}
  .katex-display{margin:5px 0}
  .katex-mathml{display:none}
  img.fig{display:block;max-width:100%;background:var(--card);border:1px solid var(--line);
     margin:7px 0}
  table.md{width:100%;border-collapse:collapse;font-size:12.5px;display:block;overflow-x:auto}
  table.md th,table.md td{text-align:left;padding:4px 7px;border:1px solid var(--line)}

  /* ---- Captain's call: one card at a time (2026-09-14, after firstmate's bearings board).
     Ryan: "it goes one question at a time ... he gives an answer, he queues it, and then it
     automatically disappears and goes to the next question. So he doesn't have to scroll."
     Same look language as the list - square corners, 1px hairlines, teal, no shadows - so this
     is the same page in a second mode, not a second design. The pile behind the card is two flat
     offsets rather than rotated paper: "the ai bubbles look ai" rules the playful version out. */
  .modebtn{padding:1px 8px;color:var(--barink);opacity:.82;background:transparent;cursor:pointer;
     font:inherit;font-weight:700;border:1px solid var(--barline);border-radius:0}
  .modebtn:hover{opacity:1;border-color:var(--barink)}
  .callwrap{flex:1;display:none;min-height:0;overflow-y:auto;padding:14px 12px 44px}
  body.call .grid{display:none}
  body.call .callwrap{display:block}
  .callin{max-width:720px;margin:0 auto}
  .callhead{display:flex;align-items:baseline;gap:9px;margin:0 0 9px;color:var(--accentink)}
  .callhead h2{margin:0;font-size:11px;font-weight:700;letter-spacing:.07em;text-transform:uppercase}
  .callhead .n{margin-left:auto;font-size:11px;font-weight:700;letter-spacing:.05em;
     text-transform:uppercase;color:var(--mute)}
  .stack{position:relative}
  .stack::before,.stack::after{content:"";position:absolute;inset:0;z-index:0;
     background:var(--card);border:1px solid var(--line)}
  .stack::before{transform:translate(4px,5px)}
  .stack::after{transform:translate(8px,10px)}
  .stack.two::after,.stack.one::before,.stack.one::after,
  .stack.none::before,.stack.none::after{display:none}
  .cc{position:relative;z-index:1;background:var(--card);border:1px solid var(--line);
     border-left:3px solid var(--accent);padding:11px 13px 12px}
  .cc.sent{border-left-color:var(--ok);opacity:.55}
  .cctop{display:flex;align-items:center;gap:8px;margin-bottom:6px}
  .tag{font-size:10px;font-weight:700;letter-spacing:.08em;text-transform:uppercase;
     padding:2px 7px;background:var(--accent);color:#fff}
  .tag.question{background:var(--blue)}
  .tag.action{background:var(--danger)}
  .cctop .who{margin-left:auto;opacity:.5;font-size:11.5px}
  .cc h3{margin:0 0 7px;font-size:15px;font-weight:700;line-height:1.3}
  .ctxrow{display:grid;grid-template-columns:58px minmax(0,1fr);gap:8px;align-items:baseline;
     margin:3px 0}
  .ctxrow .k{font-size:10px;letter-spacing:.06em;text-transform:uppercase;opacity:.5}
  .ctxrow .v{font-size:12.5px;opacity:.88;min-width:0;overflow-x:auto}
  .ctxrow .v p{margin:4px 0}
  .ctxrow .v p:first-child{margin-top:0}
  .ctxrow .v p:last-child{margin-bottom:0}
  .ccopts{display:flex;flex-direction:column;gap:4px;margin:9px 0 0}
  .ccopt{display:flex;gap:8px;align-items:flex-start;border:1px solid var(--line);
     background:var(--card);padding:6px 9px;cursor:pointer}
  .ccopt:hover{border-color:var(--accent);background:var(--hover)}
  .ccopt.on{border-color:var(--accent);background:var(--accentbg)}
  .ccopt .key{flex:none;width:14px;text-align:center;font-weight:700;opacity:.4;
     font-variant-numeric:tabular-nums}
  .ccopt input{accent-color:var(--accent);margin:3px 0 0;flex:none}
  .ccopt .bd{flex:1 1 auto;min-width:0}
  .ccopt .bd b{display:block;font-size:13px}
  .ccopt .bd em{display:block;font-size:12px;font-style:normal;opacity:.7}
  .ccopt .rec{flex:none;align-self:center;font-size:10px;font-weight:700;letter-spacing:.07em;
     text-transform:uppercase;border:1px solid var(--accent);color:var(--accentink);
     background:var(--accentbg);padding:1px 5px}
  .ccrow{display:flex;gap:7px;align-items:center;flex-wrap:wrap;margin-top:8px}
  .cc input[type=text]{font:inherit;border:1px solid var(--line);padding:4px 8px;
     background:var(--card);color:var(--ink);border-radius:0}
  .cc input.other{width:100%;margin-top:8px}
  .ccfoot{display:flex;gap:9px;align-items:center;flex-wrap:wrap;margin-top:10px}
  .ccfoot button.send{font:inherit;font-weight:700;border:1px solid var(--accent);
     background:var(--accent);color:#fff;padding:4px 12px;cursor:pointer;border-radius:2px}
  .ccfoot .queued{color:var(--ok);font-size:11.5px;display:none}
  .callnav{display:flex;align-items:center;gap:7px;margin-top:11px}
  /* an author `display` outranks the [hidden] attribute's UA rule, so Back/Skip stayed on screen
     over the empty stack until this line existed (measured 2026-09-14) */
  .callnav[hidden]{display:none}
  .callnav .pos{flex:1 1 auto;opacity:.55;font-size:11.5px;font-variant-numeric:tabular-nums}
  .callnav button{font:inherit;border:1px solid var(--line);background:var(--card);
     color:var(--mute);padding:3px 10px;cursor:pointer;border-radius:2px}
  .callnav button:hover{border-color:var(--accent);color:var(--accentink)}
  .cchint{opacity:.45;font-size:11px;margin-top:9px}
  .ccempty{border:1px solid var(--line);background:var(--card);padding:16px 14px;font-size:13px;
     opacity:.75}

  /* ---- phone: one plain column, the page scrolls, nothing clipped ---- */
  @media (max-width:900px){
    html,body{height:auto}
    body{display:block;overflow:auto}
    .grid{display:block;padding:5px}
    /* the card is the whole screen here; the keyboard hint is desktop-only and the 58px label
       column costs a wrap on every context line, so about/decide stack instead */
    /* the right padding is not symmetry: the pile behind the card sticks out 8px, and at 500px
       that was the page's only horizontal scroll (measured 2026-09-14, doc 502 vs viewport 500) */
    .callwrap{overflow:visible;padding:8px 16px 30px 6px}
    .ctxrow{grid-template-columns:1fr;gap:1px;margin:6px 0}
    .cchint{display:none}
    .col{margin-bottom:5px}
    .colbody{overflow:visible;max-height:none}
    .bar{position:sticky;top:0;z-index:9}
    .stamp{display:none}
    /* The reason and the row number are scanning aids for a wide screen; on a phone they each
       cost a whole line, so the title takes them. The picks never go - they are the point. */
    .qr{flex-wrap:wrap;padding:6px 8px 6px 10px}
    .qr .why,.qr .ix{display:none}
    .qr .t{flex:1 1 100%;-webkit-line-clamp:3}
    .picks{margin-left:auto}
  }

  /* One bar means the right-hand cluster competes with the tabs for the same line. Under 640px
     the count's words are the cheapest thing to give up - the number and its colour still read
     as "this many wait on you", and the tabs get ~90px back before they start folding away. */
  @media (max-width:640px){
    .count i{display:none}
    .bar{gap:7px;padding:3px 8px}
  }
"""

# The whole page renders in JS from state.json so a `board` command and a 5 s poll produce the same
# DOM. Python only ever writes this skeleton plus the boot copy of the state.
JS = r"""
const POLL_MS = 5000;
let state = window.__boardState ||
            {updated: null, header: {}, links: [], questions: [], show: [], sessions: [], done: []};

const esc = s => String(s == null ? '' : s).replace(/[&<>"']/g,
  c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));

// Which rows the human opened. Lives in a plain Set, not storage, so a 5 s state poll can never
// collapse a card he is reading or throw away an answer he is half-way through typing.
const openQs = new Set();
const openBlocks = new Set();
const typeset_done = new WeakSet();

function mdToHtml(src){
  src = String(src == null ? '' : src);
  const holds = [];
  const hold = h => { holds.push(h); return '\n\nLVHOLD' + (holds.length - 1) + 'ENDHOLD\n\n'; };
  // mermaid and $$math$$ come out before markdown runs, so nothing escapes or reflows them
  src = src.replace(/```mermaid[ \t]*\r?\n([\s\S]*?)```/g,
        (m, b) => hold('<pre class="mermaid">' + esc(b.replace(/\s+$/, '')) + '</pre>'));
  src = src.replace(/\$\$([\s\S]*?)\$\$/g,
        (m, b) => hold('<div class="mathblock">$$' + esc(b) + '$$</div>'));
  let html = marked.parse(src, {gfm: true});
  html = html.replace(/<pre><code[^>]*>/g, '<pre class="code">')
             .replace(/<\/code><\/pre>/g, '</pre>')
             .replace(/<img /g, '<img class="fig" ')
             .replace(/<table>/g, '<table class="md">');
  html = html.replace(/<p>\s*LVHOLD(\d+)ENDHOLD\s*<\/p>/g, (m, i) => holds[+i])
             .replace(/LVHOLD(\d+)ENDHOLD/g, (m, i) => holds[+i]);
  // marked does not sanitize (it passes raw HTML in the source straight through, and it stopped
  // shipping its own `sanitize` option after v1); a session's last message becomes this markdown
  // verbatim, so a literal <script> or onerror= typed into a report has to be stripped here, not
  // trusted to have never been there. This is also the backstop for the $$ branch above: even if a
  // future edit re-introduces an unescaped interpolation, DOMPurify still stops it reaching the DOM.
  return DOMPurify.sanitize(html);
}

// A node only gets typeset once it is actually on screen: mermaid measures a hidden node as
// zero-wide and lays the diagram out against that, so running it on a collapsed row draws a
// broken one.
async function typesetOnce(el){
  if (typeset_done.has(el)) return;
  typeset_done.add(el);
  await typeset(el);
}

async function typeset(root){
  try {
    const nodes = [...root.querySelectorAll('pre.mermaid')].filter(n => !n.dataset.processed);
    if (nodes.length) await mermaid.run({nodes});
  } catch (e) { console.warn('mermaid', e); }
  try {
    if (window.renderMathInElement) renderMathInElement(root, {
      delimiters: [{left:'$$', right:'$$', display:true}, {left:'\\(', right:'\\)', display:false}],
      throwOnError: false});
  } catch (e) { console.warn('katex', e); }
}

// ---------------------------------------------------------------- answering

function send(q, ans, badge){
  if (!ans || /=\s*(,|$)/.test(ans)) return false;   // never queue a half-filled answer
  const prompt = String(q.id).toUpperCase() + ' ' + (q.title || '') + ': ' + ans;
  if (!window.lavish){
    if (badge){ badge.textContent = 'no Bridge session'; badge.style.display = 'inline'; }
    return false;
  }
  window.lavish.queuePrompt(prompt, {tag: 'answer', text: prompt, element: badge || document.body,
    queueKey: q.id, data: {q: q.id, answer: ans}});
  if (badge){ badge.textContent = 'queued - press Send to Agent'; badge.style.display = 'inline'; }
  return true;
}

// A pick button answers the question outright. That is the whole reason the buttons are on the
// row: Ryan, 2026-09-13, wanted the board cleared without opening anything.
function pickBadge(row){
  let b = row.querySelector('.queued');
  if (!b){
    b = document.createElement('span');
    b.className = 'queued';
    b.style.cssText = 'color:var(--ok);font-size:11px;flex:none';
    row.appendChild(b);
  }
  return b;
}

function questionDetail(q){
  const d = document.createElement('div');
  d.className = 'qd';
  let h = q.from ? '<div class="from">from ' + esc(q.from) + '</div>' : '';
  if (q.context_md) h += '<div class="ctx"><div class="mdwrap">' + mdToHtml(q.context_md) +
                         '</div></div>';
  const f = document.createElement('form');
  let fh = '';
  if (q.options && q.options.length){
    fh += '<div class="optlist">';
    for (const o of q.options){
      const p = q.pick && q.pick === o.value;
      fh += '<label' + (p ? ' class="pick"' : '') + '><input type="radio" name="choice" value="' +
            esc(o.value) + '"' + (p ? ' checked' : '') + '> <b>' + esc(o.value) + '</b> ' +
            esc(o.label) + '</label>';
    }
    fh += '</div>';
    if (q.pick_why) fh += '<div class="from" style="margin-top:5px">Pick ' + esc(q.pick) + ' - ' +
                          esc(q.pick_why) + '</div>';
  }
  if (q.inputs && q.inputs.length){
    fh += '<div class="row">';
    for (const i of q.inputs)
      fh += '<span>' + esc(i.label || i.name) + '</span><input type="text" name="in_' +
            esc(i.name) + '" style="width:' + (i.width || 70) + 'px" placeholder="?">';
    fh += '</div>';
  }
  if (!(q.options || []).length && !(q.inputs || []).length)
    fh += '<div class="row"><textarea name="free" placeholder="your answer"></textarea></div>';
  fh += '<div class="row"><button type="submit" class="send">Queue answer</button>' +
        '<span class="queued"></span></div>';
  f.innerHTML = fh;
  f.setAttribute('data-lavish-question', q.id);
  f.addEventListener('submit', ev => {
    ev.preventDefault();
    const fd = new FormData(f);
    let ans = '';
    if (q.inputs && q.inputs.length)
      ans = q.inputs.map(i => (i.label || i.name).replace(/\s*=\s*$/, '') + '=' +
                              (fd.get('in_' + i.name) || '').trim()).join(', ');
    else if (q.options && q.options.length) ans = fd.get('choice') || '';
    else ans = (fd.get('free') || '').trim();
    send(q, ans, f.querySelector('.queued'));
  });
  d.innerHTML = h;
  d.appendChild(f);
  return d;
}

// "auto in 6h", drawn only when the question carries a clock, so a normal question looks exactly
// as it always did.
function autoTag(q){
  if (!q.auto || !q.auto_after) return '';
  const left = (new Date(q.auto_after) - new Date()) / 3600000;
  if (!isFinite(left)) return '';
  if (left <= 0) return '<span class="auto due">auto now</span>';
  return '<span class="auto">' + (left < 1 ? 'auto in ' + Math.max(1, Math.round(left * 60)) + 'm'
                                           : 'auto in ' + Math.round(left) + 'h') + '</span>';
}

const OVERDUE_H = 48;     // red means one thing on this page: waiting longer than this

function ageLabel(iso){
  const t = Date.parse(iso);
  if (!t) return '';
  const h = (Date.now() - t) / 3600000;
  if (h < 1) return 'new';
  if (h < 24) return Math.floor(h) + 'h';
  return Math.floor(h / 24) + 'd';
}

function isLate(iso){
  const t = Date.parse(iso);
  return !!t && (Date.now() - t) / 3600000 > OVERDUE_H;
}

function questionRow(q, ix){
  const row = document.createElement('div');
  row.className = 'qr' + (openQs.has(q.id) ? ' open' : '');
  row.title = String(q.id).toUpperCase() + ' - ' + (q.title || '');
  const opts = q.options || [];
  const late = isLate(q.created);
  row.innerHTML =
    '<span class="ix">' + ix + '</span>' +
    '<span class="t">' + esc(q.title) + '</span>' +
    '<span class="why">' + esc(q.pick_why || (q.inputs && q.inputs.length ? 'needs numbers'
                                                                          : 'needs your answer')) +
    '</span>' + autoTag(q) +
    '<span class="age' + (late ? ' late' : '') + '">' + esc(ageLabel(q.created)) + '</span>';
  const picks = document.createElement('span');
  picks.className = 'picks';
  if (opts.length && !(q.inputs || []).length){
    for (const o of opts.slice(0, 6)){
      const b = document.createElement('button');
      b.type = 'button';
      if (o.value === q.pick) b.className = 'rec';
      b.textContent = o.value;
      b.title = o.label || '';
      // stopPropagation: the row itself opens the detail, and a pick must not do both.
      b.addEventListener('click', ev => { ev.stopPropagation(); send(q, o.value, pickBadge(row)); });
      picks.appendChild(b);
    }
  }
  const more = document.createElement('span');
  more.className = 'more';
  more.textContent = opts.length && !(q.inputs || []).length ? '…' : 'open';
  picks.appendChild(more);
  row.appendChild(picks);
  return row;
}

function renderQuestions(){
  const host = document.getElementById('questions');
  const open = (state.questions || []).filter(q => !q.answered);
  document.getElementById('qcount').textContent = open.length;
  host.innerHTML = '';
  if (!open.length){ host.innerHTML = '<div class="empty">Nothing waiting on you.</div>'; return; }
  // Busiest project first, ungrouped last - the same order the mockups were reviewed in.
  const groups = new Map();
  for (const q of open){
    const g = q.group || 'no group';
    if (!groups.has(g)) groups.set(g, []);
    groups.get(g).push(q);
  }
  const order = [...groups.keys()].sort((a, b) =>
    (a === 'no group') - (b === 'no group') || groups.get(b).length - groups.get(a).length ||
    a.localeCompare(b));
  let ix = 0;
  for (const g of order){
    const h = document.createElement('div');
    h.className = 'ghead';
    h.innerHTML = '<b>' + esc(g) + '</b><span class="n">' + groups.get(g).length + '</span>';
    host.appendChild(h);
    for (const q of groups.get(g)){
      const row = questionRow(q, ++ix);
      host.appendChild(row);
      let detail = null;
      const toggle = () => {
        if (openQs.has(q.id)){
          openQs.delete(q.id);
          row.classList.remove('open');
          if (detail){ detail.remove(); detail = null; }
        } else {
          openQs.add(q.id);
          row.classList.add('open');
          detail = questionDetail(q);
          row.after(detail);
          typesetOnce(detail);
        }
      };
      row.addEventListener('click', toggle);
      if (openQs.has(q.id)){
        detail = questionDetail(q);
        row.after(detail);
        typesetOnce(detail);
      }
    }
  }
}

// ---------------------------------------------------------------- captain's call (2026-09-14)
//
// The list view answers many questions at a glance; this one answers one question at a time and
// deals the next card itself. It shares everything below the surface - the same send(), the same
// queuePrompt shape, the same board_watch.py path - so nothing downstream had to learn a new
// shape. Only the presentation differs.

// Urgency only reorders the stack. It never paints a card: red means one thing on this page.
const URGENT_RE = /\b(blocked|deadline|due|overdue|expires?|tonight|today|tomorrow)\b/i;
// The same words board.py's ACTION_ITEM_RE uses for "a human still has to go click something".
const ACTION_RE = /\b(dash|review|submit|saves?|saved|sign[\s-]?in|book|pay(?:s|ment|ing)?)\b/i;

let callMode = false;
let callIx = 0;
let callShownId = null;
const callSkipped = new Set();   // Esc'd this sitting: to the back of the stack, never answered
const callSent = new Set();      // answered here; hidden at once, before the 5 s poll agrees

// Deadline-tagged and BLOCKED first, then oldest first, then anything skipped this sitting.
// Nothing de-duplicates by group here: board.py already refuses a second open question per group,
// so the stack can never hold two from the same project to begin with.
function callDeck(){
  const open = (state.questions || []).filter(q => !q.answered && !callSent.has(q.id));
  const urgent = q => URGENT_RE.test((q.title || '') + ' ' + (q.context_md || '') + ' ' +
                                     (q.pick_why || '')) ? 0 : 1;
  open.sort((a, b) => urgent(a) - urgent(b) ||
                      (Date.parse(a.created) || 0) - (Date.parse(b.created) || 0));
  return open.filter(q => !callSkipped.has(q.id)).concat(open.filter(q => callSkipped.has(q.id)));
}

function callKind(q){
  if ((q.options || []).length > 1) return 'decision';
  if (ACTION_RE.test((q.title || '') + ' ' + (q.context_md || ''))) return 'action';
  return 'question';
}

function callAdvance(){ callShownId = null; renderCall(); }

function callCard(q){
  const card = document.createElement('div');
  card.className = 'cc';
  const opts = q.options || [], ins = q.inputs || [], multi = !!q.multi;

  const top = document.createElement('div');
  top.className = 'cctop';
  top.innerHTML = '<span class="tag ' + callKind(q) + '">' + callKind(q) + '</span>' +
                  '<span class="who">' + esc(q.from || q.group || '') + '</span>';
  card.appendChild(top);

  const h = document.createElement('h3');
  h.textContent = q.title || '';
  card.appendChild(h);

  if (q.context_md){
    const about = document.createElement('div');
    about.className = 'ctxrow';
    about.innerHTML = '<span class="k">about</span><span class="v"></span>';
    about.lastChild.innerHTML = mdToHtml(q.context_md);
    card.appendChild(about);
  }
  const d = document.createElement('div');
  d.className = 'ctxrow';
  d.innerHTML = '<span class="k">decide</span><span class="v">' +
    esc(q.pick_why || (ins.length ? 'needs numbers'
                                  : !opts.length ? 'needs your answer'
                                  : multi ? 'pick any that apply' : 'pick one')) + '</span>';
  card.appendChild(d);

  const f = document.createElement('form');
  f.setAttribute('data-lavish-question', q.id);

  if (opts.length){
    const list = document.createElement('div');
    list.className = 'ccopts';
    opts.forEach((o, i) => {
      const lab = document.createElement('label');
      lab.className = 'ccopt';
      // the leading digit is the keyboard shortcut, printed so he never has to count rows
      lab.innerHTML = '<span class="key">' + (i + 1) + '</span>' +
        '<input type="' + (multi ? 'checkbox' : 'radio') + '" name="choice" value="' +
        esc(o.value) + '">' +
        '<span class="bd"><b>' + esc(o.value) + '</b><em>' + esc(o.label || '') + '</em></span>' +
        (o.value === q.pick ? '<span class="rec">rec</span>' : '');
      list.appendChild(lab);
    });
    list.addEventListener('change', () => {
      for (const lab of list.children)
        lab.classList.toggle('on', lab.querySelector('input').checked);
    });
    f.appendChild(list);
  }

  if (ins.length){
    const row = document.createElement('div');
    row.className = 'ccrow';
    row.innerHTML = ins.map(i => '<span>' + esc(i.label || i.name) + '</span>' +
      '<input type="text" name="in_' + esc(i.name) + '" style="width:' + (i.width || 70) +
      'px" placeholder="?">').join('');
    f.appendChild(row);
  }

  // Always present, even under a full set of options: half of what this board asks him wants
  // words back, not a letter.
  const other = document.createElement('input');
  other.type = 'text';
  other.className = 'other';
  other.name = 'other';
  other.placeholder = (opts.length || ins.length) ? 'or answer in your own words'
                                                  : 'your answer';
  f.appendChild(other);

  const foot = document.createElement('div');
  foot.className = 'ccfoot';
  foot.innerHTML = '<button type="submit" class="send">Send answer</button>' +
                   '<span class="queued"></span>';
  f.appendChild(foot);

  f.addEventListener('submit', ev => {
    ev.preventDefault();
    const fd = new FormData(f);
    const note = String(fd.get('other') || '').trim();
    let ans = '';
    if (ins.length)
      ans = ins.map(i => (i.label || i.name).replace(/\s*=\s*$/, '') + '=' +
                         String(fd.get('in_' + i.name) || '').trim()).join(', ');
    else if (opts.length) ans = fd.getAll('choice').join(', ');   // multi: one Send, comma-joined
    if (note) ans = ans ? ans + ' - ' + note : note;
    if (!send(q, ans, f.querySelector('.queued'))) return;
    callSent.add(q.id);
    callSkipped.delete(q.id);
    card.classList.add('sent');
    setTimeout(callAdvance, 380);        // let the queued line land before the card leaves
  });

  card.appendChild(f);
  const hint = document.createElement('div');
  hint.className = 'cchint';
  hint.textContent = '1-9 pick · Enter send · Esc skip · ← back';
  card.appendChild(hint);
  return card;
}

function renderCall(){
  const deck = callDeck();
  const n = deck.length;
  document.getElementById('callcount').textContent =
    n ? n + ' item' + (n === 1 ? '' : 's') + ' wait on you' : 'nothing waits on you';
  const stack = document.getElementById('callstack');
  const host = document.getElementById('callcard');
  const nav = document.getElementById('callnav');
  // the pile thins as the stack empties, so the last card plainly looks like the last one
  stack.className = 'stack' + (n === 0 ? ' none' : n === 1 ? ' one' : n === 2 ? ' two' : '');
  if (!n){
    nav.hidden = true;
    if (callShownId === '(empty)') return;
    callShownId = '(empty)';
    host.innerHTML = '<div class="ccempty">All clear. Nothing waits on you right now.</div>';
    return;
  }
  nav.hidden = false;
  // Follow the question, not the index. A new BLOCKED item arriving on the 5 s poll reorders the
  // deck, and without this the card under a half-typed answer would be swapped for whatever else
  // landed on that index. callAdvance() clears callShownId first, so a deliberate move still wins.
  if (callShownId){
    const at = deck.findIndex(x => x.id === callShownId);
    if (at >= 0) callIx = at;
  }
  if (callIx >= n) callIx = 0;
  if (callIx < 0) callIx = n - 1;
  const q = deck[callIx];
  document.getElementById('callpos').textContent = 'card ' + (callIx + 1) + ' of ' + n;
  // Rebuilding the card on every 5 s poll would throw away whatever he is half-way through
  // typing - the same bug a lavish-axi reload causes. Only a different question redraws.
  if (callShownId === q.id && host.firstChild) return;
  callShownId = q.id;
  host.innerHTML = '';
  const card = callCard(q);
  host.appendChild(card);
  typesetOnce(card);
}

function callSkip(){
  const deck = callDeck();
  if (deck.length < 2) return;         // nothing to skip to
  callSkipped.add(deck[callIx].id);
  callAdvance();
}

function callBack(){ callIx -= 1; callAdvance(); }

function setCallMode(on){
  callMode = !!on;
  document.body.classList.toggle('call', callMode);
  document.getElementById('modebtn').textContent = callMode ? 'List' : 'Focus';
  if (callMode){ callShownId = null; renderCall(); }
}

// ---------------------------------------------------------------- right column

const STATE_LABEL = {working: 'working', waiting: 'waiting on you', done: 'done', error: 'error'};

function renderSessions(){
  const rows = state.sessions || [];
  document.getElementById('scount').textContent = rows.length;
  document.getElementById('sessions').innerHTML = rows.length ? rows.map(r => {
    const st = r.state || 'working';
    return '<div class="sess" title="' + esc(r.pane + ' - ' + (r.doing || '')) + '">' +
      '<span class="flag ' + esc(st) + '"></span>' +
      '<span class="nm">' + esc(r.pane) + '</span>' +
      '<span class="dg">' + esc(r.doing || STATE_LABEL[st] || '') + '</span>' +
      '<span class="ctx">' + esc(r.note || '') + '</span></div>';
  }).join('') : '<div class="empty">No panes running.</div>';
}

// One clipped line each; clicking one opens it in place. Cheaper than a <details> per row when
// there are 145 of them, and it keeps the list scannable by date.
function expandable(host, items, key, render){
  host.innerHTML = '';
  for (const it of items){
    const id = key(it);
    const row = document.createElement('div');
    row.className = 'srow' + (openBlocks.has(id) ? ' open' : '');
    row.innerHTML = render(it);
    host.appendChild(row);
    let body = null;
    const build = () => {
      const b = document.createElement('div');
      b.className = 'sbody';
      b.innerHTML = mdToHtml(it.body_md || it.text || '');
      return b;
    };
    row.addEventListener('click', () => {
      if (openBlocks.has(id)){
        openBlocks.delete(id); row.classList.remove('open');
        if (body){ body.remove(); body = null; }
      } else {
        openBlocks.add(id); row.classList.add('open');
        body = build(); row.after(body); typesetOnce(body);
      }
    });
    if (openBlocks.has(id)){ body = build(); row.after(body); typesetOnce(body); }
  }
}

function renderShow(){
  const items = state.show || [];
  document.getElementById('showcount').textContent = items.length;
  const host = document.getElementById('show');
  if (!items.length){ host.innerHTML = '<div class="empty">Nothing to look at.</div>'; return; }
  expandable(host, items, b => 'show:' + (b.id || b.caption),
    b => '<span class="flag done"></span><span class="cap">' + esc(b.caption || '(no caption)') +
         (b.for ? ' (' + esc(String(b.for).toUpperCase()) + ')' : '') + '</span>');
}

function renderDone(){
  const items = state.done || [];
  document.getElementById('donecount').textContent = items.length;
  const host = document.getElementById('done');
  if (!items.length){ host.innerHTML = '<div class="empty">Nothing finished yet.</div>'; return; }
  expandable(host, items, d => 'done:' + (d.ts || '') + (d.text || '').slice(0, 24),
    d => '<span class="cap">' + esc(d.text) + '</span><span class="when">' +
         esc(String(d.ts || '').replace('T', ' ').slice(5, 16)) + '</span>');
}

// target="_top": Bridge is itself rendered inside a Bridge artifact iframe, so a plain link would
// open the next Bridge session INSIDE that iframe - two nested chromes, two Conversation columns
// (Ryan, 2026-09-10, screenshot). Every hub link must navigate the top-level window instead.
let linksSig = '';
function renderLinks(){
  const links = state.links || [];
  // The 5 s poll must not rebuild this row: doing so re-measures the tabs and shuts a More menu
  // Ryan had just opened. Only a genuinely different link set touches the DOM.
  const sig = JSON.stringify(links);
  if (sig === linksSig) return;
  linksSig = sig;
  const home = links.find(l => l.home);
  const hb = document.getElementById('homelink');
  hb.hidden = !(home && home.url);
  if (home && home.url) hb.href = home.url;
  document.getElementById('links').innerHTML = links.filter(l => !l.home && !l.alt).map(l =>
    '<a target="_top" rel="noopener" class="' + (l.live ? 'live' : '') + '" href="' +
    esc(l.url) + '">' + esc(l.title) + '</a>').join('');
  layoutTabs();
}

// The bar is one row and stays one row: whatever does not fit goes into the More menu rather than
// wrapping onto a second one. Measured, not guessed - tab widths are titles, so no fixed count
// would hold. Two passes because showing the More button itself takes room away from the tabs.
function layoutTabs(){
  const host = document.getElementById('links');
  const more = document.getElementById('tabmore');
  const menu = document.getElementById('moremenu');
  const tabs = Array.from(host.children);
  tabs.forEach(t => { t.hidden = false; });
  closeMore();
  more.hidden = true;
  menu.innerHTML = '';
  if (!tabs.length) return;
  const widths = tabs.map(t => t.offsetWidth);
  const total = widths.reduce((a, b) => a + b, 0);
  if (total <= host.clientWidth) return;        // everything fits; no menu at all
  more.hidden = false;
  const room = host.clientWidth - 4;            // the More button sits outside .links, not in it
  let used = 0, keep = 0;
  for (let i = 0; i < tabs.length; i++){
    if (used + widths[i] > room) break;
    used += widths[i];
    keep = i + 1;
  }
  // keep may legitimately be 0: on a phone even the first title does not fit, and a tab cut
  // off mid-word looks broken where an empty strip and a "More 7" does not.
  const folded = tabs.slice(keep);
  folded.forEach(t => { menu.appendChild(t.cloneNode(true)); t.hidden = true; });
  document.getElementById('moren').textContent = folded.length;
}

function closeMore(){
  document.getElementById('moremenu').hidden = true;
  document.getElementById('tabmore').classList.remove('open');
  document.getElementById('morebtn').setAttribute('aria-expanded', 'false');
}

function renderAll(){
  const open = (state.questions || []).filter(q => !q.answered).length;
  const c = document.getElementById('waiting');
  // <i> so the phone rule can drop the words and keep the number (see the 640px media query)
  c.innerHTML = '<b>' + open + '</b> <i>waiting on you</i>';
  c.className = 'count' + (open ? '' : ' zero');
  const h = state.header || {};
  // the updated stamp used to trail the links; on a single row it shares the note's slot instead
  const when = String(state.updated || '').replace('T', ' ').slice(5, 16);
  document.getElementById('note').textContent =
    [h.usage, h.note, when].filter(Boolean).join(' - ');
  renderLinks(); renderQuestions(); renderSessions(); renderShow(); renderDone();
  if (callMode) renderCall();
}

// Keyboard only bites in call mode, and only ever on the card that is actually on screen.
document.addEventListener('keydown', ev => {
  if (!callMode || ev.ctrlKey || ev.metaKey || ev.altKey) return;
  const form = document.querySelector('#callcard form');
  if (!form) return;
  const t = ev.target;
  const typing = t && (t.tagName === 'TEXTAREA' || (t.tagName === 'INPUT' && t.type === 'text'));
  if (ev.key === 'Enter'){ ev.preventDefault(); form.requestSubmit(); return; }
  if (typing) return;                 // digits belong to the box he is typing in, not the picker
  if (ev.key === 'Escape'){ ev.preventDefault(); callSkip(); return; }
  if (ev.key === 'ArrowLeft'){ ev.preventDefault(); callBack(); return; }
  if (/^[1-9]$/.test(ev.key)){
    const box = form.querySelectorAll('.ccopt input')[+ev.key - 1];
    if (!box) return;
    ev.preventDefault();
    box.checked = box.type === 'checkbox' ? !box.checked : true;
    box.dispatchEvent(new Event('change', {bubbles: true}));
  }
});

document.getElementById('morebtn').addEventListener('click', ev => {
  ev.stopPropagation();
  const menu = document.getElementById('moremenu');
  const opening = menu.hidden;
  closeMore();
  if (opening){
    menu.hidden = false;
    document.getElementById('tabmore').classList.add('open');
    document.getElementById('morebtn').setAttribute('aria-expanded', 'true');
  }
});
document.addEventListener('click', ev => {
  if (!document.getElementById('tabmore').contains(ev.target)) closeMore();
});
// A tab's width is its title, so the same set folds differently at every width. Re-measure on
// resize - and once after load, because a webfont swap moves every one of them.
window.addEventListener('resize', layoutTabs);
window.addEventListener('load', layoutTabs);

document.getElementById('modebtn').addEventListener('click', () => setCallMode(!callMode));
document.getElementById('callprev').addEventListener('click', callBack);
document.getElementById('callskip').addEventListener('click', callSkip);

// Which mode a fresh load opens in. ?mode= wins; otherwise state.mode, set by `board mode call`.
// localStorage is deliberately not used: inside Bridge this page runs in a sandboxed iframe whose
// origin is `null`, and every storage accessor there throws, so the button's own choice cannot
// outlive a reload (measured 2026-09-14 - it silently fell back to the list every time). The
// board's own state is the only thing on this page that does survive one.
let startMode = String(state.mode || 'list');
if (/[?&]mode=call\b/.test(location.search)) startMode = 'call';
else if (/[?&]mode=list\b/.test(location.search)) startMode = 'list';
setCallMode(startMode === 'call');

// state.json is the source of truth on disk; state.js is the same object as a one-line script and
// is what the page actually reads. `fetch('state.json')` is deliberately NOT used: inside lavish-axi
// the artifact runs in a sandboxed iframe whose origin is opaque, so the fetch is CORS-refused
// even though the server answers 200, and `location.origin` still reads as the real origin there,
// so the sandbox cannot be detected up front. A <script> load is not CORS-checked, works the same
// over http:// and file://, and needs no branch.
function readState(){
  return new Promise((res, rej) => {
    const s = document.createElement('script');
    s.src = 'state.js?t=' + Date.now();
    s.onload = () => { s.remove(); res(window.__boardState); };
    s.onerror = () => { s.remove(); rej(new Error('state.js')); };
    document.head.appendChild(s);
  });
}

async function tick(){
  try {
    const s = await readState();
    if (s && s.updated !== state.updated){ state = s; renderAll(); }
  } catch (e) { /* server down, or a half-written file: try again in 5 s */ }
}

renderAll();
tick();                     // state.js may have been cached; get the live copy straight away
setInterval(tick, POLL_MS);
"""


def write_html():
    """The page skeleton. It holds no board content at all - CSS, the renderer, and nothing else -
    so it is byte-identical from one save to the next and lavish-axi has no reason to reload the
    iframe. All content arrives through state.js / state.json."""
    os.makedirs(BOARD, exist_ok=True)
    page = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<!-- podium-own-home: this page draws its own Home button in its top bar, so the Bridge chrome
     must not reserve a 40px strip above the iframe for one. patch_lavish_brand.py reads this
     marker out of the artifact's first 8KB. -->
<title>Bridge</title>
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/katex@0.16.11/dist/katex.min.css">
<style>{CSS}</style>
<script src="https://cdn.jsdelivr.net/npm/marked@14.1.3/marked.min.js"></script>
<script src="https://cdnjs.cloudflare.com/ajax/libs/dompurify/3.1.6/purify.min.js"
        integrity="sha512-jB0TkTBeQC9ZSkBqDhdmfTv1qdfbWpGE72yJ/01Srq6hEzZIz2xkz1e57p9ai7IeHMwEG7HpzG6NdptChif5Pg=="
        crossorigin="anonymous"></script>
<script src="https://cdn.jsdelivr.net/npm/katex@0.16.11/dist/katex.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/katex@0.16.11/dist/contrib/auto-render.min.js"></script>
</head>
<body>

<div class="bar">
  <span class="brand">Bridge</span>
  <a class="homelink" id="homelink" href="#" target="_top" rel="noopener" title="Bridge home"
     hidden><svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor"
     stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 11.5 12 4l9 7.5"/>
     <path d="M5.5 10v9a1 1 0 0 0 1 1H9.5v-6h5v6H17.5a1 1 0 0 0 1-1v-9"/></svg><span>Home</span></a>
  <nav class="links" id="links"></nav>
  <div class="tabmore" id="tabmore" hidden>
    <button type="button" class="morebtn" id="morebtn" aria-haspopup="menu"
            aria-expanded="false">More <b id="moren">0</b></button>
    <div class="moremenu" id="moremenu" hidden></div>
  </div>
  <span class="count" id="waiting"><b>0</b> <i>waiting on you</i></span>
  <button type="button" class="modebtn" id="modebtn">Focus</button>
  <span class="stamp" id="note"></span>
</div>

<noscript>
<div class="empty">JavaScript is off, so Bridge is empty. The whole thing in plain text is at
<a href="board-plain.html">board-plain.html</a>. Turn JavaScript on for the diagrams, the
equations, and the answer forms.</div>
</noscript>

<div class="grid">

  <section class="col">
    <div class="colhead"><h2>Needs you</h2><span class="n" id="qcount">0</span>
      <span class="hint">grouped by project &middot; pick on the row</span></div>
    <div class="colbody" id="questions"></div>
  </section>

  <section class="col">
    <div class="colhead"><h2>Panes</h2><span class="n" id="scount">0</span></div>
    <div class="colbody">
      <div id="sessions"></div>
      <div class="sub">Show &middot; <span id="showcount">0</span></div>
      <div id="show"></div>
      <div class="sub">Done today &middot; <span id="donecount">0</span></div>
      <div id="done"></div>
    </div>
  </section>

</div>

<!-- Captain's call. Same page, second mode: the Focus button in the bar and ?mode=call both land
     here, and .grid hides while this is up. -->
<div class="callwrap" id="callwrap">
  <div class="callin">
    <div class="callhead">
      <h2>Captain's call</h2>
      <span class="n" id="callcount">0 items wait on you</span>
    </div>
    <div class="stack" id="callstack"><div id="callcard"></div></div>
    <div class="callnav" id="callnav">
      <span class="pos" id="callpos"></span>
      <button type="button" id="callprev">&larr; Back</button>
      <button type="button" id="callskip">Skip &rarr;</button>
    </div>
  </div>
</div>

<script src="state.js"></script>
<script type="module">
import mermaid from "https://cdn.jsdelivr.net/npm/mermaid@11.15.0/dist/mermaid.esm.min.mjs";
mermaid.initialize({{startOnLoad: false, theme: "neutral", securityLevel: "strict"}});
window.mermaid = mermaid;
{JS}
</script>
</body>
</html>
"""
    # Only touch the file when the skeleton itself actually changed. Rewriting identical bytes
    # still bumps the mtime, and that alone is enough to make lavish-axi reload the iframe.
    if os.path.exists(HTML) and open(HTML, encoding="utf-8").read() == page:
        return HTML
    tmp = HTML + ".tmp"
    open(tmp, "w", encoding="utf-8").write(page)
    _replace(tmp, HTML)
    return HTML


# ---------------------------------------------------------------- calm page (board-calm.html)
#
# Option A "Calm inbox" from the 2026-10-07 Bridge redesign (warm stone, white cards, sage accent,
# one card at a time). It is a SECOND page beside board.html, not a replacement: the old page is
# still the default and nothing above this block changed to make room for it. Swapping the default
# is Ryan's call. Same contract as the old page where it matters:
#
#   - state arrives through <script src="state.js"> and a 5 s poll that only re-renders when
#     state.updated changes (no fetch, no storage: the lavish-axi iframe is sandboxed, null origin);
#   - an answer is window.lavish.queuePrompt("Q5 title: answer", {data: {q, answer}}), byte for byte
#     what send() does in board.html, so board_watch.py parses it the same way;
#   - the skeleton is content-free (no timestamp, no counter), so it never changes between saves
#     and lavish-axi never reloads the iframe under someone who is typing.
#
# Mapping from the state (which has no "flows" or "topics"): Decide = open questions; kind pill =
# the question's group (or its optional `kind`); Later = client-side push to the back; Watch =
# sessions that are waiting or in error, plus running sessions and the latest Done lines, with the
# fleet line; Learn = a quiet pointer to the Tutor page (built on another branch).

CALM_CSS = r"""
:root{
  --bg:#F6F4EF; --card:#FFFFFF; --text:#1F2328; --muted:#6B6F76;
  --accent:#4F7A63; --accent-ink:#3A6049; --accent-soft:#E3ECE6; --on-accent:#FFFFFF;
  --warn:#B5523B; --amber:#8A6414; --line:#E7E3DA;
  --shadow:0 1px 2px rgba(31,35,40,.04),0 12px 32px rgba(31,35,40,.06);
  --sans:"Inter",system-ui,-apple-system,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;
  --mono:ui-monospace,SFMono-Regular,Consolas,"Liberation Mono",monospace;
}
:root[data-theme="dark"]{
  --bg:#121417; --card:#1A1D21; --text:#E8E6E1; --muted:#9A9EA5;
  --accent:#8DBFA2; --accent-ink:#8DBFA2; --accent-soft:#1F2A24; --on-accent:#0F1A14;
  --warn:#E08A70; --amber:#D9B15A; --line:#262A2F; --shadow:none;
}
[hidden]{display:none!important}
*{box-sizing:border-box}
html{-webkit-text-size-adjust:100%}
body{margin:0;background:var(--bg);color:var(--text);font-family:var(--sans);font-size:18px;line-height:1.5;min-height:100vh;display:flex;flex-direction:column}
button{font:inherit;color:inherit;cursor:pointer}
button:focus-visible,input:focus-visible,textarea:focus-visible,a:focus-visible{outline:3px solid var(--accent);outline-offset:2px}

/* header */
.top{display:grid;grid-template-columns:1fr auto 1fr;align-items:center;gap:16px;padding:16px 40px 0}
.lhs{font-size:16px;color:var(--muted);font-weight:500;display:flex;flex-wrap:wrap;align-items:baseline;gap:2px 14px}
.brand{font-weight:600;color:var(--text);font-size:17px}
.homelink{color:var(--muted);text-decoration:underline;text-underline-offset:3px;font-size:15px}
.homelink:hover{color:var(--text)}
.tabs{display:flex;gap:6px}
.tab{border:1px solid transparent;background:transparent;border-radius:999px;padding:9px 20px;font-size:17px;font-weight:500;color:var(--muted)}
.tab b{font-weight:600;margin-left:6px;font-variant-numeric:tabular-nums}
.tab b:empty{display:none}
.tab:hover{color:var(--text)}
.tab[aria-selected="true"]{background:var(--accent-soft);color:var(--accent-ink);font-weight:600}
.theme{justify-self:end;border:1px solid var(--line);background:transparent;border-radius:999px;padding:8px 18px;font-size:15px;font-weight:500;color:var(--muted)}
.theme:hover{color:var(--text)}

/* layout */
main{flex:1;padding:44px 40px 40px}
.layout{display:grid;grid-template-columns:minmax(170px,280px) minmax(0,760px) minmax(170px,300px);gap:40px;justify-content:center;align-items:start}
.side{padding-top:14px;color:var(--muted);overflow-wrap:anywhere}
.side:empty{visibility:hidden}
.side h2{font-size:14px;font-weight:600;margin:0 0 14px;color:var(--muted)}
.deadlines{list-style:none;margin:0;padding:0;display:flex;flex-direction:column;gap:18px}
.deadlines .when{display:block;font-size:14px;font-weight:600;color:var(--text)}
.deadlines .what{display:block;font-size:15px;line-height:1.45;color:var(--muted);margin-top:2px}
.queue{list-style:none;margin:0;padding:0;display:flex;flex-direction:column;gap:4px}
.queue button{display:block;border:0;background:transparent;text-align:left;border-radius:10px;padding:10px 12px;margin-left:-12px;width:calc(100% + 12px);font-size:16px;line-height:1.4;color:var(--muted)}
.queue button:hover{background:var(--accent-soft);color:var(--text)}
.side-sec+.side-sec{margin-top:30px}
.ans-toggle{border:0;background:transparent;padding:4px 0;font-size:14px;font-weight:600;color:var(--muted);text-decoration:underline;text-underline-offset:3px}
.ans-list{list-style:none;margin:12px 0 0;padding:0;display:flex;flex-direction:column;gap:12px;font-size:15px;line-height:1.45}
.ans-list b{display:block;font-weight:600;color:var(--text)}
.side-toggle{display:none}

/* stage and cards */
.stage{min-width:0;position:relative}
.toast{position:absolute;top:-38px;left:0;right:0;margin:0;text-align:center;font-size:16px;font-weight:500;color:var(--accent-ink)}
.toast.bad{color:var(--warn)}
.stack{position:relative;margin-bottom:34px}
.stack.e1::before,.stack.e2::before,.stack.e2::after{content:"";position:absolute;background:var(--card);border:1px solid var(--line);border-radius:16px;height:70px;z-index:0}
.stack.e1::before,.stack.e2::before{left:20px;right:20px;bottom:-12px}
.stack.e2::after{left:40px;right:40px;bottom:-24px;opacity:.7}
.card{position:relative;z-index:1;background:var(--card);border:1px solid var(--line);border-radius:16px;padding:40px 48px;box-shadow:var(--shadow);animation:rise .28s ease both;overflow-wrap:anywhere}
.card.leaving{animation:none;opacity:0;transform:translateY(-14px);transition:opacity .2s ease,transform .2s ease}
@keyframes rise{from{opacity:0;transform:translateY(10px)}to{opacity:1;transform:none}}
.meta{display:flex;flex-wrap:wrap;gap:6px 18px;align-items:center;font-size:14px;color:var(--muted);margin-bottom:16px}
.meta:empty{display:none}
.kind{background:var(--accent-soft);color:var(--accent-ink);font-weight:600;border-radius:999px;padding:4px 12px}
h1,h2.title{font-weight:600;letter-spacing:-.02em;text-wrap:balance}
h1.title{font-size:38px;line-height:1.15;margin:0 0 16px}
.oneline{margin:-4px 0 16px;font-size:19px;font-weight:500;color:var(--accent-ink)}
.ctx{font-size:21px;line-height:1.5;margin:0 0 22px;color:var(--text)}
.detbtn{display:block;border:0;background:transparent;padding:4px 0;margin:-10px 0 18px;font-size:16px;font-weight:500;color:var(--accent-ink);text-decoration:underline;text-underline-offset:3px}
.det{margin:0 0 22px;padding:16px 20px;border:1px solid var(--line);border-radius:12px;font-size:16px;line-height:1.55;max-height:380px;overflow:auto}
.det>*:first-child{margin-top:0}
.det>*:last-child{margin-bottom:0}
.det pre.code,.det pre.mermaid{background:var(--bg);padding:10px 12px;border-radius:8px;overflow-x:auto;white-space:pre-wrap;font:13px/1.5 var(--mono)}
.det code{font:13px var(--mono)}
.det table.md{border-collapse:collapse;display:block;overflow-x:auto}
.det table.md td,.det table.md th{border:1px solid var(--line);padding:4px 10px}
.det img.fig{max-width:100%;height:auto}
.pick{background:var(--accent-soft);border-radius:12px;padding:20px 24px;margin:0 0 22px}
.pick .lbl{font-size:14px;font-weight:600;color:var(--accent-ink);display:block;margin-bottom:4px}
.pick .p{font-size:18px;font-weight:600;display:block;margin-bottom:2px}
.pick .w{font-size:18px;display:block}
.opts{display:flex;flex-direction:column;gap:10px}
.opt{width:100%;min-height:62px;border-radius:12px;border:1.5px solid var(--line);background:transparent;padding:14px 24px;font-size:19px;font-weight:600;text-align:left;color:var(--text);transition:background .15s,border-color .15s}
.opt:hover{border-color:var(--accent);background:var(--accent-soft)}
.opt.primary{background:var(--accent);border-color:var(--accent);color:var(--on-accent)}
.opt.primary:hover{filter:brightness(1.06)}
.opt[aria-pressed="true"]{background:var(--accent-soft);border-color:var(--accent)}
.opt.primary[aria-pressed="true"]{background:var(--accent);color:var(--on-accent)}
.opt:disabled{opacity:.5;cursor:default}
.ins{display:flex;flex-direction:column;gap:12px;margin:0 0 14px}
.ins label{display:flex;align-items:center;gap:14px;font-size:17px;color:var(--muted)}
.ins label span{flex:none;min-width:70px}
.field,.freebox{width:100%;border-radius:12px;border:1.5px solid var(--line);background:var(--bg);color:var(--text);font:inherit;font-size:18px;padding:12px 16px}
.freebox{display:block;min-height:110px;resize:vertical;line-height:1.5;margin:0 0 14px}
.own{margin-top:14px}
.later,.quiet{display:block;margin:12px auto 0;border:0;background:transparent;font-size:17px;color:var(--muted);padding:8px 14px;border-radius:10px;text-decoration:underline;text-underline-offset:4px;text-decoration-color:var(--line)}
.later:hover,.quiet:hover{color:var(--text)}
.keys{display:flex;justify-content:center;gap:26px;margin-top:2px;font-size:14px;color:var(--muted)}
.keys span{display:inline-flex;align-items:center;gap:8px}
kbd{font-family:var(--sans);font-size:13px;font-weight:600;color:var(--text);background:var(--bg);border:1px solid var(--line);border-bottom-width:2px;border-radius:7px;padding:2px 9px}
.progress{display:flex;flex-direction:column;align-items:center;gap:10px;margin-top:22px;font-size:14px;color:var(--muted)}
.dots{display:flex;gap:9px;flex-wrap:wrap;justify-content:center}
.dot{width:9px;height:9px;border-radius:50%;background:var(--line)}
.dot.done{background:var(--accent)}
.dot.now{background:var(--card);box-shadow:0 0 0 2px var(--accent)}
.newq{align-self:center;border:1px solid var(--line);border-radius:999px;padding:5px 14px;font-size:14px;color:var(--muted)}

/* all clear */
.clear{text-align:center}
.clear h1.title{margin-bottom:12px}
.clear .ctx{color:var(--muted);margin-bottom:32px}
.done-list{list-style:none;margin:0;padding:0;text-align:left;border-top:1px solid var(--line)}
.done-list li{padding:16px 0;border-bottom:1px solid var(--line);font-size:18px}
.done-list b{display:block;font-weight:600}
.done-list span{color:var(--muted);font-size:16px}

/* watch */
.flow-list{display:flex;flex-direction:column;gap:20px}
.flow{padding:32px 40px}
.state{display:inline-flex;align-items:center;gap:10px;font-size:15px;font-weight:600;margin-bottom:10px}
.sdot{width:11px;height:11px;border-radius:50%;background:var(--warn)}
.state.amber{color:var(--amber)}
.state.amber .sdot{background:var(--amber)}
.state.red{color:var(--warn)}
h2.flow-title{font-size:26px;line-height:1.25;margin:0 0 8px;font-weight:600;letter-spacing:-.02em}
.flow p{margin:0;font-size:18px}
.flow .sub{margin-top:10px;font-size:14px;color:var(--muted)}
.fold{margin-top:26px}
.fold:first-child{margin-top:0}
.fold-btn{width:100%;display:flex;justify-content:space-between;align-items:center;border:1px solid var(--line);background:transparent;border-radius:12px;padding:16px 22px;font-size:17px;font-weight:500;color:var(--muted)}
.fold-btn:hover{color:var(--text)}
.fold-btn .w{font-size:14px}
.fold-body{padding:18px 6px 0;color:var(--muted);font-size:16px}
.fold-body h3{font-size:14px;font-weight:600;margin:0 0 8px;color:var(--muted)}
.fold-body ul{margin:0 0 18px;padding:0;list-style:none;display:flex;flex-direction:column;gap:8px}
.fold-body li{overflow-wrap:anywhere}
.fold-body b{font-weight:600;color:var(--text)}
.fleet{margin:26px 0 0;text-align:center;font-size:15px;color:var(--muted)}
.cmd{font-family:var(--mono);font-size:.88em;background:var(--bg);border:1px solid var(--line);border-radius:7px;padding:2px 8px}
.btn{display:inline-flex;align-items:center;min-height:52px;border-radius:12px;border:1.5px solid var(--accent);background:var(--accent);color:var(--on-accent);padding:10px 24px;font-size:17px;font-weight:600;text-decoration:none}
.btn:hover{filter:brightness(1.06)}

footer{padding:6px 24px 26px;text-align:center;font-size:13px;color:var(--muted)}

@media (max-width:1440px){
  .layout{grid-template-columns:minmax(170px,210px) minmax(0,760px) minmax(170px,230px);gap:28px}
}
@media (max-width:1000px){
  .layout{grid-template-columns:minmax(0,1fr);max-width:760px;margin:0 auto;gap:20px}
  .side-toggle{display:block;margin:6px auto 0;border:1px solid var(--line);background:transparent;border-radius:999px;padding:10px 22px;font-size:16px;font-weight:500;color:var(--muted)}
  .layout:not(.open) .side{display:none}
  .layout.open .side{padding:0 4px}
  .layout.open .side:empty{display:none}
  .side-sec+.side-sec{margin-top:24px}
}
@media (max-width:700px){
  body{font-size:17px}
  .top{grid-template-columns:1fr auto;grid-template-areas:"lhs theme" "tabs tabs";padding:16px 16px 4px;gap:12px}
  .lhs{grid-area:lhs}
  .theme{grid-area:theme}
  .tabs{grid-area:tabs;justify-content:space-between}
  .tab{flex:1;padding:10px 6px;font-size:16px}
  main{padding:34px 16px 24px}
  .card{padding:24px 22px}
  .flow{padding:24px 22px}
  h1.title{font-size:30px}
  .ctx{font-size:19px}
  .opt{font-size:18px;padding:12px 18px}
  .keys{display:none}
  .pick{padding:16px 18px}
  .ins label{flex-direction:column;align-items:stretch;gap:6px}
}
@media (prefers-reduced-motion:reduce){
  *,*::before,*::after{animation:none!important;transition:none!important}
}
"""

CALM_JS = r"""
(function(){
'use strict';
// Where the Learn pill points once the Tutor page exists (built on another branch). Empty = no link.
const TUTOR_URL = '';
const POLL_MS = 5000;
const URGENT_RE = /\b(blocked|deadline|due|overdue|expires?|tonight|today|tomorrow)\b/i;
let state = window.__boardState ||
            {updated: null, header: {}, links: [], questions: [], show: [], sessions: [], done: []};
// False until a state.js has really loaded. An empty placeholder state must never read as "All clear".
let loaded = !!window.__boardState;

const $ = s => document.querySelector(s);
const esc = s => String(s == null ? '' : s).replace(/[&<>"']/g,
  c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const reduce = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
const qp = new URLSearchParams(location.search);

// ---- view state. A plain variable, never storage: the sandboxed iframe has a null origin and every
// storage accessor throws there (same constraint board.html documents).
let tab = ['decide', 'watch', 'learn'].includes(qp.get('tab')) ? qp.get('tab') : 'decide';
let dark = qp.get('theme') ? qp.get('theme') === 'dark'
                           : !!(window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches);
const laterSeq = [];          // ids pushed to the back this sitting, oldest push first
let frontId = null;           // "After this" jump: show this one next
const sentIds = new Set();    // queued this sitting; hidden at once, before the poll agrees
const sentList = [];          // {id, title, label} for the answered list until the board agrees
let sentCount = 0;
let shownId = null, busy = false, toastTimer = null;
let ansOpen = false, foldOpen = false;
const openDet = new Set(), openOwn = new Set();

// ---- text helpers
function clip(t, cap){
  t = String(t || '').replace(/[`*]+/g, '').replace(/\s+/g, ' ').trim();
  if (t.length <= cap) return t;
  const cut = Math.max(t.lastIndexOf('. ', cap), t.lastIndexOf('? ', cap), t.lastIndexOf('! ', cap));
  return cut >= 40 ? t.slice(0, cut + 1) : t.slice(0, cap - 3).trimEnd() + '...';
}

// The first plain paragraph of a context_md, outside any fence, markdown stripped. The full
// markdown stays one click away under Details.
function ctxLine(md){
  const src = String(md || '').replace(/```[\s\S]*?```/g, '\n\n').replace(/\$\$[\s\S]*?\$\$/g, '\n\n');
  for (const para of src.split(/\n\s*\n/)){
    let t = para.trim();
    if (!t || /^(\||!\[|#{1,6}\s*$)/.test(t)) continue;
    t = t.replace(/!\[[^\]]*\]\([^)]*\)/g, '').replace(/\[([^\]]+)\]\([^)]*\)/g, '$1')
         .replace(/^[#>\-*\s]+/gm, '').replace(/[`*_]+/g, '').replace(/\s+/g, ' ').trim();
    if (t) return clip(t, 260);
  }
  return '';
}

function mdToHtml(src){
  src = String(src == null ? '' : src);
  if (!window.marked || !window.DOMPurify) return '<pre class="code">' + esc(src) + '</pre>';
  let html = window.marked.parse(src, {gfm: true});
  html = html.replace(/<pre><code[^>]*>/g, '<pre class="code">').replace(/<\/code><\/pre>/g, '</pre>')
             .replace(/<img /g, '<img class="fig" ').replace(/<table>/g, '<table class="md">');
  return window.DOMPurify.sanitize(html);
}

// "auto in 6h" - drawn only when the question carries a clock, exactly as board.html does.
function autoTag(q){
  if (!q.auto || !q.auto_after) return '';
  const left = (new Date(q.auto_after) - new Date()) / 3600000;
  if (!isFinite(left)) return '';
  if (left <= 0) return '<span>auto now</span>';
  return '<span>' + (left < 1 ? 'auto in ' + Math.max(1, Math.round(left * 60)) + 'm'
                              : 'auto in ' + Math.round(left) + 'h') + '</span>';
}

// ---- the deck
function openQs(){ return (state.questions || []).filter(q => !q.answered && !sentIds.has(q.id)); }
// A question that carries its own `due` is the most urgent thing here; a deadline word in its text
// comes next; everything else keeps arrival order.
function urgent(q){
  if (q.due) return 0;
  return URGENT_RE.test((q.title || '') + ' ' + (q.context_md || '') + ' ' + (q.pick_why || '')) ? 1 : 2;
}
function deck(){
  const open = openQs();
  for (let i = laterSeq.length - 1; i >= 0; i--)
    if (!open.some(q => q.id === laterSeq[i])) laterSeq.splice(i, 1);
  const base = open.filter(q => !laterSeq.includes(q.id));
  base.sort((a, b) => urgent(a) - urgent(b) || (Date.parse(a.created) || 0) - (Date.parse(b.created) || 0));
  const d = base.concat(laterSeq.map(id => open.find(q => q.id === id)));
  if (frontId){
    const i = d.findIndex(q => q.id === frontId);
    if (i > 0) d.unshift(d.splice(i, 1)[0]);
  }
  return d;
}
function byId(id){ return (state.questions || []).find(q => q.id === id); }
// What the card on screen was drawn from: the id plus a hash of everything its controls come from, so a
// question edited in place under the same id redraws instead of leaving stale buttons live.
function qkey(q){
  const s = JSON.stringify([q.title, q.options || [], q.pick || '', q.inputs || [], !!q.multi]);
  let h = 5381;
  for (let i = 0; i < s.length; i++) h = ((h << 5) + h + s.charCodeAt(i)) | 0;
  return q.id + '#' + (h >>> 0).toString(36);
}
// A half-typed answer: a focused field, any non-empty field, or a multi-select with a choice made.
function dirty(card){
  if (!card) return false;
  const a = document.activeElement;
  if (a && card.contains(a) && (a.tagName === 'TEXTAREA' || a.tagName === 'INPUT')) return true;
  if ([...card.querySelectorAll('textarea,input')].some(e => String(e.value || '').trim() !== '')) return true;
  return !!card.querySelector('[data-multi][aria-pressed="true"]');
}

// ---- answering. This is board.html's send(), kept identical so board_watch.py needs no change.
function send(q, ans, el){
  if (!ans || /=\s*(,|$)/.test(ans)) return false;   // never queue a half-filled answer
  const prompt = String(q.id).toUpperCase() + ' ' + (q.title || '') + ': ' + ans;
  if (!window.lavish) return false;
  window.lavish.queuePrompt(prompt, {tag: 'answer', text: prompt, element: el || document.body,
    queueKey: q.id, data: {q: q.id, answer: ans}});
  return true;
}
function labelFor(q, ans){
  const o = (q.options || []).find(x => x.value === ans);
  return (o && (o.label || o.value)) || ans;
}
function say(t, bad){
  const el = $('#toast');
  el.textContent = t;
  el.className = 'toast' + (bad ? ' bad' : '');
  clearTimeout(toastTimer);
  if (t) toastTimer = setTimeout(() => { el.textContent = ''; }, 4200);
}
function leave(fn){
  if (busy) return;
  const c = $('#card');
  if (!c || reduce){ fn(); return; }
  busy = true;
  c.classList.add('leaving');
  setTimeout(() => { busy = false; fn(); }, 200);
}
function submit(q, ans){
  if (busy) return;
  if (!ans || /=\s*(,|$)/.test(ans)){ say('Finish the answer first.', true); return; }
  if (!window.lavish){ say('No Bridge session. Open this page from Bridge to send answers.', true); return; }
  const label = labelFor(q, ans);
  if (!send(q, ans, $('#card'))) return;
  sentIds.add(q.id);
  sentList.push({id: q.id, title: q.title || '', label: label});
  sentCount++;
  leave(() => { frontId = null; shownId = null; render(); say('Queued: ' + clip(label, 80) + '. Press Send to Agent to finish.'); });
}
function later(){
  const d = deck();
  if (d.length < 2){ say('This is the only one left.'); return; }
  leave(() => {
    const id = d[0].id;
    const i = laterSeq.indexOf(id);
    if (i >= 0) laterSeq.splice(i, 1);
    laterSeq.push(id);
    frontId = null; shownId = null;
    render();
  });
}

// ---- the card
function cardHTML(q){
  const opts = q.options || [], ins = q.inputs || [], multi = !!q.multi;
  const kind = q.kind || q.group || '';
  let h = '<article class="card" id="card" data-q="' + esc(q.id) + '" aria-labelledby="ctitle">';
  h += '<div class="meta">' + (kind ? '<span class="kind">' + esc(kind) + '</span>' : '') +
       (q.time ? '<span>' + esc(q.time) + '</span>' : '') + (q.due ? '<span>' + esc(q.due) + '</span>' : '') +
       autoTag(q) + '</div>';
  h += '<h1 class="title" id="ctitle">' + esc(q.title) + '</h1>';
  if (q.one) h += '<p class="oneline">' + esc(q.one) + '</p>';
  const line = ctxLine(q.context_md);
  if (line) h += '<p class="ctx">' + esc(line) + '</p>';
  const md = String(q.context_md || '').trim();
  const more = md && md.replace(/\s+/g, ' ') !== line;
  if (more){
    const o = openDet.has(q.id);
    h += '<button type="button" class="detbtn" data-det aria-expanded="' + o + '">' + (o ? 'Hide details' : 'Details') + '</button>' +
         '<div class="det" id="det"' + (o ? '' : ' hidden') + '>' + (o ? mdToHtml(md) : '') + '</div>';
  }
  const po = opts.find(o => o.value === q.pick);
  if (po) h += '<div class="pick"><span class="lbl">I would pick</span><span class="p">' + esc(po.label || po.value) +
               '</span>' + (q.pick_why ? '<span class="w">' + esc(q.pick_why) + '</span>' : '') + '</div>';
  else if (q.pick_why) h += '<div class="pick"><span class="w">' + esc(q.pick_why) + '</span></div>';

  const quick = !ins.length && opts.length && !multi && opts.length <= 6;
  if (ins.length){
    h += '<div class="ins">' + ins.map(i => '<label><span>' + esc(i.label || i.name) + '</span>' +
         '<input class="field" type="text" data-in="' + esc(i.name) + '" placeholder="?"></label>').join('') + '</div>';
    h += '<button type="button" class="opt primary" data-send-in>Send</button>';
  } else if (opts.length){
    h += '<div class="opts" role="group" aria-label="Answers">' + opts.map((o, i) =>
         '<button type="button" class="opt' + (o.value === q.pick ? ' primary' : '') + '" ' +
         (multi ? 'data-multi aria-pressed="false" ' : 'data-opt="' + i + '" ') +
         'data-val="' + esc(o.value) + '">' + esc(o.label || o.value) + '</button>').join('') + '</div>';
    if (multi) h += '<button type="button" class="opt primary" data-send-multi style="margin-top:10px">Send</button>';
  } else {
    h += '<textarea class="freebox" id="free" aria-label="Your answer" placeholder="Type your answer"></textarea>' +
         '<button type="button" class="opt primary" data-send-free>Send</button>';
  }
  if (opts.length || ins.length){
    const o = openOwn.has(q.id);
    h += '<button type="button" class="quiet" data-own aria-expanded="' + o + '">Answer in my own words</button>' +
         '<div class="own" id="own"' + (o ? '' : ' hidden') + '><textarea class="freebox" id="ownbox" aria-label="Your answer in your own words"></textarea>' +
         '<button type="button" class="opt primary" data-send-own>Send</button></div>';
  }
  h += '<button type="button" class="later" data-later>Later</button>';
  h += '<div class="keys">' + (quick && q.pick ? '<span><kbd>Enter</kbd>my pick</span>' : '') +
       '<span><kbd>L</kbd>later</span></div>';
  return h + '</article>';
}

function dots(done, tot){
  if (tot > 14) return '';
  let h = '<div class="dots" aria-hidden="true">';
  for (let i = 0; i < tot; i++) h += '<span class="dot' + (i < done ? ' done' : (i === done ? ' now' : '')) + '"></span>';
  return h + '</div>';
}

// ---- answered list: this sitting's queued answers, the board's own answered questions from the
// last 24 h, and the answer lines on Done (prune deletes answered questions hourly; Done keeps them).
function answeredList(){
  const cut = Date.now() - 24 * 3600000;
  const out = [], seen = new Set();
  for (const q of (state.questions || [])){
    if (!q.answered || (Date.parse(q.answered) || 0) < cut) continue;
    seen.add(String(q.id).toLowerCase());
    out.push({title: q.title || q.id, label: (q.answered_by === 'auto' ? 'Auto-picked: ' : '') +
              labelFor(q, q.answer || ''), t: Date.parse(q.answered) || 0});
  }
  for (const d of (state.done || [])){
    const t = Date.parse(d.ts) || 0;
    const m = /^(Q\d+)\s+([\s\S]*)$/i.exec(d.text || '');
    if (t < cut || !m || seen.has(m[1].toLowerCase())) continue;
    seen.add(m[1].toLowerCase());
    const body = m[2].replace(/\s*\[answered by[^\]]*\]\s*$/, '');
    const k = body.lastIndexOf(': ');
    out.push({title: k > 0 ? body.slice(0, k) : body, label: k > 0 ? body.slice(k + 2) : '', t: t});
  }
  out.sort((a, b) => b.t - a.t);
  const pend = sentList.filter(s => !seen.has(String(s.id).toLowerCase()) && !(byId(s.id) || {}).answered)
                       .map(s => ({title: s.title, label: s.label + ' (queued)', t: Infinity}));
  return pend.concat(out);
}

// ---- Decide
function renderDecide(){
  const d = deck();
  const host = $('#cardhost'), stack = $('#stack'), prog = $('#prog');
  if (!d.length){
    shownId = '(clear)';
    stack.className = 'stack';
    const a = answeredList();
    host.innerHTML = '<div class="card clear"><h1 class="title">All clear. Nothing waits on you.</h1>' +
      '<p class="ctx">' + (a.length ? 'Everything is answered.' : 'No open questions.') + '</p>' +
      (a.length ? '<ul class="done-list">' + a.map(x => '<li><b>' + esc(x.title) + '</b><span>' + esc(x.label) + '</span></li>').join('') + '</ul>' : '') +
      '</div>';
    prog.innerHTML = '';
    return;
  }
  const q = d[0];
  const tot = d.length + sentCount;
  stack.className = 'stack' + (d.length > 2 ? ' e2' : (d.length > 1 ? ' e1' : ''));
  prog.innerHTML = '<div class="progress"><span>' + (sentCount + 1) + ' of ' + tot + '</span>' + dots(sentCount, tot) + '</div>';
  // The 5 s poll must not rebuild a card somebody is typing in. Only a different or edited question
  // redraws, and a different one waits (quiet pill) while the open card holds a half-typed answer.
  const key = qkey(q);
  if (shownId === key && host.firstChild) return;
  const cur = $('#card');
  if (shownId && host.firstChild && cur && dirty(cur)){
    const cid = cur.getAttribute('data-q');
    if (cid !== q.id && d.some(x => x.id === cid)){
      prog.innerHTML += '<div class="newq" role="status">New question waiting</div>';
      return;
    }
  }
  shownId = key;
  host.innerHTML = cardHTML(q);
}

// ---- Watch: sessions that need a look, then everything else behind one fold. No invented flows.
function sess(){
  const r = state.sessions || [];
  const st = x => x.state || 'working';
  return {all: r, wait: r.filter(x => st(x) === 'waiting'), err: r.filter(x => st(x) === 'error'),
          work: r.filter(x => st(x) === 'working'), idle: r.filter(x => st(x) === 'done')};
}
function needLook(){ const s = sess(); return s.err.concat(s.wait); }

function recentDone(){
  const cut = Date.now() - 24 * 3600000;
  return (state.done || []).filter(d => !(Date.parse(d.ts) < cut));
}

function fleetLine(s){
  if (!s.all.length) return 'No sessions are running.';
  const p = [];
  if (s.work.length) p.push(s.work.length + ' working');
  if (s.wait.length) p.push(s.wait.length + ' ' + (s.wait.length === 1 ? 'needs' : 'need') + ' you');
  if (s.err.length) p.push(s.err.length + ' with an error');
  if (s.idle.length) p.push(s.idle.length + ' idle');
  return s.all.length + ' session' + (s.all.length === 1 ? '' : 's') + ': ' + p.join(', ') + '.';
}

function renderWatch(){
  const s = sess(), nl = needLook();
  let h = '<div class="flow-list">';
  if (!nl.length) h += '<div class="card clear"><h1 class="title">Nothing needs a look.</h1><p class="ctx">' +
                       (s.work.length ? esc(s.work.length + (s.work.length === 1 ? ' session is' : ' sessions are') + ' running.') : 'No session is waiting on you.') + '</p></div>';
  for (const r of nl.slice(0, 5)){
    const bad = (r.state === 'error');
    h += '<article class="card flow"><span class="state ' + (bad ? 'red' : 'amber') + '"><span class="sdot"></span><span>' +
         (bad ? 'Hit an error' : 'Waiting on you') + '</span></span><h2 class="flow-title">' + esc(r.pane) + '</h2>' +
         (r.doing ? '<p>' + esc(clip(r.doing, 240)) + '</p>' : '') +
         '<div class="sub">' + esc([r.group, r.model].filter(Boolean).join(' - ')) + '</div></article>';
  }
  h += '</div>';
  // The first five that need a look are cards; the rest are one line each behind the same fold, so
  // a busy fleet is never a wall of cards.
  const rest = nl.slice(5), run = s.work, dn = recentDone();
  if (rest.length || run.length || dn.length){
    h += '<div class="fold"><button type="button" class="fold-btn" data-fold aria-expanded="' + foldOpen + '"><span>' +
         (rest.length ? 'More waiting, running and done (' : 'Running and done (') +
         (rest.length + run.length + dn.length) + ')</span><span class="w">' + (foldOpen ? 'Hide' : 'Show') + '</span></button>' +
         '<div class="fold-body"' + (foldOpen ? '' : ' hidden') + '>';
    if (rest.length) h += '<h3>Also needs a look</h3><ul>' + rest.slice(0, 20).map(r => '<li><b>' + esc(r.pane) + '</b>' +
         (r.state === 'error' ? ' (error)' : '') + (r.doing ? ': ' + esc(clip(r.doing, 120)) : '') + '</li>').join('') +
         (rest.length > 20 ? '<li>and ' + (rest.length - 20) + ' more</li>' : '') + '</ul>';
    if (run.length) h += '<h3>Running now</h3><ul>' + run.slice(0, 12).map(r => '<li><b>' + esc(r.pane) + '</b>' +
         (r.doing ? ': ' + esc(clip(r.doing, 120)) : '') + '</li>').join('') +
         (run.length > 12 ? '<li>and ' + (run.length - 12) + ' more</li>' : '') + '</ul>';
    if (dn.length) h += '<h3>Done today</h3><ul>' + dn.slice(0, 8).map(d => '<li>' + esc(clip(d.text, 140)) + '</li>').join('') +
         (dn.length > 8 ? '<li>and ' + (dn.length - 8) + ' more</li>' : '') + '</ul>';
    h += '</div></div>';
  }
  h += '<p class="fleet">' + esc(fleetLine(s)) + '</p>';
  $('#cardhost').innerHTML = h;
}

// ---- Learn: a quiet pointer. The Tutor page is built elsewhere.
function renderLearn(){
  $('#cardhost').innerHTML = '<article class="card"><div class="meta"><span class="kind">Tutor</span></div>' +
    '<h1 class="title">Learning has its own page.</h1>' +
    '<p class="ctx">The Tutor page opens from <span class="cmd">python tutor</span>. Nothing to review here.</p>' +
    (TUTOR_URL ? '<a class="btn" target="_top" rel="noopener" href="' + esc(TUTOR_URL) + '">Open Tutor</a>' : '') + '</article>';
}

// ---- side columns
function renderSides(){
  const d = deck();
  const dl = d.filter(q => q.due).slice(0, 6);
  $('#sideL').innerHTML = (tab === 'decide' && dl.length)
    ? '<div class="side-sec"><h2>Deadlines</h2><ul class="deadlines">' + dl.map(q =>
        '<li><span class="when">' + esc(q.due) + '</span><span class="what">' + esc(q.title) + '</span></li>').join('') + '</ul></div>'
    : '';
  let h = '';
  if (tab === 'decide'){
    if (d.length > 1) h += '<div class="side-sec"><h2>After this</h2><ul class="queue">' + d.slice(1, 9).map(q =>
        '<li><button type="button" data-jump="' + esc(q.id) + '">' + esc(q.title) + '</button></li>').join('') +
        (d.length > 9 ? '<li style="padding:6px 0;font-size:15px">and ' + (d.length - 9) + ' more</li>' : '') + '</ul></div>';
    const a = answeredList();
    if (a.length) h += '<div class="side-sec"><button class="ans-toggle" type="button" data-ans aria-expanded="' + ansOpen +
        '">Answered today (' + a.length + ')</button><ul class="ans-list"' + (ansOpen ? '' : ' hidden') + '>' +
        a.slice(0, 12).map(x => '<li><b>' + esc(x.title) + '</b>' + esc(x.label) + '</li>').join('') + '</ul></div>';
  }
  $('#sideR').innerHTML = h;
  $('#sideToggle').hidden = !(tab === 'decide' && (dl.length || h));
}

function tabs(){
  for (const t of ['decide', 'watch', 'learn']){
    const b = $('#tab-' + t);
    b.setAttribute('aria-selected', t === tab ? 'true' : 'false');
    b.tabIndex = t === tab ? 0 : -1;
  }
  $('#cnt-decide').textContent = deck().length;
  $('#cnt-watch').textContent = needLook().length;
}

function renderLinks(){
  const home = (state.links || []).find(l => l.home && l.url);
  const a = $('#homelink');
  a.hidden = !home;
  if (home) a.href = home.url;
  const old = (state.links || []).find(l => l.alt === 'old' && l.url);
  const o = $('#oldlink');
  o.hidden = !old;
  if (old) o.href = old.url;
}

function render(){
  tabs();
  renderLinks();
  if (!loaded){
    shownId = '(unloaded)';
    $('#stack').className = 'stack';
    $('#prog').innerHTML = '';
    $('#cardhost').innerHTML = '<div class="card clear" role="status"><h1 class="title">Could not load the board. Retrying.</h1></div>';
    renderSides();
    return;
  }
  if (tab === 'decide') renderDecide();
  else {
    shownId = null;
    $('#stack').className = 'stack';
    $('#prog').innerHTML = '';
    if (tab === 'watch') renderWatch(); else renderLearn();
  }
  renderSides();
}

// ---- events
document.addEventListener('click', ev => {
  const t = ev.target.closest('[data-tab],[data-theme-btn],[data-jump],[data-ans],[data-fold],[data-side],' +
                              '[data-opt],[data-multi],[data-send-multi],[data-send-in],[data-send-free],[data-send-own],' +
                              '[data-own],[data-det],[data-later]');
  if (!t) return;
  const card = t.closest('#card');
  const q = card ? byId(card.getAttribute('data-q')) : null;
  if (t.hasAttribute('data-tab')){ tab = t.getAttribute('data-tab'); say(''); render(); window.scrollTo(0, 0); }
  else if (t.hasAttribute('data-theme-btn')){ dark = !dark; applyTheme(); }
  else if (t.hasAttribute('data-jump')){ if (!busy){ frontId = t.getAttribute('data-jump'); shownId = null; render(); window.scrollTo(0, 0); } }
  else if (t.hasAttribute('data-ans')){ ansOpen = !ansOpen; renderSides(); }
  else if (t.hasAttribute('data-fold')){ foldOpen = !foldOpen; renderWatch(); }
  else if (t.hasAttribute('data-side')){
    const l = $('#layout'), o = !l.classList.contains('open');
    l.classList.toggle('open', o); t.setAttribute('aria-expanded', o);
  }
  else if (!q) return;
  else if (t.hasAttribute('data-later')) later();
  else if (t.hasAttribute('data-det')){
    const box = $('#det'), o = box.hidden;
    if (o){ openDet.add(q.id); box.innerHTML = mdToHtml(q.context_md); } else openDet.delete(q.id);
    box.hidden = !o; t.setAttribute('aria-expanded', o); t.textContent = o ? 'Hide details' : 'Details';
  }
  else if (t.hasAttribute('data-own')){
    const box = $('#own'), o = box.hidden;
    if (o) openOwn.add(q.id); else openOwn.delete(q.id);
    box.hidden = !o; t.setAttribute('aria-expanded', o);
    if (o) $('#ownbox').focus();
  }
  else if (t.hasAttribute('data-opt')) submit(q, t.getAttribute('data-val'));
  else if (t.hasAttribute('data-multi')) t.setAttribute('aria-pressed', t.getAttribute('aria-pressed') !== 'true');
  else if (t.hasAttribute('data-send-multi'))
    submit(q, [...card.querySelectorAll('[data-multi][aria-pressed="true"]')].map(b => b.getAttribute('data-val')).join(', '));
  else if (t.hasAttribute('data-send-in'))
    submit(q, (q.inputs || []).map(i => (i.label || i.name).replace(/\s*=\s*$/, '') + '=' +
      String((card.querySelector('[data-in="' + String(i.name).replace(/"/g, '') + '"]') || {}).value || '').trim()).join(', '));
  else if (t.hasAttribute('data-send-free')) submit(q, $('#free').value.trim());
  else if (t.hasAttribute('data-send-own')) submit(q, $('#ownbox').value.trim());
});

document.addEventListener('keydown', ev => {
  if (ev.ctrlKey || ev.metaKey || ev.altKey || tab !== 'decide') return;
  const t = ev.target, tag = t && t.tagName;
  if (tag === 'TEXTAREA' || tag === 'INPUT' || tag === 'BUTTON' || tag === 'A') return;
  const card = $('#card');
  if (!card) return;
  const q = byId(card.getAttribute('data-q'));
  if (!q) return;
  if (ev.key === 'l' || ev.key === 'L'){ ev.preventDefault(); later(); }
  else if (ev.key === 'Enter'){
    const quick = !(q.inputs || []).length && !q.multi && (q.options || []).length;
    if (quick && q.pick && (q.options || []).some(o => o.value === q.pick)){ ev.preventDefault(); submit(q, q.pick); }
  }
});

function applyTheme(){
  document.documentElement.setAttribute('data-theme', dark ? 'dark' : 'light');
  $('#themeBtn').textContent = dark ? 'Light' : 'Dark';
}

// state.js is read through a <script> tag and never fetch(): the artifact runs in a sandboxed iframe
// with an opaque origin, where a sibling fetch is CORS-refused even though the server answers 200.
function readState(){
  return new Promise((res, rej) => {
    const s = document.createElement('script');
    s.src = 'state.js?t=' + Date.now();
    s.onload = () => { s.remove(); res(window.__boardState); };
    s.onerror = () => { s.remove(); rej(new Error('state.js')); };
    document.head.appendChild(s);
  });
}
async function tick(){
  try {
    const s = await readState();
    if (s && (!loaded || s.updated !== state.updated)){ state = s; loaded = true; render(); }
  } catch (e) { /* server down or a half-written file: try again in 5 s */ }
}

$('#today').textContent = new Date().toLocaleDateString('en-US', {weekday: 'long', month: 'long', day: 'numeric'});
applyTheme();
render();
tick();
setInterval(tick, POLL_MS);
})();
"""


def _calm_page():
    """The calm page skeleton. Content-free on purpose: the date is written by JS at load, every
    count starts at 0 and is filled from state.js, so these bytes never change from one save to the
    next and lavish-axi has no reason to reload the iframe."""
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<!-- podium-own-home: this page draws its own Home link in its header, so the Bridge chrome must not
     reserve a 40px strip above the iframe for one. patch_lavish_brand.py reads this marker out of
     the artifact's first 8KB. -->
<title>Bridge - Calm inbox</title>
<link rel="icon" href="data:,">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600&display=swap" rel="stylesheet">
<style>{CALM_CSS}</style>
<script src="https://cdn.jsdelivr.net/npm/marked@14.1.3/marked.min.js"></script>
<script src="https://cdnjs.cloudflare.com/ajax/libs/dompurify/3.1.6/purify.min.js"
        integrity="sha512-jB0TkTBeQC9ZSkBqDhdmfTv1qdfbWpGE72yJ/01Srq6hEzZIz2xkz1e57p9ai7IeHMwEG7HpzG6NdptChif5Pg=="
        crossorigin="anonymous"></script>
</head>
<body>
<header class="top">
  <div class="lhs"><span class="brand">Bridge</span><span id="today"></span>
    <a class="homelink" id="homelink" href="#" target="_top" rel="noopener" hidden>Home</a>
    <a class="homelink" id="oldlink" href="#" target="_top" rel="noopener" title="The previous board page" hidden>Old board</a></div>
  <div class="tabs" role="tablist" aria-label="Bridge sections">
    <button class="tab" type="button" role="tab" id="tab-decide" data-tab="decide">Decide<b id="cnt-decide"></b></button>
    <button class="tab" type="button" role="tab" id="tab-watch" data-tab="watch">Watch<b id="cnt-watch"></b></button>
    <button class="tab" type="button" role="tab" id="tab-learn" data-tab="learn">Learn<b id="cnt-learn"></b></button>
  </div>
  <button class="theme" id="themeBtn" type="button" data-theme-btn>Dark</button>
</header>

<noscript>
<p style="padding:24px 40px">JavaScript is off, so Bridge is empty. The whole thing in plain text is at
<a href="board-plain.html">board-plain.html</a>.</p>
</noscript>

<main>
  <div class="layout" id="layout">
    <aside class="side" id="sideL" aria-label="Deadlines"></aside>
    <section class="stage" id="stage" aria-live="off">
      <p class="toast" id="toast" role="status"></p>
      <div class="stack" id="stack"><div id="cardhost"></div></div>
      <div id="prog"></div>
    </section>
    <aside class="side" id="sideR" aria-label="After this"></aside>
    <button class="side-toggle" id="sideToggle" type="button" data-side aria-expanded="false" hidden>Deadlines and queue</button>
  </div>
</main>

<script src="state.js"></script>
<script>
{CALM_JS}
</script>
</body>
</html>
"""


def calm_path():
    """board-calm.html lives beside board.html, whichever directory that is right now - derived from
    HTML at call time (not a constant of its own) so a test or a worktree that repoints HTML at a
    scratch directory can never have this page land in the live board dir."""
    return os.path.join(os.path.dirname(os.path.abspath(HTML)), "board-calm.html")


_CALM_TMP_SEQ = itertools.count()


def write_calm_html():
    """Write board-calm.html, same rule as write_html(): only when the bytes changed, because an
    identical rewrite still bumps the mtime and that alone makes lavish-axi reload the iframe."""
    path = calm_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    page = _calm_page()
    if os.path.exists(path) and open(path, encoding="utf-8").read() == page:
        return path
    # pid + counter: two writers (a watcher and a CLI call) must never share one temp file name
    tmp = f"{path}.{os.getpid()}.{next(_CALM_TMP_SEQ)}.tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write(page)
        _replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)
    return path


def _write_calm_quietly():
    """save()'s call into write_calm_html. The calm page is an addition beside the board, so a
    problem writing it costs one stderr line and never the board write that already landed."""
    try:
        write_calm_html()
    except Exception as e:
        print("calm page: " + str(e).replace("\n", " ")[:160], file=sys.stderr)


# ---------------------------------------------------------------- cli

def build_parser(raise_errors=False):
    """raise_errors: a bad flag raises AxiError (for the TOON runner) instead of exiting on stderr."""

    class Parser(argparse.ArgumentParser):
        def error(self, message):
            if raise_errors:
                cmd = " ".join(self.prog.split()[1:])
                raise AxiError(message, "VALIDATION_ERROR", [f"Run `py board.py {cmd} --help`".replace("  ", " ")])
            super().error(message)

    p = Parser(prog="board", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--format", choices=["text", "toon"], default="text",
                   help="toon: compact TOON on stdout, with counts and next-step hints, errors on stdout "
                        "too. Default text keeps the old output. No command + toon prints the open "
                        "questions and live sessions.")
    p.add_argument("--version", action="version", version=VERSION)
    sub = p.add_subparsers(dest="cmd", parser_class=Parser)

    aq = sub.add_parser("add-question")
    aq.add_argument("--id")
    aq.add_argument("--title", required=True)
    aq.add_argument("--context")
    aq.add_argument("--context-file")
    aq.add_argument("--option", action="append", metavar="A=label")
    aq.add_argument("--input", action="append", metavar="a=label")
    aq.add_argument("--pick")
    aq.add_argument("--why")
    aq.add_argument("--multi", action="store_true",
                    help="options are checkboxes, not radios: he can send several at once")
    aq.add_argument("--from", dest="from_")
    aq.add_argument("--group", help="tab this lands on; default: looked up from --from's herdr tab")
    aq.add_argument("--auto", action="store_true",
                    help="free and reversible: apply --pick if nobody answers in --auto-hours")
    aq.add_argument("--auto-hours", type=float, dest="auto_hours", default=None,
                    help=f"hours before --auto fires (default {AUTOPICK_HOURS})")
    aq.add_argument("--time", help="how long it takes him, shown on the calm page (\"2 min\")")
    aq.add_argument("--due", help="when it is due, shown on the calm page (\"Fri Oct 9\")")
    aq.add_argument("--kind", help="calm page label; default is the group name")
    aq.add_argument("--one", help="one plain sentence under the title on the calm page")
    aq.set_defaults(fn=cmd_add_question)

    an = sub.add_parser("answer")
    an.add_argument("qid")
    an.add_argument("answer")
    an.add_argument("--by", help="the session writing this answer, when it is not Ryan. Ryan's "
                                 "own answer through board_watch passes neither flag.")
    an.add_argument("--reason", default="", help="why this is not Ryan answering - withdrawn, "
                                                 "superseded, answered elsewhere.")
    an.set_defaults(fn=cmd_answer)

    sh = sub.add_parser("show")
    sh.add_argument("--caption")
    sh.add_argument("--file")
    sh.add_argument("--text")
    sh.add_argument("--for", dest="for_")
    sh.add_argument("--group", help="tab this lands on; default: the --for question's group")
    sh.set_defaults(fn=cmd_show)

    se = sub.add_parser("session")
    se.add_argument("pane")
    se.add_argument("--doing")
    se.add_argument("--model")
    se.add_argument("--state", choices=["working", "waiting", "done", "error"])
    se.add_argument("--note")
    se.set_defaults(fn=cmd_session)

    ss = sub.add_parser("sessions")
    ss.add_argument("--from-ls", action="store_true", dest="from_ls")
    ss.set_defaults(fn=cmd_sessions)

    dn = sub.add_parser("done")
    dn.add_argument("text")
    dn.add_argument("--group", help="tab this lands on; default: none (All only)")
    dn.set_defaults(fn=cmd_done)

    pr = sub.add_parser("prune")
    pr.add_argument("--hours", type=float, default=None,
                     help="Show-block cutoff in hours (default 36 if --days is not given)")
    pr.add_argument("--days", type=float, default=None, help="legacy alias: hours = days * 24")
    pr.set_defaults(fn=cmd_prune)

    ap = sub.add_parser("autopick")
    ap.add_argument("--dry-run", action="store_true", dest="dry_run",
                    help="print what would fire and change nothing; works with autopick off")
    ap.set_defaults(fn=cmd_autopick)

    wa = sub.add_parser("withdraw-aged")
    wa.add_argument("--hours", type=float, default=None,
                    help=f"age cutoff in hours (default {WITHDRAW_AGE_HOURS})")
    wa.add_argument("--dry-run", action="store_true", dest="dry_run",
                    help="print what would withdraw and change nothing")
    wa.set_defaults(fn=cmd_withdraw_aged)

    md = sub.add_parser("mode")
    md.add_argument("mode", nargs="?", choices=["list", "call"],
                    help="which view a fresh load opens in; omit to print the current one")
    md.set_defaults(fn=cmd_mode)

    sub.add_parser("render").set_defaults(fn=cmd_render)
    sub.add_parser("open").set_defaults(fn=cmd_open)
    sub.add_parser("calm").set_defaults(fn=cmd_open)      # the old name for open; Calm is the default
    sub.add_parser("old").set_defaults(fn=cmd_old)
    dg = sub.add_parser("digest")
    dg.add_argument("--days", type=int, default=7,
                    help="scorecard window in days (default 7); the shipped/waiting sections are "
                         "always the last 24h")
    dg.set_defaults(fn=cmd_digest)

    for name, examples in EXAMPLES.items():
        sub.choices[name].formatter_class = argparse.RawDescriptionHelpFormatter
        sub.choices[name].epilog = "examples:\n" + "\n".join(f"  py board.py {e}" for e in examples)
    p.commands = sub.choices
    return p


EXAMPLES = {
    "add-question": ['add-question --id q5 --title "Ship it?" --option A="yes" --option B="no" --pick A',
                     'add-question --title "Which logo?" --context-file notes.md --group webapp'],
    "answer": ['answer q5 "A"', 'answer q5 "A" --by orchestrator --reason "superseded by q6"'],
    "show": ['show --caption "Result" --text "all green"', 'show --caption "Plan" --file plan.md --for q5'],
    "session": ['session kun-k3 --doing "AXI pass" --model sonnet --state working'],
    "sessions": ["sessions", "sessions --from-ls"],
    "done": ['done "shipped the AXI pass"', 'done "merged" --group webapp'],
    "prune": ["prune", "prune --hours 12"],
    "autopick": ["autopick --dry-run"],
    "withdraw-aged": ["withdraw-aged --dry-run", "withdraw-aged --hours 48"],
    "mode": ["mode", "mode call"],
    "render": ["render"],
    "open": ["open"],
    "old": ["old"],
    "digest": ["digest", "digest --days 3"],
}
NEXT = {
    "add-question": "Run `py board.py --format toon` to see what is open",
    "answer": "Run `py board.py --format toon` to see what is still open",
    "done": "Run `py board.py --format toon` to see what is still open",
    "session": "Run `py board.py sessions --format toon` to list the rows",
    "open": "Run `py board.py --format toon` for the open questions as text",
}
TITLE_CAP = 80


def _cut(text, cap=TITLE_CAP):
    text = str(text or "")
    return text if len(text) <= cap else f"{text[:cap]}...(+{len(text) - cap} chars)"


def _session_rows(sessions):
    return [{"pane": r.get("pane", ""), "state": r.get("state", ""), "model": r.get("model", ""),
             "doing": _cut(r.get("doing", ""))} for r in sessions]


def toon_home():
    """No command, --format toon: what is waiting on the human and who is running."""
    s = load()
    qs = s["questions"]
    open_q = [q for q in qs if not q.get("answered")]
    rows = [{"id": q.get("id", ""), "title": _cut(q.get("title", "")), "group": q.get("group", ""),
             "pick": q.get("pick", "")} for q in open_q]
    return {
        "open_questions": f"{len(open_q)} of {len(qs)}",
        "questions": rows if rows else f"none open ({len(qs)} on the board)",
        "sessions": _session_rows(s["sessions"]) if s["sessions"] else "none listed",
        "help": ["Run `py board.py --help` for every command",
                 'Run `py board.py answer <id> "<answer>" --by <you> --reason "<why>"` to answer a question',
                 "Run `py board.py sessions --from-ls --format toon` to refresh the session rows"],
    }


def _fix_hints(parser, name):
    """What a caller needs to correct a bad call in one try: the usage template, a runnable example,
    and the valid flags of this subcommand."""
    sub = parser.commands[name]
    usage = " ".join(sub.format_usage().split()).removeprefix("usage: ").replace("board ", "py board.py ", 1)
    flags = sorted(f for f in sub._option_string_actions if f.startswith("--") and f != "--help")
    hints = [f"Usage: {usage}", f"Example: py board.py {EXAMPLES[name][0]}"]
    if flags:
        hints.append("Valid flags: " + ", ".join(flags))
    return hints + [f"Run `py board.py {name} --help` for examples"]


def _toon_commands(parser):
    def make(name):
        def run(args):
            known = parser.commands[name]._option_string_actions
            unknown = [t for t in args if t.startswith("-") and len(t) > 1 and not t[1].isdigit()
                       and t.split("=", 1)[0] not in known]
            if unknown:     # name the bad flag before argparse complains about missing positionals
                raise AxiError(f"unrecognized arguments: {' '.join(unknown)}", "VALIDATION_ERROR",
                               _fix_hints(parser, name))
            try:
                ns = parser.parse_args([name] + list(args))
            except AxiError as e:       # a missing-argument or leftover-flag error from argparse
                e.suggestions = _fix_hints(parser, name)
                raise
            ns.format = "toon"
            buf = io.StringIO()
            try:
                with contextlib.redirect_stdout(buf):
                    rv = ns.fn(ns)
            except BoardUsage as e:
                raise AxiError(str(e.code), "VALIDATION_ERROR", e.suggestions)
            except SystemExit as e:
                if isinstance(e.code, str):
                    raise AxiError(e.code, "ERROR")
                raise
            if name == "sessions":
                rows = _session_rows(rv or [])
                return {"count": len(rows), "sessions": rows or "none listed"}
            lines = [ln for ln in buf.getvalue().splitlines() if ln.strip()]
            out = {"command": name}
            if len(lines) == 1:
                out["result"] = lines[0]
            elif lines:
                out["lines"] = lines
            else:
                out["result"] = "done"
            if name in NEXT:
                out["help"] = [NEXT[name]]
            return out

        run.__doc__ = parser.commands[name].format_help()
        return run

    return {n: make(n) for n in parser.commands}


def _pop_format(argv):
    """Remove --format X / --format=X from argv wherever it sits; return (format, rest)."""
    rest, fmt, i = [], "text", 0
    while i < len(argv):
        tok = argv[i]
        if tok == "--format" and i + 1 < len(argv):
            fmt, i = argv[i + 1], i + 2
            continue
        if tok.startswith("--format="):
            fmt, i = tok.split("=", 1)[1], i + 1
            continue
        rest.append(tok)
        i += 1
    return fmt, rest


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    fmt, rest = _pop_format(argv)
    if fmt not in ("text", "toon"):
        print(f"board: error: --format must be text or toon, got {fmt!r}", file=sys.stderr)
        sys.exit(2)
    if fast_version(rest, VERSION):         # -v, -V, --version behave the same with either format
        return
    if fmt == "toon":
        parser = build_parser(raise_errors=True)
        code = run_axi_cli(toon_home, _toon_commands(parser), version=VERSION, argv=rest, prog=os.path.abspath(__file__),
                           description="Bridge board: questions, sessions, done lines",
                           examples=["py board.py --format toon", 'py board.py done "shipped it" --format toon',
                                     "py board.py answer --help --format toon"])
        if code:
            sys.exit(code)
        return
    p = build_parser()
    a = p.parse_args(argv)
    if not getattr(a, "fn", None):
        p.print_help()
        return
    try:
        a.fn(a)
    except BoardUsage as e:
        print(e.code, file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
