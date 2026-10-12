"""Drop ghost rows from the Claude session registry.

`~/.claude/sessions/<pid>.json` is one file per claude process. A `claude --resume` starts a NEW
process (new file, often new sessionId) under the same name in the same herdr pane, and the old file
can outlive its process. `orch.py ls` then showed two `orchestrator` rows and peers sent reports to
the dead one (2026-10-02, mod-ship-gate).

`prune(rows, panes_by_sid)` takes the raw `{sessionId: row}` registry and returns it without:
  1. rows whose process is gone (crash, kill, or a restart that left the file behind), and
  2. rows that share a name with a row that owns a herdr pane, when they own none themselves
     (the old identity of a resumed pane - the new one takes over the name).
Two live rows with the same name in DIFFERENT panes are both kept: that is a real name clash and
`L.resolve` already asks the caller to disambiguate it.
"""
import ctypes
import os

_STILL_ACTIVE = 259
_QUERY_LIMITED = 0x1000


def pid_alive(pid):
    """True if `pid` is a running process. Windows OpenProcess; os.kill(pid, 0) elsewhere."""
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if pid <= 0:
        return False
    if os.name != "nt":
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except OSError:
            return True
        return True
    k32 = ctypes.windll.kernel32
    h = k32.OpenProcess(_QUERY_LIMITED, False, pid)
    if not h:
        return False
    try:
        code = ctypes.c_ulong()
        if not k32.GetExitCodeProcess(h, ctypes.byref(code)):
            return True
        return code.value == _STILL_ACTIVE
    finally:
        k32.CloseHandle(h)


def prune(rows, panes_by_sid, alive=pid_alive):
    """See module docstring. `panes_by_sid` is a callable, only invoked when two rows share a name
    (it costs a herdr call). A row with no `pid` field is kept: it cannot be judged."""
    live = {sid: m for sid, m in rows.items() if "pid" not in m or alive(m["pid"])}
    by_name = {}
    for sid, m in live.items():
        by_name.setdefault(m.get("name"), []).append(sid)
    if all(len(s) < 2 for s in by_name.values()):
        return live
    owned = set(panes_by_sid() or {})
    drop = set()
    for sids in by_name.values():
        if len(sids) < 2:
            continue
        with_pane = [s for s in sids if s in owned]
        if with_pane:
            drop.update(s for s in sids if s not in owned)
        else:
            newest = max(sids, key=lambda s: live[s].get("startedAt", 0))
            drop.update(s for s in sids if s != newest)
    return {sid: m for sid, m in live.items() if sid not in drop}
