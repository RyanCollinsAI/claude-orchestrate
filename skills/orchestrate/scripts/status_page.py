"""status-page: one local HTML page showing what every herdr pane is doing.

  py orch.py status-page            start the server if it is not up, open the page in Chrome :9333
  py orch.py status-page --serve    run the server in this terminal (what the opener starts, detached)
  py orch.py status-page --write    write board/status.html once and print its path
  py orch.py status-page --bar      set the herdr sidebar line once and print it

Read-only. Data comes from the same places as `orch.py ls` (orchlib), needs_me.waiting for the
"Needs Ryan" list, quota.claude_rows for headroom, psutil for RAM. It never sends keys to a pane.
The server rebuilds the snapshot at most every 8 s, the page reloads every 10 s, and the same loop
puts "N working, M need you" on every herdr workspace as the `fleet` token (45 s TTL, so a dead
server clears its own line). Show it with `$fleet` in `[ui.sidebar.spaces] rows` in herdr's config.
"""
import glob, html, json, os, re, socket, subprocess, sys, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import orchlib as L

REFRESH_S = 10
CACHE_S = 8
STUCK_S = 15 * 60
BAR_TTL_MS = 45_000
PORT_FILE = os.path.join(L.BOARD_DIR, "status-page-port.txt")
PAGE_FILE = os.path.join(L.BOARD_DIR, "status.html")
DEFAULT_PORT = 8791
BAR_SOURCE = "status-page"

STATES = ("needs", "stuck", "working", "done")
STATE_LABEL = {"needs": "waiting on Ryan", "stuck": "stuck", "working": "working", "done": "done"}


def one_line(text, n=160):
    text = " ".join(str(text or "").replace("**", "").replace("`", "").split()).lstrip("-* ")
    return text if len(text) <= n else text[: n - 1] + "…"


def fmt_age(secs):
    if secs is None:
        return "-"
    m = max(0, int(secs // 60))
    if m < 1:
        return f"{int(max(0, secs))}s"
    return f"{m}m" if m < 120 else f"{m // 60}h {m % 60:02d}m"


def pick_state(agent, kind, waiting_reason, idle_s, has_bg, flagged):
    """('needs'|'stuck'|'working'|'done', reason). `agent` is herdr's effective status.

    A pane in needs_me.waiting, or one that ended its turn on a question, is waiting on Ryan before
    anything else: a pane sitting on a question must stay visible. Then a pane that says working but
    has written nothing for 15 minutes and runs no background task is stuck, as is one the watchdog
    caught sitting on a permission prompt."""
    if waiting_reason:
        return "needs", waiting_reason
    if agent != "working" and kind == "QUESTION":
        return "needs", "asked a question"
    if flagged and agent != "working":
        return "stuck", "watchdog flagged it"
    if agent == "working":
        if not has_bg and idle_s is not None and idle_s > STUCK_S:
            return "stuck", f"no output for {fmt_age(idle_s)}"
        return "working", ""
    if kind == "ERROR" and agent not in ("done",):
        return "stuck", "last turn ended in an error"
    return "done", ""


def task_line(name, handoffs_dir=None):
    """First line under `## Goal` of the newest handoff task file for this pane, '' if none."""
    d = handoffs_dir or L.HANDOFFS
    hits = glob.glob(os.path.join(d, f"task-{glob.escape(name)}-*.md"))
    if not hits:
        return ""
    try:
        with open(max(hits, key=os.path.getmtime), encoding="utf-8") as fh:
            lines = fh.read().splitlines()
    except OSError:
        return ""
    for i, ln in enumerate(lines):
        if ln.strip().lower() == "## goal":
            for nxt in lines[i + 1:]:
                if nxt.strip():
                    return one_line(nxt, 200)
    return ""


def tree_mb(pid):
    """Working set in MB of a process and all its children, None when it is gone."""
    try:
        import psutil
        p = psutil.Process(int(pid))
        total = p.memory_info().rss
        for c in p.children(recursive=True):
            try:
                total += c.memory_info().rss
            except psutil.Error:
                pass
        return int(total / 1048576)
    except Exception:
        return None


def watchdog_flagged():
    try:
        with open(os.path.join(L.BOARD_DIR, "watchdog-state.json"), encoding="utf-8") as fh:
            st = json.load(fh)
    except (OSError, ValueError):
        return set()
    return {k for kind in ("prompt_seen",) for k in (st.get(kind) or {})}


def pane_rows(now=None):
    """One dict per live pane: the fields the page shows. Claude panes first, then codex/pi/shell."""
    import needs_me as N
    now = now or time.time()
    panes = L.panes()
    board = N._load(N.BOARD_STATE, {})
    wait_rows, _seen = N.waiting(panes, board.get("questions", []), N._load(N.HOLDS, {}),
                                 N._load(N.STATE, {}).get("seen", {}), now)
    waiting = {r[1]: (r[0], r[3]) for r in wait_rows}
    flagged = watchdog_flagged()
    by_sid = L.panes_by_sid()
    ws = {w.get("workspace_id"): w.get("label", "") for w in L.workspaces()}
    rows = []

    def add(name, sid, m, p, kind, last, model, pid, owner, domain, since):
        pane = p.get("pane_id", "")
        agent = L.effective_status(p)
        w = waiting.get(pane)
        idle_s = now - since if since else None
        state, why = pick_state(agent, kind, w[1] if w else "", idle_s, bool(L.bg_tasks(p)[0]),
                                pane in flagged or (sid in flagged if sid else False))
        asked = (now - w[0]) if w else None
        rows.append({"name": name, "pane": pane, "owner": owner or "", "domain": domain or "",
                     "task": task_line(name) or one_line(p.get("cwd", ""), 80), "state": state,
                     "why": why, "age": fmt_age(asked if state == "needs" and asked else idle_s),
                     "age_s": idle_s, "last": one_line(last, 400), "model": model or "-",
                     "mb": tree_mb(pid) if pid else None, "bg": L.bg_tasks(p)[1],
                     "space": ws.get(p.get("workspace_id"), ""), "ws": p.get("workspace_id", "")})

    for sid, m in L.sessions().items():
        p = by_sid.get(sid)
        if not p:
            continue
        ents = L.tail_entries(sid)
        _ctx, model = L.last_usage(sid, ents)
        kind, last = L.classify(sid, ents)
        since = (m.get("statusUpdatedAt") or m.get("updatedAt") or 0) / 1000 or None
        add(m["name"], sid, m, p, kind, last, model, m.get("pid"), m.get("owner"), m.get("domain"),
            L.last_assistant_ts(sid, ents) or since)
    side = L.sidecar_load()
    for pane, p in sorted(L.other_panes().items()):
        e = side.get(pane, {})
        kind, last = L.classify_pane(pane)
        add(e.get("label") or p.get("label") or pane, None, {}, p, kind, last, e.get("model", "-"),
            None, e.get("owner"), e.get("domain"), None)
    return rows


def group_rows(rows):
    """[{name, domain, rows}]: one group per herdr space, which is one domain mate and its crew
    (each mate gets its own space). The heading carries the mate's name and domain when a pane in
    the space has one. Needs-Ryan panes sort first inside a group."""
    groups = {}
    for r in rows:
        key = r["space"] or "(no space)"
        g = groups.setdefault(key, {"name": key, "domain": "", "rows": []})
        g["rows"].append(r)
        if r["domain"]:
            g["domain"] = f'{r["name"]}: {r["domain"]}'
    order = {s: i for i, s in enumerate(STATES)}
    for g in groups.values():
        g["rows"].sort(key=lambda r: (order[r["state"]], r["name"]))
    return sorted(groups.values(), key=lambda g: (not g["domain"], g["name"]))


def counts(rows):
    c = {s: 0 for s in STATES}
    for r in rows:
        c[r["state"]] += 1
    return c


def bar_line(c):
    n = c["needs"] + c["stuck"]
    return f"{c['working']} working, {n} need you"


_ACCOUNTS = {"at": 0.0, "rows": []}


def accounts():
    """Both Claude accounts: [{name, five, week}] with None for a window the ledger has no number for."""
    if time.time() - _ACCOUNTS["at"] > 60:    # claude_rows shells out to PowerShell, ~1-2 s
        try:
            import quota
            _ACCOUNTS["rows"] = quota.claude_rows()
        except Exception:
            _ACCOUNTS["rows"] = []
        _ACCOUNTS["at"] = time.time()
    rows = _ACCOUNTS["rows"]
    return [{"name": r["provider"].split("/", 1)[1], "five": r.get("five_hour_left"),
             "week": r.get("weekly_left"), "active": bool(r.get("active"))}
            for r in rows if r["provider"].startswith("claude/") and not r["provider"].endswith("ambient")]


def free_ram_gb():
    try:
        import psutil
        return psutil.virtual_memory().available / 2**30
    except Exception:
        return None


# ---------------------------------------------------------------- chrome

TAB_WARN = 30     # Ryan 2026-10-05: "fewer Chrome pages" - 84 chrome.exe, 41 tabs on :9333 alone
CHROME_EVERY = 30
_CHROME = {"at": 0.0, "rows": []}


def chrome_mains():
    """[{pid, profile, port}] for each main chrome.exe (no --type=). Same parse as reaper.py."""
    import psutil
    out = []
    for p in psutil.process_iter(["pid", "name", "cmdline"]):
        if (p.info["name"] or "").lower() != "chrome.exe":
            continue
        cmd = " ".join(p.info["cmdline"] or []).replace('"', "")
        if "--type=" in cmd:
            continue
        m = re.search(r"--user-data-dir=(.+?)(?:\s--|$)", cmd)
        port = re.search(r"--remote-debugging-port=(\d+)", cmd)
        out.append({"pid": p.info["pid"], "profile": os.path.basename(m.group(1).rstrip("\\/")) if m
                    else "default", "port": port.group(1) if port else ""})
    return out


def chrome_windows():
    """{pid: visible top-level Chrome windows}. A headless Chrome has none."""
    if os.name != "nt":
        return {}
    import ctypes
    from ctypes import wintypes
    u = ctypes.windll.user32
    counts = {}

    def each(hwnd, _):
        if not u.IsWindowVisible(hwnd) or u.GetWindow(hwnd, 4) or not u.GetWindowTextLengthW(hwnd):
            return True                                    # 4 = GW_OWNER: skip owned popups
        cls = ctypes.create_unicode_buffer(64)
        u.GetClassNameW(hwnd, cls, 64)
        if cls.value == "Chrome_WidgetWin_1":
            pid = wintypes.DWORD()
            u.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            counts[pid.value] = counts.get(pid.value, 0) + 1
        return True
    u.EnumWindows(ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)(each), 0)
    return counts


def chrome_tabs(port):
    """Open tabs (CDP targets of type page) on a debug port, or None when it does not answer."""
    import urllib.request
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/list", timeout=2) as r:
            return sum(1 for t in json.loads(r.read()) if t.get("type") == "page")
    except Exception:
        return None


def chrome_rows(mains, windows, tabs_of):
    """One row per profile: {profile, ports, instances, windows, tabs (None = no debug port),
    warn}. Pure, so the counting is testable without a browser."""
    by = {}
    for m in mains:
        r = by.setdefault(m["profile"], {"profile": m["profile"], "ports": [], "instances": 0,
                                         "windows": 0, "tabs": None})
        r["instances"] += 1
        r["windows"] += windows.get(m["pid"], 0)
        if m["port"]:
            r["ports"].append(m["port"])
            n = tabs_of(m["port"])
            if n is not None:
                r["tabs"] = (r["tabs"] or 0) + n
    for r in by.values():
        r["warn"] = (r["tabs"] or 0) >= TAB_WARN
    return sorted(by.values(), key=lambda r: (-(r["tabs"] or 0), r["profile"]))


def chrome():
    if time.time() - _CHROME["at"] > CHROME_EVERY:
        try:
            _CHROME["rows"] = chrome_rows(chrome_mains(), chrome_windows(), chrome_tabs)
        except Exception:
            _CHROME["rows"] = []
        _CHROME["at"] = time.time()
    return _CHROME["rows"]


def snapshot(now=None):
    now = now or time.time()
    rows = pane_rows(now)
    return {"at": now, "free_gb": free_ram_gb(), "accounts": accounts(), "chrome": chrome(),
            "counts": counts(rows),
            "by_ws": {w: counts([r for r in rows if r["ws"] == w]) for w in {r["ws"] for r in rows}},
            "groups": group_rows(rows), "needs": [r for r in rows if r["state"] in ("needs", "stuck")]}


# ---------------------------------------------------------------- html

CSS = """
body{font:20px/1.45 system-ui,Segoe UI,sans-serif;margin:0;padding:24px 32px;background:#fafafa;color:#1a1a1a}
h1{font-size:30px;margin:0 0 14px}h2{font-size:24px;margin:34px 0 10px}
.strip{display:flex;flex-wrap:wrap;gap:12px;margin-bottom:22px}
.cell{background:#fff;border:1px solid #d8d8d8;border-radius:8px;padding:10px 18px}
.cell b{display:block;font-size:34px;line-height:1.1}.cell span{font-size:15px;color:#666}
.low b{color:#b00020}
.needs{background:#fdecec;border:3px solid #c62828;border-radius:10px;padding:14px 22px;margin-bottom:26px}
.needs h2{margin:0 0 8px;color:#b00020}
.need{padding:10px 0;border-top:1px solid #e8b4b4}.need:first-of-type{border-top:0}
.need .q{font-size:18px;margin-top:4px;white-space:pre-wrap}
table{border-collapse:collapse;width:100%;background:#fff;border:1px solid #d8d8d8}
th{text-align:left;font-size:14px;color:#666;padding:8px 10px;background:#f0f0f0}
td{padding:9px 10px;border-top:1px solid #e4e4e4;vertical-align:top;font-size:17px}
td.last{max-width:520px;color:#444;font-size:15px}
.st{font-weight:700;white-space:nowrap}
.st.working{color:#1b6e2e}.st.needs{color:#b00020}.st.stuck{color:#a85a00}.st.done{color:#666}
tr.needs td{background:#fff4f4}tr.stuck td{background:#fff8ec}
.dom{font-size:16px;color:#666;font-weight:400}
.foot{margin-top:24px;font-size:14px;color:#888}
tr.warn td{background:#fff4f4}td.tabwarn{color:#b00020;font-weight:700}
"""


def esc(x):
    return html.escape(str(x if x is not None else ""))


def pct(v):
    return "?" if v is None else f"{int(v)}%"


def render(snap):
    c = snap["counts"]
    cells = []
    gb = snap["free_gb"]
    cells.append(f'<div class="cell{" low" if gb is not None and gb < 6 else ""}">'
                 f'<b>{"?" if gb is None else f"{gb:.1f} GB"}</b><span>free RAM</span></div>')
    for a in snap["accounts"]:
        low = any(v is not None and v < 20 for v in (a["five"], a["week"]))
        cells.append(f'<div class="cell{" low" if low else ""}"><b>{pct(a["five"])} / {pct(a["week"])}</b>'
                     f'<span>{esc(a["name"])}{" (in use)" if a["active"] else ""} - 5h / week left</span></div>')
    for key, label in (("working", "working"), ("needs", "waiting on Ryan"), ("stuck", "stuck"),
                       ("done", "done")):
        cells.append(f'<div class="cell"><b>{c[key]}</b><span>{label}</span></div>')
    crows = snap.get("chrome") or []
    if crows:
        tabs = sum(r["tabs"] or 0 for r in crows)
        wins = sum(r["windows"] for r in crows)
        cells.append(f'<div class="cell{" low" if any(r["warn"] for r in crows) else ""}"><b>{tabs} / '
                     f'{wins}</b><span>Chrome tabs / windows</span></div>')
    out = [f'<!doctype html><html><head><meta charset="utf-8"><title>Herdr status</title>'
           f'<meta http-equiv="refresh" content="{REFRESH_S}"><style>{CSS}</style></head><body>',
           f"<h1>Herdr status - {esc(bar_line(c))}</h1>", f'<div class="strip">{"".join(cells)}</div>']
    if snap["needs"]:
        out.append(f'<div class="needs"><h2>Needs Ryan ({len(snap["needs"])})</h2>')
        for r in snap["needs"]:
            out.append(f'<div class="need"><b>{esc(r["name"])}</b> - {esc(r["why"] or STATE_LABEL[r["state"]])}'
                       f' - {esc(r["age"])} - owner {esc(r["owner"] or "-")}'
                       f'<div class="q">{esc(r["last"])}</div></div>')
        out.append("</div>")
    for r in crows:
        if r["warn"]:
            out.append(f'<div class="needs"><h2>Chrome: {esc(r["profile"])} has {r["tabs"]} tabs</h2>'
                       f'The warning line is {TAB_WARN}. Close the tabs your panes opened '
                       f'(<code>orch.py chrome close &lt;id&gt;</code>).</div>')
    for g in snap["groups"]:
        dom = f' <span class="dom">{esc(g["domain"])}</span>' if g["domain"] else ""
        out.append(f'<h2>{esc(g["name"])}{dom}</h2><table><tr><th>Pane</th><th>Owner</th><th>Task</th>'
                   f'<th>State</th><th>Age</th><th>Last output</th><th>Model</th><th>MB</th></tr>')
        for r in g["rows"]:
            st = STATE_LABEL[r["state"]] + (f' ({esc(r["why"])})' if r["why"] and r["state"] != "needs" else "")
            bg = f' <span class="dom">[{esc(r["bg"])}]</span>' if r["bg"] else ""
            out.append(f'<tr class="{r["state"]}"><td><b>{esc(r["name"])}</b><br><span class="dom">'
                       f'{esc(r["pane"])} {esc(r["space"])}</span></td><td>{esc(r["owner"] or "-")}</td>'
                       f'<td>{esc(r["task"])}</td><td class="st {r["state"]}">{st}</td>'
                       f'<td>{esc(fmt_age(r["age_s"]) if r["state"] != "needs" else r["age"])}</td>'
                       f'<td class="last">{esc(r["last"][:200])}{bg}</td><td>{esc(r["model"])}</td>'
                       f'<td>{"-" if r["mb"] is None else r["mb"]}</td></tr>')
        out.append("</table>")
    if crows:
        out.append('<h2>Chrome</h2><table><tr><th>Profile</th><th>Port</th><th>Instances</th>'
                   '<th>Windows</th><th>Tabs</th></tr>')
        for r in crows:
            warn = r["warn"]
            out.append(f'<tr class="{"warn" if warn else ""}"><td><b>{esc(r["profile"])}</b></td>'
                       f'<td>{esc(", ".join(r["ports"]) or "none")}</td><td>{r["instances"]}</td>'
                       f'<td>{r["windows"]}</td><td class="{"tabwarn" if warn else ""}">'
                       f'{"?" if r["tabs"] is None else r["tabs"]}</td></tr>')
        out.append(f'</table><div class="foot">Tabs are counted over the debug port; a Chrome with no '
                   f'port shows ?. Warning at {TAB_WARN} tabs in one profile.</div>')
    stamp = time.strftime("%H:%M:%S", time.localtime(snap["at"]))
    out.append(f'<div class="foot">Updated {stamp}. Reloads every {REFRESH_S} s. Read-only: nothing here '
               f'sends keys to a pane.</div></body></html>')
    return "".join(out)


# ---------------------------------------------------------------- bar + server

def bar_tokens(snap, workspaces):
    """{workspace_id: {token: text or None}}. The fleet total sits on the first space only; every
    space keeps its own count (`fleetws`) when that count is not zero. None clears a token."""
    first = min(workspaces, key=lambda w: w.get("number", 0))["workspace_id"] if workspaces else None
    out = {}
    for w in workspaces:
        wid = w["workspace_id"]
        own = (snap.get("by_ws") or {}).get(wid)
        line = bar_line(own) if own else ""
        busy = bool(own) and own["working"] + own["needs"] + own["stuck"] > 0
        out[wid] = {"fleet": f"all: {bar_line(snap['counts'])}" if wid == first else None,
                    "fleetws": line if busy and wid != first else None}
    return out


def set_bar(snap):
    """Write the `fleet` / `fleetws` tokens. Returns the total line."""
    stamp = str(int(time.time() * 1000))
    for wid, toks in bar_tokens(snap, L.workspaces()).items():
        args = ["herdr", "workspace", "report-metadata", wid, "--source", BAR_SOURCE, "--seq", stamp,
                "--ttl-ms", str(BAR_TTL_MS)]
        for name, text in toks.items():
            args += ["--token", f"{name}={text}"] if text else ["--clear-token", name]
        subprocess.run(args, capture_output=True, timeout=15)
    set_need_tokens(snap)
    return bar_line(snap["counts"])


def set_need_tokens(snap):
    """Mark each pane that waits on Ryan with a `need` pane token ("▲ needs you: why"); the herdr
    agent rail shows it in amber. Same 45 s TTL, so a pane that stops waiting loses it by itself."""
    stamp = str(int(time.time() * 1000))
    for r in snap.get("needs") or []:
        if not r.get("pane"):
            continue
        word = "▲ stuck" if r["state"] == "stuck" else "▲ needs you"
        why = one_line(r.get("why") or "", 40)
        subprocess.run(["herdr", "pane", "report-metadata", r["pane"], "--source", BAR_SOURCE, "--seq", stamp,
                        "--ttl-ms", str(BAR_TTL_MS), "--token", f"need={word}" + (f": {why}" if why else "")],
                       capture_output=True, timeout=15)


class Cache:
    def __init__(self):
        self.lock = threading.Lock()
        self.at = 0.0
        self.page = ""

    def page_html(self):
        with self.lock:
            if time.time() - self.at > CACHE_S or not self.page:
                snap = snapshot()
                self.page = render(snap)
                self.at = time.time()
                try:
                    set_bar(snap)
                except Exception:
                    pass
            return self.page


def make_handler(cache):
    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            body = cache.page_html().encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass
    return H


def free_port():
    for port in (DEFAULT_PORT, 0):
        s = socket.socket()
        try:
            s.bind(("127.0.0.1", port))
            return s.getsockname()[1]
        except OSError:
            continue
        finally:
            s.close()


def running_port():
    """The port of a live status-page server, or None."""
    try:
        port = int(open(PORT_FILE, encoding="utf-8").read().strip())
        with socket.create_connection(("127.0.0.1", port), timeout=1):
            return port
    except (OSError, ValueError):
        return None


def serve():
    cache = Cache()
    srv = ThreadingHTTPServer(("127.0.0.1", free_port()), make_handler(cache))
    with open(PORT_FILE, "w", encoding="utf-8") as fh:
        fh.write(str(srv.server_address[1]))

    def beat():
        while True:
            try:
                cache.page_html()
            except Exception:
                pass
            time.sleep(REFRESH_S)
    threading.Thread(target=beat, daemon=True).start()
    print(f"status page on http://127.0.0.1:{srv.server_address[1]}/", flush=True)
    srv.serve_forever()


def start_detached():
    """Start `status-page --serve` with a hidden console, wait for its port.

    CREATE_NO_WINDOW, never DETACHED_PROCESS: detached wins when both are set, the server then has
    no console, and every herdr/powershell child it runs each 10 s opened its own visible window
    (the 2026-10-10 flashing windows). Same rule as orch.py start_mate_watch.
    """
    flags = 0x08000000 | 0x00000200 if os.name == "nt" else 0   # CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP
    subprocess.Popen([sys.executable, os.path.join(os.path.dirname(os.path.abspath(__file__)), "orch.py"),
                      "status-page", "--serve"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     stdin=subprocess.DEVNULL, creationflags=flags, close_fds=True)
    for _ in range(40):
        port = running_port()
        if port:
            return port
        time.sleep(0.5)
    return None


def main(args):
    if "--serve" in args:
        serve()
    elif "--write" in args:
        snap = snapshot()
        with open(PAGE_FILE, "w", encoding="utf-8") as fh:
            fh.write(render(snap))
        print(PAGE_FILE)
    elif "--bar" in args:
        print(set_bar(snapshot()))
    else:
        port = running_port() or start_detached()
        if not port:
            print("status page server did not start - run `py orch.py status-page --serve` to see why")
            sys.exit(1)
        url = f"http://127.0.0.1:{port}/"
        import chrome_open
        tid = own_tab(url, chrome_open._targets(9333), chrome_open._load(chrome_open.tabs_file()))
        if tid:   # the CHROME rule: reuse your tab, never open a second copy of it
            import urllib.request
            urllib.request.urlopen(f"http://127.0.0.1:9333/json/activate/{tid}", timeout=5).read()
            print(f"{url} (already open - brought tab {tid} to the front)")
        else:
            chrome_open.open_tab(url, port=9333)
            print(url)


def own_tab(url, targets, mine):
    """The id of a tab `chrome open` made that already shows this page, or None."""
    return next((t["id"] for t in targets
                 if t.get("type") == "page" and t["id"] in mine and t.get("url", "").startswith(url)),
                None)
