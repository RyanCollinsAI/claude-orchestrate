"""The Orchestrator Board: the one page your human reads, and the only place they are asked anything.

State, not HTML, is the source of truth. `board/state.json` holds everything; `board render` turns
it into `board/board.html`, and the page re-reads `state.json` every 5 s so a change shows up
without reopening. Every command below renders at the end, so the HTML is never stale.

  py board.py add-question --id q5 --title "..." --context-file x.md
        [--context "..."] [--option A="..." --option B="..."] [--pick A --why "..."]
        [--input a="a =" --input b="b ="] [--from cg4-askbox]
  py board.py answer q5 "A"                     mark answered, move it to Done
  py board.py show --caption "..." (--file x.md | --text "...") [--for q5]
  py board.py session <pane> --doing "..." --model opus --state working
  py board.py sessions --from-ls                fill the table from the live herdr/registry board
  py board.py done "text"                       one line under "Done since your last look"
  py board.py render                            rewrite board.html from state.json
  py board.py open                              lavish-axi board.html, print the URL
  py board.py prune [--days 1]                  drop old Done lines
  py board.py digest                            morning "show" block: shipped, waiting, usage

The rule, set 2026-09-03: a question carries the real thing - the code block, the picture,
the table, the diagram - never a one-line summary of it. `context_md` is markdown and may hold
```mermaid fences, $$math$$, ![img](relative.png), tables and code fences; all four render.
"""
import argparse, datetime, glob, json, os, re, shutil, subprocess, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import orchlib as L

BOARD = L.BOARD_DIR
STATE = os.path.join(BOARD, "state.json")
STATEJS = os.path.join(BOARD, "state.js")
HTML = os.path.join(BOARD, "board.html")
PLAIN = os.path.join(BOARD, "board-plain.html")

IMG_RE = re.compile(r"!\[([^\]]*)\]\(([^)\s]+)\)")
# Repos `digest` reads this morning's commits from. Machine-specific, so it comes from config.json.
DIGEST_REPOS = L.CONFIG.get("digest_repos") or []


# ---------------------------------------------------------------- state

def now_iso():
    return datetime.datetime.now().astimezone().replace(microsecond=0).isoformat()


def default_state():
    return {"updated": now_iso(), "header": {"usage": "", "note": ""},
            "questions": [], "show": [], "sessions": [], "done": []}


def load():
    if not os.path.exists(STATE):
        return default_state()
    try:
        s = json.load(open(STATE, encoding="utf-8"))
    except Exception as e:
        sys.exit(f"{STATE} is not readable JSON ({e}); fix or delete it")
    for k, v in default_state().items():
        s.setdefault(k, v)
    return s


def save(s, render=True):
    s["updated"] = now_iso()
    os.makedirs(BOARD, exist_ok=True)
    tmp = STATE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(s, fh, indent=2, ensure_ascii=False)
    os.replace(tmp, STATE)          # the page polls this file; never let it read a half-write
    # Lavish renders the artifact in a sandboxed iframe whose origin is `null`, so a fetch of
    # state.json is refused for want of an Access-Control-Allow-Origin header even though the
    # server returns 200 (measured 2026-09-03). A <script> load is not CORS-checked, so the same
    # state also goes out as state.js and the page falls back to it. Both files, one write.
    tmp = STATEJS + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write("window.__boardState = " + json.dumps(s, ensure_ascii=False) + ";\n")
    os.replace(tmp, STATEJS)
    if render:
        write_plain(s)
        write_html()
    return s


def next_qid(s):
    used = {q.get("id", "") for q in s["questions"]}
    n = 1
    while f"q{n}" in used:
        n += 1
    return f"q{n}"


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
    filename. Lavish serves the HTML's own directory, and a leading `/` never resolves there."""
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
            sys.exit(f"no such file: {file}")
        return absorb_images(open(file, encoding="utf-8").read(),
                             os.path.dirname(os.path.abspath(file)))
    return absorb_images(text or "", os.getcwd())


def kv(pairs, what):
    """['A=needs the $9 pass', ...] -> [('A', 'needs the $9 pass'), ...]"""
    out = []
    for p in pairs or []:
        if "=" not in p:
            sys.exit(f'--{what} wants NAME=label, got {p!r}')
        k, v = p.split("=", 1)
        out.append((k.strip(), v.strip().strip('"')))
    return out


# ---------------------------------------------------------------- commands

def cmd_add_question(a):
    s = load()
    qid = a.id or next_qid(s)
    if any(q.get("id") == qid for q in s["questions"]):
        sys.exit(f"{qid} already exists; pick another --id or answer that one first")
    q = {"id": qid, "title": a.title, "from": getattr(a, "from_") or "",
         "context_md": read_md(a.context, a.context_file),
         "options": [{"value": k, "label": v} for k, v in kv(a.option, "option")],
         "pick": a.pick or "", "pick_why": a.why or "",
         "inputs": [{"name": k, "label": v, "width": 70} for k, v in kv(a.input, "input")],
         "created": now_iso(), "answered": None, "answer": None}
    s["questions"].append(q)
    save(s)
    print(f"added {qid}: {a.title}")
    return qid


def add_question_dict(q):
    """Same as add-question, for callers already holding a dict (watch_sessions.py)."""
    s = load()
    q.setdefault("id", next_qid(s))
    q.setdefault("created", now_iso())
    for k, v in (("from", ""), ("options", []), ("pick", ""), ("pick_why", ""),
                 ("inputs", []), ("answered", None), ("answer", None), ("context_md", "")):
        q.setdefault(k, v)
    s["questions"].append(q)
    save(s)
    return q["id"]


def cmd_answer(a):
    s = load()
    q = next((x for x in s["questions"] if x.get("id") == a.qid), None)
    if not q:
        sys.exit(f"no question {a.qid} on the board")
    # Idempotent on purpose: two board_watch processes polling the same Lavish session both
    # deliver the same answer, and they can press Send to Agent twice. Answering an already
    # answered question must not stack a second Done line (it did, measured 2026-09-03).
    if q.get("answered") and q.get("answer") == a.answer:
        print(f"{a.qid} was already answered {a.answer}; nothing changed")
        return
    line = f"{a.qid.upper()} {q.get('title', '')}: {a.answer}"
    if q.get("answered"):
        s["done"] = [d for d in s["done"]
                     if d.get("text") != f"{a.qid.upper()} {q.get('title', '')}: {q['answer']}"]
    q["answered"] = now_iso()
    q["answer"] = a.answer
    s["done"].insert(0, {"ts": now_iso(), "text": line})
    save(s)
    print(f"answered {a.qid}: {a.answer}")


def cmd_show(a):
    s = load()
    body = read_md(a.text, a.file)
    if not body.strip():
        sys.exit("show needs --file or --text")
    sid = "s" + str(len(s["show"]) + 1)
    s["show"].insert(0, {"id": sid, "caption": a.caption or "", "body_md": body,
                         "for": getattr(a, "for_") or "", "created": now_iso()})
    save(s)
    print(f"show {sid}: {a.caption or '(no caption)'}")
    return sid


def add_show(caption, body_md, for_=""):
    s = load()
    sid = "s" + str(len(s["show"]) + 1)
    s["show"].insert(0, {"id": sid, "caption": caption, "body_md": body_md,
                         "for": for_, "created": now_iso()})
    save(s)
    return sid


def cmd_session(a):
    s = load()
    row = next((x for x in s["sessions"] if x.get("pane") == a.pane), None)
    if not row:
        row = {"pane": a.pane, "doing": "", "model": "", "state": "working", "note": ""}
        s["sessions"].append(row)
    for k, v in (("doing", a.doing), ("model", a.model), ("state", a.state), ("note", a.note)):
        if v is not None:
            row[k] = v
    save(s)
    print(f"session {a.pane}: {row['state']}")


# classify() says why a session stopped; the board only needs to know whether a human has to act.
KIND_STATE = {"QUESTION": "waiting", "BLOCKED": "waiting", "OFFER": "waiting",
              "ERROR": "error", "DONE": "done"}


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
        rows.append({"pane": p.get("label") or m["name"],
                     "doing": (p.get("terminal_title_stripped") or said)[:60],
                     "model": model.replace("-5", "").replace("-4-5-20251001", "") or "?",
                     "state": state,
                     "note": f"ctx {ctx}k" + ("  <-- ROTATE" if ctx >= L.ROTATE_AT else "")})
    return sorted(rows, key=lambda r: r["pane"])


def cmd_sessions(a):
    s = load()
    if not a.from_ls:
        for r in s["sessions"]:
            print(f"{r['pane']:16} {r['state']:8} {r['model']:8} {r['doing']}")
        return
    s["sessions"] = live_sessions()
    save(s)
    for r in s["sessions"]:
        print(f"{r['pane']:16} {r['state']:8} {r['model']:8} {r['doing']}")


def cmd_done(a):
    s = load()
    s["done"].insert(0, {"ts": now_iso(), "text": a.text})
    save(s)
    print("done: " + a.text)


def cmd_prune(a):
    s = load()
    cut = datetime.datetime.now().astimezone() - datetime.timedelta(days=a.days)
    keep = []
    for d in s["done"]:
        try:
            if datetime.datetime.fromisoformat(d["ts"]) >= cut:
                keep.append(d)
        except Exception:
            keep.append(d)                          # unparsable timestamp: never silently drop it
    dropped = len(s["done"]) - len(keep)
    s["done"] = keep
    s["questions"] = [q for q in s["questions"] if not q.get("answered")
                      or datetime.datetime.fromisoformat(q["answered"]) >= cut]
    save(s)
    print(f"pruned {dropped} done line(s) older than {a.days}d")


def cmd_render(a=None):
    save(load())            # rewrites state.js, board-plain.html and (if it changed) board.html
    print(HTML)


def lavish_cmd(*args):
    """`lavish-axi` on this box is an npm .CMD shim; CreateProcess cannot launch one directly
    (WinError 193), so a .cmd/.bat has to go through cmd /c."""
    exe = shutil.which("lavish-axi") or "lavish-axi"
    if os.name == "nt" and exe.lower().endswith((".cmd", ".bat")):
        return ["cmd", "/c", exe, *args]
    return [exe, *args]


def cmd_open(a=None):
    save(load())
    r = subprocess.run(lavish_cmd(HTML), capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=180)
    out = (r.stdout or "") + (r.stderr or "")
    print(out.strip())
    m = re.search(r"""https?://[^\s"']+""", out)
    if m:
        print("URL: " + m.group(0))


# ---------------------------------------------------------------- digest

def git_since(repo, since):
    if not os.path.isdir(os.path.join(repo, ".git")):
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
    sid = add_show("Morning digest, " + datetime.date.today().strftime("%a %b %d"),
                   "\n".join(parts))
    print(f"digest -> {sid} ({len(open_q)} question(s) waiting)")


# ---------------------------------------------------------------- html

def esc(t):
    return (str(t).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;"))


def write_plain(s):
    """board-plain.html: the script-free reading copy the <noscript> block links to.

    It lives in its own file for a reason. Lavish live-reloads the artifact whenever the HTML it is
    serving changes on disk, and a reload throws away whatever they were half-way through typing
    (measured 2026-09-03: a typed answer vanished 0.5 s after an unrelated `board done`). Keeping
    every changing byte out of board.html is what makes the page updatable without a reload.

    Deliberately plain: markdown stays as text in a <pre>, so this can never disagree with the real
    renderer about what a fence or a $$ block means."""
    out = []
    for q in s["questions"]:
        if q.get("answered"):
            continue
        out.append(f"<div class='card q'><b>{esc(q['id'].upper())}</b> {esc(q.get('title', ''))}"
                   f"<pre class='code'>{esc(q.get('context_md', ''))}</pre></div>")
    for b in s["show"]:
        out.append(f"<div class='card show'>{esc(b.get('caption', ''))}"
                   f"<pre class='code'>{esc(b.get('body_md', ''))}</pre></div>")
    for r in s["sessions"]:
        out.append(f"<div class='card'>{esc(r.get('pane'))} - {esc(r.get('state'))} - "
                   f"{esc(r.get('doing'))}</div>")
    body = "\n".join(out) or "<div class='card'>Board is empty.</div>"
    page = (f"<!doctype html><html lang='en'><head><meta charset='utf-8'>"
            f"<title>Orchestrator Board (plain)</title><style>{CSS}</style></head><body>"
            f"<div class='wrap'><h1>Orchestrator Board</h1>"
            f"<p class='small'>Script-free copy, {esc(s['updated'])}.</p>{body}</div></body></html>")
    tmp = PLAIN + ".tmp"
    open(tmp, "w", encoding="utf-8").write(page)
    os.replace(tmp, PLAIN)


CSS = """
  :root{--bg:#f7f7f5;--card:#fff;--ink:#1a1a1a;--mute:#6b6b6b;--line:#e4e2dd;--ask:#b45309;--askbg:#fff7ed;--ok:#15803d;--okbg:#f0fdf4;--blue:#1d4ed8;--bluebg:#eff6ff;--gray:#6b7280}
  *{box-sizing:border-box;min-width:0}
  body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 system-ui,-apple-system,Segoe UI,Roboto,sans-serif}
  .wrap{max-width:920px;margin:0 auto;padding:20px 18px 80px}
  header{display:flex;justify-content:space-between;align-items:baseline;gap:12px;flex-wrap:wrap;margin-bottom:18px}
  header h1{font-size:20px;margin:0}
  header .meta{color:var(--mute);font-size:13px;text-align:right}
  h2{font-size:13px;letter-spacing:.08em;text-transform:uppercase;color:var(--mute);margin:26px 0 10px}
  .card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px 16px;margin-bottom:10px;overflow-wrap:anywhere}
  .q{border-left:4px solid var(--ask);background:var(--askbg)}
  .q .num{display:inline-block;font-weight:700;color:var(--ask);margin-right:8px}
  .q .title{font-weight:600}
  .q .from{color:var(--mute);font-size:12px}
  .ctx{color:var(--mute);font-size:14px;margin:4px 0 8px}
  .ctx p{margin:6px 0}
  .ctx code,.small code{background:#f1efe9;border-radius:4px;padding:1px 4px;font:13px ui-monospace,Consolas,monospace}
  .opts{display:flex;gap:8px;flex-wrap:wrap;margin:8px 0}
  .opts label{display:flex;gap:6px;align-items:center;border:1px solid var(--line);background:#fff;border-radius:8px;padding:6px 10px;cursor:pointer}
  .opts label.pick{border-color:var(--ask)}
  .pickTag{font-size:11px;color:var(--ask);font-weight:600;margin-left:4px}
  .row{display:flex;gap:8px;align-items:center;flex-wrap:wrap}
  input[type=text],textarea{font:inherit;border:1px solid var(--line);border-radius:8px;padding:6px 10px;width:100%;background:#fff}
  textarea{min-height:64px}
  button{font:inherit;border:1px solid var(--ask);background:var(--ask);color:#fff;border-radius:8px;padding:6px 12px;cursor:pointer}
  .queued{font-size:12px;color:var(--ok);display:none}
  .show{border-left:4px solid var(--blue);background:var(--bluebg)}
  .show .cap{font-size:13px;color:var(--mute);margin-bottom:8px}
  table{width:100%;border-collapse:collapse;font-size:14px;display:block;overflow-x:auto}
  th,td{text-align:left;padding:6px 8px;border-bottom:1px solid var(--line);vertical-align:top}
  th{color:var(--mute);font-weight:600;font-size:12px;text-transform:uppercase;letter-spacing:.05em}
  .pill{display:inline-block;font-size:12px;padding:1px 8px;border-radius:999px;border:1px solid var(--line);background:#fff;white-space:nowrap}
  .pill.working{color:var(--blue);border-color:#bfdbfe;background:var(--bluebg)}
  .pill.waiting{color:var(--ask);border-color:#fed7aa;background:var(--askbg)}
  .pill.done{color:var(--ok);border-color:#bbf7d0;background:var(--okbg)}
  .pill.error{color:#b91c1c;border-color:#fecaca;background:#fef2f2}
  .doneList li{margin:3px 0}
  pre.code{background:#1f2937;color:#f3f4f6;border-radius:8px;padding:10px 12px;font:13px/1.5 ui-monospace,Consolas,monospace;overflow-x:auto;margin:8px 0;white-space:pre}
  pre.mermaid{background:transparent;margin:0;padding:0;overflow-x:auto}
  .mathblock{margin:6px 0;overflow-x:auto}
  .katex-display{margin:6px 0}
  .katex-mathml{display:none}
  .small{font-size:13px;color:var(--mute)}
  img.fig{display:block;max-width:100%;background:#fff;border:1px solid var(--line);border-radius:8px;margin:8px 0}
  footer{margin-top:30px;color:var(--mute);font-size:13px}
"""

# The whole page renders in JS from state.json so a `board` command and a 5 s poll produce the same
# DOM. Python only ever writes this skeleton plus the boot copy of the state.
JS = r"""
const POLL_MS = 5000;
let state = window.__boardState ||
            {updated: null, header: {}, questions: [], show: [], sessions: [], done: []};
const qEls = new Map();          // question id -> {el, json}; a card is rebuilt only when ITS json
                                 // changed, so a half-typed answer survives every other update.

const esc = s => String(s == null ? '' : s).replace(/[&<>"']/g,
  c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));

function mdToHtml(src){
  src = String(src == null ? '' : src);
  const holds = [];
  const hold = h => { holds.push(h); return '\n\nLVHOLD' + (holds.length - 1) + 'ENDHOLD\n\n'; };
  // mermaid and $$math$$ come out before markdown runs, so nothing escapes or reflows them
  src = src.replace(/```mermaid[ \t]*\r?\n([\s\S]*?)```/g,
        (m, b) => hold('<pre class="mermaid">' + esc(b.replace(/\s+$/, '')) + '</pre>'));
  src = src.replace(/\$\$([\s\S]*?)\$\$/g,
        (m, b) => hold('<div class="mathblock">$$' + b + '$$</div>'));
  let html = marked.parse(src, {gfm: true});
  html = html.replace(/<pre><code[^>]*>/g, '<pre class="code">')
             .replace(/<\/code><\/pre>/g, '</pre>')
             .replace(/<img /g, '<img class="fig" ');
  html = html.replace(/<p>\s*LVHOLD(\d+)ENDHOLD\s*<\/p>/g, (m, i) => holds[+i])
             .replace(/LVHOLD(\d+)ENDHOLD/g, (m, i) => holds[+i]);
  return html;
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

function queueAnswer(q, f){
  const fd = new FormData(f);
  let ans = '';
  if (q.inputs && q.inputs.length)
    ans = q.inputs.map(i => (i.label || i.name).replace(/\s*=\s*$/, '') + '=' +
                            (fd.get('in_' + i.name) || '').trim()).join(', ');
  else if (q.options && q.options.length) ans = fd.get('choice') || '';
  else ans = (fd.get('free') || '').trim();
  if (!ans || /=\s*(,|$)/.test(ans)) return;      // never queue a half-filled answer
  const prompt = String(q.id).toUpperCase() + ' ' + (q.title || '') + ': ' + ans;
  const badge = f.querySelector('.queued');
  if (!window.lavish) { badge.textContent = 'no lavish session'; badge.style.display = 'inline'; return; }
  window.lavish.queuePrompt(prompt, {tag: 'answer', text: prompt, element: f,
    queueKey: q.id, data: {q: q.id, answer: ans}});
  badge.textContent = 'queued - press Send to Agent';
  badge.style.display = 'inline';
}

function questionCard(q){
  const f = document.createElement('form');
  f.className = 'card q';
  f.setAttribute('data-lavish-question', q.id);
  let h = '<div><span class="num">' + esc(String(q.id).toUpperCase()) + '</span>' +
          '<span class="title">' + esc(q.title) + '</span>' +
          (q.from ? ' <span class="from">from ' + esc(q.from) + '</span>' : '') + '</div>';
  if (q.context_md) h += '<div class="ctx">' + mdToHtml(q.context_md) + '</div>';
  if (q.options && q.options.length){
    h += '<div class="opts">';
    for (const o of q.options){
      const p = q.pick && q.pick === o.value;
      h += '<label' + (p ? ' class="pick"' : '') + '><input type="radio" name="choice" value="' +
           esc(o.value) + '"' + (p ? ' checked' : '') + '> <b>' + esc(o.value) + '</b> ' +
           esc(o.label) + (p ? '<span class="pickTag">pick</span>' : '') + '</label>';
    }
    h += '</div>';
    if (q.pick_why) h += '<div class="small">Pick ' + esc(q.pick) + ' - ' + esc(q.pick_why) + '</div>';
  }
  if (q.inputs && q.inputs.length){
    h += '<div class="row">';
    for (const i of q.inputs)
      h += '<span>' + esc(i.label || i.name) + '</span><input type="text" name="in_' +
           esc(i.name) + '" style="width:' + (i.width || 70) + 'px" placeholder="?">';
    h += '</div>';
  }
  if (!(q.options || []).length && !(q.inputs || []).length)
    h += '<div class="row"><textarea name="free" placeholder="your answer"></textarea></div>';
  h += '<div class="row" style="margin-top:8px"><button type="submit">Queue answer</button>' +
       '<span class="queued"></span></div>';
  f.innerHTML = h;
  f.addEventListener('submit', ev => { ev.preventDefault(); queueAnswer(q, f); });
  return f;
}

function renderQuestions(){
  const host = document.getElementById('questions');
  const open = (state.questions || []).filter(q => !q.answered);
  const seen = new Set();
  for (const q of open){
    seen.add(q.id);
    const j = JSON.stringify(q), cur = qEls.get(q.id);
    if (!cur){
      const el = questionCard(q); qEls.set(q.id, {el, json: j}); host.appendChild(el); typeset(el);
    } else if (cur.json !== j){
      const el = questionCard(q); host.replaceChild(el, cur.el);
      qEls.set(q.id, {el, json: j}); typeset(el);
    }
  }
  for (const [id, v] of [...qEls]) if (!seen.has(id)){ v.el.remove(); qEls.delete(id); }
  const want = open.map(q => qEls.get(q.id).el), have = [...host.children];
  if (want.length !== have.length || want.some((e, i) => e !== have[i]))
    want.forEach(e => host.appendChild(e));        // moving a node keeps its input values
  document.getElementById('qcount').textContent = open.length;
}

function renderShow(){
  const host = document.getElementById('show');
  host.innerHTML = '';
  for (const b of state.show || []){
    const d = document.createElement('div');
    d.className = 'card show';
    d.innerHTML = (b.caption ? '<div class="cap">' + esc(b.caption) +
                   (b.for ? ' (' + esc(String(b.for).toUpperCase()) + ')' : '') + '</div>' : '') +
                  mdToHtml(b.body_md);
    host.appendChild(d);
    typeset(d);
  }
  document.getElementById('showSec').style.display = (state.show || []).length ? '' : 'none';
}

function renderSessions(){
  const rows = state.sessions || [];
  document.getElementById('sessionsSec').style.display = rows.length ? '' : 'none';
  document.getElementById('sessions').innerHTML = rows.map(r =>
    '<tr><td>' + esc(r.pane) + '</td><td>' + esc(r.doing) + '</td><td>' + esc(r.model) +
    '</td><td><span class="pill ' + esc(r.state || 'working') + '">' + esc(r.state) + '</span>' +
    (r.note ? ' <span class="small">' + esc(r.note) + '</span>' : '') + '</td></tr>').join('');
}

function renderDone(){
  const d = state.done || [];
  document.getElementById('doneSec').style.display = d.length ? '' : 'none';
  document.getElementById('done').innerHTML = d.map(x => '<li>' + esc(x.text) + '</li>').join('');
}

function renderAll(){
  const h = state.header || {};
  document.getElementById('meta').innerHTML =
    esc(h.usage) + (h.note ? '<br>' + esc(h.note) : '') +
    '<br><span class="small">updated ' + esc(String(state.updated).replace('T', ' ').slice(0, 19)) + '</span>';
  renderQuestions(); renderShow(); renderSessions(); renderDone();
}

// state.json is the source of truth on disk; state.js is the same object as a one-line script and
// is what the page actually reads. `fetch('state.json')` is deliberately NOT used: inside Lavish
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
    so it is byte-identical from one save to the next and Lavish has no reason to reload the
    iframe. All content arrives through state.js / state.json."""
    os.makedirs(BOARD, exist_ok=True)
    page = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Orchestrator Board</title>
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/katex@0.16.11/dist/katex.min.css">
<style>{CSS}</style>
<script src="https://cdn.jsdelivr.net/npm/marked@14.1.3/marked.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/katex@0.16.11/dist/katex.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/katex@0.16.11/dist/contrib/auto-render.min.js"></script>
</head>
<body>
<div class="wrap">

<header>
  <h1>Orchestrator Board</h1>
  <div class="meta" id="meta"></div>
</header>

<noscript>
<div class="card">JavaScript is off, so this page is empty. The whole board in plain text is at
<a href="board-plain.html">board-plain.html</a>. Turn JavaScript on for the diagrams, the
equations, and the answer forms.</div>
</noscript>

<h2>Needs you (<span id="qcount">0</span>)</h2>
<div id="questions"></div>

<div id="showSec"><h2>Show</h2><div id="show"></div></div>

<div id="sessionsSec"><h2>Sessions</h2><div class="card"><table>
<thead><tr><th>Pane</th><th>Doing</th><th>Model</th><th>State</th></tr></thead>
<tbody id="sessions"></tbody></table></div></div>

<div id="doneSec"><h2>Done since your last look</h2>
<div class="card"><ul class="doneList" id="done"></ul></div></div>

<footer>Answers reach me through the Lavish poll. Nothing here sends anything to anyone else.</footer>
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
    # still bumps the mtime, and that alone is enough to make Lavish reload the iframe.
    if os.path.exists(HTML) and open(HTML, encoding="utf-8").read() == page:
        return HTML
    tmp = HTML + ".tmp"
    open(tmp, "w", encoding="utf-8").write(page)
    os.replace(tmp, HTML)
    return HTML


# ---------------------------------------------------------------- cli

def main(argv=None):
    p = argparse.ArgumentParser(prog="board", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd")

    aq = sub.add_parser("add-question")
    aq.add_argument("--id")
    aq.add_argument("--title", required=True)
    aq.add_argument("--context")
    aq.add_argument("--context-file")
    aq.add_argument("--option", action="append", metavar="A=label")
    aq.add_argument("--input", action="append", metavar="a=label")
    aq.add_argument("--pick")
    aq.add_argument("--why")
    aq.add_argument("--from", dest="from_")
    aq.set_defaults(fn=cmd_add_question)

    an = sub.add_parser("answer")
    an.add_argument("qid")
    an.add_argument("answer")
    an.set_defaults(fn=cmd_answer)

    sh = sub.add_parser("show")
    sh.add_argument("--caption")
    sh.add_argument("--file")
    sh.add_argument("--text")
    sh.add_argument("--for", dest="for_")
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
    dn.set_defaults(fn=cmd_done)

    pr = sub.add_parser("prune")
    pr.add_argument("--days", type=float, default=1)
    pr.set_defaults(fn=cmd_prune)

    sub.add_parser("render").set_defaults(fn=cmd_render)
    sub.add_parser("open").set_defaults(fn=cmd_open)
    sub.add_parser("digest").set_defaults(fn=cmd_digest)

    a = p.parse_args(argv)
    if not getattr(a, "fn", None):
        p.print_help()
        return
    a.fn(a)


if __name__ == "__main__":
    main()
