#!/usr/bin/env bash
# Install the orchestrate skill into ~/.claude/skills/orchestrate (macOS / Linux).
#
#   ./install.sh [--cwd DIR] [--no-hooks] [--bypass]
#
# Nothing is overwritten without being told to: an existing config.json is left alone, and an
# existing hooks array is appended to, never replaced.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CLAUDE="${CLAUDE_CONFIG_DIR:-$HOME/.claude}"
DEST="$CLAUDE/skills/orchestrate"
CWD="$(pwd)"
HOOKS=1
BYPASS=0

while [ $# -gt 0 ]; do
  case "$1" in
    --cwd) CWD="$2"; shift 2 ;;
    --no-hooks) HOOKS=0; shift ;;
    --bypass) BYPASS=1; shift ;;
    -h|--help) sed -n '2,10p' "$0"; exit 0 ;;
    *) echo "unknown flag: $1" >&2; exit 2 ;;
  esac
done

say() {  # say <ok|miss|opt> <label> <detail>
  case "$1" in ok) tag="OK  ";; miss) tag="MISS";; *) tag="--  ";; esac
  printf "  %s %-16s %s\n" "$tag" "$2" "$3"
}

echo "claude-orchestrate installer"
echo "  repo   $REPO"
echo "  target $DEST"
echo

# ---------------------------------------------------------------- 1. copy the skill
mkdir -p "$DEST/scripts" "$DEST/templates" "$DEST/board"
cp "$REPO/skills/orchestrate/SKILL.md"      "$DEST/"
cp "$REPO"/skills/orchestrate/scripts/*.py  "$DEST/scripts/"
cp "$REPO"/skills/orchestrate/templates/*.md "$DEST/templates/"
cp "$REPO"/skills/orchestrate/board/*       "$DEST/board/"
cp "$REPO/config.example.json"              "$DEST/"
echo "Skill copied."

# ---------------------------------------------------------------- 2. config.json
CONFIG="$DEST/config.json"
if [ -f "$CONFIG" ]; then
  echo "config.json already exists - left untouched. Delete it to regenerate."
else
  FULL="$(cd "$CWD" && pwd)"
  python3 "$REPO/tools/write_config.py" "$CONFIG" "$FULL"
  echo "Wrote $CONFIG"
fi

# ---------------------------------------------------------------- 3. dependencies
echo
echo "Dependencies"
if command -v python3 >/dev/null; then
  V="$(python3 -c 'import sys;print("%d.%d" % sys.version_info[:2])')"
  if python3 -c 'import sys;sys.exit(0 if sys.version_info>=(3,11) else 1)'; then
    say ok python "$V at $(command -v python3)"
  else
    say miss python "$V - needs 3.11 or newer"
  fi
else
  say miss python "not on PATH - nothing in this skill runs without it"
fi

if command -v herdr >/dev/null; then
  say ok herdr "$(command -v herdr)"
else
  say miss herdr "REQUIRED. It is the terminal multiplexer every command drives: ls, spawn, rotate, reap, show/hide all go through it. Without herdr the skill does nothing."
fi

if command -v lavish-axi >/dev/null; then
  say ok lavish-axi "$(command -v lavish-axi)"
else
  say opt lavish-axi "optional - only \`board open\` and board_watch.py need it."
fi

if command -v pwsh >/dev/null; then
  say ok pwsh "$(command -v pwsh)"
else
  say opt pwsh "optional - only if accounts_tool points at a PowerShell account switcher."
fi

if command -v claude >/dev/null; then
  say ok claude "$(command -v claude)"
else
  say miss claude "not on PATH - \`doctor\` cannot check auth"
fi

# ---------------------------------------------------------------- 4. settings.json
if [ "$HOOKS" = "1" ]; then
  SETTINGS="$CLAUDE/settings.json"
  echo
  echo "settings.json ($SETTINGS)"
  echo "  1. PreToolUse hook that blocks the five command shapes that park a pane on a prompt."
  echo "  2. DISABLE_AUTOUPDATER=1 - a mid-work Claude update reloads every pane at once."
  [ "$BYPASS" = "1" ] && echo "  3. permissions.defaultMode = bypassPermissions (you passed --bypass)."
  printf "Apply these? [y/N] "
  read -r ANS
  if [ "$ANS" = "y" ] || [ "$ANS" = "Y" ]; then
    mkdir -p "$CLAUDE/hooks"
    cp "$REPO/hooks/block-known-traps.py" "$CLAUDE/hooks/block-known-traps.py"
    [ -f "$SETTINGS" ] && cp "$SETTINGS" "$SETTINGS.bak" && echo "  backup written to $SETTINGS.bak"
    python3 "$REPO/tools/patch_settings.py" "$SETTINGS" "$CLAUDE/hooks/block-known-traps.py" "$BYPASS"
  else
    echo "  skipped"
  fi
fi

# ---------------------------------------------------------------- 5. done
echo
echo "Installed. Next:"
echo "  python3 \"$DEST/scripts/orch.py\" doctor     one red/green line per moving part"
echo "  python3 \"$DEST/scripts/orch.py\" ls         every live Claude Code session"
echo 'Then, in the session you want to drive the others: "be the orchestrator".'
