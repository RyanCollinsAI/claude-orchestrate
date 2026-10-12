"""py orch.py restart-pane <pane> [--prep --from VER | --pending]

The restart-pane plugin's button. Restarts ONE pane (the one named; the plugin passes its own
HERDR_PANE_ID) with `claude --resume <its session id>` and every flag it started with - the same
code path as `restart-idle` (restart_idle.restart_pane), without its skip rules (the plugin only
calls this for the pane the human clicked in, so the "own pane", "seat" and "draft" skips do not apply).

  --prep --from VER   read the unsent draft on stdin, save it to handoffs/drafts-<pane>-<time>.txt and write
                      handoffs/restart-pane/<pane>.json (the marker the new session reads). Prints the marker.
  (no flag)           wait up to 60 s for the pane to be idle, then restart it. Run it detached: the
                      claude that started it is the process being closed.
  --pending           print and delete the marker as one JSON line, with the draft text under "text".
                      Prints {} when there is none or it is older than 10 minutes.
"""
import json
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import orchlib as L
import restart_idle as R

STALE_S = 600


def _dir():
    d = os.path.join(L.HANDOFFS, "restart-pane")
    os.makedirs(d, exist_ok=True)
    return d


def _safe(pane):
    return re.sub(r"[^\w.-]", "_", pane)


def marker_path(pane):
    return os.path.join(_dir(), _safe(pane) + ".json")


def prep(pane, from_version, draft):
    """Save the draft and write the marker. -> the marker dict."""
    stamp = time.strftime("%Y%m%d-%H%M%S")
    path = ""
    if draft.strip():
        os.makedirs(L.HANDOFFS, exist_ok=True)
        path = os.path.join(L.HANDOFFS, f"drafts-{_safe(pane)}-{stamp}.txt")
        with open(path, "w", encoding="utf-8", newline="") as fh:
            fh.write(draft)
    a = R.agent_for(pane)
    marker = {"pane": pane, "sid": ((a or {}).get("agent_session") or {}).get("value", ""),
              "from": from_version, "draft": path, "at": time.time()}
    with open(marker_path(pane), "w", encoding="utf-8") as fh:
        json.dump(marker, fh)
    return marker


def pending(pane):
    """Consume the marker. -> dict ({} when none or stale), draft text under "text"."""
    p = marker_path(pane)
    try:
        with open(p, encoding="utf-8") as fh:
            m = json.load(fh)
    except (OSError, ValueError):
        return {}
    try:
        os.remove(p)
    except OSError:
        pass
    if time.time() - float(m.get("at", 0)) > STALE_S:
        return {}
    m["text"] = ""
    if m.get("draft"):
        try:
            with open(m["draft"], encoding="utf-8", newline="") as fh:
                m["text"] = fh.read()
        except OSError:
            pass
    return m


def restart(pane, wait_s=60.0):
    """Wait for idle, then restart the pane. -> (ok, detail)."""
    if pane in L.acp_pane_ids():
        return False, "ACP pane (resume is session/load, not claude --resume)"
    a = R.agent_for(pane)
    if not a:
        return False, f"no claude agent in pane {pane}"
    if not (a.get("agent_session") or {}).get("value"):
        return False, "no session id"
    end = time.time() + wait_s
    while time.time() < end:
        a = R.agent_for(pane) or a
        if a.get("agent_status") in R.IDLE_STATES:   # raw: a Monitor or shell behind an idle prompt is not a turn
            break
        time.sleep(0.7)
    else:
        return False, "still busy after waiting"
    time.sleep(0.5)
    return R.restart_pane(a, ignore_bg=True)


def main(a):
    if not a or a[0].startswith("--"):
        sys.exit("usage: py orch.py restart-pane <pane> [--prep --from VER | --pending]")
    pane = a[0]
    if "--pending" in a:
        print(json.dumps(pending(pane)))
    elif "--prep" in a:
        ver = a[a.index("--from") + 1] if "--from" in a and a.index("--from") + 1 < len(a) else ""
        sys.stdin.reconfigure(encoding="utf-8", newline="")
        print(json.dumps(prep(pane, ver, sys.stdin.read())))
    else:
        ok, detail = restart(pane)
        line = f"{time.strftime('%H:%M:%S')} {'RESTARTED' if ok else 'FAILED'} {pane}: {detail}"
        with open(os.path.join(_dir(), _safe(pane) + ".log"), "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
        print(line)
        sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main(sys.argv[1:])
