"""Write a fresh config.json for the orchestrate skill.

  python3 write_config.py <config-path> <default-cwd>

Only called by install.sh, and only when there is no config.json already.
"""
import json, os, sys

path, cwd = sys.argv[1], os.path.abspath(sys.argv[2])
cfg = {
    "default_cwd": cwd,
    "session_prefix": os.path.basename(cwd).lower(),
    "orchestrator_tab": "orchestrator",
    "accounts_tool": "",
    "rotate_at_k": 400,
    "digest_repos": [],
}
with open(path, "w", encoding="utf-8") as fh:
    json.dump(cfg, fh, indent=2)
    fh.write("\n")
print(f"  default_cwd    {cfg['default_cwd']}")
print(f"  session_prefix {cfg['session_prefix']}")
