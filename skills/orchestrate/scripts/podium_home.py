"""podium_home.py: one front door for every Bridge page Ryan has open.

Ryan, 2026-09-10: "open all my learning podium pages" took four Start-Process calls. This writes
`board/home.html` - a plain, usable page (no Bridge brand, dark and light, phone ok) with one row
per link: every open Bridge session from `~/.lavish-axi/state.json` touched in the last
RECENT_DAYS days, the board (open-question and show-block counts), and the classes page (looked up
by its `podium-owners.json` label, not a hardcoded semester path, so it survives a semester
rollover). A "waiting on you" count - the board's open-question count - sits at the top.

  py podium_home.py render        write board/home.html, print a text row list
  py podium_home.py open          render, then serve it through Bridge (lavish-axi), print the URL

Deliberately independent of podium_watch.py and board_watch.py (each already imports the other, so
importing either back here would be a cycle) - it re-derives open_sessions/live_pollers/owners
itself, the same rules podium_watch.py uses.

`render` is what the watchers call: podium_watch.py's scan loop calls it whenever the open-session
set changes, and board_watch.py calls it whenever an answer or an auto-pick changes the board - so
home.html is never more than one poll-cycle stale. `py podium_home.py open` renders it and opens it as a
Bridge page (`orch.py home` was parked 2026-10-06, skills/_parked/orchestrate-commands).
"""
import datetime, json, os, re, subprocess, sys, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import board as B

STATE = os.path.join(os.environ.get("LAVISH_AXI_STATE_DIR") or os.path.join(os.path.expanduser("~"), ".lavish-axi"),
                     "state.json")
OWNERS = os.path.join(B.BOARD, "podium-owners.json")
HOME = os.path.join(B.BOARD, "home.html")
# The live Bridge URL for home.html itself, when it has one - patch_lavish_brand.py's patch_home()
# reads this server-side (readFileSync, same trick as the artifact-title patch) to give every other
# Bridge page's injected Home button somewhere real to point at. Empty when nobody has ever opened
# home.html through lavish-axi yet; the patch falls back to a plain file:// link in that case.
HOME_URLFILE = os.path.join(B.BOARD, "home-url.txt")
RECENT_DAYS = 7
CLASSES_OWNER = "classes-overview"


def _epoch(iso):
    try:
        return datetime.datetime.fromisoformat(str(iso).replace("Z", "+00:00")).timestamp()
    except Exception:
        return 0


def _raw_sessions():
    try:
        return json.load(open(STATE, encoding="utf-8")).get("sessions", {})
    except Exception:
        return {}


def owners():
    try:
        d = json.load(open(OWNERS, encoding="utf-8"))
        return {os.path.normcase(os.path.abspath(k)): v for k, v in d.items()}
    except Exception:
        return {}


def classes_path():
    """The file path whose podium-owners.json label is CLASSES_OWNER, or None when nobody has
    registered one yet - looked up by label rather than a hardcoded semester folder so this keeps
    working after a semester rollover changes the path."""
    for path, owner in owners().items():
        if owner == CLASSES_OWNER:
            return path
    return None


def open_sessions(exclude=()):
    """{abs path: state key} for every session state.json calls "open", touched inside
    RECENT_DAYS, excluding the board and anything in `exclude` (the classes page gets its own row,
    so it must not also show up in the general list). Same filter podium_watch.py uses, kept as its
    own copy so this file never has to import a watcher."""
    board_file = os.path.normcase(os.path.abspath(B.HTML))
    skip = {board_file} | {os.path.normcase(os.path.abspath(p)) for p in exclude if p}
    cutoff = time.time() - RECENT_DAYS * 86400
    out = {}
    for key, v in _raw_sessions().items():
        if v.get("status") != "open" or not v.get("file"):
            continue
        if _epoch(v.get("updated_at")) < cutoff:
            continue
        f = os.path.normcase(os.path.abspath(v["file"]))
        if f in skip or f.endswith(("\\board.html", "\\board-calm.html")):
            continue
        out[f] = key
    return out


def live_pollers():
    """Files with a live `lavish-axi ... poll <file>` process - the same check
    podium_watch.live_pollers uses. A slow or missing pwsh must not make every page look unpolled."""
    ps = ("Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*lavish*' -and "
          "$_.CommandLine -like '* poll *' } | ForEach-Object { $_.CommandLine }")
    try:
        r = subprocess.run(["pwsh", "-NoProfile", "-Command", ps], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=30)
        lines = (r.stdout or "").splitlines()
    except Exception:
        return set()
    files = set()
    for line in lines:
        if "podium_watch" in line or "--timeout-ms" in line:
            continue
        m = re.search(r'poll\s+"?([A-Za-z]:[^"]+?\.html)"?', line)
        if m:
            files.add(os.path.normcase(os.path.abspath(m.group(1))))
    return files


def title_of(path):
    """The html <title>, or the bare filename when the file has none or can't be read."""
    try:
        head = open(path, encoding="utf-8", errors="replace").read(4000)
    except OSError:
        return os.path.basename(path)
    m = re.search(r"<title[^>]*>(.*?)</title>", head, re.S | re.I)
    t = re.sub(r"\s+", " ", m.group(1)).strip() if m else ""
    return t or os.path.basename(path)


def _session_for_path(raw, path):
    """The freshest state.json entry for this file, regardless of status - used for a pinned row
    (the classes page) that must show up even when its own session has ended."""
    want = os.path.normcase(os.path.abspath(path))
    best = None
    for v in raw.values():
        f = v.get("file")
        if f and os.path.normcase(os.path.abspath(f)) == want:
            if best is None or _epoch(v.get("updated_at")) > _epoch(best.get("updated_at")):
                best = v
    return best or {}


def _row(path, v, own, polled):
    return {"title": title_of(path), "path": path, "url": v.get("url", ""),
            "owner": own, "polled": path in polled, "updated": v.get("updated_at", "")}


def podium_rows(exclude=()):
    raw = _raw_sessions()
    own = owners()
    polled = live_pollers()
    rows = [_row(path, raw.get(key, {}), own.get(path, ""), polled)
            for path, key in open_sessions(exclude).items()]
    rows.sort(key=lambda r: _epoch(r["updated"]), reverse=True)
    return rows


def classes_row():
    path = classes_path()
    if not path:
        return None
    raw = _raw_sessions()
    v = _session_for_path(raw, path)
    return _row(path, v, CLASSES_OWNER, live_pollers())


def board_summary():
    s = B.load()
    return {"open_questions": sum(1 for q in s.get("questions", []) if not q.get("answered")),
            "show_blocks": len(s.get("show", []))}


def esc(t):
    return (str(t).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;"))


def _link(row):
    return row.get("url") or ("file:///" + os.path.abspath(row["path"]).replace("\\", "/"))


def _ago(iso):
    e = _epoch(iso)
    if not e:
        return "never opened"
    mins = int((time.time() - e) / 60)
    if mins < 1:
        return "just now"
    if mins < 60:
        return f"{mins}m ago"
    hrs = mins // 60
    if hrs < 24:
        return f"{hrs}h ago"
    return f"{hrs // 24}d ago"


# Matches Bridge itself, set 2026-09-13: no orange (the old --ask #b45309 is gone), teal accent,
# square corners and 1px hairlines rather than pill chrome. This page is now a back-stop - every
# link on it also sits in Bridge's own top bar - so it stays deliberately plain, but it must not
# look like a different product when the Home button lands on it.
CSS = """
  :root{color-scheme:light dark;--bg:#ececea;--card:#fff;--ink:#1b1d1c;--mute:#5e6360;
        --line:#bcc2bf;--rowline:#dfe3e1;--hover:#e8f1ef;--accent:#00695c;--accentbg:#d9ebe8;
        --accentink:#00564b;--ok:#2e7d32}
  @media (prefers-color-scheme:dark){:root{--bg:#101312;--card:#171b1a;--ink:#dde3e1;
        --mute:#8b938f;--line:#2c3332;--rowline:#202625;--hover:#182624;--accent:#4db6a4;
        --accentbg:#10312c;--accentink:#7fd3c4;--ok:#5cb45f}}
  *{box-sizing:border-box;min-width:0}
  body{margin:0;background:var(--bg);color:var(--ink);
       font:14px/1.5 "Segoe UI",system-ui,-apple-system,Roboto,sans-serif}
  .wrap{max-width:900px;margin:0 auto;padding:18px 14px 40px}
  h1{font-size:19px;margin:0 0 6px}
  .waiting{display:inline-block;margin:2px 0 16px;padding:2px 9px;font-size:12.5px;font-weight:700}
  .waiting.zero{background:var(--card);border:1px solid var(--line);color:var(--mute)}
  .waiting.some{background:var(--accent);color:#fff}
  h2{font-size:11px;letter-spacing:.07em;text-transform:uppercase;color:var(--mute);
     margin:18px 0 6px;border-bottom:1px solid var(--line);padding-bottom:4px}
  a.row{display:flex;gap:10px;align-items:center;justify-content:space-between;
        background:var(--card);border:1px solid var(--line);border-left:3px solid var(--accentbg);
        padding:7px 11px;margin-bottom:5px;text-decoration:none;color:inherit}
  a.row:hover{background:var(--hover);border-left-color:var(--accent)}
  .row .title{font-weight:600;min-width:0;overflow-wrap:anywhere}
  .row .meta{color:var(--mute);font-size:12px;white-space:nowrap;flex:none}
  .dot{display:inline-block;width:6px;height:6px;margin-right:7px;flex:none}
  .dot.on{background:var(--ok)}
  .dot.off{background:var(--mute);opacity:.5}
  .empty{color:var(--mute);font-size:13px;padding:7px 0}
  footer{margin-top:20px;color:var(--mute);font-size:12px}
"""


def board_url(path=None):
    """The board's own live Bridge URL, same lookup as the classes row - a plain file:// fallback
    when it isn't open right now. Chrome silently refuses to navigate a TOP frame from an http(s)
    origin to a file:// URL (measured 2026-09-10: the board row went nowhere with no error), so the
    live URL is not just nicer, it is the only one that actually works once home.html is itself
    being served over http by lavish-axi rather than opened as a raw file.

    `path` defaults to the Calm inbox (board-calm.html), the default board since 2026-10-10; pass
    B.HTML for the previous page."""
    path = path or B.calm_path()
    v = _session_for_path(_raw_sessions(), path)
    return _link({"url": v.get("url", ""), "path": path})


def build():
    """Everything render() needs, split out so the CLI can print the same rows it wrote."""
    classes = classes_row()
    rows = podium_rows(exclude=(classes["path"],) if classes else ())
    board = board_summary()
    return rows, board, classes


def _page(rows, board, classes):
    waiting = board["open_questions"]
    # target="_top": home.html is itself rendered inside a Bridge artifact iframe, so a plain link
    # would open the next Bridge session INSIDE that iframe - two nested chromes, two Conversation
    # columns (Ryan, 2026-09-10, screenshot). Every row must navigate the top-level window instead.
    parts = [f"<!doctype html><html lang='en'><head><meta charset='utf-8'>"
             "<!-- podium-own-home: Home is the clickable session list. Annotate must default "
             "off so row clicks navigate instead of capturing. patch_lavish_brand.py reads "
             "this marker from the first 8KB. -->"
             f"<meta name='viewport' content='width=device-width, initial-scale=1'>"
             f"<title>Home</title><style>{CSS}</style></head><body><div class='wrap'>",
             "<h1>Bridge home</h1>",
             f"<span class='waiting {'zero' if not waiting else 'some'}'>"
             f"{waiting} waiting on you</span>",
             "<h2>Board</h2>",
             f"<a class='row' target='_top' rel='noopener' href='{esc(board_url())}'>"
             f"<span class='title'>Orchestrator board</span>"
             f"<span class='meta'>{board['open_questions']} open question(s), "
             f"{board['show_blocks']} show block(s)</span></a>",
             f"<a class='row' target='_top' rel='noopener' href='{esc(board_url(B.HTML))}'>"
             f"<span class='title'>Old board</span>"
             f"<span class='meta'>the previous page</span></a>",
             f"<a class='row' target='_top' rel='noopener' href='http://127.0.0.1:8787'>"
             f"<span class='title'>Review dashboard (reels + CutCore)</span>"
             f"<span class='meta'>127.0.0.1:8787</span></a>"]

    parts.append("<h2>Classes</h2>")
    if classes:
        dot = "on" if classes["polled"] else "off"
        parts.append(
            f"<a class='row' target='_top' rel='noopener' href='{esc(_link(classes))}'>"
            f"<span class='title'><span class='dot {dot}'></span>{esc(classes['title'])}</span>"
            f"<span class='meta'>{esc(_ago(classes['updated']))}</span></a>")
    else:
        parts.append(f"<div class='empty'>no page owned by '{CLASSES_OWNER}' in podium-owners.json"
                     f" yet</div>")

    parts.append("<h2>Learning &amp; design sessions</h2>")
    if not rows:
        parts.append(f"<div class='empty'>nothing open in the last {RECENT_DAYS} days</div>")
    for r in rows:
        dot = "on" if r["polled"] else "off"
        owner = f" - {esc(r['owner'])}" if r["owner"] else ""
        parts.append(
            f"<a class='row' target='_top' rel='noopener' href='{esc(_link(r))}'>"
            f"<span class='title'><span class='dot {dot}'></span>{esc(r['title'])}</span>"
            f"<span class='meta'>{esc(_ago(r['updated']))}{owner}</span></a>")

    parts.append(f"<footer>{len(rows) + (1 if classes else 0) + 3} link(s), "
                 f"rendered {esc(B.now_iso())}</footer></div></body></html>")
    return "\n".join(parts)


def sync_home_url():
    """Write home.html's own live Bridge URL to HOME_URLFILE, or clear it when home.html has no
    open Bridge session right now (never opened, or ended). Cheap and side-effect-free - a lookup
    against state.json already in memory, never a new lavish-axi process - so it is safe to call on
    every render(), not just from `open`."""
    v = _session_for_path(_raw_sessions(), HOME)
    url = v.get("url", "") if v.get("status") == "open" else ""
    try:
        tmp = HOME_URLFILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write(url)
        os.replace(tmp, HOME_URLFILE)
    except OSError:
        pass
    return url


def render():
    rows, board, classes = build()
    page = _page(rows, board, classes)
    os.makedirs(B.BOARD, exist_ok=True)
    tmp = HOME + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(page)
    os.replace(tmp, HOME)
    sync_home_url()
    return rows, board, classes


def print_rows(rows, board, classes):
    print(f"board        | Orchestrator board | {board['open_questions']} open question(s), "
          f"{board['show_blocks']} show block(s)")
    if classes:
        print(f"classes      | {classes['title']} | "
              f"{'polled' if classes['polled'] else 'NOBODY'} | {_ago(classes['updated'])}")
    else:
        print(f"classes      | (no page owned by '{CLASSES_OWNER}' yet)")
    for r in rows:
        who = "polled" if r["polled"] else "NOBODY"
        owner = r["owner"] or "-"
        print(f"bridge       | {r['title']} | {who} | owner={owner} | {_ago(r['updated'])}")


def main():
    a = sys.argv[1:]
    cmd = a[0] if a else "render"
    if cmd == "render":
        rows, board, classes = render()
        print(HOME)
        print_rows(rows, board, classes)
    elif cmd == "open":
        render()
        B.ensure_lavish_brand()
        r = subprocess.run(B.lavish_cmd(HOME, "--no-gate"), capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=180)
        out = (r.stdout or "") + (r.stderr or "")
        print(out.strip())
        m = re.search(r"""https?://[^\s"']+""", out)
        if m:
            print("URL: " + m.group(0))
        sync_home_url()   # lavish-axi has now written the new session to state.json
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
