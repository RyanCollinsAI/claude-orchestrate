"""Orchestrator helpers: drive the other Claude Code sessions on this box, all inside herdr.

  py orch.py ls                             board: name, status, ctx, model, pane, agent state, title
  py orch.py path <name>                    print a fresh handoff file path for <name>
  py orch.py spawn <label> <prompt-or-@file> [--model tier] [--group TAB] [--cwd DIR] [--workspace wN]
                                            [--kind claude|codex]
                                            new pane -> agent in bypass -> prompt sent
  py orch.py rotate <name> [--model tier] [--group TAB]
                                            ask the session for a handoff, verify it, spawn the
                                            replacement in the same tab, close the old pane
  py orch.py task <label> --goal "..." --done "..." [--out PATH] [--model tier] [--group TAB] [--cwd DIR]
                                            [--kind claude|codex] [--report PATH]
                                            write a task file and spawn a session on it
  py orch.py reap [--hours 6] [--dry-run]   close idle sessions that finished clean N hours ago
  py orch.py kill <name>                    close that session's herdr pane (falls back to the pid)
  py orch.py show <name> [--ratio 0.5]      move that pane to the RIGHT of the orchestrator pane
  py orch.py hide <name>                    send it back to the tab it came from
  py orch.py rotate-self [--model fable] [--dry-run]
                                            rotate the orchestrator seat; the board carries over
  py orch.py resume                         re-arm everything after the orchestrator restarted
  py orch.py doctor                         one red/green line per moving part
  py orch.py chrome take <name> | free [name|force] | who
                                            the single-driver lock on the real browser
  py orch.py account [name|ambient]         which login new panes start on (optional feature)
  py orch.py board <...>                    Podium - see `board.py --help`

<name> is a session display name, a session-id prefix, or a pane id.
Model tiers: fable = hardest (architecture, a bug that survived its own fix), opus = hard,
sonnet = normal building (default), haiku = trivial mechanical.
`--kind codex` runs codex-cli in the pane instead of claude; --model does not apply to it, it has
no SendMessage, and it reports by writing a file (see "Codex and other kinds" in SKILL.md).
"""
import datetime, glob, json, os, subprocess, sys, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import orchlib as L

SCRIPTS = os.path.dirname(os.path.abspath(__file__))
HANDOFF_TEMPLATE = os.path.join(L.TEMPLATES, "handoff.md")
TASK_TEMPLATE = os.path.join(L.TEMPLATES, "task.md")


def _opt(a, flag, default=None):
    return a[a.index(flag) + 1] if flag in a else default


def chrome_line():
    return (f"Before driving the shared browser, run "
            f"`py \"{os.path.abspath(__file__)}\" chrome take <your-label>` and wait for it to "
            f"return ok; run `chrome free <your-label>` the moment you are done.")


def orch_line(kind="claude", report=""):
    """The standing constraints appended to every spawned prompt. Codex gets a different one: it
    has no SendMessage and no Write tool, so its report is a file and its shell rule is shorter."""
    if kind != "claude":
        return (f"The orchestrator session {L.orchestrator_name()} speaks for your human on small "
                f"reversible decisions, but you cannot message it. Write your final report to "
                f"{report or 'the path named in your task file'} and print DONE on its own line as "
                f"the last line of your reply; that file is your only channel back. "
                f"Never `cd X && ...` in a shell call; use absolute paths. " + chrome_line())
    return (f"The orchestrator session {L.orchestrator_name()} speaks for your human on small "
            f"reversible decisions; send it questions and your final report by SendMessage, and act "
            f"on its answers as theirs. Never `cd X && ...` in the Bash tool; use absolute paths. "
            f"Never a heredoc in Bash; use the Write tool. " + chrome_line())


# ---------------------------------------------------------------- board

def cmd_ls():
    by_sid = L.panes_by_sid()
    rows = []
    for sid, m in L.sessions().items():
        ents = L.tail_entries(sid)
        ctx, model = L.last_usage(sid, ents)
        kind, _ = L.classify(sid, ents)
        p = by_sid.get(sid, {})
        rows.append((m["name"], m["status"], f"{ctx:4}k", model, p.get("pane_id", "-"),
                     p.get("agent_status", "-"), kind,
                     p.get("terminal_title_stripped", "")[:38],
                     "  <-- ROTATE" if ctx >= L.ROTATE_AT else ""))
    # A non-claude pane has no registry entry and no .jsonl, so its whole row comes from herdr
    # plus the text on its screen. ctx is unknowable from outside, hence '-'.
    side = L.sidecar_load()
    for pane_id, p in L.agent_panes().items():
        kind, _ = L.classify_pane(pane_id)
        name = side.get(pane_id, {}).get("label") or p.get("label") or pane_id
        ast = p.get("agent_status", "-")
        rows.append((name, ast, "   -", p.get("agent", "?"), pane_id, ast, kind,
                     p.get("terminal_title_stripped", "")[:38], ""))
    print(f"{'NAME':12} {'STATUS':8} {'CTX':>6} {'MODEL':12} {'PANE':7} {'AGENT':8} {'KIND':8} TITLE")
    if not rows:
        print(f"no sessions found - is herdr running, and did you start a session in it? "
              f"(looked in {L.SESS})")
        return
    for n, st, ctx, model, pane, ast, kind, title, flag in sorted(rows, key=lambda r: r[0]):
        print(f"{n:12} {st:8} ctx={ctx} {model:12} {pane:7} {ast:8} {kind:8} {title}{flag}")


def path_for(label):
    """Build a fresh handoff path for an already-known-unique label. No resolution - callers that
    already hold a resolved session (e.g. cmd_rotate) must use this, not cmd_path, so a second
    session sharing that name mid-rotate can't make an already-disambiguated call fail."""
    os.makedirs(L.HANDOFFS, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y-%m-%d-%H%M")
    return os.path.join(L.HANDOFFS, f"{label}-{stamp}.md")


def cmd_path(name):
    # resolve() sys.exits with the match list when name is ambiguous (e.g. two live sessions sharing
    # a name); a miss (fresh name with no live session yet) falls back to the raw name unchanged.
    m = L.resolve(name)
    p = path_for(m["name"] if m else name)
    print(p)
    return p


# ---------------------------------------------------------------- account (optional)

ACCOUNT_FILE = os.path.join(L.SKILL, "account.txt")


def current_account():
    """Account every new pane starts on: ORCH_ACCOUNT env, else account.txt, else '' (ambient)."""
    if not L.ACCOUNTS_TOOL:
        return ""
    a = os.environ.get("ORCH_ACCOUNT", "").strip()
    if not a and os.path.exists(ACCOUNT_FILE):
        a = open(ACCOUNT_FILE, encoding="utf-8").read().strip()
    return a


def account_env_args():
    """herdr --env args carrying CLAUDE_CODE_OAUTH_TOKEN for the chosen account. The switch is per
    process, so it never touches the shared login the other panes are using. Returns [] when
    account switching is off, which is the default."""
    a = current_account()
    if not a:
        return []
    r = subprocess.run(["pwsh", "-NoProfile", "-File", L.ACCOUNTS_TOOL, "token", a],
                       capture_output=True, text=True, timeout=60)
    tok = (r.stdout or "").strip().splitlines()[-1] if r.stdout else ""
    if not tok.startswith("sk-ant-"):
        sys.exit(f"could not get a token for account '{a}': {r.stderr or r.stdout}")
    return ["--env", f"CLAUDE_CODE_OAUTH_TOKEN={tok}"]


def other_account(name):
    """The saved account NOT named, so a dead-account fallback flips instead of guessing. Falls
    back to 'ambient' for a name outside the two-account setup, or for '' (already ambient)."""
    return {"primary": "secondary", "secondary": "primary"}.get(name, "ambient")


DEAD_ACCOUNT_MARKS = ("hit your session limit", "login was rejected")


def cmd_account(name=None):
    if not L.ACCOUNTS_TOOL:
        print("account switching is off (no `accounts_tool` in config.json); "
              "every pane uses the ambient login")
        return
    if name is None:
        print(current_account() or "(ambient login)")
        return
    if name in ("none", "ambient", ""):
        if os.path.exists(ACCOUNT_FILE):
            os.remove(ACCOUNT_FILE)
        print("new panes use the ambient login")
        return
    os.environ["ORCH_ACCOUNT"] = name
    if not account_env_args():
        sys.exit("token check failed")
    open(ACCOUNT_FILE, "w", encoding="utf-8").write(name + "\n")
    print(f"new panes start on account '{name}'")


# ---------------------------------------------------------------- spawn / kill

TRUST_MARK = "trust this folder"
PANES_PER_TAB = int(L.cfg("panes_per_tab", 3))


def _accept_trust_dialog(pane, timeout=120):
    """If the pane shows claude's first-open trust dialog, choose 'Yes, I trust this folder'
    (the second row), then wait for herdr to see the agent idle. Returns the session id or None."""
    txt = L.pane_read(pane, 40).lower()
    if TRUST_MARK in txt:
        L.send_keys(pane, "Down")
        L.send_keys(pane, "Enter")
    deadline = time.time() + timeout
    while time.time() < deadline:
        time.sleep(2)
        p = next((p for p in L.panes() if p.get("pane_id") == pane), {})
        s = (p.get("agent_session") or {}).get("value")
        if s and p.get("agent_status") in ("idle", "done"):
            return s
    return None


def _new_pane(label, cwd, group=None, workspace=None, env_args=()):
    """A fresh pane for one agent: split into the `--group` tab while it has room, else a new tab.

    At most PANES_PER_TAB panes in one tab. A 30-row window split six ways left each agent two
    rows (2026-09-03): its dialogs rendered as one line and the watcher misread them as DONE.
    Overflow goes to `<group>-2`, `<group>-3`, ...
    """
    env_args = list(env_args)
    tab_label = group
    if group:
        tab = None
        for n in range(1, 10):
            tab_label = group if n == 1 else f"{group}-{n}"
            t = next((t for t in L.tabs() if t.get("label", "").lower() == tab_label.lower()), None)
            if t is None:
                break                       # create this one below
            if t.get("pane_count", 0) < PANES_PER_TAB:
                tab = t
                break
        if tab:
            in_tab = [p for p in L.panes() if p["tab_id"] == tab["tab_id"]]
            anchor = in_tab[-1]["pane_id"]
            direction = "right" if len(in_tab) % 2 == 1 else "down"
            return L.herdr("pane", "split", "--pane", anchor, "--direction", direction,
                           "--cwd", cwd, "--no-focus", *env_args)["result"]["pane"]["pane_id"]
    args = ["tab", "create", "--cwd", cwd, "--label", tab_label or label, "--no-focus", *env_args]
    if workspace:
        args += ["--workspace", workspace]
    return L.herdr(*args)["result"]["root_pane"]["pane_id"]


# codex-cli's own bypass: `--dangerously-bypass-approvals-and-sandbox` is the exact analogue of
# claude's `--dangerously-skip-permissions` (the TUI header then reads "permissions: YOLO mode").
CODEX_FLAGS = ["--dangerously-bypass-approvals-and-sandbox"]
CODEX_TRUST_MARK = "do you trust the contents of this directory"
CODEX_HOOKS_MARK = "press t to trust all"


def _codex_ready(pane, timeout=150):
    """Clear codex's two first-open dialogs and wait for herdr to see the agent idle.

    Both swallow the first prompt if you skip them (measured 2026-09-04: `herdr agent prompt`
    returned agent_prompted and the text vanished into the hooks-review dialog). The directory
    dialog takes Enter for "Yes, continue"; the hooks-review dialog takes Escape, which closes it
    without trusting anything.
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        low = L.pane_read(pane, 45).lower()
        if CODEX_TRUST_MARK in low:
            L.send_keys(pane, "enter")
            time.sleep(2)
            continue
        if CODEX_HOOKS_MARK in low:
            L.send_keys(pane, "escape")
            time.sleep(2)
            continue
        p = next((p for p in L.panes() if p.get("pane_id") == pane), {})
        if p.get("agent") == "codex" and p.get("agent_status") in ("idle", "done"):
            return True
        time.sleep(2)
    return False


def cmd_spawn_codex(label, prompt, cwd=None, workspace=None, group=None, report=""):
    """A codex-cli pane. No model tier (codex picks its own), no session id, no ctx - the board
    reads its state off herdr and off the text on its screen."""
    cwd = cwd or L.DEFAULT_CWD
    prompt = prompt + " " + orch_line("codex", report)
    pane = _new_pane(label, cwd, group, workspace)
    L.herdr("pane", "rename", pane, label)

    started = L.herdr("agent", "start", label, "--kind", "codex", "--pane", pane,
                      "--timeout", "90000", "--", *CODEX_FLAGS, timeout=120, soft=True)
    if not (started or {}).get("result"):
        # On Windows `codex` on PATH is an npm .cmd/.ps1 shim, and herdr's launcher runs
        # `Start-Process -FilePath codex`, which rejects it: "%1 is not a valid Win32
        # application" (2026-09-04). Typing the same command into the pane shell starts the
        # identical TUI and herdr still detects `agent: codex` on the pane.
        L.herdr("pane", "send-text", pane, "codex " + " ".join(CODEX_FLAGS), soft=True)
        L.send_keys(pane, "enter")
    if not _codex_ready(pane):
        sys.exit(f"codex did not come up in pane {pane}:\n{L.pane_read(pane, 30)}")

    L.sidecar_put(pane, label=label, kind="codex", report=report, cwd=cwd,
                  started=datetime.datetime.now().isoformat(timespec="seconds"))
    L.herdr("agent", "prompt", pane, prompt)
    print(f"spawned {label} pane={pane} kind=codex"
          + (f" report={report}" if report else ""))
    return pane, None


def _agent_alive(pane, sid=None):
    """True only if herdr's own pane list shows a live claude agent on `pane` right now - not the
    id `agent start` handed back at launch, which can point at a pane that then failed to come up.
    FRESH (2026-09-04): spawn printed `spawned ... sid=260fb3c2` and exited 0 while the pane held a
    bare shell; this is the check that call was skipping."""
    p = next((p for p in L.panes() if p.get("pane_id") == pane), {})
    if p.get("agent") != "claude":
        return False
    live_sid = (p.get("agent_session") or {}).get("value")
    if not live_sid:
        return False
    return sid is None or live_sid == sid


def cmd_spawn(label, prompt, model="sonnet", cwd=None, workspace=None, group=None,
              kind="claude", report=""):
    if kind != "claude":
        if kind != "codex":
            sys.exit(f"unknown --kind {kind!r}; this skill spawns {' or '.join(L.KINDS)}")
        return cmd_spawn_codex(label, prompt, cwd, workspace, group, report)
    cwd = cwd or L.DEFAULT_CWD
    if prompt.startswith("@"):
        f = prompt[1:]
        if not os.path.exists(f):
            sys.exit(f"handoff file missing: {f}")
        prompt = (f"You are taking over from an earlier Claude session that ran out of context. "
                  f"Read {f} and continue exactly where it left off. "
                  f"Do not ask your human to repeat anything that file already says.")
    prompt = prompt + " " + orch_line()
    model_id = L.MODELS.get(model, model)

    # A dead/limit-hit account dies in about a second. Try the spawn, and if that's what happened,
    # kill the pane, flip account.txt to the other account, and retry exactly once - never loop
    # more than that, or a stale ledger on both accounts spins forever.
    for attempt in (1, 2):
        env_args = account_env_args()

        pane = _new_pane(label, cwd, group, workspace, env_args)
        L.herdr("pane", "rename", pane, label)
        # -n/--name sets the session display name, so the pane label and the peer-messaging name
        # stop drifting apart - two sessions landing on the same random suffix made SendMessage
        # demand a session-id ref.
        dead = None
        if env_args:
            # `herdr agent start` launches claude outside the pane shell, so the --env token never
            # reaches it (measured 2026-09-03: the pane still ran on the ambient login). Type the
            # command into the pane shell instead, which did inherit --env, and let herdr detect it.
            L.herdr("pane", "send-text", pane,
                    f'claude --dangerously-skip-permissions --model {model_id} --name "{label}"',
                    soft=True)
            L.send_keys(pane, "enter")
            sid = None
            dead_by = time.time() + 15  # a dead account fails in ~1s; do not burn the whole 120s
                                        # claude-startup budget on a pane that is never coming up.
            for _ in range(60):
                time.sleep(2)
                if dead is None and time.time() < dead_by:
                    txt = L.pane_read(pane, 40).lower()
                    dead = next((m for m in DEAD_ACCOUNT_MARKS if m in txt), None)
                    if dead:
                        break
                p = next((p for p in L.panes() if p.get("pane_id") == pane), {})
                s = (p.get("agent_session") or {}).get("value")
                if s and p.get("agent_status") in ("idle", "done"):
                    sid = s
                    break
            agent_target = pane  # prompt by pane id; no herdr agent name was registered
            if not dead and not sid:
                sys.exit(f"claude did not come up in pane {pane} within 120 s")
        else:
            started = L.herdr("agent", "start", label, "--kind", "claude", "--pane", pane,
                              "--timeout", "90000", "--",
                              "--dangerously-skip-permissions", "--model", model_id,
                              "--name", label, timeout=120, soft=True)
            sid = (((started or {}).get("result") or {}).get("agent") or {}) \
                .get("agent_session", {}).get("value")
            agent_target = label
            if not sid:
                # A cwd claude has never opened on this machine shows "Is this a project you
                # trust?" and herdr answers agent_not_ready. Three panes stalled on it 2026-09-03.
                # Pick "Yes, I trust this folder" and wait for the agent the way the env path does.
                sid = _accept_trust_dialog(pane)
                agent_target = pane
            dead = next((m for m in DEAD_ACCOUNT_MARKS if m in L.pane_read(pane, 40).lower()), None)
            if not dead and not sid:
                sys.exit(f"claude did not come up in pane {pane}: {started}")

        if not dead:
            break

        acct = current_account()
        other = other_account(acct)
        print(f"pane {pane} died on account '{acct or 'ambient'}' ({dead!r})")
        L.herdr("pane", "close", pane)
        if attempt == 2:
            sys.exit(f"still dead after falling back to '{other}'; giving up (one retry only)")
        cmd_account(other)
        print(f"flipped account.txt to '{other}', retrying spawn once")

    if not _agent_alive(pane, sid):
        print(f"FAILED to spawn {label}: pane {pane} shows no live claude agent "
              f"(sid={sid[:8] if sid else '-'})")
        print(f"--- last 20 lines of pane {pane} ---")
        print(L.pane_read(pane, 20))
        sys.exit(1)

    # --allow-dangerously-skip-permissions only PERMITS bypass; --dangerously-skip-permissions
    # enters it. Verify anyway: a deny rule still prompts inside bypass.
    if not L.ensure_bypass(pane, timeout=10):
        print(f"WARN {agent_target} pane={pane} not confirmed in bypass; "
              f"run: py \"{os.path.join(SCRIPTS, 'fix_mode.py')}\" {pane}")

    L.herdr("agent", "prompt", agent_target, prompt)
    print(f"spawned {agent_target} pane={pane} model={model_id} sid={sid[:8]}")
    return pane, sid


SHOW_FILE = os.path.join(L.BOARD_DIR, "shown.json")


def _orch_pane():
    sid = L.orchestrator_sid()
    p = L.panes_by_sid().get(sid) if sid else None
    if not p:
        sys.exit(f"cannot find the orchestrator pane (tab labelled '{L.ORCHESTRATOR_TAB}')")
    return p


def cmd_show(name, ratio="0.5"):
    """Move a session's pane to the RIGHT of the orchestrator pane, in this tab, so the human can
    watch it and type into it without switching tabs. Remembers where it came from for `hide`."""
    m = L.resolve(name)
    if not m:
        sys.exit(f"no live session named {name}")
    p = L.panes_by_sid().get(m["sessionId"])
    if not p:
        sys.exit(f"{name} has no herdr pane")
    me = _orch_pane()
    if p["pane_id"] == me["pane_id"]:
        sys.exit("that is the orchestrator pane itself")
    shown = {}
    if os.path.exists(SHOW_FILE):
        shown = json.load(open(SHOW_FILE, encoding="utf-8"))
    if p.get("tab_id") != me.get("tab_id"):
        shown[p["pane_id"]] = {"tab_id": p.get("tab_id"), "tab_label": L.tab_label(p.get("tab_id")),
                               "name": m["name"]}
        os.makedirs(L.BOARD_DIR, exist_ok=True)
        json.dump(shown, open(SHOW_FILE, "w", encoding="utf-8"), indent=2)
        L.herdr("pane", "move", p["pane_id"], "--tab", me["tab_id"], "--split", "right",
                "--target-pane", me["pane_id"], "--ratio", str(ratio), "--focus")
    print(f"showing {m['name']} ({p['pane_id']}) right of the orchestrator; "
          f"`orch.py hide {m['name']}` sends it back")


def cmd_hide(name):
    """Send a pane that `show` pulled in back to the tab it came from."""
    m = L.resolve(name)
    if not m:
        sys.exit(f"no live session named {name}")
    p = L.panes_by_sid().get(m["sessionId"])
    if not p:
        sys.exit(f"{name} has no herdr pane")
    shown = json.load(open(SHOW_FILE, encoding="utf-8")) if os.path.exists(SHOW_FILE) else {}
    home = shown.pop(p["pane_id"], None)
    json.dump(shown, open(SHOW_FILE, "w", encoding="utf-8"), indent=2)
    tabs = {t["tab_id"] for t in L.tabs()}
    if home and home["tab_id"] in tabs:
        L.herdr("pane", "move", p["pane_id"], "--tab", home["tab_id"], "--split", "right",
                "--no-focus")
        print(f"{m['name']} back in tab '{home['tab_label']}'")
    else:
        label = (home or {}).get("tab_label") or "parked"
        L.herdr("pane", "move", p["pane_id"], "--new-tab", "--label", label, "--no-focus")
        print(f"{m['name']} moved to a new tab '{label}' (its old tab is gone)")


def agent_pane_for(name):
    """A non-claude pane by its sidecar label, its herdr pane label, or its pane id.

    `resolve()` cannot find one: it goes through the session registry, and Codex writes no entry
    there."""
    side = L.sidecar_load()
    for pane_id, p in L.agent_panes().items():
        if name in (pane_id, p.get("label"), side.get(pane_id, {}).get("label")):
            return pane_id, p
    return None, None


def cmd_kill(name, quiet=False):
    pane_id, p = agent_pane_for(name)
    if p is not None:
        L.herdr("pane", "close", pane_id)
        L.sidecar_drop(pane_id)
        if not quiet:
            print(f"killed {name}: closed herdr pane {pane_id} (kind={p.get('agent')})")
        return None
    m = L.resolve(name)
    if not m:
        sys.exit(f"no live session named {name} (name, session-id prefix, or pane id)")
    sid, pid = m["sessionId"], m.get("pid")
    p = L.panes_by_sid().get(sid)
    if p:
        L.herdr("pane", "close", p["pane_id"])
        how = f"closed herdr pane {p['pane_id']}"
    elif pid:
        L.kill_pid(pid)
        how = f"killed pid {pid} (no herdr pane found)"
    else:
        sys.exit(f"{name} has no pane and no pid; nothing to close")
    time.sleep(2)
    if pid:
        for f in [os.path.join(L.SESS, f"{pid}.json"),
                  *glob.glob(os.path.join(L.SESS, f"{pid}.*.key"))]:
            if os.path.exists(f):
                try:
                    os.remove(f)
                except OSError:
                    pass
    if not quiet:
        print(f"killed {m['name']}: {how}; log kept at {L.log_path(sid)}")
    return m


# ---------------------------------------------------------------- the Chrome lock

def cmd_chrome(action=None, name=None):
    """One session drives the real browser at a time. Two drove it at once on 2026-09-03 and one
    typed over the other's draft, so the lock is a file both sides can see, not a message."""
    if action in (None, "who"):
        holder, since = L.chrome_holder()
        print(f"{holder} (since {since})" if holder else "free")
        return
    if action == "take":
        if not name:
            sys.exit("usage: orch.py chrome take <your-label>")
        ok, msg = L.chrome_take(name)
        print(msg)
        sys.exit(0 if ok else 1)
    if action == "free":
        if not name:
            # A bare `free` used to skip chrome_free's ownership check entirely and silently drop
            # whoever held it - a `take X && work && free` that forgot to repeat X did exactly that
            # and freed a different session's lock out from under it (2026-09-04). Require the name.
            sys.exit("usage: orch.py chrome free <your-label>|force")
        ok, msg = L.chrome_free(name)
        print(msg)
        sys.exit(0 if ok else 1)
    sys.exit("usage: orch.py chrome take <name> | free [name|force] | who")


# ---------------------------------------------------------------- resume

MONITOR_TPL = 'Monitor(command=\'py "{path}"{args}\', persistent=true)'


def cmd_resume():
    """Everything that has to be re-armed after the orchestrator's own process restarts.

    It restarted four times on 2026-09-03 (two Claude auto-updates, two herdr restarts) and each
    time the Monitors, the board read, and the mid-task builders were silently on their own. This
    prints the exact lines to run and the exact sessions that need a nudge - it changes nothing by
    itself, so it is safe to run whenever you are unsure."""
    sys.path.insert(0, SCRIPTS)
    import board as B

    print("== 1. Monitors to start (paste each into a Monitor call) ==")
    print("  " + MONITOR_TPL.format(path=os.path.join(SCRIPTS, "watch_sessions.py"), args=""))
    print("  " + MONITOR_TPL.format(path=os.path.join(SCRIPTS, "board_watch.py"), args=""))

    by_sid = L.panes_by_sid()
    me = L.orchestrator_sid()
    live, dead, idle_mid, prompted = [], [], [], []
    for sid, m in L.sessions().items():
        if sid == me:
            continue
        p = by_sid.get(sid, {})
        ctx, model = L.last_usage(sid)
        row = (m["name"], p.get("pane_id", "-"), m["status"], ctx, model or "-")
        # DEAD is a claim this tool can only back up when herdr has no pane for the session at all -
        # ctx=0k/blank model alone used to mean DEAD too, but that is what a session in another cwd
        # looks like when its log can't be found, and a busy, healthy pane got called DEAD for it
        # (2026-09-04, ORC-022). log_path() now falls back to a glob search, so this should be rare;
        # treat it as live-but-unreadable instead of dead when a pane genuinely exists.
        if not p.get("pane_id"):
            dead.append(row)
            continue
        live.append(row)
        kind, said = L.classify(sid)
        if m["status"] in ("idle", "waiting") and kind in ("QUESTION", "BLOCKED", "OFFER", "ERROR"):
            idle_mid.append((m["name"], kind, said[:140]))
        if p.get("pane_id"):
            txt = L.pane_read(p["pane_id"], 30)
            if L.pending_prompt(txt) or L.pane_mode(txt) in ("auto", "teach-dialog"):
                prompted.append((m["name"], p["pane_id"], L.pane_mode(txt) or "prompt"))

    if prompted:
        panes = " ".join(p for _, p, _ in prompted)
        print("  " + MONITOR_TPL.format(path=os.path.join(SCRIPTS, "auto_accept.py"),
                                        args=" " + panes))

    print(f"\n== 2. Board == {B.STATE}")
    try:
        s = B.load()
        openq = [q for q in s["questions"] if not q.get("answered")]
        print(f"  {len(openq)} open question(s), {len(s['show'])} show block(s), "
              f"{len(s['sessions'])} session row(s), {len(s['done'])} done line(s)")
        for q in openq:
            print(f"    {q['id']}  {q.get('title', '')}")
    except Exception as e:
        print(f"  could not read the board: {type(e).__name__}: {e}")

    print(f"\n== 3. Sessions ==\n  {len(live)} alive, {len(dead)} dead")
    for n, pane, st, ctx, model in sorted(live):
        print(f"  OK    {n:12} {pane:7} {st:8} ctx={ctx:4}k {model}")
    for n, pane, st, ctx, model in sorted(dead):
        print(f"  DEAD  {n:12} {pane:7} {st:8} ctx={ctx:4}k {model}   <-- kill and respawn")

    if prompted:
        print("\n== 4. Panes needing a keypress ==")
        for n, pane, why in prompted:
            print(f"  {n:12} {pane:7} {why}"
                  + ("   <-- send Escape, then re-check the mode" if why == "teach-dialog" else ""))

    if idle_mid:
        print("\n== 5. Idle mid-task - nudge with 'continue where you left off' ==")
        for n, kind, said in idle_mid:
            print(f"  {n:12} {kind:8} {said}")
    if not dead and not prompted and not idle_mid:
        print("\nNothing needs a nudge. Start the two Monitors and carry on.")


# ---------------------------------------------------------------- doctor

def _ok(label, good, detail=""):
    print(f"  {'OK  ' if good else 'BAD '} {label:26} {detail}")
    return good


def _json_get(path, *keys):
    try:
        d = json.load(open(path, encoding="utf-8"))
    except (OSError, ValueError):
        return None
    for k in keys:
        if not isinstance(d, dict):
            return None
        d = d.get(k)
    return d


def cmd_doctor():
    """One line per moving part. Everything here has failed at least once on a live day."""
    print("== herdr ==")
    t = L.herdr("tab", "list", soft=True)
    _ok("api socket", t is not None,
        f"{len(t['result']['tabs'])} tabs" if t else "herdr tab list returned nothing - restart herdr")
    if t:
        labels = [x.get("label", "") for x in t["result"]["tabs"]]
        found_tab = any(x.lower() == L.ORCHESTRATOR_TAB for x in labels)
        _ok("orchestrator tab", found_tab,
            f"found tab '{L.ORCHESTRATOR_TAB}'" if found_tab else
            f"not found - run: herdr tab create --label {L.ORCHESTRATOR_TAB}")

    print("== messaging ==")
    if L.WINDOWS:
        try:
            pipes = [p for p in os.listdir("\\\\.\\pipe\\") if "cc-msg" in p]
            _ok("peer message pipes", bool(pipes), f"{len(pipes)} open")
        except OSError as e:
            _ok("peer message pipes", False, str(e))
    else:
        socks = glob.glob(os.path.join(L.CLAUDE_HOME, "**", "cc-msg-*"), recursive=True)
        _ok("peer message sockets", bool(socks), f"{len(socks)} open")

    print("== auth and network ==")
    try:
        r = subprocess.run(["claude", "auth", "status"], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=45)
        d = json.loads(r.stdout or "{}")
        logged_in = bool(d.get("loggedIn"))
        # deliberately not printing the account address
        _ok("claude auth", logged_in,
            f"found: {d.get('authMethod', '?')} / {d.get('subscriptionType', '?')}" if logged_in
            else "not found - run: claude login")
    except Exception as e:
        _ok("claude auth", False, f"not found ({type(e).__name__}: {e}) - run: claude login")

    ping = ["ping", "-n", "1", "-w", "3000"] if L.WINDOWS else ["ping", "-c", "1", "-W", "3"]
    try:
        r = subprocess.run([*ping, "api.anthropic.com"], capture_output=True, text=True, timeout=20)
        _ok("api.anthropic.com", r.returncode == 0, "reachable" if r.returncode == 0 else "no reply")
    except Exception as e:
        _ok("api.anthropic.com", False, str(e))

    print("== settings ==")
    # The updater rewrites both the user file and the PROJECT local file (the one under the
    # working directory's .claude/), so check that one, not ~/.claude/settings.local.json.
    local = os.path.join(L.DEFAULT_CWD, ".claude", "settings.local.json")
    for path, label in ((L.SETTINGS, "settings.json"), (local, "project settings.local")):
        if not os.path.exists(path):
            print(f"  --   {label:26} not present")
            continue
        mode = _json_get(path, "permissions", "defaultMode")
        if mode is None:
            # Absent is fine: the file simply does not override the mode.
            print(f"  --   {label:26} defaultMode not set (inherits)")
            continue
        _ok(label, mode == "bypassPermissions",
            f"defaultMode={mode}"
            + ("   <-- the updater flips this to auto; panes then stall on the classifier"
               if mode != "bypassPermissions" else ""))
    _ok("DISABLE_AUTOUPDATER", str(_json_get(L.SETTINGS, "env", "DISABLE_AUTOUPDATER")) == "1",
        "a mid-work update reloads every pane")

    print("== usage ==")
    usage_dir = os.path.join(L.CLAUDE_HOME, "accounts", "usage")
    files = sorted(glob.glob(os.path.join(usage_dir, "*.json")))
    if not files:
        print("  --   usage feed                 none written yet")
    for f in files:
        d = _json_get(f) or {}
        five = (d.get("five_hour") or {}).get("used_percentage")
        week = (d.get("seven_day") or {}).get("used_percentage")
        # used%, not the status line's remaining% - reading one for the other burned a whole day
        _ok(os.path.basename(f)[:-5], (five or 0) < 90,
            f"5h used {five}%, 7d used {round(week, 1) if week else week}%")

    print("== browser lock ==")
    holder, since = L.chrome_holder()
    print(f"  --   chrome                     {holder + ' since ' + str(since) if holder else 'free'}")
    cdp = L.cfg("cdp_url", "")
    if cdp:
        # An in-place Chrome self-update kills every renderer in the running window (2026-09-03,
        # 151 -> 153); the port then answers nothing until that profile is relaunched.
        try:
            import urllib.request
            with urllib.request.urlopen(cdp.rstrip("/") + "/json/version", timeout=5) as r:
                ver = json.loads(r.read().decode("utf-8", "replace")).get("Browser", "?")
            _ok("chrome cdp", True, f"{cdp} {ver}")
        except Exception as e:
            _ok("chrome cdp", False,
                f"{cdp} not answering ({type(e).__name__}) - relaunch that profile on the same port")


# ---------------------------------------------------------------- rotate

ROTATE_REQUEST = (
    "Context handoff, highest priority - do no other work until this file is written.\n"
    "Write a handoff file at {path}.\n"
    "Follow the template at {tpl} exactly: Goal in your human's words, Done with absolute paths and "
    "commit shas, In progress right now, Next steps, Open questions, Traps hit, "
    "Files that matter.\n"
    "A replacement session with none of your context will read only that file, so write every "
    "path, command, and trap it needs. Then stop and say 'handoff written'."
)


def cmd_rotate(name, model="sonnet", group=None, cwd=None):
    cwd = cwd or L.DEFAULT_CWD
    m = L.resolve(name)
    if not m:
        sys.exit(f"no live session named {name}")
    sid = m["sessionId"]
    p = L.panes_by_sid().get(sid)
    if not p:
        sys.exit(f"{m['name']} has no herdr pane; cannot rotate (kill it by hand if it is dead)")
    pane = p["pane_id"]
    # pane_id is the one target herdr always accepts: panes started by hand have no agent name.
    label = p.get("label") or m["name"]
    group = group or L.tab_label(p["tab_id"]) or label

    path = path_for(m["name"])
    req = ROTATE_REQUEST.format(path=path, tpl=HANDOFF_TEMPLATE)
    print(f"asking {m['name']} (pane {pane}) for a handoff, up to 10 min...")
    L.herdr("agent", "prompt", pane, req, "--wait", "--until", "done", "--until", "blocked",
            "--timeout", "600000", timeout=660, soft=True)

    size = os.path.getsize(path) if os.path.exists(path) else 0
    if size <= 300:
        print(f"HANDOFF NOT WRITTEN ({size} bytes at {path}). Nothing was killed.")
        print(f"--- last 30 lines of pane {pane} ---")
        print(L.pane_read(pane, 30))
        sys.exit(1)
    print(f"handoff ok: {path} ({size} bytes)")

    new_label = label + "-2" if not label.endswith("-2") else label[:-2] + "-3"
    cmd_spawn(new_label, "@" + path, model=model, cwd=cwd, group=group)
    cmd_kill(m["name"])


SELF_HANDOFF = """# Handoff: the orchestrator seat

Written by {me} at {stamp}. You are the replacement orchestrator. You have none of the earlier
context and must not ask your human to repeat anything below. Read the `orchestrate` skill first:
`{skill}`.

## Goal

Be the one session the human talks to. Read what every other session is doing, answer their
questions, push them along, start new ones for new ideas, and retire the ones that are finished.

## Next steps

1. `py "{orch}" kill {me}`      <-- do this first; the old orchestrator is still running
2. `py "{orch}" resume`         re-arms the Monitors and lists what needs a nudge
3. `py "{orch}" doctor`         one red/green line per moving part
4. Open the board: `py "{board}" open` and give your human the URL.

## The board is the durable part

Nothing below was reconstructed from memory - it is a verbatim copy of
`{state}`, which survives this rotation. Every open question, every
Show block, the session table and the Done list are all still there, and `board render` rebuilds
the page from it. Answer small reversible questions yourself; only human-class ones stay on the
board (money, deletes, anything sent to other people, anything that changes their plans).

### Open questions on the board

{open_qs}

### Sessions the board last recorded

{sess}

## state.json, verbatim

```json
{state_json}
```

## Open questions

None beyond the board above.

## Files that matter

- `{board}` - every board command; `board render` after any hand-edit of state.json
- `{watcher}` - the Monitor that turns answers on the board into `board answer`
- `{orch}` - ls, spawn, task, rotate, rotate-self, resume, doctor, chrome, reap, kill, account
- `{state}` - the board itself, the source of truth
"""


def cmd_rotate_self(model="fable", group=None, cwd=None, dry=False):
    """Rotate the orchestrator seat. `rotate` cannot do this - it waits on a handoff written by the
    session it is running inside, which can never finish its own turn. Here the board IS the
    handoff: state.json is durable and gets copied in whole, so the replacement loses nothing."""
    sys.path.insert(0, SCRIPTS)
    import board as B

    group = group or L.ORCHESTRATOR_TAB
    cwd = cwd or L.DEFAULT_CWD
    # Only the orchestrator itself may rotate the orchestrator. Claude Code exports the caller's
    # own id as CLAUDE_CODE_SESSION_ID; on 2026-09-04 a throwaway test session ran this command,
    # it resolved the seat by tab label, relabelled the live orchestrator `orch-retiring`, and
    # spawned a replacement whose first order was to kill it. Set ORCH_ALLOW_FOREIGN_ROTATE=1
    # only when running it deliberately from outside.
    caller = os.environ.get("CLAUDE_CODE_SESSION_ID", "")
    seat = L.orchestrator_sid() or ""
    if caller and seat and caller != seat and not os.environ.get("ORCH_ALLOW_FOREIGN_ROTATE"):
        sys.exit(f"refusing: rotate-self was called from session {caller[:8]} but the orchestrator "
                 f"seat is {seat[:8]}. Run it from the orchestrator, or set ORCH_ALLOW_FOREIGN_ROTATE=1.")
    me = L.orchestrator_name()
    s = B.load()
    open_qs = [q for q in s["questions"] if not q.get("answered")]
    orch = os.path.abspath(__file__)
    body = SELF_HANDOFF.format(
        me=me, stamp=datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
        skill=os.path.join(L.SKILL, "SKILL.md"), orch=orch,
        board=os.path.join(SCRIPTS, "board.py"),
        watcher=os.path.join(SCRIPTS, "board_watch.py"), state=B.STATE,
        open_qs="\n".join(f"- **{q['id'].upper()}** {q.get('title', '')}"
                          f"{(' (from ' + q['from'] + ')') if q.get('from') else ''}"
                          for q in open_qs) or "- none; the board is clear",
        sess="\n".join(f"- `{r.get('pane')}` {r.get('state')} - {r.get('doing')}"
                       for r in s["sessions"]) or "- none recorded",
        state_json=json.dumps(s, indent=2, ensure_ascii=False))

    path = path_for(me)
    if dry:
        print(f"--dry-run: would write {path}\n")
        print(body)
        print(f"\n--dry-run: would then run  spawn {L.ORCHESTRATOR_TAB} @{path} "
              f"--model {model} --group {group}")
        return path
    open(path, "w", encoding="utf-8").write(body)
    print(f"handoff written: {path} ({os.path.getsize(path)} bytes), "
          f"{len(open_qs)} open question(s) carried over")
    # Relabel this seat first so orchestrator_sid() never picks the retiring pane while both
    # share the tab (four task files pointed builders at the dying seat on 2026-09-03).
    mine = _orch_pane()
    L.herdr("pane", "rename", mine["pane_id"], "orch-retiring", soft=True)
    cmd_spawn(L.ORCHESTRATOR_TAB, "@" + path, model=model, cwd=cwd, group=group)
    print(f"replacement spawned. It kills {me} as its first step - stop here.")
    return path


# ---------------------------------------------------------------- task intake

CLAUDE_REPORT_TO = (
    "{orchestrator} - `SendMessage` it your final report, and any question you hit.\n"
    "It speaks for the human on small reversible decisions; act on its answers as theirs.")

# Codex has no SendMessage and no peer messaging at all, so its report is a file the watcher
# looks for, and the bare word DONE on the last line is how it says it has stopped.
CODEX_REPORT_TO = (
    "You cannot send messages to another session. Write your final report to `{report}` - what you\n"
    "did, the exact commands you ran, and the proof - then print `DONE` on its own line as the last\n"
    "line of your reply. The orchestrator ({orchestrator}) watches for that file and reads it.\n"
    "A question you cannot answer goes in the same file; print `DONE` anyway so it gets read.")

CLAUDE_SHELL_RULE = ("Never `cd X && ...` in the Bash tool; use absolute paths. "
                     "Never a heredoc in Bash; use the Write tool.")
CODEX_SHELL_RULE = "Never `cd X && ...` in a shell call; use absolute paths."


def cmd_task(label, goal, done, out=None, model="sonnet", group=None, cwd=None,
             kind="claude", report=None):
    cwd = cwd or L.DEFAULT_CWD
    os.makedirs(L.HANDOFFS, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y-%m-%d-%H%M")
    path = os.path.join(L.HANDOFFS, f"task-{label}-{stamp}.md")
    orchestrator = L.orchestrator_name()
    if kind == "claude":
        report, report_to, shell_rule = "", CLAUDE_REPORT_TO, CLAUDE_SHELL_RULE
    else:
        report = report or os.path.join(L.HANDOFFS, f"report-{label}.md")
        report_to, shell_rule = CODEX_REPORT_TO, CODEX_SHELL_RULE
    body = open(TASK_TEMPLATE, encoding="utf-8").read().format(
        label=label, goal=goal, done=done, cwd=cwd,
        out=out or (report if report else "(none - report by SendMessage)"),
        shell_rule=shell_rule,
        report_to=report_to.format(orchestrator=orchestrator, report=report))
    open(path, "w", encoding="utf-8").write(body)
    print(f"task file: {path}")
    cmd_spawn(label, f"Read {path} and do exactly what it says.",
              model=model, cwd=cwd, group=group, kind=kind, report=report)
    return path


# ---------------------------------------------------------------- reap

def reap_candidates(hours=6):
    """Idle + quiet for N hours + last words classify DONE. Never the orchestrator."""
    cutoff = time.time() - hours * 3600
    skip = {L.orchestrator_sid()}
    by_sid = L.panes_by_sid()
    out = []
    for sid, m in L.sessions().items():
        if sid in skip or m["status"] != "idle":
            continue
        if L.last_assistant_ts(sid) > cutoff:
            continue
        kind, said = L.classify(sid)
        # No readable last words means we cannot prove it finished clean. Leave it alone.
        if kind != "DONE" or not said.strip():
            continue
        p = by_sid.get(sid, {})
        out.append((m["name"], p.get("terminal_title_stripped", "")[:40], said))
    return out


def cmd_reap(hours=6, dry=False):
    cands = reap_candidates(hours)
    if not cands:
        print(f"reap: nothing idle+DONE older than {hours}h")
        return
    for name, title, said in cands:
        if dry:
            print(f"WOULD CLOSE {name:12} {title:40} | {said[:120]}")
        else:
            cmd_kill(name, quiet=True)
            print(f"CLOSED {name:12} {title:40} | {said[:120]}")
    print(f"reap: {len(cands)} session(s) {'listed' if dry else 'closed'}")


# ---------------------------------------------------------------- main

def main():
    a = sys.argv[1:]
    cmd = a[0] if a else "ls"
    if cmd == "ls":
        cmd_ls()
    elif cmd == "path":
        cmd_path(a[1])
    elif cmd == "spawn":
        cmd_spawn(a[1], a[2], _opt(a, "--model", "sonnet"), _opt(a, "--cwd"),
                  _opt(a, "--workspace"), _opt(a, "--group"),
                  _opt(a, "--kind", "claude"), _opt(a, "--report", ""))
    elif cmd == "rotate":
        cmd_rotate(a[1], _opt(a, "--model", "sonnet"), _opt(a, "--group"), _opt(a, "--cwd"))
    elif cmd == "task":
        cmd_task(a[1], _opt(a, "--goal", ""), _opt(a, "--done", ""), _opt(a, "--out"),
                 _opt(a, "--model", "sonnet"), _opt(a, "--group"), _opt(a, "--cwd"),
                 _opt(a, "--kind", "claude"), _opt(a, "--report"))
    elif cmd == "reap":
        cmd_reap(int(_opt(a, "--hours", 6)), "--dry-run" in a)
    elif cmd == "kill":
        cmd_kill(a[1])
    elif cmd == "show":
        cmd_show(a[1], _opt(a, "--ratio", "0.5"))
    elif cmd == "hide":
        cmd_hide(a[1])
    elif cmd == "chrome":
        cmd_chrome(a[1] if len(a) > 1 else None, a[2] if len(a) > 2 else None)
    elif cmd == "resume":
        cmd_resume()
    elif cmd == "doctor":
        cmd_doctor()
    elif cmd == "account":
        cmd_account(a[1] if len(a) > 1 else None)
    elif cmd == "rotate-self":
        cmd_rotate_self(_opt(a, "--model", "fable"), _opt(a, "--group"),
                        _opt(a, "--cwd"), "--dry-run" in a)
    elif cmd == "board":
        import board
        board.main(a[1:])
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
