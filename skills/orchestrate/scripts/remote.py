"""A domain mate on another machine (the Dell XPS 15 "laptop"), over ssh. Built in M1.

Seat side (this box), called from orch.py:
  ls_rows()            rows for `ls`: the host's mates, WHERE = host, or cached UNREACHABLE rows
  start_task(...)      push a task file, start a detached `claude -p` on the host
  kill(host, label)    stop that mate over ssh
  pull(host)           copy NEW reports from the host's outbox into handoffs/remote/<host>/

Host side (runs on the laptop, called through ssh as `py remote.py host-<verb> ...`):
  host-rows | host-list <reldir> | host-start <label> <task> <model> <owner> | host-kill <label>
  | host-owner <label>

Only whole files cross, and only under `.claude/handoffs`. The vault never does. ssh and scp run
with BatchMode (never a password prompt) and ConnectTimeout, and `ORCH_SSH_BIN` / `ORCH_SCP_BIN`
swap the binaries, which is how test_remote.py points the seat at a fake host.
Design: references/remote-mate-design.md ("Built in M1").
"""
import json, os, re, subprocess, sys, tempfile, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import orchlib as L

CONNECT_TIMEOUT = 8
ROWS_TIMEOUT = 10         # `ls` waits at most this long (plus ConnectTimeout) for a host that connects but hangs
NEG_CACHE_S = 60         # after a failed ssh, `ls` answers from the cache for this long, no new ssh
SAFE = re.compile(r"^[A-Za-z0-9._-]+$")
SYNC_ROOT = ".claude/handoffs"
DEFAULT_SYNC = [SYNC_ROOT + "/outbox"]
INBOX = SYNC_ROOT + "/inbox"
STATE_DIR = ".claude/remote-mates"


class HostError(Exception):
    """A remote call failed. `kind` is one of UNREACHABLE_KINDS or 'remote'."""
    def __init__(self, kind, message):
        super().__init__(message)
        self.kind, self.message = kind, message


# ---------------------------------------------------------------- host table

def hosts_path():
    return os.path.join(L.BOARD_DIR, "hosts.json")


def load_hosts():
    """{name: {ssh, home, sync, remote_py, claude, claude_args}}; {} when hosts.json is absent, so
    a box with no host table never touches the network."""
    try:
        with open(hosts_path(), encoding="utf-8") as fh:
            d = json.load(fh)
    except (OSError, ValueError):
        return {}
    return {k: v for k, v in d.items() if isinstance(v, dict) and v.get("ssh")} \
        if isinstance(d, dict) else {}


def get_host(name):
    h = load_hosts().get(name)
    if not h:
        raise HostError("remote", f"no host {name!r} in {hosts_path()} "
                                  f"(copy board/hosts.example.json to hosts.json)")
    for rel in sync_paths(h):
        if not _under_handoffs(rel):
            raise HostError("remote", f"host {name!r}: sync path {rel!r} is outside {SYNC_ROOT}. "
                                      f"Only handoffs cross; the vault never does.")
    h.setdefault("home", "~")
    h.setdefault("remote_py", f"{h['home'].rstrip('/')}/.claude/skills/orchestrate/scripts/remote.py")
    return h


def _under_handoffs(rel):
    rel = rel.replace("\\", "/").strip("/")
    if not (rel == SYNC_ROOT or rel.startswith(SYNC_ROOT + "/")):
        return False
    # every segment is plain (letters, digits, . _ -), so the path can sit on an ssh command line
    return all(SAFE.match(p) and p != ".." for p in rel.split("/"))


def sync_paths(h):
    return h.get("sync") or DEFAULT_SYNC


# ---------------------------------------------------------------- ssh and scp

def _bin(env, default):
    v = os.environ.get(env, "")
    if not v:
        return [default]
    return json.loads(v) if v.lstrip().startswith("[") else [v]


def _opts():
    return ["-o", "BatchMode=yes", "-o", f"ConnectTimeout={CONNECT_TIMEOUT}",
            "-o", "ServerAliveInterval=15", "-o", "ServerAliveCountMax=3"]


def classify(rc, err, timed_out=False):
    """Name the failure from ssh's own text (design section 7). Returns (kind, message)."""
    low = (err or "").lower()
    if timed_out or "timed out" in low:
        return "timeout", f"unreachable (timeout {CONNECT_TIMEOUT}s): asleep, off, or Tailscale down"
    if "connection refused" in low:
        return "refused", "up, but sshd is not listening"
    if "permission denied" in low:
        return "key", "key refused: add ~/.ssh/id_ed25519.pub to the laptop's authorized keys"
    if "host identification has changed" in low:
        return "hostkey", "host key changed, refusing: check it is really the laptop"
    if "network is unreachable" in low or "no route to host" in low \
            or "could not resolve" in low or "name or service not known" in low:
        return "network", "this box has no route to it (is Tailscale up here?)"
    if rc == 255:
        last = (err or "").strip().splitlines()[-1:] or ["no output"]
        return "ssh", f"ssh failed: {last[0]}"[:200]
    return "", ""


UNREACHABLE_KINDS = {"timeout", "refused", "key", "hostkey", "network", "ssh"}


def _run(cmd, stdin=None, timeout=60):
    """subprocess.run with stdin, stdout and stderr on temp files, never pipes. Windows OpenSSH 9.5
    (ssh.exe and scp.exe) never exits when its stdout/stderr are pipes, so every call hung to its
    timeout and the laptop always read UNREACHABLE (measured 2026-10-10: pipes hang, files 1.4s)."""
    with tempfile.TemporaryFile() as fin, tempfile.TemporaryFile() as fout, \
            tempfile.TemporaryFile() as ferr:
        if stdin is not None:
            fin.write(stdin.encode("utf-8"))
            fin.seek(0)
        rc = subprocess.run(cmd, stdin=fin if stdin is not None else subprocess.DEVNULL,
                            stdout=fout, stderr=ferr, timeout=timeout).returncode
        fout.seek(0)
        ferr.seek(0)
        out, err = (f.read().decode("utf-8", "replace") for f in (fout, ferr))
    return subprocess.CompletedProcess(cmd, rc, out, err)


def ssh(h, remote_cmd, stdin=None, timeout=60):
    """Run one command on the host; returns stdout. HostError on any ssh-level failure, or when the
    remote command itself exits non-zero (kind 'remote')."""
    cmd = _bin("ORCH_SSH_BIN", "ssh") + _opts() + [h["ssh"], remote_cmd]
    try:
        r = _run(cmd, stdin=stdin, timeout=timeout + CONNECT_TIMEOUT)
    except subprocess.TimeoutExpired:
        kind, msg = classify(255, "", timed_out=True)
        raise HostError(kind, msg)
    except OSError as e:
        raise HostError("ssh", f"could not run ssh: {e}")
    kind, msg = classify(r.returncode, r.stderr) if r.returncode == 255 else ("", "")   # 255 = ssh's own
    if kind:
        raise HostError(kind, msg)
    if r.returncode:
        raise HostError("remote", (r.stderr or r.stdout).strip()[-300:] or f"exit {r.returncode}")
    return r.stdout


def scp(h, src, dst):
    cmd = _bin("ORCH_SCP_BIN", "scp") + ["-p"] + _opts() + [src, dst]
    try:
        r = _run(cmd, timeout=60 + CONNECT_TIMEOUT)
    except subprocess.TimeoutExpired:
        kind, msg = classify(255, "", timed_out=True)
        raise HostError(kind, msg)
    except OSError as e:
        raise HostError("ssh", f"could not run scp: {e}")
    kind, msg = classify(r.returncode, r.stderr) if r.returncode == 255 else ("", "")
    if kind or r.returncode:
        raise HostError(kind or "remote", msg or (r.stderr or "").strip()[-300:])


def _remote_py(h, verb, *args):
    """`py <remote_py> host-<verb> args`. Every arg is checked against SAFE, so nothing needs
    quoting through the laptop's shell; text travels by file or stdin, never on this line."""
    for a in args:
        if not SAFE.match(str(a)) and not _under_handoffs(str(a)):
            raise HostError("remote", f"refusing an unsafe argument for the host: {a!r}")
    py = h.get("py", "py")
    return f'{py} "{h["remote_py"]}" host-{verb} ' + " ".join(str(a) for a in args)


# ---------------------------------------------------------------- cache (for UNREACHABLE)

def _cache_path():
    return os.path.join(L.BOARD_DIR, "remote-cache.json")


def _load(path):
    try:
        with open(path, encoding="utf-8") as fh:
            d = json.load(fh)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _save(path, d):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(d, fh, indent=1)
    os.replace(tmp, path)


# ---------------------------------------------------------------- ls

def _row(host, r):
    """One `ls` tuple (the 13 fields cmd_ls builds) from a host row. WHERE is the host name and
    HARNESS is whatever the host reported."""
    name = f"{r['name']}@{host}"
    return (name, r.get("status", "?"), "   -", r.get("harness", "claude"), r.get("model", "-"),
            f"{host}:{r.get('id', '-')}", r.get("status", "?"), r.get("kind", "?"),
            r.get("owner") or "-", host, "", "", "")


def ls_rows(now=None):
    """([ls row tuples], [note lines]) for every host in hosts.json. Never raises, never hangs
    `ls`: a failed host answers from the last good snapshot with status UNREACHABLE, and is not
    asked again for NEG_CACHE_S. UNREACHABLE is not VANISHED: the pane may be fine, we cannot see."""
    now = time.time() if now is None else now
    rows, notes = [], []
    cache = _load(_cache_path())
    for host in load_hosts():
        c = cache.get(host, {})
        neg = bool(c.get("fail")) and now - c.get("fail_ts", 0) < NEG_CACHE_S
        try:
            if neg:
                raise HostError(c["fail"], c.get("reason", "unreachable"))
            h = get_host(host)
            data = json.loads(ssh(h, _remote_py(h, "rows"), timeout=ROWS_TIMEOUT))
            live = data["rows"]
            [_row(host, r) for r in live]               # a malformed row fails here, inside the try
            skew = data["epoch"] - time.time()
            cache[host] = {"rows": live, "ts": now}
            if abs(skew) > 60:
                notes.append(f"{host}: clock skew {skew:+.0f}s (file checks use names, not times)")
        except (HostError, ValueError, KeyError, TypeError, AttributeError) as e:
            kind = e.kind if isinstance(e, HostError) else "remote"
            reason = e.message if isinstance(e, HostError) else f"bad answer from host: {e}"
            if kind in UNREACHABLE_KINDS:
                if not neg:
                    cache[host] = {**c, "fail": kind, "reason": reason, "fail_ts": now}
                age = f"last seen {int(now - c['ts'])}s ago" if c.get("ts") else "never seen"
                notes.append(f"{host}: {reason}. {age}; cached rows shown UNREACHABLE.")
                live = [{**r, "status": "UNREACHABLE"} for r in c.get("rows", [])]
            else:
                notes.append(f"{host}: {reason}")
                live = []
        rows += [_row(host, r) for r in live]
    _save(_cache_path(), cache)
    return rows, notes


# ---------------------------------------------------------------- task, kill, pull

def task_body(label, goal, done, why="", read_first=(), not_in_scope=(), owner="", report=""):
    bullets = lambda xs: "\n".join(f"- {x}" for x in xs) or "(none)"
    return (f"# Task: {label}\n\n## Goal\n\n{goal}\n\n## Why\n\n{why or '(not given)'}\n\n"
            f"## Done when\n\n{done}\n\n## Out of scope\n\n{bullets(not_in_scope)}\n\n"
            f"## Read first\n\n{bullets(read_first)}\n\n## Report\n\n"
            f"You run on a remote machine. There is no SendMessage and no orchestrator pane here. "
            f"Write your final report to `{report}` as three lines: `Done: ...`, `Proof: ...`, "
            f"`Next: ...`. If you need Ryan to decide something, add a line `ASK: <question>`. "
            f"Do not submit, send or pay anything. Owner: {owner or 'the seat'}.\n")


def start_task(host, label, goal, done, model="sonnet", owner="", why="", read_first=(),
               not_in_scope=()):
    """Push a task file to the host's inbox and start a detached mate on it. Returns a summary line.
    The task file is also kept in handoffs/ here, so a retry after a failed push is safe."""
    if not SAFE.match(label):
        raise HostError("remote", f"label {label!r}: use letters, digits, . _ - only")
    if not SAFE.match(model):
        raise HostError("remote", f"model {model!r}: use a model tier or id")
    h = get_host(host)
    home = h.get("home", "~").rstrip("/\\")
    report = f"{home}/{SYNC_ROOT}/outbox/report-{label}.md"
    os.makedirs(L.HANDOFFS, exist_ok=True)
    local = os.path.join(L.HANDOFFS, f"task-{label}-{host}.md")
    with open(local, "w", encoding="utf-8") as fh:
        fh.write(task_body(label, goal, done, why, read_first, not_in_scope, owner, report))
    # the owner is a free-form session name; keep it ssh-safe
    own = owner if SAFE.match(owner or "") else "seat"
    ssh(h, _remote_py(h, "mkdirs"))
    scp(h, local, f"{h['ssh']}:{INBOX}/task-{label}.md")
    out = ssh(h, _remote_py(h, "start", label, f"{INBOX}/task-{label}.md", model, own))
    return f"started {label}@{host}: {out.strip()}"


def split_name(name, host=None):
    """('label', 'host') from `label@host` or `label` plus --host; host None if neither names one."""
    if host is None and "@" in name:
        label, _, tail = name.rpartition("@")
        if tail in load_hosts():
            return label, tail
    return name, host


def owner(host, label):
    """Who started that mate, as the host's state file recorded it ('' if no one)."""
    h = get_host(host)
    try:
        return json.loads(ssh(h, _remote_py(h, "owner", label))).get("owner") or ""
    except (ValueError, AttributeError):
        raise HostError("remote", f"bad answer from host {host!r} for owner of {label!r}")


def kill(host, label):
    h = get_host(host)
    return ssh(h, _remote_py(h, "kill", label)).strip()


def pull(host):
    """Copy reports on the host that this box has not seen into handoffs/remote/<host>/. 'Seen'
    is by file name and size, never mtime: the two clocks may disagree. Returns [(name, done_line)]."""
    h = get_host(host)
    seen_path = os.path.join(L.BOARD_DIR, "remote-seen.json")
    seen = _load(seen_path)
    mine = set(seen.get(host, []))
    dest = os.path.join(L.HANDOFFS, "remote", host)
    os.makedirs(dest, exist_ok=True)
    new = []
    for rel in sync_paths(h):
        try:
            files = json.loads(ssh(h, _remote_py(h, "list", rel)))
            if not isinstance(files, list):
                raise ValueError("not a list")
            files = [{"name": f["name"], "size": f["size"]} for f in files]
        except (ValueError, KeyError, TypeError):
            raise HostError("remote", f"bad answer from host {host!r} listing {rel}")
        for f in files:
            name = f["name"]
            key = f"{rel}/{name}:{f['size']}"       # name+size: a re-run report has the same name
            if key in mine or not SAFE.match(name):
                continue
            scp(h, f"{h['ssh']}:{rel}/{name}", os.path.join(dest, name))
            mine.add(key)
            new.append((name, _first(os.path.join(dest, name), "Done:")))
    seen[host] = sorted(mine)
    _save(seen_path, seen)
    return new


def _first(path, prefix):
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            return next((ln.strip() for ln in fh if ln.strip().startswith(prefix)), "")
    except OSError:
        return ""


# ---------------------------------------------------------------- host side

def _home():
    return os.environ.get("ORCH_REMOTE_HOME") or os.path.expanduser("~")


def _state_dir():
    return os.path.join(_home(), STATE_DIR)


def proc_start(pid):
    """When that process started, as a number that stays the same for its whole life; None if it
    cannot be read. A pid alone is not an identity: the OS hands a dead pid to a new process."""
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return None
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes
        k = ctypes.windll.kernel32
        k.OpenProcess.restype = wintypes.HANDLE
        h = k.OpenProcess(0x1000, False, pid)           # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return None
        t = [wintypes.FILETIME() for _ in range(4)]
        ok = k.GetProcessTimes(h, *map(ctypes.byref, t))
        k.CloseHandle(h)
        return (t[0].dwHighDateTime << 32) | t[0].dwLowDateTime if ok else None
    try:
        with open(f"/proc/{pid}/stat", encoding="utf-8") as fh:
            return int(fh.read().rpartition(")")[2].split()[19])      # field 22: start ticks
    except (OSError, ValueError, IndexError):
        pass
    try:
        out = subprocess.run(["ps", "-o", "lstart=", "-p", str(pid)], capture_output=True, text=True)
        return out.stdout.strip() or None
    except OSError:
        return None


def mate_alive(s):
    """True while the process this state file recorded is still the one running: the pid is alive
    AND its start time matches the one saved at start. A reused pid reads as ended."""
    if not L.pid_alive(s.get("pid")):
        return False
    want = s.get("pstart")
    return want is None or proc_start(s["pid"]) == want


def host_rows():
    """Every mate started here, as the seat's `ls` wants it. busy = process alive, done = it ended
    and wrote its report, failed = it ended with no report."""
    rows = []
    for f in sorted(os.listdir(_state_dir())) if os.path.isdir(_state_dir()) else []:
        s = _load(os.path.join(_state_dir(), f))
        if not s.get("label"):
            continue
        report = os.path.join(_home(), SYNC_ROOT, "outbox", f"report-{s['label']}.md")
        if s.get("killed"):
            status = "killed"
        elif mate_alive(s):
            status = "busy"
        else:
            status = "done" if os.path.exists(report) else "failed"
        rows.append({"name": s["label"], "id": s.get("pid", "-"), "status": status,
                     "harness": s.get("harness", "claude"), "model": s.get("model", "-"),
                     "owner": s.get("owner", ""), "kind": "remote"})
    return {"epoch": time.time(), "rows": rows}


def host_list(rel):
    if not _under_handoffs(rel):
        sys.exit(f"refusing {rel}: outside {SYNC_ROOT}")
    d = os.path.join(_home(), *rel.split("/"))
    # A file touched in the last MIN_AGE seconds may still be mid-write: leave it for the next pull.
    min_age = float(os.environ.get("ORCH_REMOTE_MIN_AGE", "2"))
    out = []
    for f in sorted(os.listdir(d)) if os.path.isdir(d) else []:
        st = os.stat(os.path.join(d, f))
        if os.path.isfile(os.path.join(d, f)) and time.time() - st.st_mtime >= min_age:
            out.append({"name": f, "size": st.st_size})
    return out


def host_start(label, task_rel, model, owner):
    """Start `claude -p` on the task file as a detached process that survives the ssh session.
    Windows: breakaway from sshd's job object (CREATE_BREAKAWAY_FROM_JOB), else it is killed on
    disconnect. Needs the Claude login to exist on this machine.
    CREATE_NO_WINDOW, never DETACHED_PROCESS: a detached claude has no console, so every bash,
    herdr or powershell child it runs allocates a visible window of its own - the same bug as the
    status-page flashing windows (dc9933c). A hidden console is inherited by every descendant."""
    h = _load_host_cfg()
    os.makedirs(_state_dir(), exist_ok=True)
    os.makedirs(os.path.join(_home(), SYNC_ROOT, "outbox"), exist_ok=True)
    task = os.path.join(_home(), *task_rel.split("/"))
    log = os.path.join(_state_dir(), f"{label}.log")
    claude = h.get("claude", "claude")
    argv = [*(claude if isinstance(claude, list) else [claude]), "-p", f"Read {task} and do exactly what it says.",
            "--model", model, *h.get("claude_args", ["--permission-mode", "acceptEdits"])]
    kw = {"cwd": _home(), "stdin": subprocess.DEVNULL, "stdout": open(log, "ab"),
          "stderr": subprocess.STDOUT}
    if os.name == "nt":
        flags = 0x08000000 | 0x00000200      # CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP
        try:
            p = subprocess.Popen(argv, creationflags=flags | 0x01000000, **kw)   # BREAKAWAY_FROM_JOB
        except OSError:
            p = subprocess.Popen(argv, creationflags=flags, **kw)  # job forbids breakaway
    else:
        p = subprocess.Popen(argv, start_new_session=True, **kw)
    _save(os.path.join(_state_dir(), f"{label}.json"),
          {"label": label, "pid": p.pid, "pstart": proc_start(p.pid), "model": model, "owner": owner,
           "harness": "claude",
           "started": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "log": log})
    return f"pid {p.pid}, report will be {SYNC_ROOT}/outbox/report-{label}.md"


def _load_host_cfg():
    """Host-side options (claude path, claude_args) from `.claude/remote-host.json` if present."""
    return _load(os.path.join(_home(), ".claude", "remote-host.json"))


def host_kill(label):
    path = os.path.join(_state_dir(), f"{label}.json")
    s = _load(path)
    if not s:
        sys.exit(f"no mate {label!r} on this host")
    if mate_alive(s):
        L.kill_tree(s["pid"])
    elif L.pid_alive(s.get("pid")):
        return f"{label} already ended; pid {s.get('pid')} now belongs to another process, not killed"
    _save(path, {**s, "killed": True})
    return f"killed {label} (pid {s.get('pid')})"


def host_owner(label):
    s = _load(os.path.join(_state_dir(), f"{label}.json"))
    if not s:
        sys.exit(f"no mate {label!r} on this host")
    return {"owner": s.get("owner", "")}


def main(argv):
    verb, args = (argv[0] if argv else ""), argv[1:]
    if verb == "host-rows":
        print(json.dumps(host_rows()))
    elif verb == "host-list" and len(args) == 1:
        print(json.dumps(host_list(args[0])))
    elif verb == "host-mkdirs":
        for rel in (INBOX, SYNC_ROOT + "/outbox"):
            os.makedirs(os.path.join(_home(), *rel.split("/")), exist_ok=True)
    elif verb == "host-start" and len(args) == 4:
        print(host_start(*args))
    elif verb == "host-kill" and len(args) == 1:
        print(host_kill(args[0]))
    elif verb == "host-owner" and len(args) == 1:
        print(json.dumps(host_owner(args[0])))
    else:
        sys.exit("remote.py is called by orch.py; host verbs: host-rows, host-list, host-mkdirs, "
                 "host-start, host-kill, host-owner")


if __name__ == "__main__":
    main(sys.argv[1:])
