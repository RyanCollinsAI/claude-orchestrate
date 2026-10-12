"""memwatch.py - one Monitor for memory pressure while a batch runs.

    py -u memwatch.py            # under Monitor: prints a line on every level change
    py -u memwatch.py --once     # one reading, then exit

Levels: OK, WARN (commit >= 90% or free RAM < 6 GB), CRIT (commit >= 95% or free RAM < 3 GB).
It prints the start reading, every level change, and a non-OK level again every 3 minutes.
6 GB is the floor `orch.py spawn` already refuses under (references/memory.md).
Copied from the 2026-10-06 spare-usage session that ran 9 workflows without a crash, minus its
D: drive check.
"""

import argparse
import ctypes
import time

GB = 1024 ** 3


class _MemStatus(ctypes.Structure):
    _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]


def read():
    """(commit percent, free RAM GB, commit limit GB)."""
    m = _MemStatus()
    m.dwLength = ctypes.sizeof(_MemStatus)
    ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
    commit = 100 * (m.ullTotalPageFile - m.ullAvailPageFile) / m.ullTotalPageFile
    return commit, m.ullAvailPhys / GB, m.ullTotalPageFile / GB


def level(commit, free, a):
    if commit >= a.crit_commit or free < a.crit_free:
        return "CRIT"
    if commit >= a.warn_commit or free < a.warn_free:
        return "WARN"
    return "OK"


def line(lv, commit, free, limit):
    return f"{lv}: commit {commit:.0f}% of {limit:.0f} GB, free RAM {free:.1f} GB"


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--warn-commit", type=float, default=90)
    p.add_argument("--crit-commit", type=float, default=95)
    p.add_argument("--warn-free", type=float, default=6)
    p.add_argument("--crit-free", type=float, default=3)
    p.add_argument("--every", type=float, default=20, help="seconds between readings")
    p.add_argument("--once", action="store_true")
    a = p.parse_args()

    c, f, lim = read()
    print("memwatch start: " + line(level(c, f, a), c, f, lim), flush=True)
    if a.once:
        return
    last, last_emit = level(c, f, a), time.time()
    while True:
        time.sleep(a.every)
        c, f, lim = read()
        lv = level(c, f, a)
        now = time.time()
        if lv != last or (lv != "OK" and now - last_emit > 180):
            print(line(lv, c, f, lim), flush=True)
            last, last_emit = lv, now


if __name__ == "__main__":
    main()
