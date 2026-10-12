"""Flip the global lavish-axi to 0.1.84 with a smoke test and an automatic restore.

    py lavish_flip.py polls [--out FILE]   list every live `lavish-axi poll` process with its pane
    py lavish_flip.py smoke                smoke test whatever lavish-axi the env points at
    py lavish_flip.py flip --go            do it: env vars, stop server, npm i, patches, restart, smoke,
                                           restore on failure, prompt the polling panes, screenshot

Smoke (all must pass): live board session page loads with the right version, one poll round trip,
podium_watch --once, the patch tests. `smoke` reads LAVISH_AXI_PORT, LAVISH_AXI_STATE_DIR and
ORCH_BOARD_DIR from the environment, so it can be tried on a side install first.
The old install lives in D:\\lavish-old-0.1.32; a failed smoke copies it back and restarts the old server.
"""
import json, os, re, shutil, subprocess, sys, threading, time, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

NPM_ROOT = os.path.expanduser(r"~\AppData\Roaming\npm\node_modules")
PKG = os.path.join(NPM_ROOT, "lavish-axi")
OLD = r"D:\lavish-old-0.1.32"
WANT = "0.1.84"
SKIP_PANES = {"axi-lead", "axi-f6"}      # the lead gets the SendMessage report; this pane re-polls itself
LOG = r"C:\Users\YOURNAME\Vault\tmp\axi-overnight-log-2026-10-08.md"


def log_line(text):
    with open(LOG, "a", encoding="utf-8") as fh:
        fh.write(f"{time.strftime('%H:%M')} | F6 | {text}\n")
PROOF = r"C:\Users\YOURNAME\Vault\tmp\f6-proof"
SMOKE_PAGE = r"C:\Users\YOURNAME\Vault\tmp\f6-canary\design-page.html"
TESTS = ["test_lavish_upstream_server_leak.py", "test_patch_lavish_atomic_state.py", "test_patch_lavish_memory.py",
         "test_patch_lavish_brand.py", "test_patch_lavish_chat_status.py", "test_patch_lavish_chat_send.py",
         "test_patch_lavish_annotate_at_cursor.py"]
ORCH = os.path.join(HERE, "orch.py")


def sh(args, **kw):
    r = subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="replace", **kw)
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def lavish(*args, timeout=120):
    import board as B
    return sh(B.lavish_cmd(*args), timeout=timeout)


def port():
    return int(os.environ.get("LAVISH_AXI_PORT") or 4387)


def state_path():
    return os.path.join(os.environ.get("LAVISH_AXI_STATE_DIR") or os.path.expanduser(r"~\.lavish-axi"), "state.json")


# ---------------------------------------------------------------- polls

def process_table():
    ps = "Get-CimInstance Win32_Process | Select-Object ProcessId,ParentProcessId,Name,CommandLine | ConvertTo-Json -Compress"
    _, out = sh(["pwsh", "-NoProfile", "-Command", ps], timeout=60)
    return {p["ProcessId"]: p for p in json.loads(out)}


def poll_rows():
    """[{pid, file, owner_flag, pane, pane_id, status, session_pid}] for every node `lavish-axi poll`."""
    import orchlib as L
    procs = process_table()
    sess_by_pid = {m.get("pid"): m for m in L._registry().values()}
    panes = {p.get("label"): p for p in L.panes()}
    rows = []
    for pid, p in procs.items():
        cmd = p.get("CommandLine") or ""
        if p.get("Name") != "node.exe" or "lavish" not in cmd or not re.search(r"\spoll\s", cmd):
            continue
        m = re.search(r'poll\s+"?([A-Za-z]:[^"]+?\.html)"?', cmd)
        owner = re.search(r"--owner\s+(\S+)", cmd)
        cur, hops, sess, watcher = p.get("ParentProcessId"), 0, None, ""
        while cur in procs and hops < 25:
            if cur in sess_by_pid:
                sess = sess_by_pid[cur]
                break
            wm = re.search(r"(podium_watch|board_watch|watch_sessions)\.py", procs[cur].get("CommandLine") or "")
            if wm and not watcher:
                watcher = "watcher:" + wm.group(1)
            cur, hops = procs[cur].get("ParentProcessId"), hops + 1
        name = (sess or {}).get("name", "") or watcher
        if not name and m:
            try:
                owners = json.load(open(os.path.join(__import__("board").BOARD, "podium-owners.json"), encoding="utf-8"))
                name = {os.path.normcase(os.path.abspath(k)): v for k, v in owners.items()}.get(
                    os.path.normcase(os.path.abspath(m.group(1))), "")
            except Exception:
                pass
        pane = panes.get(name, {})
        rows.append({"pid": pid, "file": m.group(1) if m else "?", "owner_flag": owner.group(1) if owner else "",
                     "pane": name or "(unmapped)", "pane_id": pane.get("pane_id", "-"),
                     "status": pane.get("agent_status", "-"), "session_pid": (sess or {}).get("pid", "-")})
    return sorted(rows, key=lambda r: (r["pane"], r["file"]))


def cmd_polls(out=None):
    rows = poll_rows()
    lines = [f"lavish poll processes: {len(rows)} ({time.strftime('%Y-%m-%d %H:%M:%S')})",
             f"{'node pid':>8}  {'pane':22} {'pane id':8} {'status':8} {'--owner':14} page"]
    for r in rows:
        lines.append(f"{r['pid']:>8}  {r['pane'][:22]:22} {r['pane_id']:8} {r['status']:8} {r['owner_flag'][:14]:14} {r['file']}")
    by = {}
    for r in rows:
        by.setdefault(r["pane"], []).append(r)
    lines += ["", "by pane:"] + [f"  {k}: {len(v)} poll(s), status {v[0]['status']}" for k, v in sorted(by.items())]
    text = "\n".join(lines)
    if out:
        os.makedirs(os.path.dirname(out), exist_ok=True)
        open(out, "w", encoding="utf-8").write(text + "\n")
    print(text)
    return rows


# ---------------------------------------------------------------- smoke

def board_session_url():
    import board as B
    want = os.path.normcase(os.path.abspath(B.HTML))
    for key, v in json.load(open(state_path(), encoding="utf-8")).get("sessions", {}).items():
        if os.path.normcase(os.path.abspath(v.get("file", ""))) == want:
            return f"http://127.0.0.1:{port()}/session/{key}?no-gate=1"
    return ""


def get(url, timeout=20):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return r.status, r.read().decode("utf-8", "replace")


def smoke():
    """-> (ok, [lines]). Never raises."""
    log, ok = [], True

    def step(name, passed, detail=""):
        nonlocal ok
        ok = ok and passed
        log.append(f"{'PASS' if passed else 'FAIL'} {name}" + (f": {detail}" if detail else ""))

    try:
        _, v = lavish("--version")
        step("lavish-axi --version", v.strip().endswith(WANT), v.strip())
        # the board page, served by the live server
        rc, out = lavish(os.path.join(__import__("board").BOARD, "board.html"), "--no-open", "--no-gate")
        url = board_session_url()
        step("board session found", bool(url), out.strip()[:120] if not url else url)
        if url:
            st, body = get(url)
            step("board session page loads", st == 200 and "Bridge" in body, f"http {st}, {len(body)} bytes")
            st2, _ = get(f"http://127.0.0.1:{port()}/health")
            step("health", st2 == 200, f"http {st2}")
        # one poll round trip on a throwaway page
        rc, out = lavish(SMOKE_PAGE, "--no-open", "--no-gate")
        m = re.search(r"url:\s*\"?(http://[^\s\"]+)", out)
        key = re.search(r"/session/([0-9a-f]+)", m.group(1)).group(1) if m else ""
        step("smoke page session", bool(key), out.strip()[:120] if not key else key)
        if key:
            result = {}
            t = threading.Thread(target=lambda: result.update(out=lavish(
                "poll", SMOKE_PAGE, "--owner", "f6-smoke", "--timeout-ms", "25000", timeout=60)[1]))
            t.start()
            time.sleep(4)
            body = json.dumps({"prompts": [{"tag": "note", "selector": "", "prompt": "f6 smoke nonce 7731"}]}).encode()
            req = urllib.request.Request(f"http://127.0.0.1:{port()}/api/{key}/prompts", data=body, method="POST",
                                         headers={"content-type": "application/json",
                                                  "origin": f"http://127.0.0.1:{port()}"})
            try:
                urllib.request.urlopen(req, timeout=20).read()
            except Exception as e:
                log.append(f"     prompt POST error: {e}")
            t.join(40)
            step("poll round trip", "f6 smoke nonce 7731" in result.get("out", ""), result.get("out", "")[:160].replace("\n", " | "))
            lavish("end", SMOKE_PAGE)
        # podium_watch
        rc, out = sh([sys.executable, "-X", "utf8", os.path.join(HERE, "podium_watch.py"), "--once"], timeout=120)
        step("podium_watch --once", rc == 0 and "Traceback" not in out, out.strip()[:160].replace("\n", " | ") or "exit 0")
        # patch tests
        rc, out = sh([sys.executable, "-m", "pytest", *TESTS, "-q", "-p", "no:cacheprovider"], cwd=HERE, timeout=600)
        tail = [l for l in out.strip().splitlines() if l.strip()][-1:] or [""]
        step("patch tests", rc == 0, tail[0])
    except Exception as e:
        step("smoke crashed", False, repr(e)[:300])
    return ok, log


def cmd_smoke():
    ok, log = smoke()
    print("\n".join(log))
    print("SMOKE " + ("GREEN" if ok else "RED"))
    return 0 if ok else 1


# ---------------------------------------------------------------- flip

def server_pids():
    ps = (f"Get-NetTCPConnection -State Listen -LocalPort {port()} -ErrorAction SilentlyContinue | "
          "Select-Object -ExpandProperty OwningProcess -Unique")
    _, out = sh(["pwsh", "-NoProfile", "-Command", ps], timeout=30)
    return [int(x) for x in out.split() if x.isdigit()]


def stop_server():
    lavish("stop")
    time.sleep(2)
    for pid in server_pids():
        sh(["pwsh", "-NoProfile", "-Command", f"Stop-Process -Id {pid} -Force -ErrorAction SilentlyContinue"])
    time.sleep(1)
    return not server_pids()


def start_server():
    import board as B
    return lavish(os.path.join(B.BOARD, "board.html"), "--no-open", "--no-gate")


def restore(reason_log):
    out = ["RESTORE: copying the old 0.1.32 install back"]
    stop_server()
    if os.path.exists(PKG):
        shutil.rmtree(PKG, ignore_errors=True)
    shutil.copytree(OLD, PKG)
    _, v = lavish("--version")
    out.append("old version now: " + v.strip())
    start_server()
    out.append("old server listening: " + str(bool(server_pids())))
    return out


def screenshot_live_board(dest):
    sys.path.insert(0, r"C:\Users\YOURNAME\Vault\routines\agent-chrome")
    from cdp_page import open_page
    import base64
    url = board_session_url()
    with open_page() as page:
        page.cdp.send("Emulation.setDeviceMetricsOverride", {"width": 1400, "height": 800, "deviceScaleFactor": 1, "mobile": False}, page.sid)
        page.goto(url)
        time.sleep(4)
        page.cdp.send("Page.bringToFront", {}, page.sid)
        data = page.cdp.send("Page.captureScreenshot", {"format": "png"}, page.sid)["data"]
        open(dest, "wb").write(base64.b64decode(data))
        return url, page.eval("document.body.innerText.slice(0,160)")


def notify_panes(rows):
    """Prompt every live working pane that polled a page. A poll whose pane cannot be traced (its
    parent process is gone) could belong to any pane, so then every working pane gets the line."""
    import orchlib as L
    working = {p.get("label"): p.get("agent_status") for p in L.panes()}
    names = {r["pane"] for r in rows if not r["pane"].startswith(("(", "watcher:"))}
    if any(r["pane"] == "(unmapped)" for r in rows):
        names |= set(working)
    sent, skipped = [], []
    for name in sorted(n for n in names if n):
        r = {"pane": name, "status": working.get(name, "-")}
        if name in SKIP_PANES or r["status"] != "working":
            skipped.append(f"{name} ({r['status']})")
            continue
        rc, out = sh([sys.executable, ORCH, "prompt", name,
                      "Bridge restarted on lavish 0.1.84. Re-run your poll if you were polling a page."], timeout=60)
        sent.append(f"{name} rc={rc}")
    return sent, skipped


def cmd_flip():
    if "--go" not in sys.argv:
        print("refusing: pass --go (the lead says go F6 first)")
        return 2
    os.environ["ORCH_BOARD_DIR"] = os.path.expanduser(r"~\.claude\skills\orchestrate\board")   # the LIVE board, not this worktree's
    if not os.path.exists(os.path.join(OLD, "package.json")) or json.load(open(os.path.join(OLD, "package.json")))["version"] != "0.1.32":
        print("refusing: the 0.1.32 backup at " + OLD + " is missing")
        return 2
    rows = cmd_polls(os.path.join(PROOF, "polls-before-flip.txt"))
    sessions = json.load(open(state_path(), encoding="utf-8")).get("sessions", {})
    pending = [(k, v) for k, v in sessions.items() if v.get("pending_prompts") or v.get("prompts")]
    log_line("pre-flip check | " + (f"{len(pending)} session(s) hold unsent feedback, kept in state.json across the restart: " +
             ", ".join(f"{os.path.basename(v['file'])} ({v.get('pending_prompts', 0)} pending)" for _, v in pending)
             if pending else "no session holds unsent feedback") +
             f"; {len(rows)} poll processes; state.json backed up")
    shutil.copy(state_path(), state_path() + ".bak-pre-0.1.84")
    print("== env vars")
    for k, v in (("LAVISH_AXI_HOST", "127.0.0.1"), ("LAVISH_AXI_TELEMETRY", "0")):
        sh(["pwsh", "-NoProfile", "-Command", f"[Environment]::SetEnvironmentVariable('{k}','{v}','User')"])
        os.environ[k] = v
    for k in ("LAVISH_CLI_MJS", "LAVISH_DIST", "LAVISH_AXI_STATE_DIR", "LAVISH_AXI_PORT"):
        os.environ.pop(k, None)
    print("== stop the live server:", "ok" if stop_server() else "STILL LISTENING")
    rc, out = sh(["npm", "install", "-g", f"lavish-axi@{WANT}"] if os.name != "nt" else
                 ["cmd", "/c", "npm", "install", "-g", f"lavish-axi@{WANT}"], timeout=300)
    print("== npm install rc", rc, out.strip()[-200:])
    rc, out = sh([sys.executable, os.path.join(HERE, "patch_lavish_brand.py")])
    print("== patches rc", rc, out.strip().splitlines()[-1:] )
    print("== start server:", start_server()[1].strip()[:200])
    time.sleep(3)
    ok, log = smoke()
    print("\n".join(log))
    if not ok:
        print("FAILED - smoke test red; restoring")
        print("\n".join(restore(log)))
        print("FAILED")
        log_line("flip | FAILED, restored 0.1.32: " + "; ".join(l for l in log if l.startswith("FAIL"))[:300])
        return 1
    print("SMOKE GREEN")
    sent, skipped = notify_panes(rows)
    print("prompted:", ", ".join(sent) or "none")
    print("skipped:", ", ".join(skipped) or "none")
    log_line(f"flip | lavish-axi {WANT} live on :{port()}, smoke green; re-poll prompt sent to {len(sent)} working pane(s); "
             "rollback D:\\lavish-old-0.1.32")
    os.makedirs(PROOF, exist_ok=True)
    url, text = screenshot_live_board(os.path.join(PROOF, "flip-live-board.png"))
    print("screenshot:", os.path.join(PROOF, "flip-live-board.png"), url, text.replace("\n", " | "))
    return 0


def main():
    a = sys.argv[1:]
    if not a or a[0] not in ("polls", "smoke", "flip"):
        print(__doc__)
        return 2
    if a[0] == "polls":
        cmd_polls(a[a.index("--out") + 1] if "--out" in a else None)
        return 0
    return cmd_smoke() if a[0] == "smoke" else cmd_flip()


if __name__ == "__main__":
    sys.exit(main())
