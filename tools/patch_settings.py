"""Add the trap hook and the auto-updater switch to settings.json without clobbering it.

  python3 patch_settings.py <settings.json> <hook-path> <bypass 0|1>

Rules, because this edits a file the user owns:
  - an existing PreToolUse array is APPENDED to, never replaced;
  - a block-known-traps entry that is already there is left alone;
  - every other key in the file is preserved exactly;
  - permissions.defaultMode is only touched when bypass is 1.
Called by install.sh; install.ps1 does the same thing in PowerShell.
"""
import json, os, sys

path, hook, bypass = sys.argv[1], sys.argv[2], sys.argv[3] == "1"

settings = {}
if os.path.exists(path):
    try:
        with open(path, encoding="utf-8") as fh:
            settings = json.load(fh)
    except ValueError:
        sys.exit(f"{path} is not valid JSON; fix it by hand and re-run")
if not isinstance(settings, dict):
    sys.exit(f"{path} is not a JSON object; refusing to touch it")

hooks = settings.setdefault("hooks", {})
pre = hooks.setdefault("PreToolUse", [])
if any("block-known-traps" in json.dumps(e) for e in pre):
    print("  hook already registered - left as is")
else:
    pre.append({"matcher": "Bash|PowerShell",
                "hooks": [{"type": "command",
                           "command": 'python3 "%s"' % hook.replace("\\", "/")}]})
    print("  hook appended to PreToolUse (existing entries kept)")

settings.setdefault("env", {})["DISABLE_AUTOUPDATER"] = "1"
print("  env.DISABLE_AUTOUPDATER = 1")

if bypass:
    settings.setdefault("permissions", {})["defaultMode"] = "bypassPermissions"
    print("  permissions.defaultMode = bypassPermissions")

os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
with open(path, "w", encoding="utf-8") as fh:
    json.dump(settings, fh, indent=2)
    fh.write("\n")
print("  settings.json updated")
