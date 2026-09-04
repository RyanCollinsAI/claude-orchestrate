"""Auto-accept permission prompts in named panes (a deny-rule collision still prompts inside bypass).

  py auto_accept.py <pane_id> [...]

Emits one line per accepted prompt; runs until killed. watch_sessions.py only REPORTS a stuck pane
(`<name> PROMPT | ...`) - use this when you want the pressing done for you.
"""
import os, sys, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import orchlib as L

PANES = sys.argv[1:]

while True:
    for pane in PANES:
        try:
            q = L.pending_prompt(L.pane_read(pane, 30))
        except Exception:
            continue
        if q:
            L.send_keys(pane, "enter")
            print(f"{pane} ACCEPTED | {q[:160]}", flush=True)
            time.sleep(2)
    time.sleep(5)
