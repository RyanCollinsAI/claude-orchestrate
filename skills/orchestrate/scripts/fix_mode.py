"""Put a running Claude pane into bypass mode: accept any pending prompt, then shift+tab until the
status line says bypass.

  py fix_mode.py <pane_id> [<pane_id> ...]

Needed because `--allow-dangerously-skip-permissions` only PERMITS bypass - a pane started with it
comes up in default mode and stalls on the first "Do you want to proceed?". orch.py spawn now passes
`--dangerously-skip-permissions` and calls this logic itself; this stays for panes started by hand.

A pane started with NEITHER flag can never reach bypass: shift+tab only cycles default -> auto ->
plan there (app-week-rail, 2026-09-07, came back reading "plan mode on"). The fix is a respawn
through `orch.py spawn`, or living in auto mode with `auto_accept.py <pane>` armed. To pick a
numbered choice on a parked prompt by hand, send the digit and Enter as TWO calls:
`herdr agent send-keys <pane> "2"` then `herdr agent send-keys <pane> "Enter"` - the digit alone
does not commit (cost four turns on 2026-09-07).
"""
import os, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import orchlib as L

for pane in sys.argv[1:]:
    if L.ensure_bypass(pane, timeout=12):
        print(f"{pane}: bypass permissions on")
    else:
        txt = L.pane_read(pane, 40).lower()
        if "plan mode" in txt or "auto mode" in txt or "default mode" in txt:
            print(f"{pane}: could NOT reach bypass - the pane cycles default/auto/plan only, so it "
                  f"was started without --dangerously-skip-permissions. Respawn it through "
                  f"orch.py spawn, or leave it in auto and arm auto_accept.py {pane}.")
        else:
            print(f"{pane}: could NOT confirm bypass; last lines:")
        print("\n".join(L.pane_read(pane, 6).splitlines()[-6:]))
