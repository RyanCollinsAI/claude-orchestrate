"""block-known-traps.py - PreToolUse hook for Claude Code (Bash and PowerShell tools).

Hard-blocks the five command shapes that most often park an orchestrated pane on a permission
prompt or silently lose work. A prose rule in a CLAUDE.md gets skipped about 1 run in 30; a hook
never does.

The bar for a check here is that the trap fails SILENTLY or EXPENSIVELY. A loud, instant,
self-correcting failure does not qualify - it costs one tool call, and a block for it is noise.

Traps blocked:

  1. Any Bash-tool heredoc (`<<EOF`, `<<'PY'`, `<<-"X"`). BASH ONLY. Every heredoc shape hangs the
     Bash tool until the tool timeout, then sits in the background holding the file being edited.
     PowerShell here-strings (@'...'@) are the documented working way to pass a multi-line string
     to a native command in the PowerShell tool and are never touched.
  2. `git checkout -- <path>` and `git restore <path>` without `--staged`. Both shells. Both
     overwrite the file from the index and discard every uncommitted change in it.
  3. `git commit -m` with a backtick anywhere in the message. Both shells, different mechanism: in
     bash a backtick is command substitution, in PowerShell it is the escape character. Either way
     the backticked text is silently dropped and the commit looks fine until it is read back.
  4. `cd X && ...` / `cd X; ...` at the START of a command. Both shells. A relative-path deny rule
     cannot be checked without knowing the real cwd, and a leading `cd` makes that unresolvable, so
     the permission system prompts a human instead. An unattended pane parks there until it times
     out. The shell's cwd persists between tool calls anyway, so the `cd` buys nothing.
  5. `cd <dir>` chained into a write (rm/mkdir/mv/cp/touch/tee, New-Item/Remove-Item, or a
     redirect). Both shells. Same stall, and the most common shape of it.

Which checks run is decided by `tool_name` in the PreToolUse payload, not by a flag passed at
registration time, so the settings.json matcher entries for "Bash" and "PowerShell" cannot drift
out of sync with this script.

Kill switch: a file named `.traps-off` next to this script. If it exists this exits 0 immediately.

FAIL OPEN: any exception, any unparseable input, any unexpected tool_name lets the command through.
A buggy guard that blocks real work is worse than the traps it exists to catch.

Self-test without a real payload:

    py block-known-traps.py --simulate Bash "git checkout -- src/app.py"
    py block-known-traps.py --simulate Bash "cd /srv/app && npm test"
"""

import json
import re
import sys
from pathlib import Path

LOG_PREFIX = "[block-known-traps]"
KILL_SWITCH = Path(__file__).resolve().parent / ".traps-off"


def log(msg):
    print(f"{LOG_PREFIX} {msg}", file=sys.stderr, flush=True)


# A --simulate run carries the offending command as an argument, so a self-test of any trap would
# otherwise be blocked by that same trap. BOTH markers are required: keying on "--simulate" alone
# lets any unrelated command carrying that flag skip every check.
def _is_self_test(command):
    return "block-known-traps.py" in command and "--simulate" in command


# --- Trap 1: Bash-tool heredocs ---------------------------------------------------------------
# Matches `<<` (not `<<<`, a here-string, which does not hang) followed by an optional dash and a
# quoted or bare delimiter. A real heredoc delimiter ends its COMMAND, which is not the same as
# ending its line: `py - <<'PY' 2>/dev/null || other` is a real heredoc. Redirections are consumed
# before the terminator test, because an earlier version that required end-of-line missed exactly
# that shape and hung the tool for the full timeout.
_REDIR = r'(?:\s*\d*[<>]{1,2}\s*(?:&\d+|/dev/null|[\w./\\:-]+))*'
HEREDOC_RE = re.compile(
    r'(?<!<)<<(?!<)-?\s*(["\'])?([A-Za-z_][A-Za-z0-9_]*)(?(1)\1)'
    r'(?=' + _REDIR + r'\s*(?:$|[\r\n;|&)]))'
)


def check_heredoc(command):
    if HEREDOC_RE.search(command):
        return ("Blocked: no heredoc shape works reliably in the Bash tool - it hangs until the "
                "tool timeout and then sits in the background holding the file. Use the Write tool "
                "to create a file, or the Edit tool with a unique trailing anchor to append. If "
                "this was not actually a heredoc, rewrite the quoting so `<<` does not sit at the "
                "end of a line.")
    return None


# --- Trap 2: git checkout -- <path> / git restore <path> without --staged ----------------------
CHECKOUT_RE = re.compile(r'\bgit\s+checkout\b.*?--\s+(.+?)\s*$')
RESTORE_RE = re.compile(r'\bgit\s+restore\b(.*)$')


def check_checkout_restore(command):
    checkout_reason = (
        "Blocked: `git checkout -- <path>` overwrites the file in the working tree from the index "
        "or the named ref, discarding every uncommitted change in it. To inspect an old version "
        "safely, run `git show <ref>:<path>` redirected to a NEW filename - never over the "
        "original - then diff and copy across only what you want.")
    restore_reason = (
        "Blocked: `git restore <path>` without --staged discards every uncommitted change in the "
        "file. Read the file's bytes into a variable first and write them back, and commit before "
        "any experiment that writes to a source file.")
    for segment in re.split(r'&&|\|\||[;\n]', command):
        if CHECKOUT_RE.search(segment):
            return checkout_reason
        rm = RESTORE_RE.search(segment)
        if rm:
            rest = rm.group(1)
            if "--staged" in rest:
                continue
            if any(tok and not tok.startswith("-") for tok in rest.split()):
                return restore_reason
    return None


# --- Trap 3: git commit -m with a backtick in the message ---------------------------------------
def check_commit_backtick(command, tool_name):
    if not (re.search(r'\bgit\s+commit\b', command)
            and re.search(r'(?:^|\s)-m(?:\s|=)', command)
            and '`' in command):
        return None
    cause = ("a backtick is PowerShell's escape character, so the backticked text is silently "
             "dropped from the message" if tool_name == "powershell" else
             "a backtick is command-substituted by the shell, so the backticked text is silently "
             "replaced by empty output")
    return (f"Blocked: a backtick in a `git commit -m` message is a problem in both shells - "
            f"{cause} - and the commit looks fine until you read it back. Write the message to a "
            f"file and use `git commit -F <file>`.")


# --- Trap 4: `cd X && ...` / `cd X; ...` at the start of a command ------------------------------
# Anchored at ^ on purpose: a quoted `"cd /foo && ls"` inside some other command does not start
# with cd, so the anchor alone keeps quoted asides out without extra quote-tracking.
CD_CHAIN_RE = re.compile(r'^\s*cd\s+\S+\s*(?:&&|;)')


def check_cd_chain(command):
    if CD_CHAIN_RE.search(command):
        return ("Blocked: `cd X && ...` trips a deny rule and parks the pane on a prompt. Use "
                "absolute paths, `git -C <repo>`, `npm --prefix <dir>`; cwd persists anyway.")
    return None


# --- Trap 5: `cd <dir>` chained into a write ---------------------------------------------------
CD_RE = re.compile(r'^\s*(?:cd|Set-Location|sl)\s+\S')
WRITE_VERB_RE = re.compile(r'\b(?:rm|mkdir|mv|cp|touch|tee|New-Item|Remove-Item)\b')
# A redirect that creates or truncates a file. `2>` and a descriptor dup write nothing and are
# excluded; the lookbehind keeps `>=` in a comparison and `->` in a type hint out of it.
REDIRECT_RE = re.compile(r'(?<![0-9<>=!\-])>{1,2}\s*(?!/dev/null|&\d|=)\S')
SEGMENT_SPLIT_RE = re.compile(r'&&|\|\||[;|\n]')


def check_cd_write(command):
    segments = SEGMENT_SPLIT_RE.split(command)
    for i, seg in enumerate(segments):
        if not CD_RE.search(seg):
            continue
        for later in segments[i + 1:]:
            if WRITE_VERB_RE.search(later) or REDIRECT_RE.search(later):
                return ("Blocked: `cd` chained into a write (or a redirect) is the most common "
                        "permission prompt there is, and a prompt stalls an unattended pane until "
                        "it times out. Drop the `cd` - the shell's cwd persists between tool "
                        "calls - and write the absolute path into the command itself.")
    return None


def evaluate(command, tool_name, background=False):
    if _is_self_test(command):
        return None
    if tool_name == "bash":
        reason = check_heredoc(command)
        if reason:
            return reason
    reason = check_checkout_restore(command)
    if reason:
        return reason
    reason = check_commit_backtick(command, tool_name)
    if reason:
        return reason
    for check in (check_cd_chain, check_cd_write):
        reason = check(command)
        if reason:
            return reason
    return None


def deny(reason):
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "deny",
        "permissionDecisionReason": reason,
    }}))


def main():
    try:
        if KILL_SWITCH.exists():
            return 0

        if "--simulate" in sys.argv:
            idx = sys.argv.index("--simulate")
            tool_name = sys.argv[idx + 1].lower() if idx + 1 < len(sys.argv) else ""
            command = sys.argv[idx + 2] if idx + 2 < len(sys.argv) else ""
            reason = evaluate(command, tool_name)
            log(f"WOULD DENY: {reason}" if reason else "WOULD ALLOW")
            if reason:
                deny(reason)
            return 0

        raw = sys.stdin.read()
        if not raw.strip():
            return 0
        payload = json.loads(raw)

        tool_name = (payload.get("tool_name") or payload.get("tool") or "").lower()
        if tool_name not in ("bash", "powershell"):
            return 0

        tool_input = payload.get("tool_input") or payload.get("input") or {}
        if isinstance(tool_input, dict):
            command = tool_input.get("command") or tool_input.get("cmd") or ""
        elif isinstance(tool_input, str):
            command = tool_input
        else:
            command = ""
        if not command:
            return 0

        reason = evaluate(command, tool_name)
        if reason:
            log(f"DENY: {reason}")
            deny(reason)
        return 0
    except Exception as exc:      # fail open, never block on our own bug
        log(f"unhandled error, failing open: {exc}")
        return 0


if __name__ == "__main__":
    sys.exit(main())
