"""ACP client: the protocol, and the permission policy. No orchestrator imports, stdlib only.

ACP (Agent Client Protocol): the agent runs as a child program and we exchange JSON-RPC 2.0 lines
on its stdin and stdout, instead of typing into its screen. A request has an `id` and a `method`;
the answer carries the same `id`. A line with a `method` and no `id` is a notification.

Design: acp/DESIGN.md (A0, reviewed SHIP). Pins and rollback: references/acp.md. One pane's life
(spawn, prompt, cancel, resume, tee, state file) is acp_pane.py; this file is only the wire.

Decision D2 (Ryan, 2026-10-08): Claude Max SUBSCRIPTION login only. `child_env` removes the API
key variables and `require_subscription` stops the spawn unless the adapter itself reports
`kind == "account"`. No code path here sets or reads an API key.
"""
import ctypes
import json
import os
import re
import shutil
import subprocess
import threading
import time

PROTOCOL = 1
NODE_MIN = 22
ADAPTER_REL = os.path.join("node_modules", "@agentclientprotocol", "claude-agent-acp", "dist", "index.js")
SKILL = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LIVE_ACP = os.path.join(os.path.expanduser("~"), ".claude", "skills", "orchestrate", "acp")

# Never reach the child: the API key (D2), and the markers that would make the child believe it
# is nested inside THIS claude session. HERDR_* is kept on purpose (DESIGN 2.1).
API_KEY_VARS = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")
NESTED_MARKERS = ("CLAUDECODE", "CLAUDE_CODE_SESSION_ID", "CLAUDE_CODE_CHILD_SESSION", "CLAUDE_PID",
                  "CLAUDE_CODE_MESSAGING_SOCKET", "CLAUDE_CODE_MESSAGING_TOKEN",
                  "CLAUDE_CODE_ENTRYPOINT", "CLAUDE_CODE_EXECPATH", "CLAUDE_EFFORT",
                  "CLAUDE_CODE_SESSION_ATTENDED")


class AcpError(Exception):
    """A JSON-RPC error answer, from the agent or raised by a handler for the agent."""

    def __init__(self, code, message, data=None):
        super().__init__(f"{code}: {message}")
        self.code, self.message, self.data = code, message, data


class AcpClosed(Exception):
    """The adapter exited (or was closed) while a request was open."""


class AuthRefused(Exception):
    """The adapter reported a login that is not the subscription (D2): Claude Max, or ChatGPT for codex."""


# ---------------------------------------------------------------- locating the adapter

def adapter_path(override=None):
    """The pinned adapter's index.js: $ACP_ADAPTER, else this skill's acp/node_modules, else the
    live tree's (a worktree has no node_modules; `npm ci --omit=optional` in acp/ makes one).
    Never `npx`: npx would resolve whatever version is newest on a bad day."""
    cands = [override or os.environ.get("ACP_ADAPTER", ""),
             os.path.join(SKILL, "acp", ADAPTER_REL), os.path.join(LIVE_ACP, ADAPTER_REL)]
    for c in cands:
        if c and os.path.isfile(c):
            return c
    return ""


def node_exe():
    return shutil.which("node") or ""


def node_major(exe=None):
    """The installed node's major version, or 0 when it cannot be read."""
    exe = exe or node_exe()
    if not exe:
        return 0
    try:
        out = subprocess.run([exe, "--version"], capture_output=True, text=True, timeout=15).stdout
        return int(out.strip().lstrip("v").split(".")[0])
    except (OSError, ValueError, subprocess.SubprocessError):
        return 0


def claude_exe():
    """Ryan's installed claude (not the SDK's bundled copy, which `npm ci --omit=optional` skips)."""
    c = os.environ.get("CLAUDE_CODE_EXECUTABLE") or os.path.join(os.path.expanduser("~"), ".local", "bin",
                                                                  "claude.exe")
    return c if os.path.isfile(c) else ""


def child_env(base=None, log_dir=""):
    """The adapter's environment: no API key (D2), no nested-session markers, Ryan's claude.exe.
    HERDR_* and CLAUDE_CODE_OAUTH_TOKEN (account switching) pass through untouched."""
    env = {k: v for k, v in (base if base is not None else os.environ).items()
           if k not in API_KEY_VARS and k not in NESTED_MARKERS}
    exe = claude_exe()
    if exe:
        env["CLAUDE_CODE_EXECUTABLE"] = exe
    if log_dir:
        env["CLAUDE_AGENT_LOGS"] = log_dir
    return env


def require_subscription(auth, env=None):
    """D2 guard. `auth` is the adapter's `authStatus`; only `kind == "account"` (a claude.ai
    login) passes. An api_key, gateway, external or missing answer stops the spawn.

    One measured exception (probe 4, A1 2026-10-09): a pane on a switched account runs on a
    per-process `CLAUDE_CODE_OAUTH_TOKEN`, and the adapter's status check does not see that
    variable, so it reports kind "none" / "Not logged in" even though the login is the
    subscription's. That case passes only when `env` carries an OAuth token (`sk-ant-oat...`, the
    subscription kind; an API key is `sk-ant-api...`) and no API key variable is set."""
    kind = (auth or {}).get("kind")
    if kind == "account":
        return auth
    env = env or {}
    if (kind == "none" and str(env.get("CLAUDE_CODE_OAUTH_TOKEN", "")).startswith("sk-ant-oat")
            and not any(env.get(v) for v in API_KEY_VARS)):
        return {**auth, "kind": "account", "label": "subscription OAuth token (account switch)"}
    raise AuthRefused(f"adapter reports auth kind={kind!r} label={(auth or {}).get('label')!r}; "
                      f"only the subscription login (kind 'account': Claude Max, or ChatGPT for codex) is allowed")


# ---------------------------------------------------------------- Windows job object

class _JobLimits(ctypes.Structure):
    _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                ("LimitFlags", ctypes.c_uint32), ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", ctypes.c_uint32),
                ("Affinity", ctypes.c_size_t), ("PriorityClass", ctypes.c_uint32),
                ("SchedulingClass", ctypes.c_uint32)]


class _IoCounters(ctypes.Structure):
    _fields_ = [(n, ctypes.c_uint64) for n in ("ReadOps", "WriteOps", "OtherOps", "ReadBytes",
                                                "WriteBytes", "OtherBytes")]


class _JobExtended(ctypes.Structure):
    _fields_ = [("Basic", _JobLimits), ("Io", _IoCounters), ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t), ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t)]


class KillOnCloseJob:
    """A Windows Job Object with KILL_ON_JOB_CLOSE. The adapter (and every claude it starts) joins
    it, so when this client dies for any reason the OS kills the whole tree: no orphaned claude
    keeps burning subscription after a crash. No-op elsewhere. `ok` says whether it took."""

    def __init__(self):
        self.handle, self.ok = None, False
        if os.name != "nt":
            return
        k = ctypes.windll.kernel32
        k.CreateJobObjectW.restype = ctypes.c_void_p
        k.CreateJobObjectW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p]
        k.SetInformationJobObject.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_uint32]
        k.AssignProcessToJobObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        h = k.CreateJobObjectW(None, None)
        if not h:
            return
        info = _JobExtended()
        info.Basic.LimitFlags = 0x2000          # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if k.SetInformationJobObject(h, 9, ctypes.byref(info), ctypes.sizeof(info)):
            self.handle, self.ok = h, True

    def add(self, proc):
        """Put a Popen in the job. False when it could not (it is already in a job that forbids
        nesting, for example); the caller then relies on taskkill at close."""
        if not self.ok:
            return False
        k = ctypes.windll.kernel32
        return bool(k.AssignProcessToJobObject(self.handle, ctypes.c_void_p(int(proc._handle))))


def kill_tree(pid):
    """Kill a process and its children."""
    if os.name == "nt":
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(pid)], capture_output=True)
    else:
        subprocess.run(["pkill", "-9", "-P", str(pid)], capture_output=True)
        subprocess.run(["kill", "-9", str(pid)], capture_output=True)


# ---------------------------------------------------------------- the wire

class AcpClient:
    """JSON-RPC 2.0 over a child's stdio: bytes, LF framing, one reader thread.

    The reader thread hands every line to `_dispatch`. A response wakes the thread blocked in
    `request`. A notification goes to `on_notification(method, params)` on the reader thread (fast
    and in order). A request FROM the agent runs `on_request(method, params)` on its own thread, so
    a permission question that waits on a human never stops the stream. `on_request` returns the
    result dict, or raises AcpError to answer with an error. Default: -32601 method not found.
    """

    def __init__(self, cmd, cwd, env, stderr_path=None, on_notification=None, on_request=None,
                 wire_log=None, job=True):
        self.cmd, self.cwd, self.env = list(cmd), cwd, env
        self.stderr_path, self.wire_log = stderr_path, wire_log
        self.on_notification, self.on_request = on_notification, on_request
        self.proc, self.auth = None, None
        self._auth_event = threading.Event()
        self._next_id, self._pending = 0, {}
        self._lock, self._write_lock = threading.Lock(), threading.Lock()
        self._closed = threading.Event()
        self._use_job, self.job = job, None
        self._stderr = None
        self.noise = []                       # non-JSON lines the agent printed on stdout

    # -- process
    def start(self):
        if self.stderr_path:
            os.makedirs(os.path.dirname(self.stderr_path), exist_ok=True)
            self._stderr = open(self.stderr_path, "ab")
        self.proc = subprocess.Popen(self.cmd, cwd=self.cwd, env=self.env, stdin=subprocess.PIPE,
                                     stdout=subprocess.PIPE, stderr=self._stderr or subprocess.DEVNULL)
        if self._use_job:
            self.job = KillOnCloseJob()
            self.job.add(self.proc)
        threading.Thread(target=self._read, daemon=True, name="acp-reader").start()
        return self

    @property
    def pid(self):
        return self.proc.pid if self.proc else 0

    @property
    def closed(self):
        return self._closed.is_set()

    def close(self, grace=20):
        """EOF on stdin: the adapter disposes the SDK and exits. After `grace` s, kill the tree."""
        if not self.proc:
            return
        try:
            self.proc.stdin.close()
        except (OSError, ValueError):
            pass
        try:
            self.proc.wait(timeout=grace)
        except subprocess.TimeoutExpired:
            kill_tree(self.proc.pid)
            try:
                self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                pass
        self._closed.set()
        self._fail_pending()
        if self._stderr:
            self._stderr.close()

    # -- reading
    def _wire(self, arrow, data):
        if self.wire_log:
            with open(self.wire_log, "ab") as fh:
                fh.write(arrow.encode() + b" " + (data if isinstance(data, bytes) else data.encode()) + b"\n")

    def _read(self):
        try:
            for raw in iter(self.proc.stdout.readline, b""):
                line = raw.rstrip(b"\r\n")
                if not line:
                    continue
                self._wire("<<<", line)
                try:
                    msg = json.loads(line)
                except ValueError:
                    self.noise.append(line.decode("utf-8", "replace")[:200])
                    continue
                if isinstance(msg, dict):
                    self._dispatch(msg)
        finally:
            self._closed.set()
            self._fail_pending()

    def _fail_pending(self):
        with self._lock:
            pend, self._pending = self._pending, {}
        for slot in pend.values():
            slot["error"] = AcpClosed("adapter exited")
            slot["event"].set()

    def _dispatch(self, msg):
        method, mid = msg.get("method"), msg.get("id")
        if method and mid is not None:
            threading.Thread(target=self._serve, args=(msg,), daemon=True, name="acp-serve").start()
        elif method:
            params = msg.get("params") or {}
            if method == "_auth/status_update":
                self.auth = params.get("authStatus")
                self._auth_event.set()
            if self.on_notification:
                try:
                    self.on_notification(method, params)
                except Exception as e:              # a printer bug must never kill the reader
                    self.noise.append(f"on_notification {method} raised {type(e).__name__}: {e}")
        else:
            with self._lock:
                slot = self._pending.pop(mid, None)
            if slot:
                slot["msg"] = msg
                slot["event"].set()

    def _serve(self, msg):
        try:
            if not self.on_request:
                raise AcpError(-32601, f"client does not serve {msg['method']}")
            result = self.on_request(msg["method"], msg.get("params") or {})
            self._send({"jsonrpc": "2.0", "id": msg["id"], "result": result if result is not None else {}})
        except AcpError as e:
            self._send({"jsonrpc": "2.0", "id": msg["id"], "error": {"code": e.code, "message": e.message}})
        except AcpClosed:
            pass
        except Exception as e:
            self._send({"jsonrpc": "2.0", "id": msg["id"],
                        "error": {"code": -32603, "message": f"{type(e).__name__}: {e}"}})

    # -- writing
    def _send(self, msg):
        data = json.dumps(msg, separators=(",", ":")).encode("utf-8")
        self._wire(">>>", data)
        with self._write_lock:
            try:
                self.proc.stdin.write(data + b"\n")
                self.proc.stdin.flush()
            except (OSError, ValueError) as e:
                raise AcpClosed(f"cannot write to the adapter: {e}")

    def notify(self, method, params=None):
        self._send({"jsonrpc": "2.0", "method": method, "params": params or {}})

    def request(self, method, params=None, timeout=None):
        """Send a request and block for its answer. -> the result dict. Raises AcpError for a
        JSON-RPC error, AcpClosed if the adapter exits first, TimeoutError after `timeout`."""
        with self._lock:
            self._next_id += 1
            rid = self._next_id
            slot = {"event": threading.Event(), "msg": None, "error": None}
            self._pending[rid] = slot
        try:
            self._send({"jsonrpc": "2.0", "id": rid, "method": method, "params": params or {}})
        except AcpClosed:
            with self._lock:
                self._pending.pop(rid, None)
            raise
        if not slot["event"].wait(timeout):
            with self._lock:
                self._pending.pop(rid, None)
            raise TimeoutError(f"{method} had no answer in {timeout}s")
        if slot["error"]:
            raise slot["error"]
        msg = slot["msg"]
        if "error" in msg:
            err = msg["error"] or {}
            raise AcpError(err.get("code", -32603), err.get("message", "error"), err.get("data"))
        return msg.get("result") or {}

    # -- the methods the orchestrator uses
    def initialize(self, name="orchestrate-acp", version="1", timeout=60):
        return self.request("initialize", {
            "protocolVersion": PROTOCOL,
            # No client fs and no client terminal: Claude Code then uses its own tools, so every
            # hook sees every call exactly as in a pane (DESIGN 2.2).
            "clientCapabilities": {"fs": {"readTextFile": False, "writeTextFile": False}, "terminal": False},
            "clientInfo": {"name": name, "version": version}}, timeout)

    def wait_auth(self, timeout=10):
        """The adapter pushes `_auth/status_update` on its own. -> the authStatus, or None."""
        self._auth_event.wait(timeout)
        return self.auth

    def new_session(self, cwd, name="", timeout=180):
        params = {"cwd": cwd, "mcpServers": []}
        if name:
            # reaches `claude --name`, so the registry row carries the pane label (probe 1, A1)
            params["_meta"] = {"claudeCode": {"options": {"extraArgs": {"name": name}}}}
        return self.request("session/new", params, timeout)

    def load_session(self, session_id, cwd, timeout=180):
        return self.request("session/load", {"sessionId": session_id, "cwd": cwd, "mcpServers": []}, timeout)

    def set_mode(self, session_id, mode_id, timeout=30):
        return self.request("session/set_mode", {"sessionId": session_id, "modeId": mode_id}, timeout)

    def set_config_option(self, session_id, config_id, value, timeout=30):
        return self.request("session/set_config_option",
                            {"sessionId": session_id, "configId": config_id, "value": value}, timeout)

    def authenticate(self, method_id, timeout=120):
        return self.request("authenticate", {"methodId": method_id}, timeout)

    def prompt(self, session_id, text):
        """One turn. Blocks until the agent ends it. -> stopReason (end_turn, cancelled, ...)."""
        res = self.request("session/prompt", {"sessionId": session_id,
                                              "prompt": [{"type": "text", "text": text}]})
        return res.get("stopReason", "")

    def cancel(self, session_id):
        """The Escape key. A notification: the open session/prompt then answers `cancelled`."""
        self.notify("session/cancel", {"sessionId": session_id})


def config_values(config_options, config_id):
    """The select values a session offers for one config option ("model", "effort", "mode")."""
    for opt in config_options or []:
        if opt.get("id") == config_id:
            return [o.get("value") for o in opt.get("options") or []]
    return []


def pick_config_value(config_options, config_id, wanted):
    """The first of `wanted` that the session offers for `config_id`, else None. Tier names
    (`sonnet`, `opus`) and full ids (`claude-fable-5-1[1m]`) are both offered by the adapter."""
    have = config_values(config_options, config_id)
    return next((w for w in wanted if w and w in have), None)


def pick_config(config_options, config_ids, wanted):
    """(config id, value) for the first id in `config_ids` that the session has at all. codex-acp
    calls the effort dial `reasoning_effort`; the claude adapter calls it `effort`. The value is
    None when the session has the dial but none of `wanted`."""
    for cid in config_ids:
        if config_values(config_options, cid):
            return cid, pick_config_value(config_options, cid, wanted)
    return None, None


def mode_ids(session_result):
    """The mode ids a `session/new` result offers (codex-acp: read-only, workspace-write, agent,
    agent-full-access). Empty when the agent has no modes."""
    modes = (session_result or {}).get("modes") or {}
    return [m.get("id") for m in modes.get("availableModes") or [] if isinstance(m, dict)]


# ---------------------------------------------------------------- permission policy

# board/acp-policy.json overrides any key. Reads and search auto-approve. Writes auto-approve
# inside the pane's cwd and `write_roots`. Everything on the Ryan-only list (money, deletes,
# anything sent to other people, credentials, publishing, a school submission) becomes a board
# ask. `deny_commands` is belt and braces behind deny-irreversible.py: answered reject at once.
DEFAULT_POLICY = {
    "version": 1,
    "mode": "acceptEdits",
    # Per harness. claude: acceptEdits asks for everything but cwd edits. codex-acp: workspace-write
    # edits the workspace and asks before writing outside it or using the network. pi has no
    # permission modes at all; its only questions are extension dialogs (select/confirm), and
    # `dialog_allow` / `dialog_reject` say which option labels mean yes and no, and `dialog_default`
    # (ask or allow) says what a dialog that matches no list gets: an extension that stops to ask a
    # person meant a person to answer, so the default is the board.
    "harnesses": {
        "claude": {"mode": "acceptEdits"},
        "codex": {"mode": "workspace-write"},
        "pi": {"mode": "", "dialog_default": "ask", "dialog_allow": r"(?i)^\s*(allow|yes|ok|okay|approve|proceed|continue|accept|run|confirm)\b",
               "dialog_reject": r"(?i)^\s*(block|deny|no|reject|cancel|stop|abort|decline|skip|refuse)\b"},
    },
    "ask_timeout_s": 1800,
    "default": "allow",
    "read_kinds": ["read", "search", "think", "switch_mode"],
    "write_roots": ["~/.claude/handoffs", "~/Vault/tmp", "%TEMP%"],
    "deny_commands": [
        r"\bgit\b[^|;&\n]*\bpush\b[^|;&\n]*(\s--force\b|\s--force-with-lease\b|\s--mirror\b|\s-f\b|\s\+\S)",
        r"\bgit\s+reset\s+--hard\b",
        r"\bgit\s+clean\s+-\w*f",
        r"(?i)google[\\/]chrome[\\/].*(cookies|login data)",
    ],
    "ask_commands": [
        r"(?i)\b(stripe|paypal|venmo|checkout)\b[^|;&\n]*(charge|pay|purchase|buy|refund|transfer)",
        r"(?i)\b(sendmail|send-mailmessage|mail_code\.py\s+send|gh\s+(pr|issue)\s+comment|slack[-_ ]?(post|send))\b",
        r"(?i)youtube[^|;&\n]*\b(upload|publish)\b|\bupload_video\b",
        r"(?i)\b(canvas|gradeportal|blackboard)\b[^|;&\n]*\bsubmit",
        r"(?i)\|\s*(sh|bash|zsh|iex|Invoke-Expression)\b",
        r"(?i)\bsecret-vault\b|\.credentials\.json|[\\/]\.ssh[\\/]|(^|[\s\\/])\.env(\.[\w.-]+)?(\s|$)",
    ],
    # A recursive delete is Ryan-only unless every target is a plain relative path inside the cwd
    # (`rm -rf build` is routine; `rm -rf ~/x`, `rm -rf ..` and `rm -rf /` are not).
    "delete_commands": [
        r"(?i)\b(rm|del|erase|rmdir|rd|ri|Remove-Item)\b[^|;&\n]*(\s-[a-z]*r|\s/s\b|-Recurse\b|--recursive\b)",
    ],
    "ask_paths": [
        r"(^|[\\/])\.env(\.[\w.-]+)?$",
        r"\.credentials\.json$",
        r"[\\/]\.ssh[\\/]",
    ],
}


def load_policy(path=None):
    """DEFAULT_POLICY with the file's keys laid over it. A missing or broken file is the default,
    never a crash: a policy typo must not stop a pane."""
    pol = json.loads(json.dumps(DEFAULT_POLICY))
    if path and os.path.isfile(path):
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
            if isinstance(data, dict):
                pol.update({k: v for k, v in data.items() if not k.startswith("_")})
        except (OSError, ValueError):
            pass
    return pol


class Decision:
    def __init__(self, action, reason, rule=""):
        self.action, self.reason, self.rule = action, reason, rule     # allow | reject | ask

    def __repr__(self):
        return f"Decision({self.action!r}, {self.reason!r})"


def _norm(p):
    return os.path.normcase(os.path.abspath(os.path.expandvars(os.path.expanduser(p))))


def _inside(path, root):
    try:
        return os.path.commonpath([_norm(path), _norm(root)]) == _norm(root)
    except ValueError:                          # different drives
        return False


def _delete_is_risky(cmd):
    """True unless every target of a delete command is a plain relative path with no `..`."""
    targets = [t.strip("\"'") for t in cmd.split()[1:] if not t.startswith("-") and t.lower() != "/s"]
    if not targets:
        return True
    return any(t in (".", "*") or t.startswith(("/", "\\", "~", "$", "%")) or ".." in t
               or re.match(r"^[A-Za-z]:", t) for t in targets)


def call_paths(call, cwd):
    """Every file path a tool call names, made absolute against the pane's cwd."""
    raw = call.get("rawInput") or {}
    paths = [raw.get(k) for k in ("file_path", "path", "notebook_path") if isinstance(raw.get(k), str)]
    paths += [l.get("path") for l in call.get("locations") or [] if isinstance(l, dict) and l.get("path")]
    return [p if os.path.isabs(p) else os.path.join(cwd, p) for p in dict.fromkeys(paths)]


def decide(call, cwd, policy=None):
    """What to do with one `session/request_permission` tool call -> Decision.

    Order: hard deny, then Ryan-only (ask), then the kind rules. Hooks already ran before this
    (a hook block never reaches the permission check, DESIGN 1), so this only subtracts from what
    a bypass pane would allow today.
    """
    pol = policy or DEFAULT_POLICY
    kind = call.get("kind") or "other"
    raw = call.get("rawInput") or {}
    cmd = raw.get("command")
    if isinstance(cmd, list):                    # an argv, not a string (some agents send one)
        cmd = " ".join(str(c) for c in cmd)
    cmd = cmd if isinstance(cmd, str) else ""
    if not cmd and kind == "execute" and isinstance(call.get("title"), str):
        cmd = call["title"]                      # an execute call that names its command only in the title
    if cmd:
        for rx in pol.get("deny_commands", []):
            if re.search(rx, cmd):
                return Decision("reject", "matches the deny list", rx)
        for rx in pol.get("ask_commands", []):
            if re.search(rx, cmd):
                return Decision("ask", "on the Ryan-only list", rx)
        for rx in pol.get("delete_commands", []):
            if re.search(rx, cmd) and _delete_is_risky(cmd):
                return Decision("ask", "recursive delete outside a plain relative path", rx)
        if call.get("_dialog") and harness_policy(pol, "pi").get("dialog_default") == "ask":
            return Decision("ask", "an extension dialog asked a person")
        return Decision("allow", "command, nothing on the lists")
    paths = call_paths(call, cwd)
    for p in paths:
        for rx in pol.get("ask_paths", []):
            if re.search(rx, p):
                return Decision("ask", "credential file", rx)
    if kind in pol.get("read_kinds", []):
        return Decision("allow", f"{kind} is auto-approved")
    if kind in ("edit", "delete", "move"):
        if not paths:
            return Decision("ask", f"{kind} with no path to check")
        roots = [cwd] + list(pol.get("write_roots", []))
        outside = [p for p in paths if not any(_inside(p, r) for r in roots)]
        if kind == "delete":
            outside = [p for p in paths if not _inside(p, cwd)]
        if outside:
            return Decision("ask", f"{kind} outside the pane's cwd: {outside[0]}")
        return Decision("allow", f"{kind} inside the pane's cwd")
    return Decision(pol.get("default", "allow"), f"{kind}: policy default")


def harness_policy(policy, harness):
    """The per-harness block of the policy (mode, dialog regexes), defaults filled in. The old
    top-level `mode` still answers for claude, so a policy file written before A2 keeps working."""
    policy = policy or {}
    block = dict(DEFAULT_POLICY["harnesses"].get(harness) or {})
    mine = (policy.get("harnesses") or {}).get(harness) or {}
    block.update(mine)
    if harness == "claude" and "mode" not in mine and policy.get("mode"):
        block["mode"] = policy["mode"]
    return block


def dialog_option_kind(label, hp):
    """Which kind an extension-dialog option label stands for: allow_once, reject_once, or other
    (an option nobody can map to yes or no is never picked)."""
    if re.search(hp.get("dialog_reject", ""), str(label)):
        return "reject_once"
    if re.search(hp.get("dialog_allow", ""), str(label)):
        return "allow_once"
    return "other"


def pick_option(options, action):
    """The optionId to answer with, by KIND never by id (Codex names its ids differently).
    allow -> allow_once, reject -> reject_once. Never `allow_always`: that writes a permission
    rule into Ryan's settings for good. None when the agent offered no safe option."""
    want = "allow_once" if action == "allow" else "reject_once"
    return next((o.get("optionId") for o in options or [] if o.get("kind") == want), None)


def outcome(option_id):
    """The JSON-RPC result for a permission request: a pick, or `cancelled`."""
    if option_id is None:
        return {"outcome": {"outcome": "cancelled"}}
    return {"outcome": {"outcome": "selected", "optionId": option_id}}


def short(text, n=160):
    return " ".join(str(text).split())[:n]
