"""Put a running Claude pane into bypass mode: accept any pending prompt, then shift+tab until the
status line says bypass.

  py fix_mode.py <pane_id> [<pane_id> ...]

Needed because `--allow-dangerously-skip-permissions` only PERMITS bypass - a pane started with it
comes up in default mode and stalls on the first "Do you want to proceed?". orch.py spawn now passes
`--dangerously-skip-permissions` and calls this logic itself; this stays for panes started by hand.
"""
import os, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import orchlib as L

for pane in sys.argv[1:]:
    if L.ensure_bypass(pane, timeout=12):
        print(f"{pane}: bypass permissions on")
    else:
        print(f"{pane}: could NOT confirm bypass; last lines:")
        print("\n".join(L.pane_read(pane, 6).splitlines()[-6:]))
