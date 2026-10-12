"""host_guard.py - is the box starved, or is the pane wedged? Measures RAM, commit, disk and CPU
and prints one line:

    py host_guard.py            # verdict: ok  ram_free_gb: 41.2  commit_pct: 38  disk_free_gb: 812  cpu_pct: 9
    py host_guard.py --json     # the same numbers as one JSON object
    py host_guard.py --help

Exit 0 = ok, 1 = starved (the line ends with why: ...), 2 = bad usage.
It only measures. It never kills, closes or edits anything.

Starved when any one is true (override with the env var in brackets):
    free RAM < 6 GB [ORCH_MIN_FREE_GB] - the floor `orch.py spawn` already refuses under
    commit >= 90 % [HOST_GUARD_COMMIT_PCT]
    free disk < 10 GB on the system drive [HOST_GUARD_MIN_DISK_GB]
    CPU >= 95 % over a 0.5 s sample [HOST_GUARD_CPU_PCT]

Callers use `note()` and `spawn_over_cap()`. Both are OFF until switched on, so wiring them in
changes nothing by default: env HOST_GUARD=on, or a board/host-guard.json with {"enabled": true}.
The per-repo spawn cap is a second opt-in: {"repo_cap": N} in the same file, or env
ORCH_REPO_SPAWN_CAP=N. 0 or absent = no cap.
Idea from Kun's First Mate docs (jev-guards, per-project spawn cap #5343); the code is ours.
"""

import ctypes
import json
import os
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
BOARD_DIR = os.path.join(os.path.dirname(HERE), "board")
CONFIG_PATH = os.path.join(BOARD_DIR, "host-guard.json")
GB = 1024 ** 3

MIN_FREE_GB = 6.0
MAX_COMMIT_PCT = 90.0
MIN_DISK_GB = 10.0
MAX_CPU_PCT = 95.0


class _MemStatus(ctypes.Structure):
    _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]


class _FileTime(ctypes.Structure):
    _fields_ = [("lo", ctypes.c_ulong), ("hi", ctypes.c_ulong)]


def read_memory():
    """(free RAM GB, commit percent)."""
    m = _MemStatus()
    m.dwLength = ctypes.sizeof(_MemStatus)
    ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
    commit = 100 * (m.ullTotalPageFile - m.ullAvailPageFile) / m.ullTotalPageFile
    return m.ullAvailPhys / GB, commit


def read_disk(path=None):
    """Free GB on the system drive (or `path`'s drive)."""
    path = path or os.environ.get("SystemDrive", "C:") + "\\"
    return shutil.disk_usage(path).free / GB


def _system_times():
    idle, kern, user = _FileTime(), _FileTime(), _FileTime()
    ctypes.windll.kernel32.GetSystemTimes(ctypes.byref(idle), ctypes.byref(kern), ctypes.byref(user))
    f = lambda t: (t.hi << 32) | t.lo  # noqa: E731
    return f(idle), f(kern) + f(user)          # kernel time already includes idle


def read_cpu(sample=0.5):
    """Busy percent over a short sample of all cores."""
    i0, t0 = _system_times()
    time.sleep(sample)
    i1, t1 = _system_times()
    total = t1 - t0
    return 0.0 if total <= 0 else 100 * (1 - (i1 - i0) / total)


def _env_float(name, default):
    try:
        return float(os.environ.get(name, default))
    except ValueError:
        return default


def limits():
    return {"ram": _env_float("ORCH_MIN_FREE_GB", MIN_FREE_GB),
            "commit": _env_float("HOST_GUARD_COMMIT_PCT", MAX_COMMIT_PCT),
            "disk": _env_float("HOST_GUARD_MIN_DISK_GB", MIN_DISK_GB),
            "cpu": _env_float("HOST_GUARD_CPU_PCT", MAX_CPU_PCT)}


def judge(ram_free_gb, commit_pct, disk_free_gb, cpu_pct, lim=None):
    """Pure. -> {"verdict": "ok"|"starved", the four numbers, "why": [reasons]}."""
    lim = lim or limits()
    why = []
    if ram_free_gb < lim["ram"]:
        why.append(f"ram {ram_free_gb:.1f} GB free < {lim['ram']:g}")
    if commit_pct >= lim["commit"]:
        why.append(f"commit {commit_pct:.0f}% >= {lim['commit']:g}")
    if disk_free_gb < lim["disk"]:
        why.append(f"disk {disk_free_gb:.0f} GB free < {lim['disk']:g}")
    if cpu_pct >= lim["cpu"]:
        why.append(f"cpu {cpu_pct:.0f}% >= {lim['cpu']:g}")
    return {"verdict": "starved" if why else "ok", "ram_free_gb": round(ram_free_gb, 1),
            "commit_pct": round(commit_pct), "disk_free_gb": round(disk_free_gb),
            "cpu_pct": round(cpu_pct), "why": why}


def measure():
    ram, commit = read_memory()
    return judge(ram, commit, read_disk(), read_cpu())


def line(v):
    out = (f"verdict: {v['verdict']}  ram_free_gb: {v['ram_free_gb']}  commit_pct: {v['commit_pct']}"
           f"  disk_free_gb: {v['disk_free_gb']}  cpu_pct: {v['cpu_pct']}")
    return out + (f"  why: {'; '.join(v['why'])}" if v["why"] else "")


# ---------------------------------------------------------------- opt-in hooks for callers

def config(path=None):
    """board/host-guard.json -> dict, {} when absent or unreadable."""
    try:
        with open(path or CONFIG_PATH, encoding="utf-8") as fh:
            d = json.load(fh)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def enabled(cfg=None):
    if os.environ.get("HOST_GUARD", "").lower() == "on":
        return True
    return bool((config() if cfg is None else cfg).get("enabled"))


def note(measure_fn=None, cfg=None):
    """'' when the guard is off or the box is fine; else 'box starved (why)' for a caller to add to
    an alert, so a starved box is not read as a wedged pane. Never raises."""
    try:
        if not enabled(cfg):
            return ""
        v = (measure_fn or measure)()
        return f"box starved ({'; '.join(v['why'])})" if v["verdict"] == "starved" else ""
    except Exception:
        return ""


def repo_key(cwd):
    """The repo a folder belongs to: its git common dir, so every worktree of one repo shares a key.
    Falls back to the folder itself when it is not in a repo."""
    cwd = os.path.abspath(cwd)
    try:
        r = subprocess.run(["git", "-C", cwd, "rev-parse", "--path-format=absolute", "--git-common-dir"],
                           capture_output=True, text=True, timeout=10,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if r.returncode == 0 and r.stdout.strip():
            return os.path.normcase(os.path.abspath(r.stdout.strip()))
    except (OSError, subprocess.SubprocessError):
        pass
    return os.path.normcase(cwd)


def repo_cap(cfg=None):
    """The per-repo pane cap, or 0 for none. Needs the guard on AND a cap set."""
    cfg = config() if cfg is None else cfg
    if not enabled(cfg):
        return 0
    try:
        return max(0, int(os.environ.get("ORCH_REPO_SPAWN_CAP") or cfg.get("repo_cap") or 0))
    except (TypeError, ValueError):
        return 0


def spawn_over_cap(cwd, live_cwds, cap=None, key_fn=None):
    """Pure given `key_fn`. -> '' to allow, else the refusal sentence. `live_cwds` are the cwds of
    the panes already running. A cap of 0 or None allows everything."""
    cap = repo_cap() if cap is None else cap
    if not cap:
        return ""
    key_fn = key_fn or repo_key
    mine = key_fn(cwd)
    n = sum(1 for c in live_cwds if c and key_fn(c) == mine)
    if n < cap:
        return ""
    return (f"refused: {n} pane(s) already run in this repo and the per-repo cap is {cap}. "
            f"Close one (py orch.py ls) or raise the cap in board\\host-guard.json (repo_cap).")


def main(argv=None):
    a = sys.argv[1:] if argv is None else argv
    if any(x in ("-h", "--help") for x in a):
        print(__doc__)
        return 0
    if any(x not in ("--json",) for x in a):
        print(f"error: unknown argument {' '.join(x for x in a if x != '--json')!r}. "
              f"Run `py host_guard.py --help`.", file=sys.stderr)
        return 2
    v = measure()
    print(json.dumps(v) if "--json" in a else line(v))
    return 0 if v["verdict"] == "ok" else 1


if __name__ == "__main__":
    sys.exit(main())
