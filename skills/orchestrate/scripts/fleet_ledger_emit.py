r"""Fleet ledger writers for the two events nobody wrote: a merge and a cleanup.

  py fleet_ledger_emit.py merged --task kun-k8 --via local --sha 1a2b3c4d [--branch main] [--repo NAME]
  py fleet_ledger_emit.py merged --task kun-k8 --via pr --pr https://github.com/acme/repo/pull/7
  py fleet_ledger_emit.py cleaned --task kun-k8 [--via reap]

From Python:  import fleet_ledger_emit as E;  E.merged("kun-k8", via="local", sha=sha)

Both go through `orchlib.ledger.write`, so they use its byte lock and its record shape
(board/schema.md, "Fleet ledger"). The lock is the one the other writers use, so a merge line and a
dispatch line written at the same moment never interleave. Never raises: a ledger problem must not
fail a ship or a kill. A failed write returns False, prints one stderr line, and the CLI exits 0.
The cleanup event is the ledger's `task.cleaned_up` (the ledger rejects any other name).
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import orchlib as L  # noqa: E402

MERGE_VIA = ("pr", "local")
CLEAN_VIA = ("kill", "reap")


def merged(task, via="local", pr="", sha="", branch="", repo=""):
    """-> True when a `task.merged` line was appended. `via` is `pr` or `local`; via `pr` needs a
    nonempty `pr` URL, and `pr` is dropped when via is `local`. sha, branch and repo are optional
    extra members (readers ignore unknowns)."""
    if via not in MERGE_VIA:
        print(f"fleet_ledger_emit merged: via must be one of {MERGE_VIA}, got {via!r}", file=sys.stderr)
        return False
    if via == "pr" and not pr:
        print("fleet_ledger_emit merged: via pr needs a nonempty pr URL", file=sys.stderr)
        return False
    fields = {"via": via}
    if via == "pr":
        fields["pr"] = pr
    for k, v in (("sha", sha), ("branch", branch), ("repo", repo)):
        if v:
            fields[k] = v
    return L.ledger.write("task.merged", task, **fields)


def cleaned(task, via="reap"):
    """-> True when a `task.cleaned_up` line was appended. `via` is `kill` or `reap`."""
    if via not in CLEAN_VIA:
        print(f"fleet_ledger_emit cleaned: via must be one of {CLEAN_VIA}, got {via!r}", file=sys.stderr)
        return False
    return L.ledger.write("task.cleaned_up", task, via=via)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Append a task.merged or task.cleaned_up line to the fleet ledger.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    m = sub.add_parser("merged", help="a task's work landed on main")
    m.add_argument("--task", required=True, help="the pane label")
    m.add_argument("--via", default="local", choices=MERGE_VIA)
    m.add_argument("--pr", default="", help="PR URL (via pr)")
    m.add_argument("--sha", default="")
    m.add_argument("--branch", default="")
    m.add_argument("--repo", default="")
    c = sub.add_parser("cleaned", help="a task's pane or worktree was cleaned up")
    c.add_argument("--task", required=True)
    c.add_argument("--via", default="reap", choices=CLEAN_VIA)
    a = ap.parse_args(argv)
    if a.cmd == "merged" and a.via == "pr" and not a.pr:
        ap.error("merged --via pr needs --pr URL")
    ok = (merged(a.task, a.via, a.pr, a.sha, a.branch, a.repo) if a.cmd == "merged"
          else cleaned(a.task, a.via))
    print(f"ledger {'wrote' if ok else 'skipped'} {a.cmd} {a.task}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
