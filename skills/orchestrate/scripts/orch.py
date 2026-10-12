"""Orchestrator helpers: drive the other Claude Code sessions on this box, all inside herdr.

  py orch.py ls                             board: name, status, ctx, model, pane, agent state, title
  py orch.py spawn <label> <prompt-or-@file> --model tier --cwd DIR [--effort low|medium|high|xhigh]
                                            [--group TAB] [--workspace wN-or-label] [--harness claude|codex|pi|grok|agy|cursor]
                                            [--owner NAME] [--lead] [--domain "<plain-language subject it owns>"]
                                            new pane -> agent in bypass -> prompt sent
                                            [--acp] run the pane as an ACP child (acp_pane.py): prompts go
                                            in as files, state is read from the protocol, not the screen.
                                            claude, codex and pi (--harness picks the client), never
                                            a mate. Refused while board/acp.off exists
                                            [--mcp full|min] min (default for workers) starts no MCP
                                            servers; leads and --domain mates default to full. Spawn
                                            refuses when one more pane would leave under 6 GB RAM
                                            free (a memory cap, not a pane count; ORCH_MIN_FREE_GB)
                                            --model and --cwd are required - refuses (exit 2) without them
                                            a --cwd in a repo's main checkout is refused: add
                                            --worktree (own worktree + branch) or --shared (read-only)
  py orch.py dispatch                      print crew-dispatch.json with each rule's quota-resolved
                                            pick (harness/model/effort) - read it before a spawn
                                            spawn/task/review also need --rule <n|default|override>: the
                                            rule number you matched; --harness/--model/--effort must equal
                                            one of its USE entries. `--rule override --reason "<why>"` is
                                            the logged way off-table. Every spawn is logged to
                                            state/dispatch-log.jsonl. ORCH_DISPATCH_HOOK=off disables it.
  py orch.py dispatch --pick "<task>"      opt-in: ask Jev which rule fits; under 0.6 it says ambiguous
  py orch.py task <label> "<goal>" [--done-when "..."] [--why "..."] [--read-first PATH] --model tier --cwd DIR [--out PATH] [--effort LEVEL]
                                            [--group TAB] [--harness claude|codex|pi|grok|agy|cursor] [--report PATH]
                                            [--owner NAME] [--lead] [--domain "<plain-language subject it owns>"]
                                            [--why "..."] [--read PATH] [--not "..."]
                                            write a task file and spawn a session on it
                                            --model and --cwd are required - refuses (exit 2) without them
                                            --why takes one value (not repeatable); --read/--not are
                                            repeatable - repeat the flag for each one (--read a --read b),
                                            never comma-separate. Each fills a section of the task file
                                            (goal/reason, files to read first, explicit non-goals) - omit
                                            any for a placeholder
  py orch.py task <label> --host NAME --goal "..." --done "..." [--model tier] [--owner NAME]
                                            start a mate on another machine (board/hosts.json) over ssh;
                                            no --rule/--cwd. `ls` shows it with WHERE = host, `pull`
                                            copies its reports back, `kill <label>@NAME` stops it.
                                            An unreachable host shows UNREACHABLE, never VANISHED
  py orch.py pull [--host NAME]             copy new reports from the host's outbox to handoffs/remote/NAME
  py orch.py review <name> [--diff <ref-range>|--pr <n> --repo owner/name] [--model sonnet] [--effort LEVEL]
                                            [--harness claude|pi] [--cwd DIR] [--owner NAME]
                                            spawn a reviewer: reads the diff, reproduces one thing,
                                            runs the tests, reports SHIP/FIX-FIRST; refuses with no diff.
                                            --harness pi puts a GPT reviewer on it (report by file)
  py orch.py prompt <name> <text...>        one follow-up prompt to any pane, routed by harness:
                                            send-text for pi, an inbox file for --acp, verified agent
                                            prompt for the rest
  py orch.py prompt <name> --cancel         the Escape key for an --acp pane: ends its open turn
  py orch.py own <name> [--owner NAME] [--domain "<subject>"]
                                            re-parent a live session, or promote it to a domain
                                            mate, without respawning it and losing its context
  py orch.py afk on --words "..." [--until T] | off | status | tick
                                            Ryan is away: auto-pick free questions, park watchdog noise
  py orch.py quiet on|off                   park watchdog noise in board/afk-digest.md, questions still reach the seat
  py orch.py reap [--hours 6] [--dry-run]   close idle sessions that finished clean N hours ago
  py orch.py reap --done [--older 20m] [--dry-run]
                                            close finished panes whose report reached the owner;
                                            skips seats, mates, dirty own worktrees; a pane in a shared
                                            checkout is judged by its reported shas on origin/main;
                                            prints each skip
  py orch.py kill <name> [--force]          close that session's herdr pane (falls back to the pid);
                                            refuses a pane owned by another live session
  py orch.py kill <name>@<host> [--force]   stop a mate on another machine; same owner rule
  py orch.py seat [<name>|--clear]          who holds the orchestrator seat (any harness)
  py orch.py seat <name> [--no-announce]    set the seat, then message every live session once
  py orch.py seat --who                     print the seat's name only (the lookup a session runs
                                            when its report target is gone)
  py orch.py show <name> [--ratio 0.5]      move that pane to the RIGHT of the orchestrator pane
  py orch.py hide <name>                    send it back to the tab it came from, in its own space
  py orch.py space <mate> [--dry-run]       move a live domain mate, and everything it owns, into
  py orch.py space --all [--dry-run]        its own herdr workspace (creating or reusing one
                                            labeled with the mate's name); --all does every mate.
  py orch.py space --order [--dry-run]      sort the sidebar to match config.json's `space_order`,
                                            creating the Slack space if it's missing.
                                            --dry-run prints the plan and moves nothing
  py orch.py restart-idle [--dry-run] [--include-busy-when-done] [--include-seat] [--timeout 30m] [--only NAME ...]
                                            after an update: restart every IDLE claude pane in place
                                            with `claude --resume <its session id>` (context kept);
                                            busy panes are QUEUED, see restart_idle.py
  py orch.py restart-pane <pane>            restart ONE pane in place (the restart-pane plugin button);
                                            --prep --from VER saves the draft, --pending reads it back
  py orch.py resume                         re-arm everything after the orchestrator restarted
                                            (the three watchers run under reloader.py now, which
                                            restarts a watcher - never itself - on a code change;
                                            this list is only for a Monitor that actually died)
  py orch.py doctor                         one red/green line per moving part
  py orch.py chrome agent                   the agent-only Chrome (:9334) - starts it, prints the url
  py orch.py chrome open <url> [--port 9333|9334] [--front] [--wait-for <css>] | close <id>
                                            THE way to open a tab (full URL intact, never /json/new);
                                            close only closes a tab `open` made
  py orch.py chrome take <name> | free [name|force] | who
                                            ADVISORY notes on RYAN'S browser (:9333); no lock - own tab only
  py orch.py go <name> | ungo <name>        let one pane touch hardware (lights, monitor power) for
                                            30 min; block-known-traps.py refuses it without this.
                                            Only after Ryan says go - never your own judgement
  py orch.py account [name|ambient]         which login new panes start on (optional feature)
  py orch.py quota [--json] [--all] [--no-refresh]
  py orch.py quota --tui [--once] [--no-refresh]
                                            one table: every harness's plan, 5h left, weekly left,
                                            resets. Claude from accounts/ledger.json (quota-axi
                                            cannot see this box's two logins); codex/grok/kimi from
                                            quota-axi. Reports only - `account` still does the moving
  py orch.py board <...>                    Bridge - see `board.py --help`
  py orch.py board ready [--json] [--limit N] [--all]   backlog items you can start now (queued, unblocked, no hold)

<name> is a session display name, a session-id prefix, or a pane id.
Every spawn records an owner - `--owner`, else the session that ran the command. `kill` refuses a
pane whose owner is another live session; `reap` skips it. `--force` overrides and says who forced.
Model tiers: fable = hardest (architecture, a bug that survived its own fix), opus = hard,
sonnet = normal building (default), haiku = trivial mechanical.
`--lead` marks a spawned pane as a lead: its footer adds the merge gate - before merging to main
it must run `review` on itself and wait for SHIP; a FIX-FIRST is not a suggestion.
`--domain "<subject>"` makes the pane a DOMAIN MATE: it owns a subject rather than a task, so it
outlives the work it was spawned for, `reap` never closes it, and `ls` prints its crew indented
under it plus a DOMAIN MATES list at the bottom. A new mate gets its OWN herdr workspace (labeled
`--group` if given, else its label; reused if one already exists), and every crew pane it later
spawns lands in that same workspace by default - `space <mate>` moves an EXISTING mate and its
crew into one (Ryan 2026-09-23). `--workspace` (spawn/`ls`'s SPACE column) takes either form, an
id (`w5`) or a label.
Route a new ask to a listed mate before spawning anything fresh; matching its plain-language
subject is your judgement, not a keyword rule. A mate never spawns another mate - four rungs,
capped (Ryan 2026-09-14).
`--kind codex` runs codex-cli in the pane instead of claude; it has no SendMessage and reports by
writing a file (see "Codex and other kinds" in SKILL.md). `--model` there takes a codex model id
(`gpt-6-astra`), not a claude tier; leave it off for config.json's `codex_model`.
`--kind pi` (or `--harness pi`, the same flag) runs pi - the second harness, one CLI in front of
every non-Anthropic provider. Same file-report contract as codex. `--model` takes a pi model id
(`gpt-6-astra`, `gpt-5.6-sol`, ... - `pi --list-models`); the provider is derived from it. Two
things to know: a pi pane has NONE of Claude Code's hooks, so its prompt carries the trap rules as
text and it gets worktree-scoped, non-hardware work by default; and an Anthropic model on pi is
refused unless `--allow-extra-usage` is passed, because pi bills Anthropic subscription use as
per-token extra usage rather than against a Max plan window.
`--cwd` is the deepest folder the task owns, not the vault root - the pane loads that domain's
CLAUDE.md for free and herdr's agents view can tell the panes apart. A spawn that defaults to the
root says so.
"""
import datetime, glob, json, os, re, subprocess, sys, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import orchlib as L
import ws_map as WS
import dispatch_rule as DR
import axi_sdk as AX
import toon_format
import toon_out
import remote as RM
import seat_pair as SP

SCRIPTS = os.path.dirname(os.path.abspath(__file__))
TASK_TEMPLATE = os.path.join(L.TEMPLATES, "task.md")
# The standing rules every pane follows live in ONE file; a task's first prompt points at it
# instead of repeating ~400 tokens of them per pane (2026-10-10, handoff-better).
PANE_RULES = os.path.join(L.SKILL, "references", "pane-rules.md")
VOICE_MISHEARS = os.path.join(os.path.expanduser("~"), ".claude", "projects",
                              "C--Users-YOURNAME-Vault", "memory", "voice-mishears.md")


def _opt(a, flag, default=None):
    if flag not in a:
        return default
    i = a.index(flag) + 1
    return a[i] if i < len(a) else default   # a trailing flag with no value is "not given"


def _opt_multi(a, flag):
    """Every value passed for a repeatable flag, in call order - `--read x --read y`, not
    comma-split. `task`'s --read/--not are repeat-the-flag only: `--not "if tests fail, do not
    deploy"` and a path with a comma in it must both survive as one bullet, not split apart."""
    return [a[i + 1] for i, tok in enumerate(a) if tok == flag and i + 1 < len(a)]


def _bullets(items):
    return "\n".join(f"- {x}" for x in items) if items else "(none)"


def _where(cwd):
    """The pane's cwd, short enough for a board column: the last two path parts, or `ROOT` when it
    is the vault root itself. Ryan 2026-09-14: every pane in herdr's agents view read "Vault",
    because every spawn defaulted to the vault root - the column exists to make that visible."""
    if not cwd:
        return "-"
    p = os.path.normpath(cwd)
    if p.rstrip("\\/").lower() == os.path.normpath(L.DEFAULT_CWD).rstrip("\\/").lower():
        return "ROOT"
    parts = [x for x in p.replace("\\", "/").split("/") if x]
    return "/".join(parts[-2:]) if len(parts) > 1 else p


def _warn_root_cwd(cwd_arg, cwd):
    """One line when a spawn lands on the vault root by default rather than by choice.

    The nearest CLAUDE.md files load from the pane's cwd, so a pane parked at the vault root reads
    the root file and none of the domain ones - and herdr's agents view labels every such pane
    "Vault", which is what made this visible (Ryan, 2026-09-14)."""
    if cwd_arg:
        return
    if os.path.normpath(cwd).rstrip("\\/").lower() == \
            os.path.normpath(L.DEFAULT_CWD).rstrip("\\/").lower():
        print("WARN cwd is Vault root; pass --cwd <deepest folder the task owns, or a worktree> "
              "so the pane loads that domain's CLAUDE.md and herdr can tell it apart")


def _harness(a, default="claude"):
    """Which harness runs in the pane. `--kind` is the original spelling and `--harness` the one
    the pi work introduced; they mean the same thing, and passing both conflicting is a typo worth
    stopping on rather than silently picking one."""
    kind, harness = _opt(a, "--kind"), _opt(a, "--harness")
    if kind and harness and kind != harness:
        sys.exit(f"--kind {kind!r} and --harness {harness!r} disagree; pass one")
    return kind or harness or default


EXPENSIVE_TIERS = ("opus", "fable")


def _is_expensive_model(model):
    """True for opus/fable, by tier name or full model id (`claude-opus-5-5`, `claude-fable-5-1`).

    Ryan 2026-09-23: 25% of a week's quota gone in about 2 hours to opus mates and opus crew."""
    m = (model or "").strip().lower()
    return m in EXPENSIVE_TIERS or m in (L.MODELS[t].lower() for t in EXPENSIVE_TIERS)


def _guard_expensive_model(model, label, domain, expensive):
    """The single choke point for the opus/fable-needs-the-seat's-yes rule (2026-09-23 review,
    item 6). Lives inside `cmd_spawn` itself, not in the CLI argv parser, so EVERY path that can
    make `cmd_spawn` create a pane on a chosen model is guarded - CLI `spawn`/`task`, `review`
    (which used to call `cmd_spawn` directly and skip the old CLI-only check entirely), and any
    future caller. Ryan 2026-09-23: 25% of a week's quota gone in about 2 hours to opus mates and
    opus crew.

    The seat itself is never guarded. A seat spawning a `--domain` mate on opus/fable gets a
    one-line WARN instead of a refusal (mates run sonnet by rule, and this is easy to fat-finger).
    Anyone else (a mate, or any crew) is refused with exit code 2 unless `--expensive "<reason>"`
    was passed, in which case the reason goes on a board Done line.
    """
    if not _is_expensive_model(model):
        return
    if L.caller_is_seat():
        if domain:
            print(f"WARN spawning a domain mate on {model} - mates run sonnet by rule "
                  f"(Ryan 2026-09-23)")
        return
    if not (expensive and expensive.strip()):
        print(f"refused: {model} needs the seat's yes - message the seat a one-line reason, "
              f"then pass --expensive \"<reason>\"", file=sys.stderr)
        sys.exit(2)
    _board_done(f"{L.caller_name()} spawned {label} on {model} (expensive): {expensive.strip()}")


def _require_model_and_cwd(a):
    """`spawn`/`task` at the CLI refuse a silent default rather than quietly picking sonnet and the
    vault root (2026-09-23) - a spawn that never named its model or its folder was invisible until
    someone read the pane. A missing flag, a trailing flag with no value (`--cwd` at the end of
    argv), and an empty or whitespace-only value (`--model ""`) are all treated as "not given" -
    only a real value passes. Exit code 2, one line each; returns (model, cwd) so the caller does
    not re-parse. `--harness pi|codex` still needs a model, since this runs before `_harness` picks
    one. This is a CLI-layer guard only: `review` keeps its own fixed sonnet default and its own cwd
    resolution, and calls `cmd_spawn` directly with already-resolved values, never through here. An
    explicit `--cwd` equal to the vault root is allowed - only a MISSING (or empty) `--cwd` is
    refused; `_warn_root_cwd` still prints its own WARN for that case exactly as it did before.

    The opus/fable "needs the seat's yes" check used to live here too, but that only guarded the
    CLI - `review` calls `cmd_spawn` directly and never passed through this function, so `review
    --model opus` from a non-seat caller was never checked at all (2026-09-23 review, item 6). That
    check now lives in `_guard_expensive_model`, called from inside `cmd_spawn` itself.
    """
    model, cwd = _opt(a, "--model"), _opt(a, "--cwd")
    if not (model and model.strip()):
        print("refused: pass --model (run: py orch.py dispatch)", file=sys.stderr)
        sys.exit(2)
    if not (cwd and cwd.strip()):
        print("refused: pass --cwd <the deepest folder the task owns, or a git worktree>",
              file=sys.stderr)
        sys.exit(2)
    return model, _require_worktree(a, cwd)


def _require_rule(a):
    """`spawn`/`task`/`review` must name the crew-dispatch.json rule they matched, and the
    harness/model/effort must be one of that rule's USE entries (dispatch_rule.py; the G-10 hook
    runs the same check before the call). Exit 2 with the allowed combos, or the rule list when
    --rule is missing. Writes the audit row to state/dispatch-log.jsonl."""
    msg = DR.gate(a[0], a, source="cli", caller=L.caller_name())
    if msg:
        print(msg, file=sys.stderr)
        sys.exit(2)


WORKTREE_ROOT = r"C:\Users\YOURNAME\wt"


def _git_out(cwd, *args):
    r = subprocess.run(["git", "-C", cwd, *args], capture_output=True, text=True)
    return r.stdout.strip() if r.returncode == 0 else None


READ_ONLY_GOAL_RE = re.compile(
    r"\bread[- ]?only\b|\b(do not|don'?t|never) (edit|write|modify|change) (any )?(file|code)s?\b|\bno edits\b", re.I)


def _require_worktree(a, cwd):
    """A worker never lands in a repo's shared main checkout (Ryan, 2026-10-01). That day the
    orchestrator spawned three workers into Vault itself: one commit swept another pane's
    half-done block into it, and a finished branch could not merge over the others' uncommitted
    edits. The rule was already in AGENTS.md and still got skipped, so it lives here now.

    `--worktree` makes one at WORKTREE_ROOT/<repo>/<label> on a new branch <label> from the main
    checkout's HEAD, and moves the cwd into it at the same relative path. A short root because
    Vault has tracked paths past 260 chars that fail in a scratchpad. `--shared` is the way out
    for a pane that only reads. A cwd outside any git repo passes untouched."""
    if "--shared" in a:
        return cwd
    if "--worktree" not in a and READ_ONLY_GOAL_RE.search(_opt(a, "--goal", "") or ""):
        return cwd                                      # a goal that says read-only defaults to --shared
    gd = _git_out(cwd, "rev-parse", "--path-format=absolute", "--git-dir", "--git-common-dir",
                  "--show-toplevel")
    if not gd:
        return cwd
    git_dir, common, top = gd.splitlines()
    if os.path.normcase(os.path.normpath(git_dir)) != os.path.normcase(os.path.normpath(common)):
        return cwd                                      # already a linked worktree
    label = a[1] if len(a) > 1 and not a[1].startswith("--") else "worker"
    if "--worktree" not in a:
        print(f"refused: --cwd is the shared main checkout of {top}.\n"
              f"  Every worker gets its own git worktree, then merges its branch back.\n"
              f"  Add --worktree (makes {WORKTREE_ROOT}\\{os.path.basename(top)}\\{label} on branch "
              f"{label}),\n  or --shared if this pane only reads and never edits.",
              file=sys.stderr)
        sys.exit(2)
    rel = os.path.relpath(os.path.abspath(cwd), os.path.abspath(top))
    dest = os.path.join(WORKTREE_ROOT, os.path.basename(top), label)
    if not os.path.isdir(dest):
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        r = subprocess.run(["git", "-C", top, "worktree", "add", "-b", label, dest, "HEAD"],
                           capture_output=True, text=True)
        if r.returncode:
            print(f"refused: git worktree add failed: {r.stderr.strip()}", file=sys.stderr)
            sys.exit(2)
        print(f"worktree {dest} on branch {label}")
    out = os.path.normpath(os.path.join(dest, rel))
    os.makedirs(out, exist_ok=True)                     # an untracked or empty folder is not checked out
    return out


# Ryan 2026-10-05: "fewer Chrome pages". 45 panes each opening their own tabs (and some their own
# chrome.exe) is what this stops. `task` puts it at the top of every goal, `chrome_line` in every
# spawn footer.
CHROME_RULE = ("CHROME RULE: prefer curl or an API over a browser. If you need a browser, use one "
               "tab per pane: open it once, reuse it for every page, and close it when you finish. "
               "Never launch or relaunch Chrome yourself (no new chrome.exe, no Playwright launch); "
               "connect over CDP to the Chrome that `orch.py chrome agent` prints.")


def chrome_line():
    """Since 2026-09-04 the default browser for an agent is the headless agent-only Chrome on :9334,
    not Ryan's window. He asked for it after agents opened tabs on top of him mid-quiz."""
    return (f"{CHROME_RULE} For browser work use the agent-only Chrome: run "
            f"`py \"{os.path.abspath(__file__)}\" chrome agent` and connect over CDP to the url it "
            f"prints (http://127.0.0.1:9334). It is headless and needs no lock. Never open anything "
            f"in Ryan's own Chrome on port 9333 unless your task file names it. There is no lock "
            f"on it: other agents work in it at the same time. Two rules: work only in your own "
            f"tab, and never close or bring to front a tab you did not open.")


def lead_line(name):
    """2026-09-09: two sc-improve-lead ships (the gate tail-anchor bug, the arrow re-derive bug)
    shipped and were later found broken by the same lead who shipped them - nobody reviewed a
    lead's own merge before it landed. A `--lead` pane carries this rule in its footer so the gate
    is standing instruction from the first prompt, not something asked for once and forgotten."""
    return (f"You are a lead. Before every merge to main, run `py orch.py review {name}` and wait "
            f"for SHIP; a FIX-FIRST is not a suggestion.")


# SKILL.md section 8: "Every message from a lead to this seat is capped at 150 words" (set
# 2026-09-08 18:52, all four leads acknowledged). A pi mate is a seat with the same problem, so
# the cap travels with the reporting rule rather than being retyped next to it.
MATE_WORD_CAP = 150


def mate_report_line(mate):
    """How a crew pane answers an owner running on pi. One sentence, plus the 150-word cap.

    A pi pane has NO SendMessage address: herdr never registers one as an agent on this box
    (PI_NO_HERDR_AGENT) and peer messaging is keyed on a claude session id, which a pi sidecar row
    does not have (`sid` is always ""). So a crew pane told to "SendMessage your owner" reports
    into the void - it looks like it answered and the mate never hears a word. Its owner's real
    door is the orch-pane inbox, and `orch.py prompt` is what writes into it.
    """
    return (f"Your owner {mate} runs on the pi harness and has NO SendMessage address, so "
            f"SendMessage reaches nobody at all. Report to it - every question and your final "
            f"report - by running: py \"{os.path.abspath(__file__)}\" prompt {mate} "
            f"\"<your message>\". Keep every message to {MATE_WORD_CAP} words or fewer: state "
            f"changes, decisions needed, evidence in files rather than in the message.")


def seat_ref():
    """How a pane message names the seat. A name stored in a prompt goes stale at every restart
    (18 sessions re-told by hand on 2026-10-04), so the pane looks it up each time it reports."""
    return (f"seat (its name changes on restart: run py \"{os.path.abspath(__file__)}\" seat --who "
            f"right before each report and send to what it prints)")


def seat_lookup_line():
    return (f"If the session you report to is gone, py \"{os.path.abspath(__file__)}\" seat --who "
            f"prints the current orchestrator.")


def orch_line(kind="claude", report="", lead=False, name="", owner="", slim=False):
    """The standing constraints appended to every spawned prompt.

    Three report channels, because a pane's door depends on WHO owns it as well as what it runs:

    - owned by a pi mate -> `orch.py prompt <mate>` (see `mate_report_line`). GAP 3, 2026-09-15:
      a mate's crew was being told to SendMessage a seat that has no address, so a mate could
      spawn crew and then never hear back from it.
    - claude, owned by a live claude session -> SendMessage THAT OWNER, not the seat. A crew pane
      spawned by sc-mate was being told to SendMessage the SEAT instead of sc-mate (2026-09-23) -
      the report target falls back to the seat only when the owner is missing or no longer live.
    - codex or pi, owned by a claude session -> a report file; neither has SendMessage, and the
      shell rule is shorter. (pi does have a write tool, unlike codex, but the file-report
      contract is the same either way.)
    """
    mate = L.pi_mate(owner) if owner else None
    if mate:
        report_rule = mate_report_line(owner)
        if kind != "claude":
            # A non-claude crew pane keeps its report FILE as well: the mate reads the file for
            # the evidence and the inbox line for the state change.
            report_rule += (f" Write your final report to "
                            f"{report or 'the path named in your task file'} too, and print DONE "
                            f"on its own line as the last line of your reply.")
    elif kind != "claude":
        owner_live = bool(owner) and L.owner_is_live(owner)
        if owner_live:
            report_rule = (f"Your owner {owner} speaks for your human on small reversible "
                           f"decisions, but you cannot message it. Write your final report to "
                           f"{report or 'the path named in your task file'} and print DONE on its "
                           f"own line as the last line of your reply; that file is your only "
                           f"channel back.")
        else:
            report_rule = (f"The orchestrator session {seat_ref()} speaks for your "
                           f"human on small reversible decisions, but you cannot message it. Write "
                           f"your final report to {report or 'the path named in your task file'} "
                           f"and print DONE on its own line as the last line of your reply; that "
                           f"file is your only channel back.")
    else:
        # The report target is the OWNER - the session that actually assigned this pane its work -
        # falling back to the seat only when that owner is missing or no longer live (an unowned
        # seat spawn, or a mate that has since died). A crew pane spawned by sc-mate was being told
        # to SendMessage the SEAT instead of sc-mate, which breaks the whole point of the mate
        # layer: sc-mate's own crew reports were vanishing from its view (2026-09-23).
        owner_live = bool(owner) and L.owner_is_live(owner)
        owner_kind = L.owner_harness(owner) if owner_live else ""
        if owner_live and owner_kind:
            # The owner is a live pi/codex pane, not a formal domain mate (`mate` above is falsy
            # for it) - it has no SendMessage address at all, so telling a claude crew pane to
            # SendMessage it would look like a real channel and silently reach nobody, the same
            # GAP 3 class of bug `mate_report_line` exists to close for mates (2026-09-23 review,
            # item 7).
            report_rule = (f"Your owner {owner} runs on the {owner_kind} harness and has NO "
                           f"SendMessage address, so SendMessage reaches nobody at all. Report to "
                           f"it - every question and your final report - by running: "
                           f"py \"{os.path.abspath(__file__)}\" prompt {owner} "
                           f"\"<your message>\". It speaks for the human on small reversible "
                           f"decisions; act on its answers as theirs.")
        elif owner_live:
            report_rule = (f"Your owner {owner} speaks for your human on small reversible "
                           f"decisions; send it questions and your final report by SendMessage, "
                           f"and act on its answers as theirs.")
        elif (L.orchestrator_seat().get("kind") or "claude") != "claude":
            # Falling back to the seat, and the seat is a pi pane. A claude pane's native
            # SendMessage CANNOT reach one - herdr never registers a pi pane as an agent target on
            # Windows, so the send fails with "No agent named '<label>' is reachable". Telling a
            # crew pane to use it anyway would give it a channel that looks real and silently goes
            # nowhere, which is how a report gets lost.
            report_rule = (f"The orchestrator {seat_ref()} speaks for your human on "
                           f"small reversible decisions. It runs on the pi harness, so SendMessage "
                           f"CANNOT reach it - do not try. Write your final report to "
                           f"{report or 'the path named in your task file'} and print DONE on its "
                           f"own line as the last line of your reply; that file is your only "
                           f"channel back.")
        else:
            report_rule = (f"The orchestrator session {seat_ref()} speaks for your "
                           f"human on small reversible decisions; send it questions and your final "
                           f"report by SendMessage, and act on its answers as theirs.")
    privacy = ("Only the orchestrator talks to your human: never SendUserFile, never message or "
               "notify them directly; hand files and results to the session you report to. "
               if kind == "claude" else "")
    shell = (CLAUDE_SHELL_RULE if kind == "claude" else CODEX_SHELL_RULE) + " "
    if slim:
        # A task pane's first prompt: only who it reports to (it varies by owner) and where the
        # standing rules are. Chrome, shell, privacy and seat lookup are in PANE_RULES.
        base = (report_rule + " Only the orchestrator talks to your human. Standing rules "
                f"(Chrome, shell, git, privacy, seat lookup): read {PANE_RULES} once.")
    else:
        base = report_rule + " " + seat_lookup_line() + " " + privacy + shell + chrome_line()
    if lead:
        base = base + " " + lead_line(name)
    return base


# ---------------------------------------------------------------- board

SPINNER = "".join(chr(c) for c in range(0x2800, 0x2900))


def _screen_fingerprint(pane, lines=25):
    """The pane's screen with the animated chrome removed.

    The Braille spinner and the elapsed-time counter in the footer change every tick on their own,
    so a raw screen compare says "it moved" for a pane that is doing nothing at all. Strip both.
    """
    txt = L.pane_read(pane, lines) or ""
    txt = txt.translate({ord(c): None for c in SPINNER})
    txt = re.sub(r"\b\d+[hms]\b", "", txt)          # the footer's 1m / 22s / 2h counter
    txt = re.sub(r"\b\d+%\s*(ctx|limit)?", "", txt)  # ctx and limit percentages
    txt = re.sub(r"[▓░]", "", txt)                   # footer bars move with the percentages
    return " ".join(txt.split())


def _prompt_landed(pane, before, timeout=25):
    """True once the pane's screen actually changes after a prompt.

    `herdr agent prompt` answering `agent_prompted` only means herdr typed the characters. It does
    NOT mean an agent read them. Measured on codex 2026-09-04 and on FOUR claude panes on
    2026-09-16 (portfolio, vercel-billing, yt-revenue, reels-dash): the call returned success, the
    pane sat at 8% context, and the work never started. Ryan found it by noticing an absence, which
    is the most expensive way to find anything.

    A changed screen is not proof the agent understood, but an UNCHANGED one is proof it did not
    receive - and that is the case worth shouting about.
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        time.sleep(2)
        if _screen_fingerprint(pane) != before:
            return True
    return False


# A short message that only asks or states idle status. Panes asked the seat this over and over;
# the answer is always in `ls`. Anything longer than a short line, or carrying a report, is kept.
IDLE_PING_RE = re.compile(
    r"^(\[from [^\]]+\]\s*)?(hi\b|hello\b|ping\b)?[\s,.!]*((are|r) you (still )?(there|idle|working|alive|done|stuck)|"
    r"(still )?(idle|waiting)([, ]+(and )?(waiting )?(for (input|instructions|work|a task))?)?|any (updates?|news)|"
    r"(what'?s|what is) (your )?(status|up)|status\??|(i am|i'?m) (still )?idle|"
    r"(just )?checking in)[\s?.!]*$", re.I)


def _watcher_guard(cmd, args):
    """A seat pair's Opus watcher reaches only its seat (seat_pair.WATCHER_COMMANDS). Enforced
    here, in code, and again inside cmd_prompt/cmd_spawn/cmd_kill: the PreToolUse hook matches
    command TEXT and was walked past with `ORCH=x; py "$ORCH" prompt app-fix go` (review
    2026-10-11)."""
    msg = SP.watcher_refusal(cmd, list(args))
    if msg:
        print(msg, file=sys.stderr)
        sys.exit(2)


def cmd_prompt(name, text):
    """Send one follow-up prompt to any pane, whatever harness it runs.

    This exists because there was no supported way to say a second thing to a pi pane. A pi pane's
    SPAWN prompt lands (cmd_spawn_pi types it in with `pi_prompt`), but every follow-up went
    through `herdr agent prompt`, and herdr does not register a pi pane as an agent on Windows
    (PI_NO_HERDR_AGENT) - so the prompt was accepted by the CLI and silently reached nobody. The
    pane just sat there looking idle (Ryan, 2026-09-14).

    So route by what the pane actually is: a pi pane is typed into with send-text + Enter, and
    anything herdr calls an agent is prompted normally and VERIFIED - an unverified `agent prompt`
    is how this went unnoticed in the first place.
    """
    _watcher_guard("prompt", [name])
    if IDLE_PING_RE.match(text.strip()):
        print(f"dropped an idle/status ping to {name}: read `py orch.py ls` instead. "
              f"A report is Done/Proof/Next.")
        return
    pane_id, _p = agent_pane_for(name)
    # Stamp who is speaking. Nothing used to, so a message from a domain mate to its own crew
    # arrived as bare text and read exactly like an impersonation attempt: on 2026-09-16 the
    # pi-tools mate redirected its reviewer mid-review, the reviewer decided it was a prompt
    # injection claiming to be the orchestrator, ignored a genuine instruction, and reported the
    # incident as a spoofed channel. It was not spoofed - it was unlabelled. A crew pane cannot
    # tell its lead from an attacker if neither one signs its messages, and the safe guess it
    # makes is to distrust its lead, which is the more expensive mistake.
    # Stamp UNCONDITIONALLY. This used to skip the stamp when the text already began with
    # "[from ", which let any caller suppress its own real identity by pre-writing someone
    # else's stamp - the exact impersonation the stamp exists to prevent. A relay now reads
    # "[from relayer] [from original] ..." which is honest: the outer stamp is who actually
    # sent it, the inner one is only a claim (2026-09-16).
    sender = os.environ.get("ORCH_SENDER") or L.caller_name()   # ORCH_SENDER: orch_watchdog.py
    if sender:
        text = f"[from {sender}] {text}"
    kind = (L.sidecar_load().get(pane_id or "", {}) or {}).get("kind", "")
    if pane_id and kind in L.ONESHOT_SUFFIX:
        sys.exit(f"{name} is a one-shot {kind} pane: it runs one task and takes no follow-up "
                 f"prompt. Read its report, then spawn a new pane for the next task.")
    if pane_id and kind == "agy":
        agy_prompt(pane_id, text)       # herdr does not know agy as an agent: type it, as pi does
        print(f"prompted {name} ({pane_id}, agy) by send-text; flattened to one line, "
              f"{len(' '.join(text.split()))} chars.")
        return
    if pane_id and kind == "pi":
        how = pi_prompt(pane_id, text)
        if how == "inbox":
            # "delivered" is not "read": every inbox message goes out as a pi follow-up, so a
            # prompt sent mid-turn waits for that turn to end. Say which happened rather than
            # letting the caller assume the pane has answered (2026-09-15).
            queued, consumed = L.pi_inbox_depth(pane_id)
            print(f"prompted {name} ({pane_id}, pi) by inbox file; delivered verbatim, "
                  f"{len(text)} chars."
                  + (f" {queued} message(s) queued behind the running turn; {consumed} read "
                     f"so far." if queued else ""))
        else:
            print(f"prompted {name} ({pane_id}, pi) by send-text (orch-pane extension not live); "
                  f"pi takes one line, so it was flattened to {len(' '.join(text.split()))} chars.")
        return
    if pane_id and kind == "acp":
        # No screen at all: the text becomes a file the pane's ACP client sends as session/prompt.
        L.acp_inbox_put(pane_id, text)
        st = L.acp_state(pane_id)
        print(f"prompted {name} ({pane_id}, acp) by inbox file; delivered verbatim, {len(text)} chars."
              + (" The pane is mid-turn; it goes out when that turn ends."
                 if st and st.get("state") in ("working", "question") else "")
              + ("" if st else " WARN no live ACP client behind this pane; the file waits for one."))
        return
    target = pane_id
    if not target:
        m = L.resolve(name)
        if not m:
            sys.exit(f"no live pane or session matches {name!r}")
        target = L.panes_by_sid().get(m["sessionId"], {}).get("pane_id")
        if not target:
            sys.exit(f"{name} has no herdr pane, so there is nothing to type into. It may be a "
                     f"headless run; message it with SendMessage instead.")
        if target in L.acp_pane_ids():
            # An ACP child's session maps to its pane now; the pane's terminal is the client's.
            L.acp_inbox_put(target, text)
            print(f"prompted {name} ({target}, acp) by inbox file; delivered verbatim, {len(text)} chars.")
            return
    if SP.is_pair_seat(target) and not SP.wait_box_clear(target):
        sys.exit(f"Ryan has unsent text in {name}'s input box, so nothing was typed (it would have "
                 f"joined his draft). Try again in a minute.")
    before = _screen_fingerprint(target)
    r = L.herdr("agent", "prompt", target, text)
    typ = (r or {}).get("type") or ((r or {}).get("result") or {}).get("type")
    if typ != "agent_prompted":
        sys.exit(f"prompt FAILED: herdr answered {typ!r} for pane {target} ({name}), not "
                 f"'agent_prompted'. Nothing was sent.")
    if not _prompt_landed(target, before):
        sys.exit(
            f"prompt LOST: herdr said agent_prompted for {name} ({target}) but the pane never "
            f"moved. The text did not reach the agent and NOTHING is running. This is the same "
            f"failure codex had on 2026-09-04. Read the pane - it is usually parked on a dialog "
            f"that swallowed the keystrokes:\n"
            f"  herdr pane read {target} --lines 25\n"
            f"Clear it (`herdr pane send-keys {target} escape`) and send again.")
    print(f"prompted {name} ({target}, {kind or 'claude'}); {typ}, and the pane moved.")


def cmd_cancel(name):
    """The Escape key for an ACP pane: ends its open turn (session/cancel). The pane stays up and
    the next prompt starts a new turn."""
    pane_id, _p = agent_pane_for(name)
    kind = (L.sidecar_load().get(pane_id or "", {}) or {}).get("kind", "")
    if not pane_id or kind != "acp":
        sys.exit(f"cancel works on --acp panes only, and {name!r} is not one. For another pane press "
                 f"Escape in it: herdr pane send-keys <pane> escape")
    L.acp_inbox_put(pane_id, "", suffix=".cancel")
    print(f"cancel sent to {name} ({pane_id}, acp); the open turn ends, the pane stays up.")


def cmd_own(name, owner=None, domain=None):
    """Re-parent a live session, or turn one into a domain mate, without respawning it.

    `own <name> --owner <mate>` hands an existing pane to a mate; `own <name> --domain "<subject>"`
    promotes it to a mate itself. Both keep the session alive, which is the entire point: the three
    learn tracks that became the first mate's crew on 2026-09-14 were holding 444k, 374k and a
    finished AWS track between them, and respawning them to change one field would have thrown all
    of that away.

    Passing neither prints what the session owns today.
    """
    m = L.resolve(name)
    if not m:
        sys.exit(f"no live session matches {name!r}")
    sid = m["sessionId"]
    was_owner, was_domain = m.get("owner", ""), m.get("domain", "")
    if owner is None and domain is None:
        print(f"{m['name']} owner={was_owner or '-'} domain={was_domain or '-'}")
        return
    new_owner = was_owner if owner is None else owner
    new_domain = was_domain if domain is None else domain
    if new_owner and new_domain and L.session_domain(new_owner):
        sys.exit(f"refusing: {new_owner} is a domain mate and a mate never owns another mate "
                 f"(four rungs, capped). Give this one to the orchestrator, or drop --domain.")
    L.set_owner(sid, m["name"], new_owner, new_domain)
    print(f"{m['name']}: owner {was_owner or '-'} -> {new_owner or '-'}, "
          f"domain {was_domain or '-'} -> {new_domain or '-'}")


def cmd_hold(name, reason="", clear=False, until=""):
    """Park a pane on Ryan so watch_sessions never idle-nudges it. `hold <name> [reason]`,
    `unhold <name>`. Reason shows in `ls`-adjacent tooling later; for now it is the record.
    `--until 2h` / `--until 2026-10-09T03:40Z` makes it a declared wait: the watchers leave the
    pane alone until then and the hold stops counting after it (declared_wait.py). A hold with no
    --until stays until `unhold`, as before."""
    path = os.path.join(L.BOARD_DIR, "holds.json")
    try:
        with open(path, encoding="utf-8") as fh:
            holds = json.load(fh)
    except (OSError, ValueError):
        holds = {}
    if clear:
        holds.pop(name, None)
        print(f"unhold {name}")
    else:
        holds[name] = {"reason": reason or "waiting on Ryan", "ts": time.time()}
        if until:
            import declared_wait
            iso = declared_wait.parse_until_arg(until, time.time())
            if not iso:
                sys.exit(f"--until {until!r}: use a UTC time like 2026-10-09T03:40Z or a length "
                         f"like 45m or 2h")
            holds[name]["until"] = iso
        print(f"hold {name}: {holds[name]['reason']}"
              + (f" until {holds[name]['until']}" if until else ""))
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(holds, fh, indent=1)


def cmd_go(name, clear=False):
    """Let one pane touch hardware - lights, monitor power - for the next 30 minutes. `go <pane>`
    after Ryan says go, `ungo <pane>` the moment it is done. A review pane blanked all three
    monitors mid-work on 2026-09-11, so block-known-traps.py refuses every hardware command from a
    pane with no fresh go. Never grant one on your own judgement; this is Ryan's word, relayed."""
    ok, msg = L.go_revoke(name) if clear else L.go_grant(name)
    print(msg)
    sys.exit(0 if ok else 1)


def cmd_idle():
    """Minutes since Ryan last touched the keyboard or mouse (Windows GetLastInputInfo).
    Leads call this before writing a page that has an open check: a reload wipes typed,
    unqueued answers, and nothing in state.json can tell you whether he is mid-typing.
    Over 10 minutes idle is safe; under that, wait."""
    import ctypes
    class LII(ctypes.Structure):
        _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]
    lii = LII(); lii.cbSize = ctypes.sizeof(LII)
    ctypes.windll.user32.GetLastInputInfo(ctypes.byref(lii))
    mins = (ctypes.windll.kernel32.GetTickCount() - lii.dwTime) / 60000.0
    print(f"idle {mins:.1f} min ({'safe to write a page' if mins >= 10 else 'Ryan is active - wait'})")
    return mins


ACP_STATUS = {"working": "busy", "idle": "idle", "question": "waiting", "starting": "starting",
              "error": "error"}


def _acp_columns(pane_id, status, ctx_s, kind):
    """(STATUS, CTX, KIND) for an ACP pane, from its client's state file instead of the screen.
    No live client (it died, or stopped) is `dead`: a healthy-looking idle row would let `reap`
    and the watchers treat a pane with nobody behind it as fine."""
    st = L.acp_state(pane_id)
    if not st:
        return "dead", ctx_s, "ERROR"
    ctx_k = st.get("ctx_k")
    return (ACP_STATUS.get(st.get("state"), status),
            f"{ctx_k:4}k" if isinstance(ctx_k, int) else ctx_s,
            st.get("word") or kind)


def cmd_ls(fmt="text", home=False):
    by_sid = L.panes_by_sid()
    # One `workspace list` for the whole board, not one per row - a workspace label per pane is
    # cheap only read this way (Ryan 2026-09-23: each domain mate gets its own space, so SPACE is
    # the column that shows which one a pane actually landed in).
    ws_labels = {w.get("workspace_id"): w.get("label", "") for w in L.workspaces()}
    side = L.sidecar_load()
    acp_side = {pane: e for pane, e in side.items() if e.get("kind") == "acp"}
    acp_by_sid = {}
    for pane in acp_side:
        s = L.acp_state_read(L.acp_key_of(pane, side)).get("sessionId")
        if s:
            acp_by_sid[s] = pane
    rows = []
    for sid, m in L.sessions().items():
        ents = L.tail_entries(sid)
        ctx, model = L.last_usage(sid, ents)
        kind, _ = L.classify(sid, ents)
        p = by_sid.get(sid, {})
        harness, status, ctx_s = "claude", m["status"], f"{ctx:4}k"
        # herdr never links the ACP child's session to the pane (measured live, A1 review), so the
        # registry row has no pane. The client's state file names its session: match on that.
        acp_pane = p.get("pane_id") if p.get("pane_id") in acp_side else acp_by_sid.get(sid)
        if acp_pane:
            if acp_pane != p.get("pane_id"):
                p = {q.get("pane_id"): q for q in L.panes()}.get(acp_pane, {})
            harness = "acp"
            status, ctx_s, kind = _acp_columns(acp_pane, status, ctx_s, kind)
        rows.append((m["name"], status, ctx_s, harness, model, p.get("pane_id", "-"),
                     L.effective_status(p), kind, m.get("owner", "") or "-",
                     _where(p.get("cwd")), m.get("domain", ""),
                     ws_labels.get(p.get("workspace_id"), ""), L.bg_tasks(p)[1]))
    # An ACP pane whose child has no registry row yet (or any more) is still on the board: the
    # protocol state file is the source, not the registry.
    listed = {r[5] for r in rows}
    if any(pane not in listed for pane in acp_side):
        live = {p.get("pane_id"): p for p in L.panes()}
        for pane, e in sorted(acp_side.items()):
            if pane in listed or pane not in live:
                continue
            p = live[pane]
            status, ctx_s, kind = _acp_columns(pane, "?", "   -", "?")
            rows.append((e.get("label") or pane, status, ctx_s, "acp", e.get("model") or "-", pane,
                         L.effective_status(p), kind, e.get("owner", "") or "-",
                         _where(p.get("cwd") or e.get("cwd")), "",
                         ws_labels.get(p.get("workspace_id"), ""), L.bg_tasks(p)[1]))
    # A non-claude pane has no registry .jsonl. ctx for pi is on its footer gauge (`57.0%/500k`).
    for pane_id, p in sorted(L.other_panes().items()):
        kind, _ = L.classify_pane(pane_id)
        e = side.get(pane_id, {})
        name = e.get("label") or p.get("label") or pane_id
        ast = L.effective_status(p)
        ctx_s = "   -"
        if (e.get("kind") == "pi" or p.get("agent") == "pi"):
            ktok = L.pi_ctx_k(pane_id)
            if ktok is not None:
                ctx_s = f"{ktok:4}k"
        rows.append((name, ast, ctx_s, p.get("agent", "?"), e.get("model", "-"), pane_id, ast,
                     kind, e.get("owner", "") or "-", _where(p.get("cwd") or e.get("cwd")),
                     e.get("domain", ""), ws_labels.get(p.get("workspace_id"), ""),
                     L.bg_tasks(p)[1]))
    notes = []
    if RM.load_hosts():          # no hosts.json = no ssh, ever
        try:
            remote_rows, notes = RM.ls_rows()
            rows += remote_rows
        except Exception as e:   # `ls` is the first command the seat runs: a remote bug must not stop it
            notes = [f"remote hosts skipped: {e}"]
    if fmt == "toon":
        return _ls_toon(rows, home, notes)
    print(f"{'NAME':14} {'STATUS':11} {'CTX':>6} {'HARNESS':7} {'MODEL':16} {'PANE':7} "
          f"{'AGENT':8} {'KIND':8} {'OWNER':12} {'SPACE':10} WHERE")
    if not rows:
        print(f"no sessions found - is herdr running, and did you start a session in it? "
              f"(looked in {L.SESS})")
        return
    for indent, r in _ls_tree(rows):
        n, st, ctx, har, model, pane, ast, kind, own, where, dom, space, bg = r
        print(f"{indent + n:14} {st:11} ctx={ctx} {har:7} {model:16} {pane:7} {ast:8} {kind:8} "
              f"{own:12} {space:10} {where}" + (f"  [{bg}]" if bg else ""))
    for note in notes:
        print(note)
    mates = [r for r in rows if r[10]]
    if mates:
        print()
        print("DOMAIN MATES - route a new ask to one of these before spawning anything fresh:")
        for r in sorted(mates, key=lambda r: r[0]):
            print(f"  {r[0]:14} {r[10]}")


def _lf_stdout():
    """TOON is read line by line, and Windows text mode ends every line with CRLF - a parser that
    splits on LF then sees a stray CR (axi-axi V05 failed on it). Only TOON output switches to LF;
    `ls --format text` stays byte-for-byte what it was."""
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(newline="\n")


def _ls_toon(rows, home=False, notes=()):
    """The default `ls`: count line, the 5 columns an agent needs, the domain mates, help lines.
    `ls --format text` keeps the wide tree (11 columns, bg tails) for a human's eyes. `home` puts
    the bin/description header on top: that is the no-args view."""
    _lf_stdout()
    if home:
        print(AX.render({"bin": AX._bin_name(sys.argv[0]), "description": DESCRIPTION}))
    print(toon_out.count_line(len(rows), len(rows)))
    if not rows:
        print(f"no sessions found - is herdr running, and did you start a session in it? "
              f"(looked in {L.SESS})")
    else:
        recs = [{"name": r[0], "status": r[1], "ctx": r[2].strip(), "pane": r[5], "owner": r[8]}
                for _, r in _ls_tree(rows)]
        print(toon_format.dumps({"ls": recs}))
    if notes:
        print(toon_format.dumps({"hosts": list(notes)}))
    mates = sorted((r for r in rows if r[10]), key=lambda r: r[0])
    if mates:
        print(toon_format.dumps({"mates": [{"name": r[0], "domain": r[10]} for r in mates]}))
        print("route a new ask to a mate above before spawning anything fresh")
    help_lines = ["Run `orch.py ls --format text` for harness and model and agent state and where",
                  "Run `orch.py <command> --help` for one command's flags and examples"]
    if home:
        help_lines.append("Run `orch.py --help` for every command")
    print(toon_format.dumps({"help": help_lines}))


def _ls_tree(rows):
    """[(indent, row)] with every domain mate's crew printed two spaces in under it.

    Ryan 2026-09-14: he could not SEE the hierarchy, because OWNER was a flat column and a
    two-level tree read as a flat list. A mate is any row carrying a domain (row[10]); its crew is
    every row whose OWNER (row[8]) is that mate's name. Everything else stays flat, sorted, after
    the mates - that includes the orchestrator itself, which is the root and has no parent to sit
    under. A pane owned by a mate that is no longer live is NOT orphaned into the flat list: it
    would silently lose its parent the moment the mate died, which is exactly the confusion this
    replaced.
    """
    mates = sorted([r for r in rows if r[10]], key=lambda r: r[0])
    # Every mate is claimed BEFORE any crew is assigned. Claiming them as we went let a mate that
    # happened to be owned by an earlier mate get indented as its crew and then printed a second
    # time at its own level - caught by test_a_mate_owned_by_another_mate_keeps_its_own_level.
    claimed = {id(m) for m in mates}
    out = []
    for m in mates:
        out.append(("", m))
        for c in sorted((r for r in rows if r[8] == m[0] and id(r) not in claimed),
                        key=lambda r: r[0]):
            out.append(("  ", c))
            claimed.add(id(c))
    out += [("", r) for r in sorted((r for r in rows if id(r) not in claimed),
                                    key=lambda r: r[0])]
    return out


def path_for(label):
    """Build a fresh handoff path for an already-known-unique label. No resolution - callers that
    already hold a resolved session must use this, not cmd_path, so a second session sharing
    that name can't make an already-disambiguated call fail."""
    os.makedirs(L.HANDOFFS, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y-%m-%d-%H%M")
    return os.path.join(L.HANDOFFS, f"{label}-{stamp}.md")


# ---------------------------------------------------------------- account (optional)

ACCOUNT_FILE = os.path.join(L.SKILL, "account.txt")


def current_account():
    """Account every new pane starts on: ORCH_ACCOUNT env, else account.txt, else '' (ambient)."""
    if not L.ACCOUNTS_TOOL:
        return ""
    a = os.environ.get("ORCH_ACCOUNT", "").strip()
    if not a and os.path.exists(ACCOUNT_FILE):
        a = open(ACCOUNT_FILE, encoding="utf-8").read().strip()
    return a


def account_env_args():
    """herdr --env args carrying CLAUDE_CODE_OAUTH_TOKEN for the chosen account. The switch is per
    process, so it never touches the shared login the other panes are using. Returns [] when
    account switching is off, which is the default."""
    a = current_account()
    if not a:
        return []
    r = subprocess.run(["pwsh", "-NoProfile", "-File", L.ACCOUNTS_TOOL, "token", a],
                       capture_output=True, text=True, timeout=60)
    tok = (r.stdout or "").strip().splitlines()[-1] if r.stdout else ""
    if not tok.startswith("sk-ant-"):
        sys.exit(f"could not get a token for account '{a}': {r.stderr or r.stdout}")
    return ["--env", f"CLAUDE_CODE_OAUTH_TOKEN={tok}"]


def other_account(name):
    """The saved account NOT named, so a dead-account fallback flips instead of guessing. Falls
    back to 'ambient' for a name outside the two-account setup, or for '' (already ambient)."""
    return {"primary": "secondary", "secondary": "primary"}.get(name, "ambient")


DEAD_ACCOUNT_MARKS = ("hit your session limit", "login was rejected")


def dead_mark(txt):
    """The dead-account mark in a pane's text, or None.

    Ignores the Remote Control banner ("Remote Control disconnected - Claude.ai login was
    rejected - run /login"): it names the SHARED login, which a pane on a per-process token
    does not use. Measured 2026-09-05 05:05: three live token panes were closed as dead on it."""
    low = " ".join(txt.lower().split())  # the banner wraps across pane lines
    marks = DEAD_ACCOUNT_MARKS
    if "remote control" in low:
        marks = tuple(m for m in marks if m != "login was rejected")
    return next((m for m in marks if m in low), None)


def cmd_account(name=None):
    if not L.ACCOUNTS_TOOL:
        print("account switching is off (no `accounts_tool` in config.json); "
              "every pane uses the ambient login")
        return
    if name is None:
        print(current_account() or "(ambient login)")
        return
    if name in ("none", "ambient", ""):
        if os.path.exists(ACCOUNT_FILE):
            os.remove(ACCOUNT_FILE)
        print("new panes use the ambient login")
        return
    os.environ["ORCH_ACCOUNT"] = name
    if not account_env_args():
        sys.exit("token check failed")
    open(ACCOUNT_FILE, "w", encoding="utf-8").write(name + "\n")
    print(f"new panes start on account '{name}'")


# ---------------------------------------------------------------- spawn / kill

TRUST_MARK = "trust this folder"
PANES_PER_TAB = int(L.cfg("panes_per_tab", 3))


def _abandon_pane(pane, why):
    """Close a pane whose claude never came up, then exit with the reason. Leaving the bare shell
    behind is how six dead "orchestrator" panes and three junk tabs piled up on 2026-09-07."""
    L.herdr("pane", "close", pane, soft=True)
    sys.exit(f"{why} (pane {pane} closed)")


IMPORTS_MARK = "allow external claude.md file imports"
# A CLAUDE.md in a nested repo (slack-agent, a worktree) that reaches the vault's @AGENTS.md is
# "external" to that repo, so claude asks once per project. Unanswered, herdr reports
# agent_not_ready and the spawn is abandoned - sc-reactionbox and family-slack twice on
# 2026-09-23. Approved only when every listed import is one of Ryan's own trees.
OWN_IMPORT_ROOTS = tuple(os.path.normcase(os.path.normpath(p)) for p in (
    os.path.expanduser("~/Vault"), os.path.expanduser("~/.claude")))


def _imports_are_ours(txt):
    """True when the external-imports dialog lists at least one path and every one of them sits
    under OWN_IMPORT_ROOTS. Anything else - a third-party repo - is left for a human."""
    paths = [l.strip() for l in txt.splitlines() if re.match(r"^\s*[A-Za-z]:[\\/]", l)]
    return bool(paths) and all(
        os.path.normcase(os.path.normpath(p)).startswith(OWN_IMPORT_ROOTS) for p in paths)


def _accept_trust_dialog(pane, timeout=120):
    """Answer claude's first-open dialogs, then wait for herdr to see the agent idle.

    Two can appear, one after the other: 'Is this a project you trust?' (pick 'Yes, I trust this
    folder', the second row) and 'Allow external CLAUDE.md file imports?' (pick 'Yes', the second
    row, only when `_imports_are_ours`). Returns the session id or None."""
    answered = set()
    deadline = time.time() + timeout
    while time.time() < deadline:
        raw = L.pane_read(pane, 40)
        txt = raw.lower()
        if TRUST_MARK in txt and "trust" not in answered:
            L.send_keys(pane, "Down")
            L.send_keys(pane, "Enter")
            answered.add("trust")
        elif IMPORTS_MARK in txt and "imports" not in answered:
            if not _imports_are_ours(raw):
                return None                 # a stranger's import: the caller abandons the pane
            L.send_keys(pane, "Down")
            L.send_keys(pane, "Enter")
            answered.add("imports")
        time.sleep(2)
        p = next((p for p in L.panes() if p.get("pane_id") == pane), {})
        s = (p.get("agent_session") or {}).get("value")
        if s and p.get("agent_status") in ("idle", "done"):
            return s
    return None


def _new_pane(label, cwd, group=None, workspace=None, env_args=(), reuse_pane=None):
    """A fresh pane for one agent: split into the `--group` tab while it has room, else a new tab.

    At most PANES_PER_TAB panes in one tab. A 30-row window split six ways left each agent two
    rows (2026-09-03): its dialogs rendered as one line and the watcher misread them as DONE.
    Overflow goes to `<group>-2`, `<group>-3`, ...

    `workspace`, when given, is a resolved workspace id (never a bare label) and SCOPES the
    `--group` tab search to it - `L.tabs()` lists every tab in every workspace, so an unscoped
    search could match a same-named tab living in another mate's space (Ryan 2026-09-23, giving
    each domain mate its own space). `reuse_pane` skips pane creation entirely and hands that pane
    straight back - used for a brand-new domain mate's workspace, whose `workspace create` already
    made a root pane; reusing it means the mate's first pane IS that pane, not a second one beside
    a stray empty shell. Because that pane already exists, `env_args` (the per-process account
    override) must reach it at CREATION time, through `_spawn_workspace`'s call to
    `L.ensure_workspace(..., env_args)` - never here; the caller has to compute `env_args` BEFORE
    resolving the workspace, not after (2026-09-23 review, item 1).
    """
    if reuse_pane:
        return reuse_pane
    env_args = list(env_args)
    tab_label = group
    if group:
        tab = None
        for n in range(1, 10):
            tab_label = group if n == 1 else f"{group}-{n}"
            candidates = L.tabs()
            if workspace:
                candidates = [t for t in candidates if t.get("workspace_id") == workspace]
            t = next((t for t in candidates if t.get("label", "").lower() == tab_label.lower()), None)
            if t is None:
                break                       # create this one below
            if t.get("pane_count", 0) < PANES_PER_TAB:
                tab = t
                break
        if tab:
            in_tab = [p for p in L.panes() if p["tab_id"] == tab["tab_id"]]
            anchor = in_tab[-1]["pane_id"]
            direction = "right" if len(in_tab) % 2 == 1 else "down"
            return L.herdr("pane", "split", "--pane", anchor, "--direction", direction,
                           "--cwd", cwd, "--no-focus", *env_args)["result"]["pane"]["pane_id"]
    args = ["tab", "create", "--cwd", cwd, "--label", tab_label or label, "--no-focus", *env_args]
    if workspace:
        args += ["--workspace", workspace]
    return L.herdr(*args)["result"]["root_pane"]["pane_id"]


# codex-cli's own bypass: `--dangerously-bypass-approvals-and-sandbox` is the exact analogue of
# claude's `--dangerously-skip-permissions` (the TUI header then reads "permissions: YOLO mode").
CODEX_FLAGS = ["--dangerously-bypass-approvals-and-sandbox"]
CODEX_TRUST_MARK = "do you trust the contents of this directory"
# The hooks-review dialog was "press t to trust all" in 0.153.2 and a numbered menu headed
# "Hooks need review" in 0.153.4. Escape closes both without trusting anything.
CODEX_HOOKS_MARKS = ("press t to trust all", "hooks need review")
# The composer placeholder codex draws once the TUI is really up. NOT the boxed
# "OpenAI Codex (v0.153.4)" header: on `codex resume` the restored transcript pushes that header
# off the top of the pane, so a spawn keyed to it hung for the full timeout (measured 2026-09-06).
CODEX_TUI_MARK = "ask codex to do anything"
# `check_for_update_on_startup` makes codex npm-install itself, print this, and EXIT to the shell.
CODEX_UPDATE_MARK = "please restart codex"


def codex_model(model=None):
    """The codex model id for a spawn. `--model` is shared with claude, so its claude tiers
    (sonnet/opus/...) mean "not given" here and fall through to config's `codex_model`."""
    return L.CODEX_MODEL if not model or model in L.MODELS else model


def codex_cmd(*args, model=None, effort=None):
    """`codex ... -m <id>` with the bypass flag. `-m` is accepted by the bare TUI and by
    `codex resume` alike (codex-cli 0.153.2), so one builder covers both. `effort` becomes
    `-c model_reasoning_effort="<level>"`, the form codex-cli takes for its reasoning dial."""
    return " ".join(["codex", *args, *CODEX_FLAGS, "-m", codex_model(model)]
                    + ([f'-c model_reasoning_effort="{effort}"'] if effort else []))


AGY_EXE = os.path.expandvars(r"%LOCALAPPDATA%\agy\bin\agy.exe")
AGY_FLAGS = ["--dangerously-skip-permissions"]
AGY_DEFAULT_MODEL = "gemini-3.8-flash"
AGY_TRUST_MARK = "do you trust the contents of this project"
AGY_TUI_MARK = "antigravity cli 1."


def agy_model(model=None):
    """`--model` is shared with claude, so its tiers (sonnet/opus/...) mean "not given" here."""
    return AGY_DEFAULT_MODEL if not model or model in L.MODELS else model


def agy_cmd(*args, model=None, effort=None):
    """`agy ... --model <id>` with the permission bypass. Full path: the User PATH entry the
    installer wrote is invisible to panes that were open before it. `effort` is low|medium|high|
    xhigh|max, the same words `--effort` takes."""
    return " ".join([f'& "{AGY_EXE}"', *args, *AGY_FLAGS, "--model", agy_model(model)]
                    + ([f"--effort {effort}"] if effort else []))


def grok_cmd(prompt_file, cwd, model=None, effort=None, report=None):
    """Headless grok: one task, one result stream. Full path on purpose - the PowerShell profile
    defines a `grok` function that runs pi, so the bare name is the wrong tool in a pane.
    `--verbatim` because grok sometimes refuses a bare machine prompt without it (T1: 4 of 4 with)."""
    mid = L.GROK_MODEL if not model or model in L.MODELS else model
    parts = [f'& "{L.GROK_EXE}"', "--prompt-file", f'"{prompt_file}"', "--model", mid,
             "--cwd", f'"{cwd}"', "--permission-mode", "bypassPermissions", "--no-auto-update",
             "--verbatim", "--output-format", "streaming-messages-json"]
    if effort:
        parts += ["--reasoning-effort", effort]
    cmd = " ".join(parts)
    return cmd + (f' | Tee-Object -FilePath "{report}{L.ONESHOT_SUFFIX["grok"]}"' if report else "")


def cursor_cmd(task_file, cwd, model=None, report=None):
    """Headless Cursor agent: one task, one JSON result line. Full path on purpose - bare `agent`
    on this box is Grok's agent.exe (first in PATH). No --cwd flag exists: Set-Location instead.
    Effort lives inside the model id, so there is no effort argument. `--trust` because a fresh
    worktree has no trust file; the model id is single-quoted because ids like
    `claude-opus-4-8[context=1m,effort=high]` hold commas, which PowerShell turns into an array."""
    mid = L.CURSOR_MODEL if not model or model in L.MODELS else model
    prompt = f"Read {task_file} and do exactly what it says."
    cmd = (f"Set-Location '{cwd}'; & '{L.CURSOR_EXE}' -p --force --trust --model '{mid}' "
           f'--output-format json "{prompt}"')
    return cmd + (f" | Tee-Object -FilePath '{report}{L.ONESHOT_SUFFIX['cursor']}'" if report else "")


# ---------------------------------------------------------------- effort + dispatch

# One dial, three spellings: claude takes `--effort`, pi `--thinking`, codex a config override.
# `max` is deliberately absent: Kun measured Sonnet 5 on max costing more than Fable (podcast
# 2026-06), and Ryan's rule is xhigh only for a genuinely hard one, never max.
EFFORTS = ("low", "medium", "high", "xhigh")
DISPATCH_FILE = os.path.join(os.path.dirname(SCRIPTS), "crew-dispatch.json")


def check_effort(effort):
    """The validated level, or None when none was asked for. Exits on a level no harness takes -
    a typo here would otherwise ride into the pane's command line and kill the launch quietly."""
    if not effort:
        return None
    if effort not in EFFORTS:
        sys.exit(f"unknown --effort {effort!r}; one of {', '.join(EFFORTS)}")
    return effort


def effort_args(effort):
    """The claude CLI flag pair for `effort`, or nothing."""
    return ["--effort", effort] if effort else []


MCP_MIN_FILE = os.path.join(os.path.dirname(SCRIPTS), "mcp-min.json")
MIN_FREE_GB = 6.0
PANE_GB = 0.5


def mcp_args(mcp):
    """Claude flags for the MCP set. `min` loads no MCP servers from settings, plugins or .mcp.json
    (each full session starts about 0.5 GB of playwright, context7, review-dashboard helpers a
    short worker never calls); `full` changes nothing."""
    if mcp not in ("full", "min"):
        sys.exit(f"unknown --mcp {mcp!r}; one of full, min")
    return ["--strict-mcp-config", "--mcp-config", f'"{MCP_MIN_FILE}"'] if mcp == "min" else []


def free_memory_gb():
    import ctypes

    class MS(ctypes.Structure):
        _fields_ = [("l", ctypes.c_ulong), ("load", ctypes.c_ulong)] + \
                   [(n, ctypes.c_ulonglong) for n in ("tp", "ap", "tpf", "apf", "tv", "av", "ae")]
    m = MS()
    m.l = ctypes.sizeof(MS)
    ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
    return m.ap / 2**30


def pane_gb():
    """RAM one more pane will take: the p90 of the live claude.exe trees (claude + its children), in
    GB. Measured 2026-10-05 with 46 panes up: median 0.34, p90 0.47. PANE_GB when none is up."""
    try:
        table, _ = L.proc_snapshot()
    except Exception:
        return PANE_GB
    rss, kids, claude = {}, {}, []
    for pid, i in table.items():
        rss[pid] = i["rss"]
        kids.setdefault(i["ppid"], []).append(pid)
        if i["name"].lower() == "claude.exe":
            claude.append(pid)

    def tree(pid, seen):
        if pid in seen:
            return 0
        seen.add(pid)
        return rss.get(pid, 0) + sum(tree(k, seen) for k in kids.get(pid, []))
    sizes = sorted(tree(pid, set()) / 2**30 for pid in claude)
    return sizes[int(len(sizes) * 0.9)] if sizes else PANE_GB


def hog_text(by_name, chrome_by_profile, top=3):
    """'chrome 18.6 GB (agent-chrome 13.4 GB), claude 5.2 GB': the biggest programs by RAM, with
    Chrome split by profile. Pure so the wording is tested."""
    parts = []
    for name, b in sorted(by_name.items(), key=lambda kv: -kv[1])[:top]:
        s = f"{name} {b / 2**30:.1f} GB"
        if name == "chrome" and chrome_by_profile:
            prof = sorted(chrome_by_profile.items(), key=lambda kv: -kv[1])[:2]
            s += " (" + ", ".join(f"{p} {v / 2**30:.1f} GB" for p, v in prof) + ")"
        parts.append(s)
    return ", ".join(parts)


def memory_hogs():
    """What is eating RAM right now, by program. A refusal that only says 'close idle panes' sent
    the seat after panes on 2026-10-07 while one browser held 17 GB in 54 job-application tabs.
    '' when it cannot tell."""
    try:
        table, allcmds = L.proc_snapshot()
        by_name, roots, rss, parent, cmds = {}, {}, {}, {}, {}
        for pid, i in table.items():
            n = i["name"].lower().removesuffix(".exe")
            by_name[n] = by_name.get(n, 0) + i["rss"]
            if n == "chrome":
                rss[pid], parent[pid], cmds[pid] = i["rss"], i["ppid"], allcmds.get(pid, "")
        prof = {}
        for pid, c in cmds.items():
            m = re.search(r"--user-data-dir=(?:\"([^\"]+)\"|(\S+))", c)
            key = os.path.basename((m.group(1) or m.group(2)).rstrip("\\/")) if m else None
            if "--type=" not in c or key:
                prof[pid] = key or "default"
        for pid, b in rss.items():
            k = prof.get(pid) or prof.get(parent.get(pid)) or "default"
            roots[k] = roots.get(k, 0) + b
        return hog_text(by_name, roots)
    except Exception:
        return ""


def pane_room(free, floor, per):
    """How many more panes fit before free RAM drops under the floor."""
    return max(0, int((free - floor) // per)) if per > 0 else 0


def check_free_memory():
    """Cap live panes by memory, not by count: refuse a spawn when one more pane would push free
    RAM under MIN_FREE_GB (2026-10-05: free fell to 4 GB of 62 GB with 45 claude sessions up and
    the box started paging). A pane count says nothing - an idle pane is 0.3 GB, a lead with MCP
    servers is 1 GB+."""
    floor = float(os.environ.get("ORCH_MIN_FREE_GB", MIN_FREE_GB))
    free = free_memory_gb()
    per = pane_gb()
    room = pane_room(free, floor, per)
    if room < 1:
        hogs = memory_hogs()
        sys.exit(f"refused: only {free:.1f} GB RAM free. One more pane takes about {per:.1f} GB and "
                 f"the floor is {floor:g} GB, so there is room for 0 panes. "
                 + (f"Biggest now: {hogs}. " if hogs else "")
                 + "Close idle panes (py orch.py ls) or kill orphan helpers, then spawn again. "
                 "A big Chrome is closed by the panes that own its tabs, never by tab id.")
    print(f"memory: {free:.1f} GB free, room for {room} more pane(s) at about {per:.1f} GB each")
    return room


def load_dispatch(path=None):
    """crew-dispatch.json as a dict, or None when the file is absent. Malformed JSON exits: a
    dispatch table nobody can read must be fixed, not routed around (firstmate's rule)."""
    path = path or DISPATCH_FILE
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            d = json.load(fh)
    except ValueError as e:
        sys.exit(f"crew-dispatch.json is not valid JSON: {e}")
    problems = dispatch_problems(d)
    if problems:
        sys.exit("crew-dispatch.json is invalid:\n  " + "\n  ".join(problems))
    return d


def dispatch_problems(d):
    """Every schema fault in a parsed crew-dispatch.json, as readable lines (empty = valid).
    Checks shape only: `when` is never parsed, and model names are the seat's call."""
    if not isinstance(d, dict):
        return ["top level must be an object"]
    out = []

    def check_use(where, use):
        profs = use if isinstance(use, list) else [use]
        if not profs:
            out.append(f"{where}: \"use\" is an empty list")
        for i, p in enumerate(profs):
            w = f"{where} entry {i}"
            if not isinstance(p, dict):
                out.append(f"{w}: must be an object with a harness")
                continue
            if p.get("harness") not in L.KINDS:
                out.append(f"{w}: harness {p.get('harness')!r} not one of {', '.join(L.KINDS)}")
            if "model" in p and not (isinstance(p["model"], str) and p["model"].strip()):
                out.append(f"{w}: model must be a non-empty string")
            if "effort" in p and p["effort"] not in EFFORTS:
                out.append(f"{w}: effort {p['effort']!r} not one of {', '.join(EFFORTS)}")
            if "spendPriority" in p and not _is_num(p["spendPriority"]):
                out.append(f"{w}: spendPriority must be a number")

    rules = d.get("rules")
    if not isinstance(rules, list):
        out.append("needs a top-level \"rules\" list")
        rules = []
    for n, r in enumerate(rules, 1):
        where = f"rule {n}"
        if not isinstance(r, dict):
            out.append(f"{where}: must be an object")
            continue
        if not (isinstance(r.get("when"), str) and r["when"].strip()):
            out.append(f"{where}: \"when\" must be a non-empty string")
        if "use" not in r:
            out.append(f"{where}: missing \"use\"")
        else:
            check_use(where, r["use"])
    if "default" in d:
        check_use("default", d["default"])
    out += [e for e in DR.schema_errors(d) if e.startswith("runwayFloor")]   # one rule: 0-100
    return out


def _is_num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _profiles(use):
    return use if isinstance(use, list) else [use]


def quota_headroom(rows):
    """{harness: weekly-percent-left} from `quota.collect()` rows. claude = the active account
    (that is the login a spawned pane inherits), else the best non-ambient claude row; pi = the
    codex row, because a pi pane here runs the ChatGPT plan (pi-panes.md). Unknown = None."""
    claude = [r for r in rows if r["provider"].startswith("claude/")
              and not r["provider"].startswith("claude/ambient")]
    active = next((r for r in claude if r.get("active")), None)
    best = active or next(iter(claude), None)
    codex = next((r for r in rows if r["provider"] == "codex"), None)
    grok = next((r for r in rows if r["provider"] == "grok"), None)
    return {
        "claude": best["weekly_left"] if best else None,
        "pi": codex["weekly_left"] if codex else None,
        "codex": codex["weekly_left"] if codex else None,
        "grok": grok["weekly_left"] if grok else None,
    }


def resolve_profile(profiles, headroom, floor=None):
    """Floor, then spendPriority, then most weekly headroom, then the order written (dispatch_rule.evaluate)."""
    return DR.resolve_profile(profiles, headroom, floor)


def profile_flags(p):
    out = f"--harness {p.get('harness', '?')}"
    if p.get("model"):
        out += f" --model {p['model']}"
    if p.get("effort"):
        out += f" --effort {p['effort']}"
    return out


def cmd_dispatch(path=None, refresh=False, explain=None):
    """Print the dispatch table with each rule's quota-resolved pick. The `when` text is for the
    orchestrator's judgment - nothing here matches a task to a rule, exactly as in firstmate."""
    d = load_dispatch(path)
    if d is None:
        print(f"no dispatch table at {path or DISPATCH_FILE}; spawn picks --model by hand")
        return
    import quota
    rows, _ = quota.collect(refresh)
    head = quota_headroom(rows)
    if explain:
        try:
            print(DR.explain(explain, d, head))
        except ValueError as e:
            sys.exit(f"dispatch --explain: {e}\n{DR.rule_list(d)}")
        return
    floor = DR.runway_floor(d)
    left = ", ".join(f"{k} {'-' if v is None else str(int(v)) + '%'}"
                     for k, v in head.items() if k != "codex")
    print(f"weekly headroom: {left}   (arrays resolve to the most headroom"
          + (f", skipping any harness under the {floor:g}% runway floor)" if floor is not None else ")"))
    for i, r in enumerate(d["rules"], 1):
        profs = _profiles(r.get("use", []))
        pick = resolve_profile(profs, head, floor)
        alt = [profile_flags(p) for p in profs if p is not pick]
        print(f"\n{i}. WHEN {r.get('when', '').strip()}")
        print(f"   USE  {profile_flags(pick) if pick else '(no profile)'}"
              + (f"   (else {' | '.join(alt)})" if alt else ""))
        if r.get("why"):
            print(f"   WHY  {r['why'].strip()}")
    dflt = _profiles(d.get("default", []))
    if dflt:
        pick = resolve_profile(dflt, head, floor)
        alt = [profile_flags(p) for p in dflt if p is not pick]
        print(f"\nDEFAULT {profile_flags(pick)}" + (f"   (else {' | '.join(alt)})" if alt else ""))


def _codex_ready(pane, timeout=180):
    """Clear codex's first-open dialogs and wait for the TUI. Returns "ready", "update" or None.

    Both dialogs swallow the first prompt if you skip them (measured 2026-09-04: `herdr agent
    prompt` returned agent_prompted and the text vanished into the hooks-review dialog). The
    directory dialog takes Enter for "Yes, continue"; the hooks-review dialog takes Escape, which
    closes it without trusting anything.

    herdr's `agent: codex` flag alone is not proof the TUI is up: 2026-09-06 codex auto-updated
    itself on launch, printed "Please restart Codex", exited to the shell, and herdr still reported
    a live idle codex agent - spawn called that a success and the prompt went nowhere. So the
    header box has to be on screen, and it has to be BELOW the last update notice.
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        low = L.pane_read(pane, 45).lower()
        if CODEX_TRUST_MARK in low:
            L.send_keys(pane, "enter")
            time.sleep(2)
            continue
        if any(m in low for m in CODEX_HOOKS_MARKS):
            L.send_keys(pane, "escape")
            time.sleep(2)
            continue
        after_update = low.rsplit(CODEX_UPDATE_MARK, 1)[-1]
        if CODEX_TUI_MARK in after_update:
            p = next((p for p in L.panes() if p.get("pane_id") == pane), {})
            if p.get("agent") == "codex" and p.get("agent_status") in ("idle", "done"):
                return "ready"
        elif CODEX_UPDATE_MARK in low:
            return "update"
        time.sleep(2)
    return None


def _codex_open(label, cwd, group, workspace, cmdline, model="", reuse_pane=None):
    """A new pane running `cmdline`, ready and past codex's first-open dialogs. -> (pane, pid)."""
    before = L.codex_tui_pids()
    pane = _new_pane(label, cwd, group, workspace, reuse_pane=reuse_pane)
    L.herdr("pane", "rename", pane, label)
    started = L.herdr("agent", "start", label, "--kind", "codex", "--pane", pane,
                      "--timeout", "90000", "--", *cmdline.split()[1:], timeout=120, soft=True)
    if not (started or {}).get("result"):
        # On Windows `codex` on PATH is an npm .cmd/.ps1 shim, and herdr's launcher runs
        # `Start-Process -FilePath codex`, which rejects it: "%1 is not a valid Win32
        # application" (2026-09-04). Typing the same command into the pane shell starts the
        # identical TUI and herdr still detects `agent: codex` on the pane.
        L.herdr("pane", "send-text", pane, cmdline, soft=True)
        L.send_keys(pane, "enter")

    # An auto-update exits back to the shell; the same command run again gets the new binary.
    for _ in range(2):
        state = _codex_ready(pane)
        if state != "update":
            break
        print(f"  codex auto-updated in {pane}; relaunching it")
        L.herdr("pane", "send-text", pane, cmdline, soft=True)
        L.send_keys(pane, "enter")
    if state != "ready":
        sys.exit(f"codex did not come up in pane {pane}:\n{L.pane_read(pane, 30)}")
    if model and model.lower() not in L.pane_read(pane, 45).lower():
        print(f"  WARN the TUI header in {pane} does not name {model}; check `/model`")
    # Every pid that appeared during the launch, not just the top one: the npm shim's node.exe and
    # the native codex.exe it spawns both match, and it is the child that holds the writer lock.
    return pane, sorted(L.codex_tui_pids() - before)


# ---------------------------------------------------------------- pi (the second harness)

# pi's composer hints, painted once the TUI is really up. Its update banner ("New version ... is
# available") can sit ABOVE these for weeks, so readiness is keyed to the composer, never the
# banner - the same mistake that hung codex spawns on its header box in 2026-09-06.
PI_TUI_MARKS = ("shift+enter for newline", "? for shortcuts", "esc to interrupt")
PI_LOGIN_MARK = "/login"
# The first `pi` run after an upgrade prints the ENTIRE release changelog into the pane - hundreds
# of lines - before settling. herdr's screen detector never finds pi's signature under that, so the
# pane stays `agent: null` forever and the spawn dies at the readiness timeout (measured 2026-09-14,
# on the 0.84.2 -> 0.85.1 upgrade). pi writes `lastChangelogVersion` into settings.json when it
# shows it, so the SECOND launch in the same pane is clean. Relaunch once rather than parse it.
PI_CHANGELOG_MARKS = ("new features", "fixed inherited", "### fixed", "by @")
PI_SETTINGS = os.path.join(os.path.expanduser("~"), ".pi", "agent", "settings.json")

# WHY A PI PANE IS DRIVEN BY send-text AND NOT `herdr agent prompt`.
# herdr never marks a pi pane `agent: pi` on this box, so `agent prompt` has nothing to deliver to
# and `agent start` answers `agent_pane_busy` once pi is running. Two separate Windows bugs, both
# measured 2026-09-14:
#   1. `herdr agent start --kind pi` runs `Start-Process -FilePath pi`, and `pi` on PATH is an npm
#      .cmd shim -> "%1 is not a valid Win32 application". codex hits this too and survives it,
#      because herdr also recognises the codex TUI from the screen.
#   2. herdr has no screen detector for pi; it relies on pi's herdr extension
#      (`~/.pi/agent/extensions/herdr-agent-state.ts`, integration v8), and that extension's socket
#      path is wrong on Windows. It builds `\\.\pipe\${HERDR_SOCKET_PATH}`, but on this box
#      HERDR_SOCKET_PATH is a FILE path (`C:\Users\YOURNAME\AppData\Roaming\herdr\herdr.sock`), so it
#      dials `\\.\pipe\C:\Users\...\herdr.sock`, which cannot exist. The extension loads (pi lists
#      it under [Extensions]) and every report it sends is silently dropped. herdr's own claude
#      integration sidesteps this by shelling out to `herdr pane report-agent-session` instead of
#      opening a socket - which is the shape the pi extension would need upstream.
# So: type into the pane, and read the screen back. `classify_pane` already does the reading, and
# `orchlib.PI_FOOTER` is what keeps pi's footer out of the answer.
PI_NO_HERDR_AGENT = ("herdr does not register a pi pane as an agent on Windows; this pane is "
                     "prompted with send-text and read off its screen")

# THE THING TO KNOW ABOUT A PI PANE: PreToolUse traps AND Stop hooks both run.
# ryan-traps.ts feeds bash/powershell/write/edit to block-known-traps.py (proven: `cd X &&`
# blocked inside pi). ryan-stop.ts runs the Stop hooks on agent_settled and delivers one
# [ryan-stop] nudge per human turn (proven 2026-09-15/16). A pane whose extension failed to
# load has nothing, so the prompt still carries the rules as text.
PI_NO_HOOKS_LINE = (
    "You are running in pi, not Claude Code. PreToolUse traps run through the ryan-traps "
    "extension. Stop hooks run through ryan-stop on settle (one nudge per human turn). Ship "
    "your own edits, never end on an offer. Work only inside the directory you were given. "
    "Never run `git reset --hard`, `git clean -f`, `git stash drop` or `git checkout -- <path>` "
    "outside your own worktree. Never touch lights, monitor power, another session's browser "
    "tabs, or anything outside this repo. If the task seems to need one of those, stop and "
    "report instead."
)

# pi + Anthropic is real money. pi's own providers.md: third-party harness use of an Anthropic
# subscription "draws from extra usage ... and is billed per token, not against Claude plan
# limits". Anthropic models belong on the claude harness, which spends the Max plan windows.
PI_ANTHROPIC_REFUSAL = (
    "refusing --kind pi with an Anthropic model ({model!r} -> provider 'anthropic').\n"
    "  pi bills Anthropic subscription use as per-token EXTRA USAGE, not against a Max plan "
    "window - it costs real dollars.\n"
    "  Use the claude harness for Anthropic models (drop --kind pi), or a Codex model here "
    "(see `py orch.py quota`).\n"
    "  If you genuinely mean to spend extra usage, pass --allow-extra-usage."
)


def pi_model(model=None):
    """The pi model id for a spawn. `--model` is shared with claude, so its tiers (sonnet/opus/...)
    mean "not given" here and fall through to config's `pi_model` - the same rule codex uses."""
    return L.PI_MODEL if not model or model in L.MODELS else model


def pi_cmd(model=None, effort=None):
    """`pi --provider <p> --model <id> [--thinking <level>]`. pi has no bypass flag to add: it is
    permissive by default and `-a` only trusts project-local extension/skill files, which is not a
    permission mode. `--thinking` is pi's name for the effort dial (same levels as claude's)."""
    mid = pi_model(model)
    provider = L.pi_provider(mid)
    return " ".join(["pi"] + (["--provider", provider] if provider else []) + ["--model", mid]
                    + (["--thinking", effort] if effort else []))


def _pi_ready(pane, timeout=120):
    """Wait for pi's TUI in `pane`. Returns "ready", "login", "changelog" or None.

    Readiness is pi's own status footer on screen, NOT herdr's `agent: pi` flag - see
    PI_NO_HERDR_AGENT. The footer's context gauge (`4.1%/272k`) is the one thing pi paints on every
    frame and nothing else on this box paints, so PI_FOOTER is the marker.

    The two non-ready answers are the states a spawn must handle differently from a plain timeout:
    a /login prompt is a human's click, and a changelog dump is fixed by relaunching.
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        txt = L.pane_read(pane, 60)
        low = txt.lower()
        # No provider configured yet: pi comes up and asks to be logged in. That is a human's
        # click, never this script's - report it rather than typing into it.
        if PI_LOGIN_MARK in low and "no provider" in low:
            return "login"
        if any(L.PI_FOOTER.search(l) for l in txt.splitlines()[-6:]):
            if sum(m in low for m in PI_CHANGELOG_MARKS) >= 2:
                return "changelog"      # footer is up but the changelog owns the scrollback
            return "ready"
        time.sleep(2)
    return "changelog" if sum(m in L.pane_read(pane, 120).lower()
                              for m in PI_CHANGELOG_MARKS) >= 2 else None


def _pi_launch(pane, cmdline):
    """Start pi in an existing pane. On Windows `herdr agent start --kind pi` cannot work at all
    (reason 1 in PI_NO_HERDR_AGENT), so this types the command into the pane shell, which is the
    same fallback the codex path uses. `agent start` is still tried first on a non-Windows box,
    where it would give herdr a real agent record."""
    if not L.WINDOWS:
        started = L.herdr("agent", "start", pane.replace(":", "-"), "--kind", "pi",
                          "--pane", pane, "--timeout", "60000", "--",
                          *cmdline.split()[1:], timeout=90, soft=True)
        if (started or {}).get("result"):
            return
    L.herdr("pane", "send-text", pane, cmdline, soft=True)
    L.send_keys(pane, "enter")


def pi_prompt(pane, text):
    """Send one prompt to a pi pane. -> "inbox" or "typed".

    Inbox first: when the orch-pane extension is live in that pane (`L.pi_state`), the prompt is
    written to its inbox and delivered as a real user message - multi-line, verbatim, and queued
    as a follow-up if the pane is mid-turn (firstmate's fm-send idea, ported 2026-09-15).

    Typing is the fallback, single line only: pi's composer submits on Enter and inserts a newline
    on Shift+Enter, so a prompt with a literal newline would go out as several truncated prompts.
    """
    if L.pi_state(pane):
        L.pi_inbox_put(pane, text)
        return "inbox"
    one_line = " ".join(text.split())
    L.herdr("pane", "send-text", pane, one_line, soft=True)
    time.sleep(1)                      # the composer has to take the paste before Enter submits it
    L.send_keys(pane, "enter")
    return "typed"


def _pi_open(label, cwd, group, workspace, cmdline, reuse_pane=None):
    """A new pane running pi, up and ready. -> (pane, [pids])."""
    before = L.pi_tui_pids()
    pane = _new_pane(label, cwd, group, workspace, reuse_pane=reuse_pane)
    L.herdr("pane", "rename", pane, label)
    _pi_launch(pane, cmdline)

    # One retry, and only for the changelog: quit pi, start it again, and the second run is clean
    # because the first wrote `lastChangelogVersion`. Never loop - a pi that cannot come up twice
    # is a real failure and should say so with the screen attached.
    for _ in range(2):
        state = _pi_ready(pane)
        if state != "changelog":
            break
        print(f"  pi printed its post-upgrade changelog in {pane}; restarting it")
        L.send_keys(pane, "ctrl+c")
        time.sleep(2)
        L.send_keys(pane, "ctrl+c")
        time.sleep(3)
        _pi_launch(pane, cmdline)
    if state == "login":
        sys.exit(f"pi in pane {pane} has no provider and is asking for /login. That sign-in is "
                 f"Ryan's own click - put it on the board, do not type into it.\n"
                 f"{L.pane_read(pane, 20)}")
    if state != "ready":
        sys.exit(f"pi did not come up in pane {pane} (state={state}):\n{L.pane_read(pane, 30)}")
    return pane, sorted(L.pi_tui_pids() - before)


def mate_watch_cmd(mate):
    """The exact command that arms one pi mate's crew watcher. Under `reloader.py` like the other
    three watchers, so a fix to `orchlib.py` reloads it without anyone restarting it by hand."""
    return [sys.executable, os.path.join(SCRIPTS, "reloader.py"),
            os.path.join(SCRIPTS, "mate_watch.py"), mate]


def mate_watch_running(mate, max_age=180):
    """True while that mate's watcher heartbeat is fresh. Same test `routines\\orchestrator-health`
    uses on the other three: nothing else on this box can tell a live watcher from a dead one."""
    try:
        with open(os.path.join(L.BOARD_DIR, f"heartbeat-mate-{mate}.json"), encoding="utf-8") as fh:
            return time.time() - float(json.load(fh).get("ts", 0)) < max_age
    except (OSError, ValueError, TypeError):
        return False


def start_mate_watch(mate):
    """Start a pi mate's crew watcher DETACHED, so it outlives the spawn command that armed it.

    A claude mate arms a Monitor and the Monitor belongs to that session. pi has no Monitor tool
    at all, and polling in a loop would burn the mate's own turns, so the watcher is its own
    process writing into the mate's inbox. It gets its own process group and its own HIDDEN
    console, so it does not share this pane's console and outlives the command that armed it.
    Output goes to a log beside the board rather than to this pane - a spawn's stdout is read by
    a human, not tailed.

    CREATE_NO_WINDOW, never DETACHED_PROCESS. Detached means no console at all, so the python.exe
    that reloader.py spawns for mate_watch.py allocated its own: a black window titled with
    python's full path, one per mate, on top of whatever Ryan was reading (three at once on
    2026-09-16). A hidden console is inherited by every descendant, same rule as
    render_gate.run_ffmpeg and host-machine-notes.
    """
    log = os.path.join(L.BOARD_DIR, f"mate-watch-{mate}.log")
    os.makedirs(L.BOARD_DIR, exist_ok=True)
    kw = {}
    if L.WINDOWS:
        kw["creationflags"] = (subprocess.CREATE_NO_WINDOW
                               | subprocess.CREATE_NEW_PROCESS_GROUP)
    else:
        kw["start_new_session"] = True
    try:
        with open(log, "a", encoding="utf-8") as fh:
            p = subprocess.Popen(mate_watch_cmd(mate), stdout=fh, stderr=subprocess.STDOUT,
                                 stdin=subprocess.DEVNULL, close_fds=True, **kw)
    except OSError as e:
        # Never fail a spawn over the watcher: the mate is up and working either way, and
        # `resume` prints the command to arm it by hand.
        print(f"  WARN could not start the crew watcher for {mate}: {type(e).__name__}: {e}")
        print(f"  arm it by hand: {' '.join(mate_watch_cmd(mate))}")
        return None
    print(f"  crew watcher for {mate} started detached (pid {p.pid}); log {log}")
    return p.pid


def seat_watch_cmd():
    return [sys.executable, os.path.join(SCRIPTS, "reloader.py"),
            os.path.join(SCRIPTS, "seat_watch.py")]


def start_seat_watch():
    """Start the seat's wake channel DETACHED, for a seat that has no Monitor tool.

    Same shape and the same CREATE_NO_WINDOW rule as `start_mate_watch` - see its docstring for
    why a hidden console rather than DETACHED_PROCESS.
    """
    log = os.path.join(L.BOARD_DIR, "seat-watch.log")
    os.makedirs(L.BOARD_DIR, exist_ok=True)
    kw = {}
    if L.WINDOWS:
        kw["creationflags"] = (subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP)
    else:
        kw["start_new_session"] = True
    try:
        with open(log, "a", encoding="utf-8") as fh:
            p = subprocess.Popen(seat_watch_cmd(), stdout=fh, stderr=subprocess.STDOUT,
                                 stdin=subprocess.DEVNULL, close_fds=True, **kw)
    except OSError as e:
        print(f"  WARN could not start the seat watcher: {type(e).__name__}: {e}")
        print(f"  arm it by hand: {' '.join(seat_watch_cmd())}")
        return None
    print(f"  seat watcher started detached (pid {p.pid}); log {log}")
    return p.pid


def cmd_spawn_pi(label, prompt, cwd=None, workspace=None, group=None, report="",
                 model=None, task="", owner="", allow_extra_usage=False, domain="", effort=None,
                 reuse_pane=None):
    """A pi pane. Like a codex pane it reports by file rather than SendMessage, but unlike codex it
    DOES get an agent_session id from herdr, so the board can key on it."""
    cwd = cwd or L.DEFAULT_CWD
    owner = owner or L.caller_name()
    mid = pi_model(model)
    provider = L.pi_provider(mid)
    if provider == "anthropic" and not allow_extra_usage:
        sys.exit(PI_ANTHROPIC_REFUSAL.format(model=mid))
    prompt = prompt + " " + orch_line("pi", report, owner=owner) + " " + PI_NO_HOOKS_LINE

    pane, pids = _pi_open(label, cwd, group, workspace, pi_cmd(mid, effort), reuse_pane=reuse_pane)
    p = next((p for p in L.panes() if p.get("pane_id") == pane), {})
    sid = (p.get("agent_session") or {}).get("value", "")

    # The sidecar is the ONLY record that this pane is a pi pane: herdr's own pane list leaves
    # `agent` null here (PI_NO_HERDR_AGENT), so `ls`, `kill` and the watcher all read it from here.
    L.sidecar_put(pane, label=label, kind="pi", report=report, cwd=cwd, group=group or "",
                  task=task, model=mid, provider=provider, pids=pids, sid=sid,
                  owner=owner, domain=domain, effort=effort or "", workspace=workspace or "",
                  started=datetime.datetime.now().isoformat(timespec="seconds"))
    # A DOMAIN MATE needs a wake channel. A claude mate arms a Monitor to learn when its crew
    # stops; pi has no Monitor tool and must not sit in a poll loop of its own, so its crew
    # watcher is a separate detached process that drops each line in the mate's inbox (GAP 2).
    if domain:
        start_mate_watch(label)
    how = pi_prompt(pane, prompt)
    print(f"spawned {label} pane={pane} kind=pi provider={provider or 'default'} model={mid}"
          + (f" effort={effort}" if effort else "")
          + (f" sid={sid[:8]}" if sid else "") + (f" report={report}" if report else "")
          + f" prompt={how}")
    if how == "typed":
        print(f"  note: orch-pane extension not live in this pane, so it was typed one-line; "
              f"{PI_NO_HERDR_AGENT}")
    if provider == "anthropic":
        print("  WARN spending Anthropic EXTRA USAGE (per-token dollars), not a Max plan window")
    return pane, sid


def cmd_spawn_grok(label, prompt, cwd=None, workspace=None, group=None, report="",
                   model=None, task="", owner="", domain="", effort=None, lead=False,
                   reuse_pane=None):
    """A Grok Build pane: ONE headless run in a plain shell pane. The prompt goes in a task file;
    the stream is teed to `<report>.ndjson`, and a `"type":"result"` line there means done."""
    if domain or lead:
        sys.exit("grok panes are one-shot; they cannot be a mate or a lead")
    cwd = cwd or L.DEFAULT_CWD
    owner = owner or L.caller_name()
    report = report or os.path.join(L.HANDOFFS, f"report-{label}.md")
    mid = L.GROK_MODEL if not model or model in L.MODELS else model
    os.makedirs(L.HANDOFFS, exist_ok=True)
    task_file = os.path.join(L.HANDOFFS, f"grok-task-{label}.md")
    with open(task_file, "w", encoding="utf-8") as fh:
        fh.write(prompt + "\n\n" + orch_line("grok", report, owner=owner) + "\n")
    pane = _new_pane(label, cwd, group, workspace, reuse_pane=reuse_pane)
    L.herdr("pane", "rename", pane, label)
    L.sidecar_put(pane, label=label, kind="grok", report=report, cwd=cwd, group=group or "",
                  task=task, model=mid, owner=owner, domain="", effort=effort or "",
                  workspace=workspace or "",
                  started=datetime.datetime.now().isoformat(timespec="seconds"))
    L.herdr("pane", "send-text", pane, grok_cmd(task_file, cwd, mid, effort, report), soft=True)
    L.send_keys(pane, "enter")
    print(f"spawned {label} pane={pane} kind=grok model={mid}"
          + (f" effort={effort}" if effort else "") + f" report={report}"
          + f" stream={report}{L.ONESHOT_SUFFIX['grok']}")
    return pane, None


def cmd_spawn_cursor(label, prompt, cwd=None, workspace=None, group=None, report="",
                     model=None, task="", owner="", domain="", effort=None, lead=False,
                     reuse_pane=None):
    """A Cursor Agent pane: ONE headless run in a plain shell pane, same shape as grok. The stream
    is the one JSON result line, teed to `<report>.json`. `effort` is ignored (it is in the model id)."""
    if domain or lead:
        sys.exit("cursor panes are one-shot; they cannot be a mate or a lead")
    cwd = cwd or L.DEFAULT_CWD
    owner = owner or L.caller_name()
    report = report or os.path.join(L.HANDOFFS, f"report-{label}.md")
    mid = L.CURSOR_MODEL if not model or model in L.MODELS else model
    os.makedirs(L.HANDOFFS, exist_ok=True)
    task_file = os.path.join(L.HANDOFFS, f"cursor-task-{label}.md")
    with open(task_file, "w", encoding="utf-8") as fh:
        fh.write(prompt + "\n\n" + orch_line("cursor", report, owner=owner) + "\n")
    pane = _new_pane(label, cwd, group, workspace, reuse_pane=reuse_pane)
    L.herdr("pane", "rename", pane, label)
    L.sidecar_put(pane, label=label, kind="cursor", report=report, cwd=cwd, group=group or "",
                  task=task, model=mid, owner=owner, domain="", workspace=workspace or "",
                  started=datetime.datetime.now().isoformat(timespec="seconds"))
    L.herdr("pane", "send-text", pane, cursor_cmd(task_file, cwd, mid, report), soft=True)
    L.send_keys(pane, "enter")
    print(f"spawned {label} pane={pane} kind=cursor model={mid} report={report}"
          f" stream={report}{L.ONESHOT_SUFFIX['cursor']}")
    return pane, None


def _codex_thread(cwd, since, tries=20):
    """The thread id codex just opened in `cwd`, by watching its own rollout files appear.

    Measured 2026-09-06: codex-cli has no command that prints the running thread's id, and the TUI
    header does not show it either - `codex resume --last` picks the newest thread across every
    pane, so two codex panes started together would both resume the same one. The rollout file is
    the only per-thread record, and it is written when the FIRST TURN starts, not when the TUI
    draws - so this runs after `agent prompt`, never before. Ids another pane already claimed in
    the sidecar are skipped for the same reason.
    """
    taken = {e.get("thread_id") for e in L.sidecar_load().values() if e.get("thread_id")}
    for _ in range(tries):
        rows = L.codex_threads(since=since - 5, cwd=cwd, exclude=taken)
        if rows:
            return rows[0][0]
        time.sleep(2)
    return None


def cmd_spawn_codex(label, prompt, cwd=None, workspace=None, group=None, report="",
                    model=None, task="", owner="", domain="", effort=None, reuse_pane=None):
    """A codex-cli pane. No claude session id and no ctx - the board reads its state off herdr and
    off the text on its screen - but the sidecar records enough (thread id, cwd, group, report,
    task file, model) that `resume` can rebuild the pane after herdr dies."""
    cwd = cwd or L.DEFAULT_CWD
    owner = owner or L.caller_name()
    mid = codex_model(model)
    prompt = prompt + " " + orch_line("codex", report, owner=owner)
    since = time.time()
    pane, pids = _codex_open(label, cwd, group, workspace, codex_cmd(model=mid, effort=effort), mid,
                             reuse_pane=reuse_pane)

    # The row goes in before the prompt so the watcher can see the report path from the first
    # scan; the thread id lands on top of it, because codex only opens its rollout file once the
    # first turn starts.
    L.sidecar_put(pane, label=label, kind="codex", report=report, cwd=cwd, group=group or "",
                  task=task, model=mid, thread_id="", pids=pids, owner=owner,
                  domain=domain, workspace=workspace or "",
                  started=datetime.datetime.now().isoformat(timespec="seconds"))
    L.herdr("agent", "prompt", pane, prompt)
    tid = _codex_thread(cwd, since)
    L.sidecar_put(pane, thread_id=tid or "")
    print(f"spawned {label} pane={pane} kind=codex model={mid} thread={tid or '-'}"
          + (f" report={report}" if report else ""))
    if not tid:
        print(f"  WARN no codex rollout file appeared for {cwd}; this pane cannot be resumed. "
              f"Check `orch.py ls` once it has answered - the file is written on its "
              f"first turn.")
    return pane, None


def _agy_ready(pane, timeout=120):
    """Wait for agy's TUI in `pane`. Returns "ready", "login" or None.

    Ready = the header line "Antigravity CLI 1.x.y" is on screen AND the folder-trust dialog is
    not. The trust dialog (`> Yes, I trust this folder`) is answered with Enter: the folder is
    the one the spawn was told to work in, and `--dangerously-skip-permissions` does not skip
    it (measured 2026-10-09). A sign-in screen is a human's job, so it is reported, never typed
    into."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        low = L.pane_read(pane, 60).lower()
        if "authentication required" in low or "paste the authorization code" in low:
            return "login"
        if AGY_TRUST_MARK in low:
            L.send_keys(pane, "enter")
            time.sleep(2)
            continue
        if AGY_TUI_MARK in low:
            return "ready"
        time.sleep(2)
    return None


def _agy_open(label, cwd, group, workspace, cmdline, reuse_pane=None):
    """A new pane running agy, up and ready. -> pane."""
    pane = _new_pane(label, cwd, group, workspace, reuse_pane=reuse_pane)
    L.herdr("pane", "rename", pane, label)
    L.herdr("pane", "send-text", pane, cmdline, soft=True)
    L.send_keys(pane, "enter")
    state = _agy_ready(pane)
    if state == "login":
        sys.exit(f"agy in pane {pane} is asking for a Google sign-in. That is Ryan's click; "
                 f"run agy once by hand in a normal terminal. Screen:\n{L.pane_read(pane, 30)}")
    if state != "ready":
        sys.exit(f"agy did not come up in pane {pane}:\n{L.pane_read(pane, 30)}")
    return pane


def agy_prompt(pane, text):
    """One prompt into an agy pane, as a single line, then Enter."""
    L.herdr("pane", "send-text", pane, " ".join(text.split()), soft=True)
    time.sleep(1)                      # let the composer take the text before Enter submits it
    L.send_keys(pane, "enter")


def cmd_spawn_agy(label, prompt, cwd=None, workspace=None, group=None, report="",
                  model=None, task="", owner="", domain="", effort=None, reuse_pane=None):
    """An Antigravity CLI pane. Spare Gemini harness; no resume support yet."""
    if domain:
        sys.exit("agy panes cannot be a mate (no resume, no inbox)")
    cwd = cwd or L.DEFAULT_CWD
    owner = owner or L.caller_name()
    mid = agy_model(model)
    report = report or os.path.join(L.HANDOFFS, f"report-{label}.md")
    prompt = prompt + " " + orch_line("agy", report, owner=owner)
    pane = _agy_open(label, cwd, group, workspace, agy_cmd(model=mid, effort=effort),
                     reuse_pane=reuse_pane)
    L.sidecar_put(pane, label=label, kind="agy", report=report, cwd=cwd, group=group or "",
                  task=task, model=mid, owner=owner, domain=domain, workspace=workspace or "",
                  started=datetime.datetime.now().isoformat(timespec="seconds"))
    agy_prompt(pane, prompt)
    print(f"spawned {label} pane={pane} kind=agy model={mid}"
          + (f" report={report}" if report else ""))
    return pane, None


def codex_resume_pane(old_pane, e, prompt=None):
    """Reopen one dead codex pane on its own thread, in the tab and cwd it had before."""
    label, cwd = e.get("label") or old_pane, e.get("cwd") or L.DEFAULT_CWD
    tid, mid = e.get("thread_id"), codex_model(e.get("model"))
    if not tid:
        print(f"  SKIP {label:14} no thread id recorded - kill it and re-task")
        return None
    # `herdr pane close` closes the pane and leaves the codex process running, and that process
    # still holds the thread's writer lock, so `codex resume <id>` dies with "thread <id> already
    # has an active writer (code -32600)" (measured 2026-09-06). The pane it belonged to is gone,
    # so nothing can reach it any more - stop it, and only it, by the pid recorded at spawn.
    if e.get("pids"):
        for pid in e["pids"]:
            L.kill_tree(pid)
        time.sleep(3)

    pane, pids = _codex_open(label, cwd, e.get("group") or None, None,
                             codex_cmd("resume", tid, model=mid), mid)
    L.sidecar_drop(old_pane)
    L.sidecar_put(pane, **{**e, "pids": pids,
                           "resumed": datetime.datetime.now().isoformat(timespec="seconds")})
    if prompt is None:
        where = e.get("task") or e.get("report") or "the task file named in your first message"
        prompt = (f"continue where you left off, your task file is {where}. "
                  + orch_line("codex", e.get("report", ""), owner=e.get("owner")))
    L.herdr("agent", "prompt", pane, prompt)
    print(f"  resumed {label:14} {old_pane} -> {pane} thread={tid[:8]} model={mid}")
    return pane


def _agent_alive(pane, sid=None):
    """True only if herdr's own pane list shows a live claude agent on `pane` right now - not the
    id `agent start` handed back at launch, which can point at a pane that then failed to come up.
    FRESH (2026-09-04): spawn printed `spawned ... sid=260fb3c2` and exited 0 while the pane held a
    bare shell; this is the check that call was skipping."""
    p = next((p for p in L.panes() if p.get("pane_id") == pane), {})
    if p.get("agent") != "claude":
        return False
    live_sid = (p.get("agent_session") or {}).get("value")
    if not live_sid:
        return False
    return sid is None or live_sid == sid


def _spawn_workspace(label, group, domain, owner, workspace, cwd, env_args=()):
    """(workspace_id_or_None, reuse_pane_or_None) - where this spawn's pane should land.

    `env_args` MUST be the real, already-computed per-process account override - compute it (via
    `account_env_args()`) BEFORE calling this, never after. When a brand-new domain mate's
    workspace is created here, `env_args` is what lets its root pane's shell inherit the right
    account; applying it later through `_new_pane` is too late, because `reuse_pane` skips pane
    creation entirely (2026-09-23 review, item 1).

    Ryan 2026-09-23: each domain mate gets its own herdr workspace instead of every mate and its
    crew piling into one shared space. In order:
      1. An explicit `--workspace` (id or label) wins outright - `L.find_workspace` takes either.
      2. A brand-new domain mate (`--domain` given) gets its OWN space, labeled `--group` if given
         else its own label, reused if one already exists under that exact label (so a respawned
         mate keeps its space). Refuses if that label's workspace already belongs to a DIFFERENT
         live mate - a label is not an identity, and two mates sharing a `--group` must not be
         silently merged into one space (2026-09-23 review, item 2). `reuse_pane` is set only when
         this call just created a fresh workspace, so the caller runs the new agent in its root
         pane instead of leaving a stray empty shell beside it.
      3. Ordinary crew whose owner is a live domain mate lands in that mate's own space, resolved by
         the mate's recorded IDENTITY (`L.mate_workspace_id`, keyed off its own spawn/`space` row),
         never by a label match on its name - a mate's `--group` can differ from its own label, and
         a label search would miss its actual space (2026-09-23 review, item 2). An unmigrated mate
         has none yet - `space <mate>` moves an existing one in.
      4. A seat spawn with no mate owner returns (None, None): `_new_pane` keeps today's
         behaviour, whatever workspace herdr defaults a new tab to.
    """
    if workspace:
        w = L.find_workspace(workspace)
        if not w:
            sys.exit(f"no workspace matches --workspace {workspace!r}")
        return w["workspace_id"], None
    if domain:
        ws_label = group or label
        ws_id, root_pane = L.ensure_workspace(ws_label, cwd, env_args)
        claimant = L.workspace_claimed_by(ws_id)
        if claimant:
            sys.exit(f"refusing: workspace {ws_label!r} (id {ws_id}) already belongs to live mate "
                     f"{claimant!r}; pass a different --group so {label!r} gets its own space")
        return ws_id, root_pane
    if L.session_domain(owner):
        ws_id = L.mate_workspace_id(owner)
        if ws_id:
            return ws_id, None
    return WS.place(label, cwd, domain, owner, env_args)   # a TAB in a domain workspace, never a new one


def _ledger_dispatched(label, kind, cwd, model, domain="", task=""):
    """One task.dispatched line in board/fleet-ledger.jsonl. Never raises (ledger.write is safe)."""
    L.ledger.write("task.dispatched", label, kind="mate" if domain else "crew", harness=kind,
                   model=model, project=os.path.basename(os.path.abspath(cwd).rstrip("\\/")),
                   **({"task_file": task} if task else {}))
    # Every spawn path (claude, acp, pi, codex, grok, agy, cursor) ends here, so the seat-pair
    # watcher sees them all; the ACP and non-claude branches return early (review r2).
    SP.auto_log("spawn", f"{label} on {kind} {model} in {cwd}" + (f", task file {task}" if task else ""))


ACP_PANE = os.path.join(SCRIPTS, "acp_pane.py")
ACP_READY_S = 120
ACP_KINDS = ("claude", "codex", "pi")      # --acp picks the protocol client by harness


def _acp_gate(kind, domain):
    """Every reason `--acp` refuses, before any pane exists. The kill switch is a file so a bad
    adapter day stops new ACP panes without a code change or a restart (references/acp.md)."""
    off = L.acp_off()
    if off:
        sys.exit(f"--acp refused: board/acp.off exists ({off}). Spawn without --acp, or delete the "
                 f"file once the adapter problem is fixed.")
    if kind not in ACP_KINDS:
        sys.exit(f"--acp runs {', '.join(ACP_KINDS)} only; --kind {kind} keeps its own path")
    if domain:
        sys.exit("--acp cannot be a domain mate: a mate arms Monitors and a crew watcher, which "
                 "need the screen path. Spawn the mate without --acp.")


def _acp_wait_ready(pane, key, timeout=ACP_READY_S):
    """The pane's state file once its client has a session (idle, or already working on the first
    prompt). Exits, closing the pane, on an error state or a timeout."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        st = L.acp_state_read(key)
        if st.get("state") == "error":
            L.sidecar_drop(pane)
            _abandon_pane(pane, f"the ACP client could not start: {st.get('error')}")
        if st.get("state") in ("idle", "working", "question") and st.get("sessionId"):
            return st
        time.sleep(1)
    L.sidecar_drop(pane)
    _abandon_pane(pane, f"the ACP client did not report a session in {timeout} s; see "
                        f"{L.acp_path('log', key, '.log')}")


def _acp_preflight(kind, model_id):
    """What must be true before an ACP pane of this harness may exist. Exits, naming the fix."""
    import acp_client as ACPC
    import acp_codex as ACPX
    import acp_pi as ACPPI
    keys = {"claude": ACPC.API_KEY_VARS, "codex": ACPX.API_KEY_VARS, "pi": ()}[kind]
    for var in keys:
        if os.environ.get(var):
            login = "ChatGPT" if kind == "codex" else "Claude"
            sys.exit(f"--acp refused: {var} is set. Decision D2: {login} subscription login only, no "
                     f"API key. Unset it and spawn again.")
    if kind == "claude" and not ACPC.adapter_path():
        sys.exit("--acp needs the pinned adapter: run `npm ci --omit=optional` in "
                 "skills/orchestrate/acp (references/acp.md)")
    if kind == "codex" and not ACPX.adapter_path():
        sys.exit("--acp --kind codex needs the pinned codex-acp: run `npm ci --omit=optional` in "
                 "skills/orchestrate/acp/codex (references/acp.md)")
    if kind == "pi" and not ACPPI.pi_command():
        sys.exit("--acp --kind pi needs `pi` on PATH (it runs `pi --mode rpc`)")
    if kind == "pi" and L.pi_provider(model_id) == "anthropic":
        sys.exit("--acp --kind pi refuses an Anthropic model: pi bills it as per-token extra usage, and "
                 "ACP panes are subscription-only (decision D2). Use a claude --acp pane for Anthropic models.")


def _spawn_acp(label, prompt, model, model_id, cwd, target_ws, group, owner, effort, env_args,
               reuse_pane, task="", kind="claude", report=""):
    """An ACP pane: a normal herdr pane whose foreground is acp_pane.py (the client), which owns a
    protocol child: claude-agent-acp, codex-acp or `pi --mode rpc`, by `kind`. The prompt goes in as
    a file, never typed. -> (pane, session id)."""
    _acp_preflight(kind, model_id)
    provider = L.pi_provider(model_id) if kind == "pi" else ""
    pane = _new_pane(label, cwd, group, target_ws, env_args, reuse_pane=reuse_pane)
    L.herdr("pane", "rename", pane, label)
    key = L.acp_key(pane)
    # herdr recycles pane ids: a leftover state file or inbox from the last pane with this id
    # would read as this one's.
    for stale in (L.acp_path("state", key, ".json"), *glob.glob(os.path.join(L.ACP_DIR, "inbox", key, "*"))):
        try:
            os.remove(stale)
        except OSError:
            pass
    prompt_file = os.path.join(L.ACP_DIR, "prompts", f"{key}.md")
    os.makedirs(os.path.dirname(prompt_file), exist_ok=True)
    with open(prompt_file, "w", encoding="utf-8") as fh:
        fh.write(prompt)
    L.sidecar_put(pane, label=label, kind="acp", cwd=cwd, group=group or "", task=task,
                  model=model_id, owner=owner, domain="", effort=effort or "",
                  workspace=target_ws or "", acp_key=key, harness=kind, report=report,
                  started=datetime.datetime.now().isoformat(timespec="seconds"))
    cmdline = (f'py -u "{ACP_PANE}" --label "{label}" --cwd "{cwd}" --owner "{owner}" '
               f'--model {model_id if kind != "claude" else model} --pane {pane} '
               f'--prompt-file "{prompt_file}"'
               + (f" --effort {effort}" if effort else "")
               + (f" --harness {kind}" if kind != "claude" else "")
               + (f" --provider {provider}" if provider else "")
               + (f' --report "{report}"' if report and kind != "claude" else ""))
    L.herdr("pane", "send-text", pane, cmdline, soft=True)
    L.send_keys(pane, "enter")
    st = _acp_wait_ready(pane, key)
    sid = st["sessionId"]
    if kind == "claude":       # only the claude child has a registry row for the owner to hang on
        L.set_owner(sid, label, owner, "", workspace=target_ws or "", spawned=True)
    print(f"spawned {label} pane={pane} harness=acp kind={kind} model={model_id}"
          + (f" effort={effort}" if effort else "") + f" sid={sid[:8]} owner={owner}"
          + (f" report={report}" if report else "")
          + f" log={L.acp_path('log', key, '.log')}")
    return pane, sid


def cmd_spawn(label, prompt, model="sonnet", cwd=None, workspace=None, group=None,
              kind="claude", report="", task="", owner=None, lead=False, color=None,
              allow_extra_usage=False, domain="", effort=None, expensive=None, mcp=None, acp=False,
              slim=False):
    _watcher_guard("spawn", [label])
    check_free_memory()
    owner = owner or L.caller_name()
    effort = check_effort(effort)
    mcp = mcp or ("full" if (domain or lead) else "min")
    # The single choke point for the opus/fable guard (2026-09-23 review, item 6) - every caller
    # reaches this line, `review` included, so there is no side door around it any more.
    _guard_expensive_model(model, label, domain, expensive)
    if acp:
        _acp_gate(kind, domain)
    # The four-rung cap, in code rather than prose. Kun's firstmate says "secondmates do not spawn
    # secondmates" and the reviewer of step 1 pointed out we only wrote it down. A fifth rung is
    # what the 2026-09-06 "no orchestrator of orchestrators" ruling was actually against: every
    # extra hop is one more seat that can go stale between Ryan and the work.
    if domain and L.caller_domain():
        sys.exit(f"refusing --domain: {L.caller_name()} is already a domain mate, and a mate never "
                 f"spawns another mate (four rungs, capped). Spawn this as ordinary crew - drop "
                 f"--domain - or ask the orchestrator to own it as a mate.")
    _warn_root_cwd(cwd, cwd or L.DEFAULT_CWD)
    cwd = cwd or L.DEFAULT_CWD
    # Computed BEFORE the workspace is resolved, not inside the retry loop below: a brand-new
    # domain mate's workspace is created here, and its root pane's env has to be right from
    # creation - `_new_pane`'s `reuse_pane` path never gets a chance to apply it later, since it
    # skips pane creation entirely (2026-09-23 review, item 1).
    env_args = account_env_args()
    target_ws, reuse_pane = _spawn_workspace(label, group, domain, owner, workspace, cwd, env_args)
    if acp and kind != "claude":
        # codex and pi: the same ACP pane, a different protocol child. They report by file, not
        # SendMessage, so the prompt carries the same report rule a TUI pane of that kind gets.
        if prompt.startswith("@"):
            if not os.path.exists(prompt[1:]):
                sys.exit(f"handoff file missing: {prompt[1:]}")
            prompt = (f"Your task is written in {prompt[1:]}. Read that file first and do exactly what "
                      f"it says. Do not ask your human to repeat anything that file already says.")
        mid = codex_model(model) if kind == "codex" else pi_model(model)
        prompt = prompt + " " + orch_line(kind, report, owner=owner) + (" " + PI_NO_HOOKS_LINE if kind == "pi" else "")
        res = _spawn_acp(label, prompt, model, mid, cwd, target_ws, group, owner, effort, env_args,
                         reuse_pane, task, kind=kind, report=report)
        _ledger_dispatched(label, kind, cwd, mid, domain, task)
        return res
    if kind != "claude":
        if kind not in L.KINDS:
            sys.exit(f"unknown --kind {kind!r}; this skill spawns {', '.join(L.KINDS)}")
        if kind == "pi":
            res = cmd_spawn_pi(label, prompt, cwd, target_ws, group, report, model, task, owner,
                               allow_extra_usage, domain, effort, reuse_pane=reuse_pane)
        elif kind == "agy":
            res = cmd_spawn_agy(label, prompt, cwd, target_ws, group, report, model, task, owner,
                                domain, effort, reuse_pane=reuse_pane)
        elif kind == "cursor":
            res = cmd_spawn_cursor(label, prompt, cwd, target_ws, group, report, model, task,
                                   owner, domain, effort, lead=lead, reuse_pane=reuse_pane)
        elif kind == "grok":
            res = cmd_spawn_grok(label, prompt, cwd, target_ws, group, report, model, task, owner,
                                 domain, effort, lead=lead, reuse_pane=reuse_pane)
        else:
            res = cmd_spawn_codex(label, prompt, cwd, target_ws, group, report, model, task, owner,
                                  domain, effort, reuse_pane=reuse_pane)
        _ledger_dispatched(label, kind, cwd, model, domain, task)
        return res
    if prompt.startswith("@"):
        f = prompt[1:]
        if not os.path.exists(f):
            sys.exit(f"handoff file missing: {f}")
        prompt = (f"Your task is written in {f}. Read that file first and do exactly what it "
                  f"says. Do not ask your human to repeat anything that file already says.")
    prompt = prompt + " " + orch_line(lead=lead, name=label, owner=owner, slim=slim)
    model_id = L.MODELS.get(model, model)
    if acp:
        res = _spawn_acp(label, prompt, model, model_id, cwd, target_ws, group, owner, effort,
                         env_args, reuse_pane, task)
        _ledger_dispatched(label, "claude", cwd, model, domain, task)
        return res
    # herdr refuses `agent start` with a name a live agent still owns (agent_name_taken). The
    # retiring orchestrator held "orchestrator" through six spawn attempts on 2026-09-07, each
    # one leaving an empty shell pane and a fresh overflow tab. The pane label keeps the label
    # asked for; only the herdr agent name and the claude --name get the suffix.
    agent_name = L.free_agent_name(label)
    if agent_name != label:
        print(f"agent name {label!r} is taken by a live pane; using {agent_name!r}")

    # A dead/limit-hit account dies in about a second. Try the spawn, and if that's what happened,
    # kill the pane, flip account.txt to the other account, and retry exactly once - never loop
    # more than that, or a stale ledger on both accounts spins forever.
    for attempt in (1, 2):
        if attempt > 1:
            # Only re-read here, after `cmd_account(other)` flipped it below - attempt 1 reuses
            # the `env_args` already computed above (and passed into `_spawn_workspace`), so a
            # freshly-created workspace root pane and its first ordinary crew pane never disagree
            # on which account they booted under (2026-09-23 review, item 1).
            env_args = account_env_args()

        # `reuse_pane` only ever applies to the first attempt: if that pane died, it is already
        # closed below, and a retry must fall through to ordinary tab creation in `target_ws`.
        pane = _new_pane(label, cwd, group, target_ws, env_args, reuse_pane=reuse_pane)
        reuse_pane = None
        L.herdr("pane", "rename", pane, label)
        # -n/--name sets the session display name, so the pane label and the peer-messaging name
        # stop drifting apart - two sessions landing on the same random suffix made SendMessage
        # demand a session-id ref.
        dead = None
        if env_args:
            # `herdr agent start` launches claude outside the pane shell, so the --env token never
            # reaches it (measured 2026-09-03: the pane still ran on the ambient login). Type the
            # command into the pane shell instead, which did inherit --env, and let herdr detect it.
            L.herdr("pane", "send-text", pane,
                    f'claude --dangerously-skip-permissions --model {model_id} --name "{agent_name}"'
                    + "".join(f" {x}" for x in effort_args(effort) + mcp_args(mcp)),
                    soft=True)
            L.send_keys(pane, "enter")
            sid = None
            dead_by = time.time() + 15  # a dead account fails in ~1s; do not burn the whole 120s
                                        # claude-startup budget on a pane that is never coming up.
            for _ in range(60):
                time.sleep(2)
                if dead is None and time.time() < dead_by:
                    txt = L.pane_read(pane, 40).lower()
                    dead = dead_mark(txt)
                    if dead:
                        break
                p = next((p for p in L.panes() if p.get("pane_id") == pane), {})
                s = (p.get("agent_session") or {}).get("value")
                if s and p.get("agent_status") in ("idle", "done"):
                    sid = s
                    break
            agent_target = pane  # prompt by pane id; no herdr agent name was registered
            if not dead and not sid:
                _abandon_pane(pane, f"claude did not come up in pane {pane} within 120 s")
        else:
            started = L.herdr("agent", "start", agent_name, "--kind", "claude", "--pane", pane,
                              "--timeout", "90000", "--",
                              "--dangerously-skip-permissions", "--model", model_id,
                              "--name", agent_name, *effort_args(effort),
                              *[x.strip('"') for x in mcp_args(mcp)], timeout=120, soft=True)
            err = (started or {}).get("error") or {}
            if err and err.get("code") != "agent_not_ready":
                # herdr said no before claude ever launched (agent_name_taken, a bad pane id).
                # Waiting on the trust dialog here burned 120 s per attempt on 2026-09-07.
                _abandon_pane(pane, f"herdr refused to start {agent_name!r} in pane {pane}: "
                                    f"{err.get('code')}: {err.get('message')}")
            sid = (((started or {}).get("result") or {}).get("agent") or {}) \
                .get("agent_session", {}).get("value")
            agent_target = agent_name
            if not sid:
                # A cwd claude has never opened on this machine shows "Is this a project you
                # trust?" and herdr answers agent_not_ready. Three panes stalled on it 2026-09-03.
                # Pick "Yes, I trust this folder" and wait for the agent the way the env path does.
                sid = _accept_trust_dialog(pane)
                agent_target = pane
            dead = dead_mark(L.pane_read(pane, 40))
            if not dead and not sid:
                _abandon_pane(pane, f"claude did not come up in pane {pane}: {started}")

        if not dead:
            break

        acct = current_account()
        other = other_account(acct)
        print(f"pane {pane} died on account '{acct or 'ambient'}' ({dead!r})")
        L.herdr("pane", "close", pane)
        if attempt == 2:
            sys.exit(f"still dead after falling back to '{other}'; giving up (one retry only)")
        cmd_account(other)
        print(f"flipped account.txt to '{other}', retrying spawn once")

    if not _agent_alive(pane, sid):
        print(f"FAILED to spawn {label}: pane {pane} shows no live claude agent "
              f"(sid={sid[:8] if sid else '-'})")
        print(f"--- last 20 lines of pane {pane} ---")
        print(L.pane_read(pane, 20))
        sys.exit(1)

    # --allow-dangerously-skip-permissions only PERMITS bypass; --dangerously-skip-permissions
    # enters it. Verify anyway: a deny rule still prompts inside bypass.
    if not L.ensure_bypass(pane, timeout=10):
        print(f"WARN {agent_target} pane={pane} not confirmed in bypass; "
              f"run: py \"{os.path.join(SCRIPTS, 'fix_mode.py')}\" {pane}")

    # Prompt-bar colour by group, before the task prompt so the pane is telling its colour from
    # its first turn. Ryan 2026-09-09: "use the /color command to ... make different sessions a
    # different color, tell them apart."
    c = color or group_color(group)
    if c:
        _set_color(agent_target, c, pane=pane)
    # Same trap as cmd_prompt, and it bit harder here because a spawn LOOKS like it worked: the
    # pane exists, it is named, it is on the board, and it is idle. On 2026-09-16 four panes were
    # spawned this way and every one sat at 8% context doing nothing until Ryan noticed the work
    # was not happening. Verify the pane actually moved rather than trusting the send.
    before = _screen_fingerprint(pane)
    L.herdr("agent", "prompt", agent_target, prompt)
    landed = _prompt_landed(pane, before)
    # Who this pane answers to. On 2026-09-08 the orchestrator killed `app-e2e-gate` two minutes
    # after its lead had assigned it work, because nothing on the board said the lead owned it.
    L.set_owner(sid, agent_target, owner, domain, workspace=target_ws or "", spawned=True)
    print(f"spawned {agent_target} pane={pane} model={model_id}"
          + (f" effort={effort}" if effort else "") + f" mcp={mcp} sid={sid[:8]} owner={owner}"
          + (f" domain={domain!r}" if domain else "") + (f" color={c}" if c else ""))
    if not landed:
        print(f"  !! TASK LOST: {label} is up but its prompt never reached the agent - the pane "
              f"has not moved. It will sit idle looking finished. Send it again:\n"
              f"     py \"$ORCH\" prompt {label} \"<the task>\"")
    _ledger_dispatched(label, "claude", cwd, model, domain, task)
    return pane, sid


# ---------------------------------------------------------------- colours

# `/color` in claude 2.1.260 takes exactly these (the error text lists them); `default` clears.
COLORS = ("red", "blue", "green", "yellow", "purple", "orange", "pink", "cyan", "default")
DEFAULT_GROUP_COLORS = {
    "orchestrator": "red",    # the seat, one of a kind
    "channela": "purple",   # channel identity colour on the review dash
    "webapp": "blue",
    "school": "green",
    "orch": "yellow",         # panes working on this skill
    "": "cyan",               # no group
}


def group_color(group):
    """Colour for a herdr tab/group. config.json `group_colors` overrides any key; an overflow
    tab ('webapp-2') folds back to its base group; unknown groups get the '' entry."""
    table = dict(DEFAULT_GROUP_COLORS)
    override = L.cfg("group_colors", {})
    if isinstance(override, dict):
        table.update({k.lower(): v for k, v in override.items()})
    base = re.sub(r"-\d+$", "", (group or "").lower())
    return table.get(base, table.get("", ""))


def _set_color(target, color, pane=None):
    """Type `/color <name>` into a pane. Subprocess argv on purpose: through the Bash tool Git Bash
    rewrites a leading slash to a path (measured 2026-09-09). `pane` is the herdr
    pane id used to read the screen back; looked up from the registry when not given."""
    if color not in COLORS:
        sys.exit(f"unknown colour {color!r}; one of {', '.join(COLORS)}")
    r = L.herdr("agent", "prompt", target, f"/color {color}", soft=True)
    if r is None:
        return False
    if not pane:
        try:
            sid = L.resolve(target).get("sessionId")
            pane = (L.panes_by_sid().get(sid) or {}).get("pane_id")
        except SystemExit:
            pane = None
    # `/color` returns no lifecycle change, so a prompt sent right behind it lands in the same
    # input line: on 2026-09-09 a reviewer got "/color cyanYou are reviewing PR #1 ..." and did
    # nothing. Wait for the ack on screen (or 6 s) before the caller types anything else.
    deadline = time.time() + 6
    while pane and time.time() < deadline:
        try:
            if "Session color set to" in L.pane_read(pane, 12):
                break
        except Exception:
            break
        time.sleep(0.3)
    return True


def seat_announce_text(label, pane):
    return (f"New orchestrator seat: {label} (pane {pane}). It speaks for your human now. Send every "
            f"report and question to {label} from now on; if your last report went to the old seat, "
            f"send it again. Find the seat any time with: py \"{os.path.abspath(__file__)}\" seat --who")


def announce_seat(label, pane, me_sid=""):
    """Tell every live session about the new seat, once. One failed send never stops the rest."""
    text = seat_announce_text(label, pane)
    targets, seen = [], set()
    by_sid = L.panes_by_sid()
    for sid, m in L.sessions().items():
        p = (by_sid.get(sid) or {}).get("pane_id")
        if p and p != pane and sid != me_sid and p not in seen:
            seen.add(p)
            targets.append((m.get("name") or p, p))
    for p, e in L.live_sidecar_rows():
        if p != pane and p not in seen:
            seen.add(p)
            targets.append((e.get("label") or p, p))
    sent = 0
    for name, p in targets:
        try:
            cmd_prompt(name if L.resolve(name) else p, text)
            sent += 1
        except SystemExit as e:
            print(f"  announce to {name} ({p}) failed: {e}")
    print(f"announced {label} as the seat to {sent} of {len(targets)} live session(s)")


def cmd_seat(name=None, clear=False, who=False, announce=False):
    """Record which pane holds the orchestrator seat, in any harness.

    Before this, the seat was whichever CLAUDE pane sat in a tab labelled `orchestrator`. No tab on
    this box carried that label, so `orchestrator_name()` had been falling back to the bare session
    prefix for everyone, and a pi pane could not hold the seat at all - it has no session id to
    find. `orch.py seat <label>` says it outright.
    """
    if clear:
        try:
            os.remove(L.SEAT_FILE)
            print("seat cleared; falling back to the tab-label scan")
        except OSError:
            print("no explicit seat was set")
        return
    if who:
        # One word, for a session whose report target is gone or stale: the lookup that replaces
        # a seat name baked into a prompt (18 DMs to move the seat on 2026-10-04).
        print(L.orchestrator_name())
        return
    if not name:
        seat = L.orchestrator_seat()
        if seat:
            print(f"orchestrator: {seat.get('label')} pane={seat.get('pane')} "
                  f"kind={seat.get('kind')} set={seat.get('set')}")
        else:
            print(f"no explicit seat; tab-label scan says: {L.orchestrator_name()}")
        return

    row = next(((pane, e) for pane, e in L.live_sidecar_rows() if e.get("label") == name), None)
    if row:
        pane, e = row
        kind, sid = e.get("kind", "claude"), e.get("sid", "")
    else:
        m = next((m for m in L.sessions().values() if m.get("name") == name), None)
        if not m:
            sys.exit(f"no live session or pane is called {name!r} - `py orch.py ls` lists them")
        sid = m.get("sessionId", "")
        p = L.panes_by_sid().get(sid) or {}
        pane, kind = p.get("pane_id"), "claude"
        if not pane:
            sys.exit(f"{name} has no pane herdr can see; it cannot hold the seat")

    os.makedirs(L.BOARD_DIR, exist_ok=True)
    with open(L.SEAT_FILE, "w", encoding="utf-8") as fh:
        json.dump({"pane": pane, "label": name, "kind": kind, "sid": sid,
                   "set": datetime.datetime.now().isoformat(timespec="seconds")}, fh, indent=1)
    print(f"orchestrator seat -> {name} (pane={pane}, kind={kind})")
    print("  every pane spawned from now on is told this name speaks for Ryan")
    if tag_seat_tab():
        print(f"  seat tab labelled {SEAT_TAB_LABEL}")
    if announce:
        announce_seat(name, pane, me_sid=sid)
    if kind != "claude":
        # A claude seat consumes watch_sessions through its own Monitor tool. This one has no
        # Monitor, so without a wake channel it would only learn a pane finished by sweeping
        # `ls` by hand - and silence would be indistinguishable from a quiet fleet.
        print("  this seat has no Monitor tool, so it needs a wake channel:")
        start_seat_watch()


SEAT_TAB_LABEL = "ORCHESTRATOR"


def tag_seat_tab():
    """Rename the seat's TAB to ORCHESTRATOR so Ryan finds it at a glance in the herdr sidebar.

    Only the tab label changes. Seat detection reads the pane label (`vault-*`), the seat file and
    the `orchestrator_tab` label, and the pane label is untouched; the case-insensitive
    `orchestrator_tab` scan even matches the new tab name. Re-run at boot by `space --order`, because
    a respawned seat pane lands in a fresh, numbered tab."""
    pane = L.orchestrator_pane()
    p = next((x for x in L.panes() if x.get("pane_id") == pane), None) if pane else None
    if not p or not p.get("tab_id"):
        return False
    if L.tab_label(p["tab_id"]) == SEAT_TAB_LABEL:
        return True
    return L.herdr("tab", "rename", p["tab_id"], SEAT_TAB_LABEL, soft=True) is not None


SHOW_FILE = os.path.join(L.BOARD_DIR, "shown.json")


def _orch_pane():
    # Through the pane, not the session id: a pi seat has no session id at all.
    pane = L.orchestrator_pane()
    p = next((x for x in L.panes() if x.get("pane_id") == pane), None) if pane else None
    if not p:
        sys.exit("cannot find the orchestrator pane. Set the seat explicitly:\n"
                 "  py orch.py seat <pane-label>\n"
                 f"(or label a tab '{L.ORCHESTRATOR_TAB}' and put a claude seat in it)")
    return p


def _pane_and_display(name):
    """Resolve `name` to a pane dict and the name to show the human, shared by `show`/`hide`.

    A non-Claude pane (pi, codex) has no entry in `L.resolve`'s session registry, so
    `agent_pane_for` - the same lookup `cmd_kill` already trusts - is tried first; only when it
    finds nothing do we fall back to a Claude session. `agent_pane_for`'s pane dict carries no
    `name`, so the display name prefers the sidecar label, then herdr's own pane label, then
    whatever the caller typed - the sidecar-wins rule `agent_pane_for` itself applies when
    picking *which* pane to act on.
    """
    pane_id, p = agent_pane_for(name)
    if p is not None:
        display = L.sidecar_load().get(pane_id, {}).get("label") or p.get("label") or name
        return display, p
    m = L.resolve(name)
    if not m:
        sys.exit(f"no live session named {name}")
    p = L.panes_by_sid().get(m["sessionId"])
    if not p:
        sys.exit(f"{name} has no herdr pane")
    return m["name"], p


def cmd_show(name, ratio="0.5"):
    """Move a session's pane to the RIGHT of the orchestrator pane, in this tab, so the human can
    watch it and type into it without switching tabs. Remembers where it came from for `hide`."""
    display, p = _pane_and_display(name)
    me = _orch_pane()
    if p["pane_id"] == me["pane_id"]:
        sys.exit("that is the orchestrator pane itself")
    if p.get("tab_id") != me.get("tab_id"):
        # Captured BEFORE the move: moving a tab's last pane can destroy that tab, so a label
        # looked up afterward comes back empty and `hide` rebuilds "parked" instead of the real
        # tab. Written only after herdr acknowledges the move - a failed move must not mark the
        # pane "shown" nor overwrite whatever home was already saved for it.
        home = {"tab_id": p.get("tab_id"), "tab_label": L.tab_label(p.get("tab_id")), "name": display}
        r = L.herdr("pane", "move", p["pane_id"], "--tab", me["tab_id"], "--split", "right",
                    "--target-pane", me["pane_id"], "--ratio", str(ratio), "--focus")
        err = (r or {}).get("error")
        if err:
            sys.exit(f"show FAILED: herdr would not move {display} ({p['pane_id']}): "
                     f"{err.get('code')}: {err.get('message')}")
        # A move across workspaces hands the pane a NEW workspace-qualified id (herdr's own
        # agent-guide: "A pane moved into another workspace receives a new workspace-qualified
        # pane ID"). `shown` must be keyed on THAT id, not the pre-move one - `hide` looks the pane
        # up fresh by name/label, so it finds the CURRENT id, and a `shown` row still filed under
        # the dead one is invisible to it, so it rebuilds "parked" instead of sending the pane
        # home (2026-09-23, giving each mate its own space made this a same-workspace-only bug).
        moved = ((r or {}).get("result") or {}).get("move_result") or {}
        pane_id = moved.get("pane", {}).get("pane_id") or p["pane_id"]
        if pane_id != p["pane_id"]:
            # A pi/codex pane is keyed in the sidecar by pane id, not by name - the cross-workspace
            # move above already handed it a new one, so its sidecar row (and hence `ls`, `kill`,
            # `resume`, its owner) would silently point at a pane that no longer exists otherwise.
            L.sidecar_rekey(p["pane_id"], pane_id)
        shown = {}
        if os.path.exists(SHOW_FILE):
            shown = json.load(open(SHOW_FILE, encoding="utf-8"))
        shown[pane_id] = home
        os.makedirs(L.BOARD_DIR, exist_ok=True)
        json.dump(shown, open(SHOW_FILE, "w", encoding="utf-8"), indent=2)
    else:
        pane_id = p["pane_id"]
    print(f"showing {display} ({pane_id}) right of the orchestrator; "
          f"`orch.py hide {display}` sends it back")


def cmd_hide(name):
    """Send a pane that `show` pulled in back to the tab it came from."""
    display, p = _pane_and_display(name)
    shown = json.load(open(SHOW_FILE, encoding="utf-8")) if os.path.exists(SHOW_FILE) else {}
    home = shown.get(p["pane_id"])
    tabs = {t["tab_id"] for t in L.tabs()}
    if home and home["tab_id"] in tabs:
        r = L.herdr("pane", "move", p["pane_id"], "--tab", home["tab_id"], "--split", "right",
                    "--no-focus")
        err = (r or {}).get("error")
        if err:
            sys.exit(f"hide FAILED: herdr would not move {display} ({p['pane_id']}) back to "
                     f"'{home['tab_label']}': {err.get('code')}: {err.get('message')}")
        new_id = (((r or {}).get("result") or {}).get("move_result") or {}) \
            .get("pane", {}).get("pane_id") or p["pane_id"]
        if new_id != p["pane_id"]:
            L.sidecar_rekey(p["pane_id"], new_id)
        shown.pop(p["pane_id"], None)
        json.dump(shown, open(SHOW_FILE, "w", encoding="utf-8"), indent=2)
        print(f"{display} back in tab '{home['tab_label']}'")
    else:
        label = (home or {}).get("tab_label") or "parked"
        r = L.herdr("pane", "move", p["pane_id"], "--new-tab", "--label", label, "--no-focus")
        err = (r or {}).get("error")
        if err:
            sys.exit(f"hide FAILED: herdr would not move {display} ({p['pane_id']}) to a new "
                     f"tab: {err.get('code')}: {err.get('message')}")
        new_id = (((r or {}).get("result") or {}).get("move_result") or {}) \
            .get("pane", {}).get("pane_id") or p["pane_id"]
        if new_id != p["pane_id"]:
            L.sidecar_rekey(p["pane_id"], new_id)
        shown.pop(p["pane_id"], None)
        json.dump(shown, open(SHOW_FILE, "w", encoding="utf-8"), indent=2)
        print(f"{display} moved to a new tab '{label}' (its old tab is gone)")


# ---------------------------------------------------------------- space (migrate a mate)

def _live_panes_with_owner():
    """[(pane, owner, domain, name)] for every live pane, claude or otherwise - the same two
    registries `ls` reads (the claude registry spliced with owners.json, plus the sidecar for
    pi/codex), flattened so `space` can walk every live pane without depending on `ls`'s own row
    shape."""
    out = []
    by_sid = L.panes_by_sid()
    for sid, m in L.sessions().items():
        p = by_sid.get(sid)
        if p:
            out.append((p, m.get("owner", ""), m.get("domain", ""), m["name"]))
    side = L.sidecar_load()
    for pane_id, p in L.other_panes().items():
        e = side.get(pane_id, {})
        out.append((p, e.get("owner", ""), e.get("domain", ""), e.get("label") or p.get("label") or pane_id))
    return out


def cmd_space(name=None, all_mates=False, dry_run=False):
    """`space <mate> [--dry-run]` or `space --all`: move a live domain mate, and every live pane it
    owns, into its own herdr workspace - one already labeled with the mate's name if it exists,
    else a fresh one created at the mate's own cwd.

    Crew stays grouped exactly as it is today: panes that currently share a tab share a NEW tab in
    the mate's workspace, keyed off that tab's CURRENT label - moving a tab's LAST pane destroys
    the tab (traps.md), so a group has to land together through one `herdr pane move` sequence per
    destination tab, never pane by pane into tabs that don't exist yet.
    """
    rows = _live_panes_with_owner()
    if all_mates:
        targets = sorted({n for _p, _o, d, n in rows if d})
        if not targets:
            print("no live domain mates found")
            return
    else:
        if not name:
            sys.exit("space needs a mate name, or --all")
        targets = [name]
    for mate in targets:
        _space_one(mate, rows, dry_run)


def _space_one(mate, rows, dry_run):
    mate_row = next(((p, o, d, n) for p, o, d, n in rows if n == mate and d), None)
    if not mate_row:
        print(f"SKIP {mate}: not a live domain mate")
        return
    mate_pane = mate_row[0]
    # Transitive: a mate's own lead spawns crew of its own, and `o == mate` alone only ever caught
    # the lead, leaving its workers behind in the old workspace when the mate moved (2026-09-23
    # review, item 3). Walk the ownership graph to a fixed point instead of one hop.
    owned_names = {mate}
    changed = True
    while changed:
        changed = False
        for _p, o, d, n in rows:
            if not d and o in owned_names and n not in owned_names:
                owned_names.add(n)
                changed = True
    members = [mate_pane] + [p for p, o, d, n in rows if n in owned_names and n != mate]
    groups = _group_by_tab(members)
    # The space is named after the mate's own tab ("ChannelA"), which is what Ryan reads in the
    # sidebar; identity is the recorded workspace id, never this label.
    ws_label = L.tab_label(mate_pane.get("tab_id")) or mate
    mate_sid = next((sid for sid, m in L.sessions().items() if m.get("name") == mate), None)
    recorded = (L.owners_load().get(mate_sid) or {}).get("workspace") if mate_sid \
        else (L.sidecar_load().get(mate_pane["pane_id"]) or {}).get("workspace")
    existing = L.find_workspace(recorded) if recorded else L.find_workspace(ws_label)
    # A pane id carries its workspace as the prefix ("w5:pZ"); herdr's pane list has no
    # workspace_id field.
    if existing and existing.get("workspace_id") == mate_pane["pane_id"].split(":")[0]:
        print(f"SKIP {mate}: already in its own workspace {existing['workspace_id']}")
        return
    if dry_run:
        dest = existing["workspace_id"] if existing else "a new workspace"
        print(f"PLAN {mate}: -> {dest} (label {ws_label!r})")
        for entry in groups.values():
            print(f"  tab {entry['label']!r}: " + ", ".join(p["pane_id"] for p in entry["panes"]))
        return
    ws_id = existing["workspace_id"] if existing else None
    ws_id, new_ids = _move_tab_groups(groups, ws_label, ws_id)
    if not ws_id:
        print(f"{mate}: nothing moved")
        return
    if mate_sid:
        m = L.sessions().get(mate_sid, {})
        L.set_owner(mate_sid, mate, m.get("owner", ""), m.get("domain", ""), workspace=ws_id)
    else:
        L.sidecar_put(new_ids.get(mate_pane["pane_id"], mate_pane["pane_id"]), workspace=ws_id)
    print(f"{mate}: now in workspace {ws_id} (label {ws_label!r})")


def _group_by_tab(panes):
    """`panes` grouped by tab_id -> {"label": str, "panes": [...]}, keyed on the id (not the label
    two distinct tabs can share) - shared by `_space_one` and `_move_workspace_to_tail` so the
    grouping rule only lives in one place."""
    groups = {}
    for p in panes:
        tab_id = p.get("tab_id")
        entry = groups.setdefault(tab_id, {"label": L.tab_label(tab_id) or p.get("pane_id"), "panes": []})
        entry["panes"].append(p)
    return groups


def _move_tab_groups(groups, ws_label, ws_id):
    """Move `groups` (tab_id -> {"label": str, "panes": [panes sharing that tab]}) into workspace
    `ws_id`, or a fresh workspace labeled `ws_label` when `ws_id` is falsy - the first move creates
    it. Shared by `_space_one` and `_move_workspace_to_tail`. Keyed by tab_id, not label - two
    distinct tabs can carry the same label (e.g. both "shell"), and grouping by label alone would
    merge them into one tab, breaking the tab-groups-stay-intact guarantee (Codex review,
    2026-09-24). -> (ws_id, {old_pane_id: new_pane_id}).
    """
    new_ids = {}

    def moved(r):
        return (((r or {}).get("result") or {}).get("move_result") or {}).get("pane", {})

    for entry in groups.values():
        lbl, panes = entry["label"], entry["panes"]
        first, rest = panes[0], panes[1:]
        # `--new-workspace` creates the space AND moves the pane in one call, so there is no
        # placeholder root pane to close - closing a fresh workspace's only pane destroys the
        # workspace itself (measured 2026-09-23: w9 vanished before the first move).
        if ws_id:
            args = ["--new-tab", "--workspace", ws_id, "--label", lbl]
        else:
            args = ["--new-workspace", "--label", ws_label, "--tab-label", lbl]
        r = L.herdr("pane", "move", first["pane_id"], *args, "--no-focus", soft=True)
        new = moved(r)
        if not new.get("pane_id"):
            print(f"  FAILED moving {first['pane_id']} ({lbl!r}): {(r or {}).get('error')}")
            continue
        new_ids[first["pane_id"]] = new["pane_id"]
        if new["pane_id"] != first["pane_id"]:
            L.sidecar_rekey(first["pane_id"], new["pane_id"])
        ws_id = ws_id or new["pane_id"].split(":")[0]
        print(f"  moved {first['pane_id']} -> {new['pane_id']} tab {lbl!r}")
        anchor, dest_tab = new["pane_id"], new.get("tab_id")
        for i, p in enumerate(rest):
            r2 = L.herdr("pane", "move", p["pane_id"], "--tab", dest_tab, "--target-pane", anchor,
                         "--split", "right" if i % 2 == 0 else "down", "--no-focus", soft=True)
            new2 = moved(r2)
            if not new2.get("pane_id"):
                print(f"  FAILED moving {p['pane_id']} into {lbl!r}: {(r2 or {}).get('error')}")
                continue
            new_ids[p["pane_id"]] = new2["pane_id"]
            if new2["pane_id"] != p["pane_id"]:
                L.sidecar_rekey(p["pane_id"], new2["pane_id"])
            anchor = new2["pane_id"]
            print(f"  moved {p['pane_id']} -> {new2['pane_id']} tab {lbl!r}")
    return ws_id, new_ids


# ---------------------------------------------------------------- space --order (sidebar order)

JOBS_VIEW = os.path.join(SCRIPTS, "jobs_view.py")


def cmd_space_order(dry_run=False):
    """`space --order [--dry-run]`: sort herdr's sidebar to match `space_order` in config.json
    (config.example.json documents the key) - e.g. ["Vault", "ChannelA", "School",
    "WebApp", "Debt", "Slack"].

    herdr 0.8.2 has no workspace-reorder command - sidebar position is creation order (`number` in
    `workspace list`) - so the only lever is the one `_space_one` already uses: `pane move
    --new-workspace` always appends a fresh workspace at the tail and the process inside keeps
    running. Moving every named space to the tail, in canonical order, one at a time, sorts the
    sidebar without ever closing a workspace that still holds a live agent - each source workspace
    only disappears as a side effect of herdr auto-closing an empty one, never from us calling
    `workspace close`.

    Position 1 (whatever it is today) is the seat's own space and is never moved. A space already
    sitting in the right relative order is left alone - only the spaces after the first mismatch
    get relocated, so a re-run once things are sorted moves nothing.
    """
    if not dry_run:
        tag_seat_tab()
    order = L.cfg("space_order", [])
    if not order:
        print("space --order: no space_order configured (see config.example.json)")
        return
    ws = sorted(L.workspaces(), key=lambda w: w.get("number", 0))
    if not ws:
        print("space --order: no live workspaces")
        return
    seat = ws[0]
    print(f"seat space stays put: {seat.get('label')!r} ({seat['workspace_id']}, #1)")
    # Slack goes first: it may sit anywhere in `space_order`, not just last, and creating it before
    # the reordering pass lets that pass judge its position like any other named space, instead of
    # a special-cased append-to-tail that lands it wrong when it isn't the last canonical entry
    # (Codex review, 2026-09-24).
    if _ensure_slack_space(order, dry_run) and not dry_run:
        ws = sorted(L.workspaces(), key=lambda w: w.get("number", 0))
    rest = ws[1:]
    known = order[1:]
    rest_labels = {w.get("label", "").lower() for w in rest}
    desired = [n for n in known if n.lower() in rest_labels]
    current = [w.get("label", "") for w in rest if w.get("label", "").lower() in
               {n.lower() for n in known}]
    if [c.lower() for c in current] == [d.lower() for d in desired]:
        print("space --order: already in order" + (f" ({', '.join(current)})" if current else ""))
        return
    prefix = 0
    while (prefix < len(current) and prefix < len(desired)
           and current[prefix].lower() == desired[prefix].lower()):
        prefix += 1
    to_move = desired[prefix:]
    if dry_run:
        print(f"PLAN space --order: move to the tail, in order: {', '.join(to_move)}")
        return
    for name in to_move:
        w = next(x for x in rest if x.get("label", "").lower() == name.lower())
        _move_workspace_to_tail(w)


def _move_workspace_to_tail(w):
    """Relocate every live pane in workspace `w`, tab groups intact, into a brand new workspace -
    which lands at the tail because `--new-workspace` always appends. `_space_one` does the same
    move for a named mate's OWNED panes; this does it for an entire existing herdr workspace,
    whatever lives in it."""
    ws_id, label = w["workspace_id"], w.get("label", w["workspace_id"])
    panes_here = [p for p in L.panes() if p["pane_id"].split(":")[0] == ws_id]
    if not panes_here:
        print(f"  SKIP {label}: no live panes")
        return
    groups = _group_by_tab(panes_here)
    new_ws_id, new_ids = _move_tab_groups(groups, label, None)
    if not new_ws_id:
        print(f"  {label}: nothing moved")
        return
    failed = len(panes_here) - len(new_ids)
    if failed:
        print(f"  {label}: {failed} pane(s) failed to move - owner records left on {ws_id} for "
              f"whatever is still there")
    _retarget_workspace_records(ws_id, new_ws_id)
    print(f"  {label}: now in workspace {new_ws_id}")


def _retarget_workspace_records(old_ws_id, new_ws_id):
    """Every owners.json/sidecar row recorded against `old_ws_id` now points at `new_ws_id` -
    but ONLY for a session whose live pane actually landed there. A partial move (one tab group's
    `pane move` failed, so the workspace ends up split across old and new) must not blanket-retarget
    a record that is still, correctly, on the old id (Codex review, 2026-09-24)."""
    by_sid = L.panes_by_sid()
    for sid, row in L.owners_load().items():
        if row.get("workspace") != old_ws_id:
            continue
        p = by_sid.get(sid)
        if p and p["pane_id"].split(":")[0] == new_ws_id:
            L.set_owner(sid, row.get("name", ""), row.get("owner", ""), row.get("domain", ""),
                        workspace=new_ws_id)
    for pane_id, row in L.sidecar_load().items():
        if row.get("workspace") == old_ws_id and pane_id.split(":")[0] == new_ws_id:
            L.sidecar_put(pane_id, workspace=new_ws_id)


def _ensure_slack_space(order, dry_run):
    """A `Slack` entry in `space_order` with no live workspace yet gets created fresh, with one
    pane running the Slack viewer - via `pane run`, never `send-text` (herdr injects escape bytes
    into a typed shell command, traps.md). Skipped with a message when the viewer script isn't
    merged yet (herdr-slack-view owns it). -> True only when a workspace was actually created (the
    caller re-reads `workspace list` before ordering, so the new space competes for its position
    like any other named one instead of always landing at the tail)."""
    if not any(n.lower() == "slack" for n in order):
        return False
    if L.find_workspace("Slack"):
        return False
    if not os.path.exists(JOBS_VIEW):
        print(f"SKIP Slack: {JOBS_VIEW} not merged yet")
        return False
    if dry_run:
        print("PLAN Slack: create a new space running the Slack viewer")
        return False
    ws_id, root_pane = L.ensure_workspace("Slack", os.path.dirname(JOBS_VIEW))
    if not root_pane:
        print(f"Slack: workspace {ws_id} already existed with no root pane to run the viewer in")
        return False
    # `pane run` returns EMPTY stdout/stderr on success (measured live 2026-09-24) - `L.herdr`'s
    # soft=True then reports that as None, same as a crashed subprocess call. A real failure comes
    # back as a parsed `{"error": {...}}` dict instead (also measured live, against a bad pane id),
    # so `None` here means success and a truthy "error" key means failure - the opposite of every
    # other herdr call in this file, which all return a real JSON envelope on success.
    r = L.herdr("pane", "run", root_pane, "py", JOBS_VIEW, soft=True)
    if isinstance(r, dict) and r.get("error"):
        print(f"Slack: workspace {ws_id} created but `pane run` failed to launch the viewer in "
              f"{root_pane}: {r['error']} - re-run to retry")
        return True
    print(f"Slack: created workspace {ws_id} running the viewer in {root_pane}")
    return True


def agent_pane_for(name):
    """A non-claude pane by its sidecar label, its herdr pane label, or its pane id.

    `resolve()` cannot find one: it goes through the session registry, and Codex writes no entry
    there.

    The sidecar wins over herdr's own pane label. A stale pane can carry the same label - a resume
    leaves the old name on screen - and iteration order then decided which one `kill` closed; on
    2026-09-06 it closed the stale one and left the live pane running.
    """
    side = L.sidecar_load()
    live = L.other_panes()          # not agent_panes: herdr never flags a pi pane as an agent,
                                    # so `kill pi-e2e` could not find one (measured 2026-09-14)
    for pane_id, e in side.items():
        if name in (pane_id, e.get("label")) and pane_id in live:
            return pane_id, live[pane_id]
    # An ACP pane is a claude session to herdr (agent "claude"), so `other_panes` never lists it;
    # its sidecar row is what says it is ACP.
    for pane_id, e in side.items():
        if e.get("kind") == "acp" and name in (pane_id, e.get("label")):
            p = next((p for p in L.panes() if p.get("pane_id") == pane_id), None)
            if p:
                return pane_id, p
    for pane_id, p in live.items():
        if name in (pane_id, p.get("label")):
            return pane_id, p
    return None, None


def _owner_gate(name, owner, force, quiet=False):
    """Refuse to close a pane another live session assigned work to. Returns True to go ahead.

    An owner that is gone (killed or crashed) cannot be asked, so its panes stop being
    protected - otherwise a dead lead would leave orphans nobody may close.
    """
    caller = L.caller_name()
    if not owner or owner == caller or not L.owner_is_live(owner):
        return True
    if not force:
        print(f"refusing to kill {name}: owned by {owner}; ask it, or pass --force")
        return False
    if not quiet:
        print(f"FORCED kill of {name}, owned by {owner}, by {caller}")
    _board_done(f"{caller} force-killed {name}, which {owner} owned")
    return True


def _acp_forget(pane_id):
    """After an ACP pane's tree is killed: the state file says stopped, and the child claude's
    owner row and registry file (it cannot clean up after a taskkill) go with it."""
    key = L.acp_key_of(pane_id)
    sid = L.acp_state_read(key).get("sessionId")
    if sid:
        L.drop_owner(sid)
        f = (L._registry().get(sid) or {}).get("_file")
        if f and os.path.exists(f):
            try:
                os.remove(f)
            except OSError:
                pass
    L.acp_state_put(key, state="stopped", word="")


def cmd_kill(name, quiet=False, force=False, via="kill"):
    _watcher_guard("kill", [name])
    pane_id, p = agent_pane_for(name)
    if p is not None:
        if not _owner_gate(name, L.sidecar_load().get(pane_id, {}).get("owner", ""), force, quiet):
            sys.exit(1)
        # Closing the pane leaves the codex process running and holding its thread's writer lock,
        # which then blocks a later `codex resume` on that thread. Killing is the one case where
        # nobody wants the thread back, so take the process with it.
        pids = L.sidecar_load().get(pane_id, {}).get("pids") or []
        import orch_watchdog; orch_watchdog.mark_killed(pane_id)
        L.herdr("pane", "close", pane_id)
        for pid in pids:
            L.kill_tree(pid)
        if L.sidecar_load().get(pane_id, {}).get("kind") == "acp":
            _acp_forget(pane_id)
        L.sidecar_drop(pane_id)
        if via == "kill":
            SP.auto_log("kill", f"closed {name}")
        if not quiet:
            print(f"killed {name}: closed herdr pane {pane_id} (kind={p.get('agent')})"
                  + (f", stopped pid(s) {', '.join(map(str, pids))}" if pids else ""))
        L.ledger.write("task.cleaned_up", name, via=via)
        return None
    m = L.resolve(name)
    if not m:
        sys.exit(f"no live session named {name} (name, session-id prefix, or pane id)")
    if not _owner_gate(m["name"], m.get("owner", ""), force, quiet):
        sys.exit(1)
    sid, pid = m["sessionId"], m.get("pid")
    L.drop_owner(sid)
    import orch_watchdog; orch_watchdog.mark_killed(sid)
    p = L.panes_by_sid().get(sid)
    if p:
        L.herdr("pane", "close", p["pane_id"])
        how = f"closed herdr pane {p['pane_id']}"
    elif pid:
        L.kill_pid(pid)
        how = f"killed pid {pid} (no herdr pane found)"
    else:
        sys.exit(f"{name} has no pane and no pid; nothing to close")
    if via == "kill":
        SP.auto_log("kill", f"closed {name}")
    time.sleep(2)
    if pid:
        for f in [os.path.join(L.SESS, f"{pid}.json"),
                  *glob.glob(os.path.join(L.SESS, f"{pid}.*.key"))]:
            if os.path.exists(f):
                try:
                    os.remove(f)
                except OSError:
                    pass
    if not quiet:
        print(f"killed {m['name']}: {how}; log kept at {L.log_path(sid)}")
    L.ledger.write("task.cleaned_up", m["name"], via=via)
    return m


# ---------------------------------------------------------------- the Chrome lock

AGENT_CHROME = r"C:\Users\YOURNAME\Vault\routines\agent-chrome\agent_chrome.py"


def cmd_chrome(action=None, name=None):
    """take/free/who are ADVISORY notes about RYAN'S browser (:9333) since 2026-10-01 - no hook
    refuses on them, and several agents may work in it at once, each in its own tab. Since
    2026-09-04 :9333 is also the exception path: agents default to their own headless Chrome on
    :9334 (`chrome agent`), which needs no lock."""
    if action == "agent":
        # Starts it if it is down, then prints the url. Idempotent either way.
        import urllib.request
        cdp = os.environ.get("AGENT_CHROME_CDP", "http://127.0.0.1:9334")
        try:
            with urllib.request.urlopen(cdp.rstrip("/") + "/json/version", timeout=3):
                pass
        except Exception:
            subprocess.run(["py", AGENT_CHROME, "start"], capture_output=True, text=True)
        print(cdp)
        return
    if action in ("open", "close"):
        import chrome_open
        chrome_open.cli(sys.argv[2:])
        return
    if action in (None, "who"):
        holder, since = L.chrome_holder()
        print(f"{holder} (since {since})" if holder else "free")
        return
    if action == "take":
        if not name:
            sys.exit("usage: orch.py chrome take <your-label>")
        ok, msg = L.chrome_take(name)
        print(msg)
        sys.exit(0 if ok else 1)
    if action == "free":
        if not name:
            sys.exit("usage: orch.py chrome free <your-label>|force")
        ok, msg = L.chrome_free(name)
        print(msg)
        sys.exit(0 if ok else 1)
    sys.exit("usage: orch.py chrome agent | open <url> | close <id> | take <name> | free [name|force] | who")


# ---------------------------------------------------------------- home

# ---------------------------------------------------------------- resume

MONITOR_TPL = 'Monitor(command=\'py "{path}"{args}\', persistent=true)'
# The three watchers run under reloader.py (a supervisor that relays the watcher's stdout and
# restarts it - never itself - when its code changes on disk), not directly, so the Monitor
# command wraps reloader.py around the real script. `--also` (reloader.py's own flag) has to come
# BEFORE the watcher's path; the watcher's own args come after it.
RELOADER_TPL = 'Monitor(command=\'py "{reloader}"{also} "{path}"{args}\', persistent=true)'


def cmd_resume():
    """Everything that has to be re-armed after the orchestrator's own process restarts.

    It restarted four times on 2026-09-03 (two Claude auto-updates, two herdr restarts) and each
    time the Monitors, the board read, and the mid-task builders were silently on their own. This
    prints the exact lines to run and the exact sessions that need a nudge - it changes nothing by
    itself, so it is safe to run whenever you are unsure."""
    sys.path.insert(0, SCRIPTS)
    import board as B

    reloader_py = os.path.join(SCRIPTS, "reloader.py")
    print("== 1. Monitors to start (paste each into a Monitor call) ==")
    print("  " + RELOADER_TPL.format(reloader=reloader_py,
                                      path=os.path.join(SCRIPTS, "watch_sessions.py"),
                                      also="", args=""))
    print("  " + RELOADER_TPL.format(reloader=reloader_py,
                                      path=os.path.join(SCRIPTS, "board_watch.py"),
                                      also="", args=""))
    print("  " + RELOADER_TPL.format(reloader=reloader_py,
                                      path=os.path.join(SCRIPTS, "podium_watch.py"),
                                      also=f' --also "{os.path.join(SCRIPTS, "board_watch.py")}"',
                                      args=""))
    print("  (each of these three runs under reloader.py now, which restarts the watcher - never "
          "itself - the moment its code changes on disk; a stale import no longer needs a restart "
          "by hand, only a Monitor that has actually died does)")

    # A pi mate's crew watcher is NOT a Monitor - pi has no such tool, so it is a detached process
    # `spawn` starts and nothing re-arms on a restart of this seat. It survives THIS session
    # dying, but not a reboot or a `taskkill`, so `resume` has to print the command. The
    # heartbeat is the only way to tell a live one from a dead one (same lesson as 2026-09-09).
    pi_mates = [(pane, e) for pane, e in L.live_sidecar_rows() if e.get("domain")]
    if pi_mates:
        print("\n== 1b. pi domain mates: crew watchers (run in a shell, not a Monitor) ==")
        for pane, e in sorted(pi_mates, key=lambda r: r[1].get("label", "")):
            mate = e.get("label") or pane
            alive = mate_watch_running(mate)
            print(f"  {'OK  ' if alive else 'DEAD'} {mate:14} {pane:7} "
                  + ("heartbeat fresh" if alive
                     else "no heartbeat  <-- re-arm it:\n       "
                          + " ".join(f'"{x}"' if " " in x else x for x in mate_watch_cmd(mate))))

    by_sid = L.panes_by_sid()
    me = L.orchestrator_sid()
    live, dead, idle_mid, prompted = [], [], [], []
    for sid, m in L.sessions().items():
        if sid == me:
            continue
        p = by_sid.get(sid, {})
        ctx, model = L.last_usage(sid)
        row = (m["name"], p.get("pane_id", "-"), m["status"], ctx, model or "-")
        # DEAD is a claim this tool can only back up when herdr has no pane for the session at all -
        # ctx=0k/blank model alone used to mean DEAD too, but that is what a session in another cwd
        # looks like when its log can't be found, and a busy, healthy pane got called DEAD for it
        # (2026-09-04, ORC-022). log_path() now falls back to a glob search, so this should be rare;
        # treat it as live-but-unreadable instead of dead when a pane genuinely exists.
        if not p.get("pane_id"):
            dead.append(row)
            continue
        live.append(row)
        kind, said = L.classify(sid)
        if m["status"] in ("idle", "waiting") and kind in ("QUESTION", "BLOCKED", "OFFER", "ERROR"):
            idle_mid.append((m["name"], kind, said[:140]))
        if p.get("pane_id"):
            txt = L.pane_read(p["pane_id"], 30)
            if L.pending_prompt(txt) or L.pane_mode(txt) in ("auto", "teach-dialog"):
                prompted.append((m["name"], p["pane_id"], L.pane_mode(txt) or "prompt"))

    if prompted:
        panes = " ".join(p for _, p, _ in prompted)
        print("  " + MONITOR_TPL.format(path=os.path.join(SCRIPTS, "auto_accept.py"),
                                        args=" " + panes))

    print(f"\n== 2. Board == {B.STATE}")
    try:
        s = B.load()
        openq = [q for q in s["questions"] if not q.get("answered")]
        print(f"  {len(openq)} open question(s), {len(s['show'])} show block(s), "
              f"{len(s['sessions'])} session row(s), {len(s['done'])} done line(s)")
        for q in openq:
            print(f"    {q['id']}  {q.get('title', '')}")
    except Exception as e:
        print(f"  could not read the board: {type(e).__name__}: {e}")

    print(f"\n== 3. Sessions ==\n  {len(live)} alive, {len(dead)} dead")
    for n, pane, st, ctx, model in sorted(live):
        print(f"  OK    {n:12} {pane:7} {st:8} ctx={ctx:4}k {model}")
    for n, pane, st, ctx, model in sorted(dead):
        print(f"  DEAD  {n:12} {pane:7} {st:8} ctx={ctx:4}k {model}   <-- kill and respawn")

    if prompted:
        print("\n== 4. Panes needing a keypress ==")
        for n, pane, why in prompted:
            print(f"  {n:12} {pane:7} {why}"
                  + ("   <-- send Escape, then re-check the mode" if why == "teach-dialog" else ""))

    if idle_mid:
        print("\n== 5. Idle mid-task - nudge with 'continue where you left off' ==")
        for n, kind, said in idle_mid:
            print(f"  {n:12} {kind:8} {said}")

    # Codex panes die with herdr and have no registry entry to come back through, so unlike the
    # claude rows above this section does the work instead of printing a line to paste.
    orphans = L.sidecar_orphans("codex")
    if orphans:
        print(f"\n== 6. Codex panes to reopen == {len(orphans)}")
        for pane, e in orphans:
            codex_resume_pane(pane, e)

    if not dead and not prompted and not idle_mid and not orphans:
        print("\nNothing needs a nudge. Start the two Monitors and carry on.")


# ---------------------------------------------------------------- doctor

def _ok(label, good, detail=""):
    print(f"  {'OK  ' if good else 'BAD '} {label:26} {detail}")
    return good


def _json_get(path, *keys):
    try:
        d = json.load(open(path, encoding="utf-8"))
    except (OSError, ValueError):
        return None
    for k in keys:
        if not isinstance(d, dict):
            return None
        d = d.get(k)
    return d


def cmd_doctor():
    """One line per moving part. Everything here has failed at least once on a live day."""
    print("== herdr ==")
    t = L.herdr("tab", "list", soft=True)
    _ok("api socket", t is not None,
        f"{len(t['result']['tabs'])} tabs" if t else "herdr tab list returned nothing - restart herdr")
    if t:
        labels = [x.get("label", "") for x in t["result"]["tabs"]]
        found_tab = any(x.lower() == L.ORCHESTRATOR_TAB for x in labels)
        _ok("orchestrator tab", found_tab,
            f"found tab '{L.ORCHESTRATOR_TAB}'" if found_tab else
            f"not found - run: herdr tab create --label {L.ORCHESTRATOR_TAB}")

    print("== messaging ==")
    if L.WINDOWS:
        try:
            pipes = [p for p in os.listdir("\\\\.\\pipe\\") if "cc-msg" in p]
            _ok("peer message pipes", bool(pipes), f"{len(pipes)} open")
        except OSError as e:
            _ok("peer message pipes", False, str(e))
    else:
        socks = glob.glob(os.path.join(L.CLAUDE_HOME, "**", "cc-msg-*"), recursive=True)
        _ok("peer message sockets", bool(socks), f"{len(socks)} open")

    dupes = [m["sessionId"] for m in L.sessions().values()
             if m.get("name", "").lower() == L.ORCHESTRATOR_TAB]
    _ok("orchestrator name unique", len(dupes) <= 1,
        "exactly one live session named 'orchestrator'" if len(dupes) <= 1 else
        f"{len(dupes)} live sessions named 'orchestrator' - two seats share the name; "
        f"SendMessage to the bare name is ambiguous until the old seat is killed/renamed")

    print("== auth and network ==")
    try:
        r = subprocess.run(["claude", "auth", "status"], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=45)
        d = json.loads(r.stdout or "{}")
        logged_in = bool(d.get("loggedIn"))
        # deliberately not printing the account address
        _ok("claude auth", logged_in,
            f"found: {d.get('authMethod', '?')} / {d.get('subscriptionType', '?')}" if logged_in
            else "not found - run: claude login")
    except Exception as e:
        _ok("claude auth", False, f"not found ({type(e).__name__}: {e}) - run: claude login")

    ping = ["ping", "-n", "1", "-w", "3000"] if L.WINDOWS else ["ping", "-c", "1", "-W", "3"]
    try:
        r = subprocess.run([*ping, "api.anthropic.com"], capture_output=True, text=True, timeout=20)
        _ok("api.anthropic.com", r.returncode == 0, "reachable" if r.returncode == 0 else "no reply")
    except Exception as e:
        _ok("api.anthropic.com", False, str(e))

    print("== settings ==")
    # The updater rewrites both the user file and the PROJECT local file (the one under the
    # working directory's .claude/), so check that one, not ~/.claude/settings.local.json.
    local = os.path.join(L.DEFAULT_CWD, ".claude", "settings.local.json")
    for path, label in ((L.SETTINGS, "settings.json"), (local, "project settings.local")):
        if not os.path.exists(path):
            print(f"  --   {label:26} not present")
            continue
        mode = _json_get(path, "permissions", "defaultMode")
        if mode is None:
            # Absent is fine: the file simply does not override the mode.
            print(f"  --   {label:26} defaultMode not set (inherits)")
            continue
        _ok(label, mode == "bypassPermissions",
            f"defaultMode={mode}"
            + ("   <-- the updater flips this to auto; panes then stall on the classifier"
               if mode != "bypassPermissions" else ""))
    _ok("DISABLE_AUTOUPDATER", str(_json_get(L.SETTINGS, "env", "DISABLE_AUTOUPDATER")) == "1",
        "a mid-work update reloads every pane")

    print("== usage ==")
    usage_dir = os.path.join(L.CLAUDE_HOME, "accounts", "usage")
    files = sorted(glob.glob(os.path.join(usage_dir, "*.json")))
    if not files:
        print("  --   usage feed                 none written yet")
    for f in files:
        d = _json_get(f) or {}
        five = (d.get("five_hour") or {}).get("used_percentage")
        week = (d.get("seven_day") or {}).get("used_percentage")
        # used%, not the status line's remaining% - reading one for the other burned a whole day
        _ok(os.path.basename(f)[:-5], (five or 0) < 90,
            f"5h used {five}%, 7d used {round(week, 1) if week else week}%")

    print("== browser lock ==")
    holder, since = L.chrome_holder()
    print(f"  --   chrome                     {holder + ' since ' + str(since) if holder else 'free'}")
    cdp = L.cfg("cdp_url", "")
    if cdp:
        # An in-place Chrome self-update kills every renderer in the running window (2026-09-03,
        # 151 -> 153); the port then answers nothing until that profile is relaunched.
        try:
            import urllib.request
            with urllib.request.urlopen(cdp.rstrip("/") + "/json/version", timeout=5) as r:
                ver = json.loads(r.read().decode("utf-8", "replace")).get("Browser", "?")
            _ok("chrome cdp", True, f"{cdp} {ver}")
        except Exception as e:
            # Ryan closing his own browser is not a fault. Only a running Chrome that will not
            # answer is one (the self-update case above). The health routine nudged the seat twice
            # on 2026-09-09 for a browser he had simply shut before leaving for a test.
            # tasklist alone is useless here: the agent Chrome (:9334) is also chrome.exe. Look
            # for a chrome process carrying THIS port's flag.
            port = cdp.rsplit(":", 1)[-1].strip("/")
            try:
                ps = ("(Get-CimInstance Win32_Process -Filter \"name='chrome.exe'\" | "
                      f"Where-Object {{ $_.CommandLine -match 'remote-debugging-port={port}' }} | "
                      "Measure-Object).Count")
                n = subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                                   capture_output=True, text=True, timeout=20).stdout.strip()
                chrome_running = int(n or "0") > 0
            except Exception:
                chrome_running = True
            if not chrome_running:
                _ok("chrome cdp", True, f"{cdp} closed (no chrome.exe on that port; Ryan's browser, leave it)")
            else:
                _ok("chrome cdp", False,
                    f"{cdp} not answering ({type(e).__name__}) - relaunch that profile on the same port")

    # The agent-only browser. This is the one agents are supposed to be using, so a red line here
    # matters more than a red line on Ryan's - it means the next browser task lands in his window.
    agent_cdp = os.environ.get("AGENT_CHROME_CDP", "http://127.0.0.1:9334")
    try:
        import urllib.request
        with urllib.request.urlopen(agent_cdp.rstrip("/") + "/json/version", timeout=5) as r:
            ver = json.loads(r.read().decode("utf-8", "replace")).get("Browser", "?")
        _ok("agent chrome", True, f"{agent_cdp} {ver}")
    except Exception as e:
        _ok("agent chrome", False,
            f"{agent_cdp} down ({type(e).__name__}) - `py {AGENT_CHROME} start`")

    print("== secret vault ==")
    good, detail = _vault_health()
    _ok("secret-vault", good, detail)

    print("== tool versions ==")
    import shutil
    # Floors from the AXI master spec 2.3 (G10).
    for name, args, floor in (("gh-axi", ["--version"], "0.1.35"),
                              ("quota-axi", ["--version"], "0.1.59"),
                              ("lavish-axi", ["--version"], "0.1.84"),
                              ("chrome-devtools-axi", ["--version"], "0.1.39"),
                              ("no-mistakes", ["--version"], "1.84.0"),
                              ("treehouse", ["--version"], "3.1.2")):
        exe = shutil.which(name)
        if not exe:
            if floor is None:
                print(f"  --   {name:26} missing (expected)")
            else:
                _ok(name, False, "command not found - install it")
            continue
        try:
            r = subprocess.run([exe] + args, capture_output=True, text=True, timeout=10)
        except (OSError, subprocess.TimeoutExpired) as e:
            _ok(name, False, f"version check failed ({type(e).__name__})")
            continue
        out = (r.stdout + r.stderr).strip()
        m = re.search(r"(\d+)\.(\d+)\.(\d+)", out)
        if not m:
            _ok(name, False, f"no version in output: {out[:50]}")
            continue
        found = tuple(int(x) for x in m.groups())
        want = tuple(int(x) for x in floor.split(".")) if floor else (0, 0, 0)
        _ok(name, found >= want, f"{'.'.join(map(str, found))} {'>=' if found >= want else '<'} {floor}"
            + ("" if found >= want else " - upgrade needed"))


def _vault_health():
    """Static check of the secret-vault plugin: enabled, all four tools registered, store readable.
    Never reads a secret value. The live proof is a fresh `claude -p` calling the list tool."""
    if not _json_get(L.SETTINGS, "enabledPlugins", "secret-vault@ryan-secret-vault"):
        return False, "plugin not enabled in settings.json"
    src = os.path.join(L.CLAUDE_HOME, "plugins", "secret-vault", "hooks", "hooks.js")
    try:
        text = open(src, encoding="utf-8").read()
    except OSError:
        return False, f"{src} missing"
    missing = [t for t in ("list", "store", "get", "forget") if f"name: '{t}'" not in text]
    if missing:
        return False, f"tools not registered: {', '.join(missing)}"
    stores = [p for p in glob.glob(os.path.join(L.CLAUDE_HOME, "plugins", "store",
                                                "secret-vault_ryan-secret-vault-*.json"))
              if ".bak" not in p]
    if not stores:
        return True, "4 tools registered; no store yet (nothing vaulted)"
    secrets = _json_get(stores[0], "secrets")
    if not isinstance(secrets, dict):
        return False, f"{os.path.basename(stores[0])} unreadable or has no 'secrets' object"
    return True, f"4 tools registered; {len(secrets)} secret(s) vaulted"


def _board_done(line, group=None):
    """One Done line on the board. Never fatal - a board that is down must not fail the caller."""
    cmd = [sys.executable, os.path.join(SCRIPTS, "board.py"), "done", line]
    if group:
        cmd += ["--group", group]
    try:
        subprocess.run(cmd, capture_output=True, timeout=120)
    except Exception as e:
        print(f"(board done line not written: {type(e).__name__})")


# ---------------------------------------------------------------- task intake

CLAUDE_REPORT_TO = (
    "{orchestrator} - `SendMessage` it your final report, and any question you hit.\n"
    "It speaks for the human on small reversible decisions; act on its answers as theirs.")

# Codex has no SendMessage and no peer messaging at all, so its report is a file the watcher
# looks for, and the bare word DONE on the last line is how it says it has stopped.
CODEX_REPORT_TO = (
    "You cannot send messages to another session. Write your final report to `{report}` - what you\n"
    "did, the exact commands you ran, and the proof - then print `DONE` on its own line as the last\n"
    "line of your reply. The orchestrator ({orchestrator}) watches for that file and reads it.\n"
    "A question you cannot answer goes in the same file; print `DONE` anyway so it gets read.")

# A pi mate has no SendMessage address (see `mate_report_line`), so its crew's task file cannot
# carry the SendMessage contract either. The task file is what a pane re-reads when it loses its
# place, so it has to say the same thing the spawn footer says. (GAP 3, 2026-09-15.)
MATE_REPORT_TO = (
    "{mate} - it runs on the pi harness and has NO SendMessage address, so `SendMessage` reaches\n"
    "nobody at all. Report to it - every question and your final report - by running:\n"
    "    py \"{orch}\" prompt {mate} \"<your message>\"\n"
    "Keep every message to {cap} words or fewer: state changes, decisions needed, evidence in\n"
    "files rather than in the message.\n"
    "It speaks for the human on small reversible decisions; act on its answers as theirs.")

MATE_REPORT_FILE_TOO = (
    "\nWrite your final report to `{report}` as well - what you did, the exact commands you ran,\n"
    "and the proof - then print `DONE` on its own line as the last line of your reply.")

# A live pi/codex owner that is NOT a formal domain mate (no `MATE_REPORT_TO`/`mate_report_line`
# for it) still has no SendMessage address - only a mate's crew got this fix until the 2026-09-23
# review (item 7) pointed out ordinary crew under an ordinary pi/codex owner had the exact same
# gap. Kept separate from `MATE_REPORT_TO` rather than parameterizing it, so the mate wording (and
# its existing tests) are untouched.
ORDINARY_HARNESS_OWNER_REPORT_TO = (
    "{owner} - it runs on the {kind} harness and has NO SendMessage address, so `SendMessage`\n"
    "reaches nobody at all. Report to it - every question and your final report - by running:\n"
    "    py \"{orch}\" prompt {owner} \"<your message>\"\n"
    "It speaks for the human on small reversible decisions; act on its answers as theirs.")

CLAUDE_SHELL_RULE = ("Never `cd X && ...` in the Bash tool; use absolute paths. "
                     "Never a heredoc in Bash; use the Write tool.")
CODEX_SHELL_RULE = "Never `cd X && ...` in a shell call; use absolute paths."


def _one_line(text, cap=160):
    """First sentence of `text` on one line, cut at `cap` chars: the task file's header line and
    the first prompt's Goal / Done-when."""
    t = " ".join((text or "").split())
    m = re.match(r"((?:.+?(?<!\be\.g)(?<!\bi\.e)(?<!\bvs)(?<!\betc)(?<!\bapprox)[.!?]))(\s|$)", t)
    s = m.group(1) if m else t
    return s if len(s) <= cap else s[:cap - 3].rstrip() + "..."


def _owner_line(report_to):
    """Who the pane reports to and how, in a few words, for the task file's header."""
    first = report_to.splitlines()[0] if report_to else ""
    if first.startswith("You cannot"):
        return "a report file (see Report to below)"
    name = first.split(" - ", 1)[0].strip()
    if name.startswith("seat ("):
        name = "the orchestrator seat"
    if "NO SendMessage" in report_to:
        return f"{name} via `orch.py prompt` (no SendMessage address)"
    return f"{name} by SendMessage"


def task_report_to(kind, owner, report, orchestrator):
    """The "Report to" block of a task file: who this pane answers to, and how.

    Owner first, harness second. A crew pane's channel is decided by the seat it reports to, not
    by the model it runs - a claude pane under a pi mate has SendMessage and still must not use
    it, because there is no address on the other end.
    """
    if L.pi_mate(owner):
        block = MATE_REPORT_TO.format(mate=owner, orch=os.path.abspath(__file__),
                                      cap=MATE_WORD_CAP)
        if kind != "claude":
            block += MATE_REPORT_FILE_TOO.format(report=report)
        return block
    # The report target is the OWNER - the session that actually assigned this pane its work -
    # falling back to `orchestrator` (the seat) only when that owner is missing or no longer live.
    # A task file spawned BY sc-mate still said "vault - SendMessage it your final report",
    # so crew reports skipped their mate and landed on the seat (2026-09-23).
    owner_live = bool(owner) and L.owner_is_live(owner)
    if kind == "claude" and owner_live:
        # A claude crew pane CAN use SendMessage, but a live pi/codex owner that is not a formal
        # mate has no address for it to reach - the same GAP 3 class of bug, just without a mate
        # record to catch it (2026-09-23 review, item 7).
        owner_kind = L.owner_harness(owner)
        if owner_kind:
            return ORDINARY_HARNESS_OWNER_REPORT_TO.format(owner=owner, kind=owner_kind,
                                                            orch=os.path.abspath(__file__))
    target = owner if owner_live else seat_ref()
    tpl = CLAUDE_REPORT_TO if kind == "claude" else CODEX_REPORT_TO
    return tpl.format(orchestrator=target, report=report)


def cmd_task(label, goal, done, out=None, model="sonnet", group=None, cwd=None,
             kind="claude", report=None, owner=None, lead=False, allow_extra_usage=False,
             domain="", effort=None, why="", read_first=None, not_in_scope=None, expensive=None,
             mcp=None, acp=False):
    cwd = cwd or L.DEFAULT_CWD
    owner = owner or L.caller_name()
    os.makedirs(L.HANDOFFS, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y-%m-%d-%H%M")
    path = os.path.join(L.HANDOFFS, f"task-{label}-{stamp}.md")
    orchestrator = L.orchestrator_name()
    if kind == "claude":
        report, shell_rule = "", CLAUDE_SHELL_RULE
    else:
        report = report or os.path.join(L.HANDOFFS, f"report-{label}.md")
        shell_rule = CODEX_SHELL_RULE
        # The task file is what the pane re-reads when it loses its place, so it has to carry
        # the same pi-vs-claude rules as the spawn prompt (PI_NO_HOOKS_LINE).
        if kind == "pi":
            shell_rule = shell_rule + " " + PI_NO_HOOKS_LINE
    report_to = task_report_to(kind, owner, report, orchestrator)
    try:
        import project_mode as PMODE
        _m, _y, _n, _r = PMODE.resolve(cwd)
        ship_mode = (f"{_m} {'on' if _y else 'off'} ({_n or 'not in projects.json'})" if _r else "not inside a git repo")
    except (OSError, ValueError, KeyError) as e:
        ship_mode = f"unknown ({e})"
    goal_line, done_line = _one_line(goal), _one_line(done, 200)
    body = open(TASK_TEMPLATE, encoding="utf-8").read().format(
        label=label, goal=goal, done=done, cwd=cwd,
        goal_line=goal_line, done_line=done_line, owner_line=_owner_line(report_to),
        pane_rules=PANE_RULES, voice_mishears=VOICE_MISHEARS,
        out=out or (report if report else "(none - report by SendMessage)"),
        why=why.strip() if why else "(not given - ask the orchestrator if it matters)",
        read_first=_bullets(read_first or []),
        out_of_scope=_bullets(not_in_scope or []),
        shell_rule=shell_rule, ship_mode=ship_mode,
        report_to=report_to)
    open(path, "w", encoding="utf-8").write(body)
    print(f"task file: {path}")
    # One line, so the rail and the pane show the label, goal and done-when before anything else.
    first = (f"TASK {label} | Goal: {goal_line} | Done when: {done_line} | "
             f"Read {path} and do exactly what it says (why, not-to-do and report format are in it).")
    cmd_spawn(label, first,
              model=model, cwd=cwd, group=group, kind=kind, report=report, task=path, owner=owner,
              lead=lead, allow_extra_usage=allow_extra_usage, domain=domain, effort=effort,
              expensive=expensive, mcp=mcp, **({"acp": True} if acp else {}),
              **({"slim": True} if kind == "claude" else {}))
    return path


def _remote_call(fn, *args):
    """Run a remote.py call; a HostError is one clear line and exit 1, never a traceback."""
    try:
        return fn(*args)
    except RM.HostError as e:
        sys.exit(f"{'UNREACHABLE: ' if e.kind in RM.UNREACHABLE_KINDS else ''}{e.message}")


def cmd_task_remote(a):
    """`task <label> --host NAME --goal ... --done ...`: start a mate on another machine. The
    remote mate runs on that host's own Claude login, so --rule/--cwd/--worktree do not apply."""
    host = _opt(a, "--host")
    goal, done = _opt(a, "--goal", ""), _opt(a, "--done", "")
    if not (goal.strip() and done.strip()):
        _usage_fail("task --host needs --goal and --done", "Pass both, the host mate has no one to ask")
    print(_remote_call(RM.start_task, host, a[1], goal, done, _opt(a, "--model", "sonnet"),
                       _opt(a, "--owner") or L.caller_name(), _opt(a, "--why", ""),
                       _opt_multi(a, "--read"), _opt_multi(a, "--not")))


def cmd_pull(host):
    hosts = [host] if host else list(RM.load_hosts())
    if not hosts:
        sys.exit("no hosts: copy board/hosts.example.json to board/hosts.json")
    for h in hosts:
        new = _remote_call(RM.pull, h)
        print(f"{h}: {len(new)} new report(s) in {os.path.join(L.HANDOFFS, 'remote', h)}")
        for name, done_line in new:
            print(f"  {name}: {done_line or '(no Done: line)'}")


# ---------------------------------------------------------------- review gate

def _diff_check(diff=None, pr=None, cwd=None, repo=None):
    """Refuse to spawn a reviewer with nothing to review. Returns (ok, detail-or-reason).

    `--pr` needs `--repo owner/name`: on 2026-09-09 `review x --pr 1` run from the vault cwd
    reviewed vault-notes PR #1 (an old, merged screen-tracker change) instead of claude-config
    PR #1, and the reviewer never knew."""
    if not diff and not pr:
        return False, "no --diff <ref-range> or --pr <n> given"
    if pr and not repo:
        return False, "--pr needs --repo owner/name (gh resolves a bare number in whatever repo the cwd is)"
    cwd = cwd or L.DEFAULT_CWD
    try:
        run_kw = dict(cwd=cwd, capture_output=True, text=True, encoding="utf-8",
                      errors="replace", timeout=30)
        if pr:
            out = subprocess.run(["gh", "pr", "diff", str(pr), "--repo", repo], **run_kw)
            what = f"PR #{pr} in {repo}"
        else:
            out = subprocess.run(["git", "diff", diff], **run_kw)
            what = diff
    except (OSError, subprocess.TimeoutExpired) as e:
        return False, f"could not read the diff: {e}"
    if out is None:
        return False, f"could not read {what}: no output"
    if out.returncode != 0:
        return False, f"could not read {what}: {(out.stderr or '').strip()[:200]}"
    if not (out.stdout or "").strip():
        return False, f"{what} has no diff"
    return True, what


def cmd_review(name, diff=None, pr=None, model="sonnet", cwd=None, owner=None, repo=None,
               harness="claude", effort=None, expensive=None):
    """Spawn a reviewer pane on a fixed brief: read the diff, reproduce one thing end to end, run
    the tests, report SHIP or FIX-FIRST in under 150 words. Built 2026-09-09 because a lead's own
    ships (sc-improve-lead's gate tail-anchor and arrow re-derive bugs) were only ever checked by
    the lead that wrote them. Refuses when there is no diff to review - a reviewer with nothing to
    read is a rubber stamp, not a gate.

    `harness="pi"` puts a GPT reviewer on it (crew-dispatch.json's review rule, after Kun: "I like
    GPT the most as a reviewer ... really good at identifying edge cases"). A pi pane has no
    SendMessage, so that reviewer reports by file only and ends with DONE; the orchestrator relays
    it to the lead."""
    cwd = cwd or L.DEFAULT_CWD
    if harness not in ("claude", "pi"):
        sys.exit(f"review takes --harness claude or pi, not {harness!r}")
    # `name` is the lead being reviewed; it becomes the pane label `review-<name>` and the report
    # filename. An arg-order slip made it a FLAG on 2026-09-14 and produced a pane called
    # `review---diff` reviewing nothing identifiable. A name that starts with `-` is never a lead.
    if not name or name.startswith("-"):
        sys.exit(f"review refused: {name!r} is not a lead name - it looks like a flag, so the "
                 f"arguments are out of order. Usage: review <lead-name> --diff <range> "
                 f"[--pr <n> --repo owner/name] [--model sonnet] [--cwd DIR]")
    ok, detail = _diff_check(diff, pr, cwd, repo)
    if not ok:
        sys.exit(f"review refused for {name!r}: {detail}")
    owner = owner or L.caller_name()
    os.makedirs(L.HANDOFFS, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y-%m-%d-%H%M")
    # The stamp is minute-resolution, so two reviews of the same lead started in the same minute
    # were handed the SAME report path and the second silently overwrote the first. That happened
    # for real on 2026-09-15 when a step-4 and a step-5 review were spawned back to back. Take the
    # next free name instead: a lost review reads exactly like a review that was never run.
    out = os.path.join(L.HANDOFFS, f"review-{name}-{stamp}.md")
    n = 2
    while os.path.exists(out):
        out = os.path.join(L.HANDOFFS, f"review-{name}-{stamp}-{n}.md")
        n += 1
    orchestrator = L.orchestrator_name()
    read_cmd = f"gh pr diff {pr} --repo {repo}" if pr else f"git diff {diff}"
    label = f"review-{name}"
    # `name` is normally a live lead pane and gets the verdict directly, plus the seat. When it is
    # NOT a live session - a bare PR number, a merged branch, an already-closed lead - there is
    # nothing there to SendMessage, and sending to the literal string anyway let herdr's own fuzzy
    # name match hand the verdict to an unrelated live pane TWICE on 2026-09-23 (vault-a6, then
    # an offline ci-bot). Fall back to whoever ASKED for this review instead - its owner - and
    # only to the seat when that owner is gone too.
    name_live = L.owner_is_live(name)
    fallback = owner if L.owner_is_live(owner) else orchestrator
    # Who the reviewer answers to, not just what it runs. A review spawned BY a pi mate used to be
    # told to SendMessage that mate, which reaches nobody (GAP 3) - so the gate's verdict was
    # written to a file the mate was never told to read.
    lead_is_pi = name_live and bool(L.pi_mate(name))
    fallback_is_pi = (not name_live) and bool(L.pi_mate(fallback))

    def _prompt_hop(target):
        return f"py \"{os.path.abspath(__file__)}\" prompt {target} \"<SHIP or FIX-FIRST + the list>\""

    if harness == "claude":
        if name_live:
            deliver = (f"Write your report to `{out}`, then " +
                       (f"SendMessage it to {orchestrator}, and tell {name} by running: "
                        f"{_prompt_hop(name)}. " if lead_is_pi else
                        f"SendMessage it to both {name} and {orchestrator}. "))
        else:
            deliver = (f"Write your report to `{out}`, then " +
                       (f"tell {fallback} by running: {_prompt_hop(fallback)}. " if fallback_is_pi
                        else f"SendMessage it to {fallback} ({name!r} is not a live session). "))
    else:
        if name_live:
            deliver = (f"Write your report to `{out}` - that file is the whole deliverable; the "
                       f"orchestrator reads it and tells {name}. End with DONE on its own line. ")
            if lead_is_pi:
                deliver += f"Also tell {name} directly by running: {_prompt_hop(name)}. "
        else:
            deliver = (f"Write your report to `{out}` - that file is the whole deliverable; "
                       f"{fallback} reads it ({name!r} is not a live session). End with DONE on "
                       f"its own line. ")
            if fallback_is_pi:
                deliver += f"Also tell {fallback} directly by running: {_prompt_hop(fallback)}. "
    prompt = (
        f"You are reviewing {detail} in `{cwd}` for {name}, before it merges to main. Read the "
        f"diff yourself (`{read_cmd}`), run the tests, and then USE THE FEATURE THE WAY RYAN "
        f"WOULD - the real Slack message, the real hotkey, the real page in a browser at his "
        f"window size, the real command - and paste what you saw. Tests passing is not a SHIP: "
        f"on 2026-09-10 three reviews said SHIP on tests alone and all three failed live. Report "
        f"in under 150 words: SHIP, or FIX-FIRST with the exact list of what must change first. "
        + deliver +
        f"Review only - do not fix anything yourself. `{cwd}` is a SHARED checkout other panes are "
        f"editing right now: never `git stash`, `reset`, `checkout`, `switch` or `clean` there (a "
        f"reviewer's stash wiped another pane's edits on 2026-09-09). To test the committed state "
        f"cleanly, make your own worktree: `git -C \"{cwd}\" worktree add <scratchpad>\\review-{name} "
        f"<head sha or branch>`, work there, and `git worktree remove` it when done. Never use "
        f"sparse-checkout in that worktree (hook-blocked: on 2026-09-10 the cone applied to the "
        f"main vault checkout and deleted 2,626 files); for one subtree of a big repo use "
        f"`git archive <sha> -- <path> | tar -x -C <dir>` instead."
    )
    pane, sid = cmd_spawn(label, prompt, model=model, cwd=cwd, kind=harness, owner=owner,
                          report=out if harness == "pi" else "", effort=effort,
                          expensive=expensive)
    print(f"review file: {out}")
    return pane, sid, out


# ---------------------------------------------------------------- reap

def reap_candidates(hours=6):
    """Idle + quiet for N hours + last words classify DONE. Never the orchestrator, never a mate."""
    cutoff = time.time() - hours * 3600
    skip = {L.orchestrator_sid()}
    by_sid = L.panes_by_sid()
    out = []
    caller = L.caller_name()
    for sid, m in L.sessions().items():
        if sid in skip or m["status"] != "idle" or m["name"] in PROTECTED_NAMES:
            continue
        # A domain mate owns a subject, not a task, so finishing one piece of work is not a reason
        # to close it - an empty queue is the healthy state for a mate, and it keeps the subject's
        # context so the next ask in that domain does not start from zero. Ryan 2026-09-14.
        if m.get("domain", ""):
            continue
        # An owned pane is its lead's to close, even when it looks finished from here - the lead
        # may be about to give it the next piece of work.
        owner = m.get("owner", "")
        if owner and owner != caller and L.owner_is_live(owner):
            continue
        if L.last_assistant_ts(sid) > cutoff:
            continue
        kind, said = L.classify(sid)
        # No readable last words means we cannot prove it finished clean. Leave it alone.
        if kind != "DONE" or not said.strip():
            continue
        p = by_sid.get(sid, {})
        out.append((m["name"], p.get("terminal_title_stripped", "")[:40], said))
    return out


def cmd_reap(hours=6, dry=False):
    cands = reap_candidates(hours)
    if not cands:
        print(f"reap: nothing idle+DONE older than {hours}h")
        return
    for name, title, said in cands:
        if dry:
            print(f"WOULD CLOSE {name:12} {title:40} | {said[:120]}")
        else:
            cmd_kill(name, quiet=True, force=True, via="reap")   # reap_candidates already skipped owned panes
            print(f"CLOSED {name:12} {title:40} | {said[:120]}")
    print(f"reap: {len(cands)} session(s) {'listed' if dry else 'closed'}")


# ---------------------------------------------------------------- auto-close
#
# Ryan 2026-09-24: auto-close finished panes, built on reap. reap_candidates() already proves a
# pane is idle+DONE with readable last words and is neither the seat nor a domain mate; auto-close
# adds three more gates on top, all of which must hold before a pane is closed with no human in
# the loop at all:
#
#   1. it was spawned by orch.py - its owners.json row carries `spawned: true`, set only by
#      `cmd_spawn` (orchlib.set_owner). A bare row is NOT enough: `own` writes one for any live
#      session, hand-opened ones included, when it re-parents or promotes it, so mere presence
#      would have let a hand-opened pane become closable the moment anyone ran `orch.py own` on
#      it - reap_candidates' owner check only protects a pane whose owner is a live SOMEONE ELSE,
#      and says nothing about a pane with no owner row at all.
#   2. it is not a domain mate, the orchestrator seat, or one of the seat's own named exceptions
#      (`_is_protected_pane`) - a mate and the seat both ARE spawned by orch.py, so gate 1 alone
#      would trust them like any other crew pane. reap_candidates() already skips both upstream,
#      but auto-close repeats the check itself rather than depending solely on a filter that
#      exists to serve `reap`, not auto-close (the seat's own review, 2026-09-24).
#   3. no OPEN board question names it (`from`, or its name inside an unanswered title) - Ryan may
#      still owe it an answer.
#   4. its git worktree has no uncommitted changes and nothing unpushed - closing it would not lose
#      the pane's own work, or leave a branch nobody remembers exists.
#
# Off by default (`auto_close_enabled` in config.json, board.auto_close_enabled()); board_watch.py
# is the only caller that runs it live, at most every 30 minutes, and only once that flag is on.

AUTO_CLOSE_HOURS = 0.5
AUTO_CLOSE_LOG = os.path.join(L.BOARD_DIR, "auto-close.log")


def _worktree_status(cwd):
    """(clean, reason) for the git repo at `cwd` - no uncommitted changes AND nothing unpushed
    against its remote-tracking branch. Anything that cannot actually be checked - no cwd on
    record, not a repo, no upstream, a git failure - reads as NOT clean: auto-close refuses rather
    than guesses on a worktree it cannot verify."""
    if not cwd or not os.path.isdir(cwd):
        return False, "no cwd on record"

    def _git(*args):
        return subprocess.run(["git", "-C", cwd, *args], capture_output=True, text=True,
                              encoding="utf-8", errors="replace")

    r = _git("rev-parse", "--is-inside-work-tree")
    if r.returncode != 0 or r.stdout.strip() != "true":
        return False, "not a git worktree"
    # --untracked-files=normal overrides a local status.showUntrackedFiles=no (repo or global
    # config) that would otherwise make an untracked file invisible here - Codex review 2026-09-24
    # reproduced this reading a real untracked file as a clean tree.
    r = _git("status", "--porcelain", "--untracked-files=normal")
    if r.returncode != 0:
        return False, "git status failed"
    if r.stdout.strip():
        return False, "uncommitted changes"
    r = _git("rev-list", "--count", "@{u}..HEAD")
    if r.returncode != 0:
        return False, "no upstream tracking branch"
    ahead = r.stdout.strip()
    if ahead != "0":
        return False, f"{ahead} unpushed commit(s)"
    return True, "clean, pushed"


def _board_open_question_names():
    """({every live `from` pane name}, [every unanswered question's title, lowercased]) so a
    caller can check whether an open Bridge question names a given pane - either as its asker or
    by appearing in the title. None means the board could not be read at all, and a caller must
    treat that as "named" (block the close) rather than guess it is clear.

    Deferred import: board.py imports orchlib at its own top, so importing it back at module level
    here would be a cycle. The import
    and the load both sit inside the same guard - Codex review 2026-09-24: an exception ANYWHERE
    in reaching the board (a broken import, not just `B.load()`'s own `sys.exit` on bad JSON) must
    fail closed the same way, not escape and crash the caller."""
    try:
        import board as B
        s = B.load()
    except Exception:
        return None
    names, titles = set(), []
    for q in s.get("questions", []):
        if q.get("answered"):
            continue
        frm = (q.get("from") or "").strip()
        if frm:
            names.add(frm)
        titles.append((q.get("title") or "").lower())
    return names, titles


def _board_names_pane(name, open_names_titles):
    """Word-bounded, like board.py's own ACTION_ITEM_RE ("book doesn't fire on Facebook") - an
    unanchored substring match would block "gate" on a title that only contains "delegate"
    (code review 2026-09-24)."""
    if open_names_titles is None:
        return True
    names, titles = open_names_titles
    if name in names:
        return True
    pat = re.compile(r"\b" + re.escape(name.lower()) + r"\b")
    return any(pat.search(t) for t in titles)


# A domain mate IS spawned by orch.py, so gate 1 alone would trust it the same as any ordinary
# crew pane - reap_candidates() already skips a mate (non-empty `domain`) and the seat
# (`orchestrator_sid()`) upstream, but auto-close is irreversible and must not depend SOLELY on an
# upstream helper's filter for that: a future change to reap's own filtering (it exists to serve
# `reap`, not auto-close) could silently drop this protection with nobody noticing. The seat asked
# for this repeated explicitly, inside auto-close's own gate, plus a name-pattern backstop for the
# panes it named directly - family-slack (ci) and every vault-* pane - regardless of what
# their owners.json domain field says today (2026-09-24 review). The seat also asked for the four
# named mates (sc-mate, app-mate, school-mate, debt-mate) hardcoded here too, belt-and-suspenders on
# top of the domain/spawned checks, so a future re-spawn that forgets --domain fails safe instead of
# silently losing protection.
PROTECTED_NAMES = {"family-slack", "sc-mate", "app-mate", "school-mate", "debt-mate", "unstick-mate",
                   "seat-haiku", "seat-watcher"}
PROTECTED_NAME_PREFIXES = ("vault",)


def _is_protected_pane(name, sid, sessions):
    """True when `name`/`sid` is a domain mate, the orchestrator seat, or one of the seat's named
    exceptions - auto-close must never close any of these no matter what the other gates say."""
    if (sessions.get(sid) or {}).get("domain"):
        return True
    if sid and sid == L.orchestrator_sid():
        return True
    if name in PROTECTED_NAMES:
        return True
    return any(name.startswith(p) for p in PROTECTED_NAME_PREFIXES)


def auto_close_candidates(hours=AUTO_CLOSE_HOURS):
    """Every `reap_candidates(hours)` hit that also clears the extra auto-close gates (see module
    note above). -> [(name, title, said, reason)]; `reason` is why the worktree qualified, used in
    the dry-run/log line so a close is never just "trust me"."""
    owners = L.owners_load()
    sessions = L.sessions()
    sid_by_name = {m.get("name"): sid for sid, m in sessions.items()}
    cwd_by_name = {m.get("name"): m.get("cwd") for m in sessions.values()}
    open_named = _board_open_question_names()
    out = []
    for name, title, said in reap_candidates(hours):
        sid = sid_by_name.get(name)
        # `spawned` (not mere row presence) is the actual "came up through orch.py" signal -
        # `own` writes a row for ANY live session, hand-opened ones included, when it re-parents
        # or promotes one (code review + Codex review, both 2026-09-24).
        if not sid or not owners.get(sid, {}).get("spawned"):
            continue                                    # hand-opened, or not spawned by orch.py
        if _is_protected_pane(name, sid, sessions):
            continue                                     # a mate, the seat, or a named exception
        if _board_names_pane(name, open_named):
            continue                                     # an open question still names this pane
        clean, detail = _worktree_status(cwd_by_name.get(name))
        if not clean:
            continue
        out.append((name, title, said, detail))
    return out


def _log_auto_close(name, title, detail, said):
    os.makedirs(L.BOARD_DIR, exist_ok=True)
    ts = datetime.datetime.now().astimezone().replace(microsecond=0).isoformat()
    with open(AUTO_CLOSE_LOG, "a", encoding="utf-8") as fh:
        fh.write(f"{ts}  CLOSED {name}  {detail}  | {title} | {said[:160]}\n")


def cmd_auto_close(hours=AUTO_CLOSE_HOURS, dry=False):
    """Refuses to close anything for real unless `auto_close_enabled` is true in config.json - the
    CLI itself gates this, the same as `board.py autopick` gates `cmd_autopick` (Codex review
    2026-09-24: board_watch.py's caller checked the flag, but a bare `orch.py auto-close` run by
    hand did not, and would have closed panes with the flag off). `--dry-run` still works either
    way, so a manual check never needs the flag on."""
    import board as B
    if not dry and not B.auto_close_enabled():
        sys.exit("auto-close is off: set \"auto_close_enabled\": true in "
                 f"{L.CONFIG_PATH} (or run with --dry-run)")
    cands = auto_close_candidates(hours)
    if not cands:
        print(f"auto-close: nothing idle+DONE+clean older than {hours}h")
        return
    for name, title, said, detail in cands:
        if dry:
            print(f"WOULD CLOSE {name:12} {title:40} | {detail} | {said[:120]}")
        else:
            cmd_kill(name, quiet=True, force=True)   # auto_close_candidates already gated ownership
            _log_auto_close(name, title, detail, said)
            print(f"CLOSED {name:12} {title:40} | {detail} | {said[:120]}")
    print(f"auto-close: {len(cands)} session(s) {'listed' if dry else 'closed'}")


# ---------------------------------------------------------------- reap --done
#
# Ryan's purge-unused-panes rule: finished reviewers and done leads get killed, not left open. The
# idle+DONE+owner+protected+board+worktree gates are the auto-close ones above, reused as they are;
# this adds the one auto-close lacks - the pane's report must have reached its owner - and reports
# every pane it left alone with the reason, which `reap` and `auto-close` never did.

REAP_DONE_LOG = os.path.join(L.BOARD_DIR, "reap-done.log")


def _parse_age(s):
    """'20m' / '1h' / '90s' / bare minutes -> seconds."""
    m = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*([smh]?)\s*", str(s))
    if not m:
        sys.exit(f"bad --older {s!r}: use 20m, 1h or 90s")
    return float(m.group(1)) * {"s": 1, "m": 60, "h": 3600, "": 60}[m.group(2)]


def _report_delivered(sid, owner):
    """True when the pane's report has reached its owner. A pane with no owner, or one whose owner
    is gone, has nobody left to read it, so it passes. Otherwise the pane must have sent a
    SendMessage in its log tail - a pane that finished without sending one still holds a report
    nobody has seen."""
    if not owner or not L.owner_is_live(owner):
        return True
    for line in L.tail_entries(sid):
        if '"SendMessage"' not in line:
            continue
        e = L._load(line)
        c = ((e or {}).get("message") or {}).get("content")
        if isinstance(c, list) and any(isinstance(b, dict) and b.get("type") == "tool_use"
                                       and b.get("name") == "SendMessage" for b in c):
            return True
    return False


def _merged_into_main(cwd):
    """True when HEAD is already inside main (or master, or their origin copies). A worktree branch
    with no upstream is not unpushed work if main already holds every commit on it - this repo
    merges locally, so most finished branches never get an upstream."""
    for ref in ("main", "origin/main", "master", "origin/master"):
        r = subprocess.run(["git", "-C", cwd, "merge-base", "--is-ancestor", "HEAD", ref],
                           capture_output=True, text=True)
        if r.returncode == 0:
            return True
    return False


REPORTED_SHA_RE = re.compile(r"\b[0-9a-f]{7,40}\b")


def _is_shared_checkout(cwd):
    """True when `cwd` sits in a repo's main checkout (git-dir is the common dir), not in a linked
    worktree. Other panes edit a main checkout at the same time, so its `git status` shows their
    work, never this pane's."""
    gd = _git_out(cwd, "rev-parse", "--path-format=absolute", "--git-dir", "--git-common-dir") \
        if cwd and os.path.isdir(cwd) else None
    if not gd or len(gd.splitlines()) != 2:
        return False
    git_dir, common = gd.splitlines()
    return os.path.normcase(os.path.normpath(git_dir)) == os.path.normcase(os.path.normpath(common))


def _reported_text(sid, said):
    """The pane's last words plus every SendMessage body in its log tail - where it names its shas."""
    out = [said or ""]
    for line in L.tail_entries(sid):
        if '"SendMessage"' not in line:
            continue
        c = ((L._load(line) or {}).get("message") or {}).get("content")
        for b in c if isinstance(c, list) else []:
            if isinstance(b, dict) and b.get("name") == "SendMessage":
                out.append(json.dumps((b.get("input") or {}).get("message", "")))
    return "\n".join(out)


def _shared_pane_state(sid, said, cwd):
    """(ok, reason) for a finished pane in a shared main checkout. The tree's `git status` is every
    pane's work at once, so it says nothing about this one. Judge the pane by what it reported
    instead: every commit sha it named must already be on origin/main. A token that is not a
    commit in this repo (a hex-looking word) is not a sha. A pane that named no commit made none.
    Read-only git only - no fetch, nothing that moves a ref in the shared checkout."""
    if _git_out(cwd, "rev-parse", "--verify", "-q", "origin/main") is None:
        return False, "shared checkout has no origin/main to check against"
    on_main = []
    for tok in dict.fromkeys(REPORTED_SHA_RE.findall(_reported_text(sid, said))):
        if _git_out(cwd, "rev-parse", "--verify", "-q", f"{tok}^{{commit}}") is None:
            continue
        r = subprocess.run(["git", "-C", cwd, "merge-base", "--is-ancestor", tok, "origin/main"],
                           capture_output=True, text=True)
        if r.returncode != 0:
            return False, f"reported commit {tok} is not on origin/main"
        on_main.append(tok)
    if on_main:
        return True, f"shared checkout, {len(on_main)} reported commit(s) on origin/main"
    return True, "shared checkout, no commits reported"


def reap_done_plan(older_s):
    """([(name, title, said, detail, log)], [(name, why)]) - what to close, and what was left."""
    sessions = L.sessions()
    by_sid = L.panes_by_sid()
    open_named = _board_open_question_names()
    caller = L.caller_name()
    cutoff = time.time() - older_s
    reap, skip = [], []
    for sid, m in sessions.items():
        name = m["name"]
        if _is_protected_pane(name, sid, sessions):
            skip.append((name, "seat or mate (protected)"))
            continue
        if m["status"] != "idle":
            skip.append((name, f"not idle ({m['status']})"))
            continue
        owner = m.get("owner", "")
        if owner and owner != caller and L.owner_is_live(owner):
            skip.append((name, f"owner {owner} is live - ask it"))
            continue
        if L.last_assistant_ts(sid) > cutoff:
            skip.append((name, "not idle long enough"))
            continue
        kind, said = L.classify(sid)
        if kind != "DONE" or not said.strip():
            skip.append((name, f"not finished clean ({kind})"))
            continue
        if not _report_delivered(sid, owner):
            skip.append((name, f"report not yet sent to {owner}"))
            continue
        if _board_names_pane(name, open_named):
            skip.append((name, "an open board question names it"))
            continue
        if _is_shared_checkout(m.get("cwd")):
            clean, detail = _shared_pane_state(sid, said, m.get("cwd"))
        else:
            clean, detail = _worktree_status(m.get("cwd"))
            if not clean and detail == "no upstream tracking branch" and _merged_into_main(m.get("cwd")):
                clean, detail = True, "clean, merged into main"
        if not clean:
            skip.append((name, f"worktree: {detail} ({m.get('cwd')})"))
            continue
        reap.append((name, by_sid.get(sid, {}).get("terminal_title_stripped", "")[:40], said,
                     detail, L.log_path(sid)))
    return reap, skip


def cmd_reap_done(older="20m", dry=False):
    reap, skip = reap_done_plan(_parse_age(older))
    for name, title, said, detail, log in reap:
        if dry:
            print(f"WOULD CLOSE {name:14} {detail} | log {log}")
            continue
        cmd_kill(name, quiet=True, force=True)   # reap_done_plan already gated ownership
        os.makedirs(L.BOARD_DIR, exist_ok=True)
        ts = datetime.datetime.now().astimezone().replace(microsecond=0).isoformat()
        with open(REAP_DONE_LOG, "a", encoding="utf-8") as fh:
            fh.write(f"{ts}  CLOSED {name}  {detail}  log={log}  | {said[:160]!r}\n")
        print(f"CLOSED {name:14} {detail} | log {log}")
    for name, why in skip:
        print(f"SKIPPED {name:14} {why}")
    print(f"reap --done: {len(reap)} {'listed' if dry else 'closed'}, {len(skip)} skipped")


# ---------------------------------------------------------------- usage contract (AXI)

VERSION = "1.0.0"
DESCRIPTION = "drive the other Claude Code sessions on this box, all inside herdr"


def _spec(usage, summary, examples, values="", bools="", pos=0, free=False):
    """One command's flag table. `values` take the next token, `bools` do not, `pos` is how many
    positionals it needs, `free` skips the unknown-flag check (the rest is free text or belongs to
    a module that parses it). Keep it in step with the usage block in the module docstring."""
    return dict(usage=usage, summary=summary, examples=examples, values=set(values.split()),
                bools=set(bools.split()), pos=pos, free=free)


_PANE_FLAGS = "--model --cwd --effort --group --harness --kind --report --owner --domain --mcp " \
                "--expensive --rule --reason"
SPECS = {
    "ls": _spec("ls [--format toon|text]", "list every session: count line, then name, status, ctx, "
                "pane, owner. --format text is the wide tree.", ["orch.py ls", "orch.py ls --format text"],
                values="--format"),
    "idle": _spec("idle", "minutes since Ryan last touched the keyboard or mouse", ["orch.py idle"]),
    "approve": _spec("approve <pane> --action \"<exact action>\" [--words \"...\"]",
                     "mint a single-use approval id for one action", ["orch.py approve w1 --action \"submit form\""],
                     values="--action --words"),
    "approval": _spec("approval [ls | check <id> --as <pane> --action \"...\"]",
                      "list live approvals, or check one before acting on it",
                      ["orch.py approval ls", "orch.py approval check ab12 --as w1 --action \"submit form\""],
                      values="--as --action"),
    "ask": _spec("ask [--options \"A|B\"] [--action \"<exact action>\"] [--to <name>] <question>",
                 "put a question to Ryan on the board", ["orch.py ask --options \"A|B\" \"Which one?\""],
                 values="--options --action --to", pos=1),
    "asks": _spec("asks [--json] [--all]", "list the open questions", ["orch.py asks"], bools="--json --all"),
    "answer": _spec("answer <id> <option number or text>", "answer a board question",
                    ["orch.py answer q5 1"], pos=2, free=True),
    "prompt": _spec("prompt <name> <text...> [--approval <id>] | prompt <name> --cancel",
                    "send one follow-up prompt to any pane; --cancel ends an --acp pane's open turn",
                    ["orch.py prompt w1 \"run the tests again\"", "orch.py prompt w1 --cancel"],
                    values="--approval", bools="--cancel", pos=1, free=True),
    "own": _spec("own <name> [--owner NAME] [--domain \"<subject>\"]",
                 "show, re-parent or promote a live session", ["orch.py own w1", "orch.py own w1 --owner lead"],
                 values="--owner --domain", pos=1),
    "hold": _spec("hold <name> [reason...] [--until 2h|<UTC time>] | hold <name> --clear",
                  "park a pane on Ryan so it is never idle-nudged",
                  ["orch.py hold w1 waiting on Ryan", "orch.py hold w1 render running --until 2h",
                   "orch.py hold w1 --clear"], pos=1, free=True),
    "unhold": _spec("unhold <name>", "release a hold", ["orch.py unhold w1"], pos=1),
    "go": _spec("go <name>", "let one pane touch hardware for 30 min - only after Ryan says go",
                ["orch.py go w1"], pos=1),
    "ungo": _spec("ungo <name>", "revoke a go", ["orch.py ungo w1"], pos=1),
    "spawn": _spec("spawn <label> <prompt-or-@file> --rule N --model TIER --cwd DIR [--effort LEVEL] "
                   "[--group TAB] [--workspace W] [--harness claude|codex|pi|grok|agy|cursor] [--owner NAME] [--lead] "
                   "[--domain \"<subject>\"] [--mcp full|min] [--report PATH] [--worktree|--shared] [--acp]",
                   "open a new pane, start an agent in it, send the prompt",
                   ["orch.py spawn w1 @prompt.md --rule 5 --model sonnet --cwd C:\\work --worktree"],
                   values=_PANE_FLAGS + " --workspace --color",
                   bools="--lead --allow-extra-usage --worktree --shared --acp", pos=2),
    "dispatch": _spec("dispatch [--file PATH] [--refresh] [--explain RULE] [--pick \"TASK TEXT\"]", "print each dispatch rule with its quota-resolved pick; --explain RULE says why each entry won or lost; --pick asks Jev which rule fits the task (opt-in, you still pass --rule)",
                      ["orch.py dispatch", "orch.py dispatch --explain 5", "orch.py dispatch --pick \"fix the flaky board test\""], values="--file --explain --pick", bools="--refresh"),
    "task": _spec("task <label> \"<goal>\" [--done-when \"...\"] --rule N --model TIER --cwd DIR [--out PATH] "
                  "[--why \"...\"] [--read-first PATH] [--not \"...\"] [spawn flags]. "
                  "--goal/--done/--read still work; the goal text is required",
                  "write a task file and spawn a session on it",
                  ["orch.py task w1 \"fix X\" --done-when \"tests pass\" --rule 5 --model sonnet --cwd C:\\work --worktree"],
                  values=_PANE_FLAGS + " --workspace --goal --done --done-when --out --why --read --read-first --not --host",
                  bools="--lead --allow-extra-usage --worktree --shared --acp", pos=1),
    "review": _spec("review <name> [--diff REF-RANGE | --pr N --repo OWNER/NAME] --rule N [--model TIER] "
                    "[--effort LEVEL] [--harness claude|pi] [--cwd DIR] [--owner NAME]",
                    "spawn a reviewer for a diff", ["orch.py review lead --diff main..HEAD --rule 6"],
                    values=_PANE_FLAGS + " --diff --pr --repo", pos=1),
    "reap": _spec("reap [--hours N] [--dry-run] | reap --done [--older 20m] [--dry-run]",
                  "close idle sessions that finished clean N hours ago", ["orch.py reap --dry-run",
                  "orch.py reap --done --older 20m --dry-run"], values="--hours --older",
                  bools="--dry-run --done"),
    "auto-close": _spec("auto-close [--hours N] [--dry-run]", "close finished panes that nothing is waiting on",
                        ["orch.py auto-close --dry-run"], values="--hours", bools="--dry-run"),
    "kill": _spec("kill <name> [--force] | kill <name>@<host> | kill <name> --host <host>",
                  "close that session's herdr pane, or stop a mate on another machine over ssh",
                  ["orch.py kill w1", "orch.py kill w1@laptop"], values="--host", bools="--force", pos=1),
    "pull": _spec("pull [--host NAME]", "copy new reports from a remote host's outbox to handoffs\\remote\\<host>",
                  ["orch.py pull --host laptop"], values="--host"),
    "seat": _spec("seat [<name> [--no-announce] | --clear | --who]", "show or set who holds the orchestrator seat",
                  ["orch.py seat --who"], bools="--clear --who --no-announce"),
    "seat-pair": _spec("seat-pair up [--cwd DIR] | down [--seat-to NAME] | status | fix \"<text>\" | "
                       "answer <id> \"<text>\" | restart-watcher",
                       "the two-tier seat: a fast Haiku seat plus an Opus watcher behind it (seat_pair.py)",
                       ["orch.py seat-pair up", "orch.py seat-pair status",
                        "orch.py seat-pair down --seat-to vault-dc"],
                       values="--cwd --seat-to", pos=1, free=True),
    "decide": _spec("decide --kind <spawn|model|plan|answer|approve|route|kill|fix-applied|other> "
                    "--what \"...\" [--why \"...\"] [--rule N]",
                    "the seat logs one decision for the Opus watcher to review",
                    ["orch.py decide --kind spawn --what \"app-fix on sonnet\" --why \"multi-file fix\" --rule 5"],
                    values="--kind --what --why --rule"),
    "ask-opus": _spec("ask-opus \"<question>\" [--timeout S] | ask-opus --wait <id> [--timeout S]",
                      "the seat asks the Opus watcher a hard question and waits for the answer",
                      ["orch.py ask-opus \"should the WebApp fix ship before the Stripe change?\""],
                      values="--timeout --wait", pos=0, free=True),
    "show": _spec("show <name> [--ratio 0.5]", "move that pane to the right of the orchestrator pane",
                  ["orch.py show w1"], values="--ratio", pos=1),
    "hide": _spec("hide <name>", "send a shown pane back to its own tab", ["orch.py hide w1"], pos=1),
    "space": _spec("space [<mate> | --all | --order] [--dry-run]", "move a domain mate and its crew into its own workspace",
                   ["orch.py space --all --dry-run"], bools="--all --order --dry-run"),
    "chrome": _spec("chrome agent | open <url> [--port 9333|9334] [--front] [--wait-for <css>] | close <id> | "
                    "take <name> | free [name|force] | who", "the agent-only Chrome and the notes on Ryan's own",
                    ["orch.py chrome agent", "orch.py chrome open http://127.0.0.1:8787"], free=True),
    "restart-idle": _spec("restart-idle [--dry-run] [--include-busy-when-done] [--include-seat] [--timeout 30m] "
                          "[--only NAME ...]", "restart every idle claude pane in place, context kept",
                          ["orch.py restart-idle --dry-run"], values="--timeout --only",
                          bools="--dry-run --include-busy-when-done --include-seat"),
    "restart-pane": _spec("restart-pane <pane> [--prep --from VER | --pending]", "restart one pane in place",
                          ["orch.py restart-pane w1:p1"], values="--from", bools="--prep --pending", pos=1),
    "resume": _spec("resume", "re-arm everything after the orchestrator restarted", ["orch.py resume"]),
    "doctor": _spec("doctor", "one red/green line per moving part", ["orch.py doctor"]),
    "account": _spec("account [name|ambient]", "which login new panes start on", ["orch.py account"]),
    "quota": _spec("quota [--json] [--all] [--no-refresh] [--tui [--once]]", "every harness's plan, 5h left, weekly left",
                   ["orch.py quota", "orch.py quota --json"], bools="--json --all --no-refresh --tui --once"),
    "needs-me": _spec("needs-me [--next]", "everything blocked on Ryan right now", ["orch.py needs-me"],
                      bools="--next"),
    "afk": _spec('afk on --words "<his words>" [--until <time>] [--auto-minutes N] [--auto-all] [--wedge-minutes N] [--loud] | off | status | tick',
                 "Ryan is away: auto-pick free questions, park watchdog noise, print a return brief on off",
                 ['orch.py afk on --auto-all --words "asleep, back 7am" --until 07:00', "orch.py afk off"],
                 values="--words --until --auto-minutes --wedge-minutes", bools="--loud --auto-all", pos=1),
    "quiet": _spec("quiet on|off", "Ryan is here: park watchdog noise in board/afk-digest.md, questions still reach the seat",
                   ["orch.py quiet on", "orch.py quiet off"], pos=1),
    "status-page": _spec("status-page [--serve | --write | --bar]", "the live fleet status page",
                         ["orch.py status-page --write"], bools="--serve --write --bar"),
}


def _usage_fail(message, fix):
    """AXI usage error: `error:` and a `help:` line on STDOUT, exit 2."""
    _lf_stdout()
    print(AX.render({"error": message, "code": "VALIDATION_ERROR", "help": fix}))
    sys.exit(2)


def _is_flag(tok):
    return tok.startswith("--") and len(tok) > 2 and not any(c.isspace() for c in tok)


def _print_help(cmd):
    s = SPECS[cmd]
    print(f"usage: orch.py {s['usage']}\n{s['summary']}\nexamples:")
    for ex in s["examples"]:
        print(f"  {ex}")


def _check_usage(cmd, a):
    """Before any command runs: `--help` prints help and stops, an unknown flag or a missing
    argument is a usage error. `reap --help` used to reap."""
    s, rest = SPECS[cmd], a[1:]
    if "--help" in rest or "-h" in rest:
        _print_help(cmd)
        sys.exit(0)
    pos, i = 0, 0
    while i < len(rest):
        tok = rest[i]
        if tok in s["values"]:
            i += 2
            continue
        if not _is_flag(tok):
            pos += 1
        elif not s["free"] and tok not in s["bools"]:
            valid = " ".join(sorted(s["values"] | s["bools"]) + ["--help"])
            _usage_fail(f"Unknown flag {tok} for {cmd}", f"Valid flags for {cmd} are {valid}")
        i += 1
    if pos < s["pos"]:
        _usage_fail(f"{cmd} needs {s['pos']} argument(s) but got {pos}",
                    f"Usage is orch.py {s['usage']} - run `orch.py {cmd} --help` for examples")


def _positionals(cmd, a):
    """The non-flag tokens after the command, skipping each value flag's value (same walk as
    _check_usage). For `task`, [0] is the label and [1] is the goal text."""
    values, out, i, rest = SPECS[cmd]["values"], [], 0, a[1:]
    while i < len(rest):
        if rest[i] in values:
            i += 2
            continue
        if not _is_flag(rest[i]):
            out.append(rest[i])
        i += 1
    return out


def _task_text(a):
    """Goal, done-when, read-first for `task`. Positional text is the goal; --goal wins when both
    are given. --done-when and --read-first are the long spellings of --done and --read."""
    pos = _positionals("task", a)
    goal = _opt(a, "--goal") or (pos[1] if len(pos) > 1 else "")
    done = _opt(a, "--done") or _opt(a, "--done-when", "")
    return goal, done, _opt_multi(a, "--read") + _opt_multi(a, "--read-first")


def _num(a, flag, default):
    raw = _opt(a, flag, default)
    try:
        return float(raw)
    except ValueError:
        _usage_fail(f"{flag} needs a number but got {raw}", f"Pass a number, for example `{flag} 6`")


def _top_help():
    print((__doc__ or "").strip())
    print("\nexamples:")
    for ex in ("orch.py ls", "orch.py ls --format text", "orch.py reap --dry-run",
               "orch.py <command> --help"):
        print(f"  {ex}")


# ---------------------------------------------------------------- main

PARKED = {
    "path": "handoffs go through `task` now.",
    "color": "spawn colours every pane itself.",
    "codex": "`ls` lists codex panes and `resume` reopens dead ones.",
    "home": "run `py podium_home.py open`.",
}


def main():
    a = sys.argv[1:]
    if not a:
        return cmd_ls("toon", home=True)
    cmd = a[0]
    if cmd in ("--help", "-h", "help"):
        return _top_help()
    if cmd in AX.VERSION_FLAGS:
        return print(f"orch.py {VERSION}")
    if cmd.startswith("-"):
        _usage_fail(f"Flags must come after the command, got {cmd}",
                    "Run `orch.py <command> [args] [flags]`; `orch.py --help` lists the commands")
    if cmd in SPECS:
        _check_usage(cmd, a)
    elif cmd not in PARKED and cmd != "board":
        _usage_fail(f"Unknown command {cmd}", "Run `orch.py --help` to see the commands")
    _watcher_guard(cmd, a[1:])
    if cmd == "ls":
        fmt = _opt(a, "--format", "toon")
        if fmt not in ("toon", "text"):
            _usage_fail(f"--format must be toon or text, got {fmt!r}", "Run `orch.py ls --format text`")
        cmd_ls(fmt)
    elif cmd == "idle":
        cmd_idle()
    elif cmd == "approve" or cmd == "approval":
        import approvals
        (approvals.cmd_approve if cmd == "approve" else approvals.cmd_approval)(a)
    elif cmd in ("ask", "asks", "answer"):
        import asks
        asks.main(a, cmd_prompt)
    elif cmd == "prompt" and a[2:] == ["--cancel"]:      # exactly `prompt <name> --cancel`, never prompt text
        cmd_cancel(a[1])
    elif cmd == "prompt":
        rest = a[2:]
        if "--approval" in rest:
            import approvals
            i = rest.index("--approval")
            aid = rest[i + 1] if i + 1 < len(rest) else ""
            rest = rest[:i] + rest[i + 2:]
            rest = [" ".join(rest) + approvals.approval_suffix(aid)]
        cmd_prompt(a[1], " ".join(rest))
    elif cmd == "own":
        cmd_own(a[1], _opt(a, "--owner"), _opt(a, "--domain"))
    elif cmd == "hold":
        rest, until = a[2:], _opt(a, "--until") or ""
        if "--until" in rest:
            i = rest.index("--until")
            rest = rest[:i] + rest[i + 2:]
        if "--clear" in rest:
            cmd_hold(a[1], clear=True)
        else:
            cmd_hold(a[1], " ".join(rest), until=until)
    elif cmd == "unhold":
        cmd_hold(a[1], clear=True)
    elif cmd == "go":
        cmd_go(a[1] if len(a) > 1 else None)
    elif cmd == "ungo":
        cmd_go(a[1] if len(a) > 1 else None, clear=True)
    elif cmd == "spawn":
        _require_rule(a)     # before the worktree check, which can create a worktree
        model, cwd = _require_model_and_cwd(a)
        cmd_spawn(a[1], a[2], model, cwd,
                  _opt(a, "--workspace"), _opt(a, "--group"),
                  _harness(a), _opt(a, "--report", ""),
                  owner=_opt(a, "--owner"), lead="--lead" in a, color=_opt(a, "--color"),
                  allow_extra_usage="--allow-extra-usage" in a,
                  domain=_opt(a, "--domain", ""), effort=_opt(a, "--effort"),
                  expensive=_opt(a, "--expensive"), mcp=_opt(a, "--mcp"), acp="--acp" in a)
    elif cmd == "dispatch" and "--pick" in a:
        if not _opt(a, "--pick"):
            sys.exit('dispatch --pick needs the task text: orch.py dispatch --pick "<task text>"')
        import jev_pick as JP     # opt-in helper (references/dispatch.md); the seat still passes --rule
        JP.cmd_pick(_opt(a, "--pick"), _opt(a, "--file"), caller=L.caller_name())
    elif cmd == "dispatch":
        cmd_dispatch(_opt(a, "--file"), "--refresh" in a, _opt(a, "--explain"))
    elif cmd == "task" and "--host" in a:
        cmd_task_remote(a)
    elif cmd == "pull":
        cmd_pull(_opt(a, "--host"))
    elif cmd == "task":
        goal, done, read_first = _task_text(a)
        if not goal.strip():
            _usage_fail("task has no Goal text, so the pane would not know what to do",
                        'Pass it after the label: orch.py task <label> "<goal>" [--done-when "..."]')
        _require_rule(a)     # before the worktree check, which can create a worktree
        model, cwd = _require_model_and_cwd(a)
        cmd_task(a[1], goal, done, _opt(a, "--out"),
                 model, _opt(a, "--group"), cwd,
                 _harness(a), _opt(a, "--report"), _opt(a, "--owner"),
                 lead="--lead" in a, allow_extra_usage="--allow-extra-usage" in a,
                 domain=_opt(a, "--domain", ""), effort=_opt(a, "--effort"),
                 why=_opt(a, "--why", ""), read_first=read_first,
                 not_in_scope=_opt_multi(a, "--not"), expensive=_opt(a, "--expensive"),
                 mcp=_opt(a, "--mcp"), acp="--acp" in a)
    elif cmd == "review":
        _require_rule(a)
        cmd_review(a[1], _opt(a, "--diff"), _opt(a, "--pr"), _opt(a, "--model", "sonnet"),
                   _opt(a, "--cwd"), _opt(a, "--owner"), _opt(a, "--repo"),
                   harness=_harness(a), effort=_opt(a, "--effort"),
                   expensive=_opt(a, "--expensive"))
    elif cmd == "reap" and "--done" in a:
        cmd_reap_done(_opt(a, "--older", "20m"), "--dry-run" in a)
    elif cmd == "reap":
        cmd_reap(_num(a, "--hours", 6), "--dry-run" in a)
    elif cmd == "auto-close":
        cmd_auto_close(_num(a, "--hours", AUTO_CLOSE_HOURS), "--dry-run" in a)
    elif cmd == "kill" and RM.split_name(a[1], _opt(a, "--host"))[1]:
        label, host = RM.split_name(a[1], _opt(a, "--host"))
        if not _owner_gate(f"{label}@{host}", _remote_call(RM.owner, host, label), "--force" in a):
            sys.exit(1)
        print(_remote_call(RM.kill, host, label))
    elif cmd == "kill":
        cmd_kill(a[1], force="--force" in a)
    elif cmd == "seat":
        cmd_seat(a[1] if len(a) > 1 and not a[1].startswith("--") else None, "--clear" in a,
                 "--who" in a, "--no-announce" not in a)
    elif cmd == "seat-pair":
        sub = a[1]
        if sub == "up":
            SP.up(_opt(a, "--cwd"), cmd_spawn)
        elif sub == "down":
            SP.down(_opt(a, "--seat-to"), cmd_kill, lambda n: cmd_seat(n, announce=True))
        elif sub == "status":
            SP.status()
        elif sub == "restart-watcher":
            SP.restart_watcher(cmd_spawn, cmd_kill)
        elif sub == "fix" and len(a) > 2:
            SP.fix(" ".join(a[2:]))
        elif sub == "answer" and len(a) > 3:
            SP.answer(a[2], " ".join(a[3:]))
        else:
            _usage_fail(f"seat-pair {sub!r} is not a subcommand, or it is missing its text",
                        "Run `orch.py seat-pair --help`")
    elif cmd == "decide":
        SP.decide(_opt(a, "--kind", ""), _opt(a, "--what", ""), _opt(a, "--why", ""), _opt(a, "--rule", ""))
    elif cmd == "ask-opus":
        timeout = _num(a, "--timeout", str(SP.ASK_TIMEOUT))
        if "--wait" in a:
            sys.exit(SP.wait_answer(_opt(a, "--wait", ""), timeout))
        rest, i = [], 1
        while i < len(a):
            if a[i] == "--timeout":
                i += 2
                continue
            rest.append(a[i])
            i += 1
        sys.exit(SP.ask_opus(" ".join(rest), timeout))
    elif cmd == "show":
        cmd_show(a[1], _num(a, "--ratio", "0.5"))
    elif cmd == "hide":
        cmd_hide(a[1])
    elif cmd == "space":
        if "--order" in a:
            cmd_space_order(dry_run="--dry-run" in a)
        else:
            name = a[1] if len(a) > 1 and not a[1].startswith("--") else None
            cmd_space(name, all_mates="--all" in a, dry_run="--dry-run" in a)
    elif cmd == "chrome":
        cmd_chrome(a[1] if len(a) > 1 else None, a[2] if len(a) > 2 else None)
    elif cmd == "restart-idle":
        import restart_idle
        restart_idle.main(a[1:])
    elif cmd == "restart-pane":
        import restart_pane
        restart_pane.main(a[1:])
    elif cmd == "resume":
        cmd_resume()
    elif cmd == "doctor":
        cmd_doctor()
    elif cmd == "account":
        cmd_account(a[1] if len(a) > 1 else None)
    elif cmd == "quota":
        import quota
        quota.cmd_quota("--json" in a, "--no-refresh" not in a, "--all" in a,
                        "--tui" in a, "--once" in a)
    elif cmd == "needs-me":
        import needs_me
        needs_me.cmd_needs_me("--next" in a)
    elif cmd in ("afk", "quiet"):
        import afk
        afk.main(a)
    elif cmd == "status-page":
        import status_page
        status_page.main(a[1:])
    elif cmd == "board" and a[1:2] == ["ready"]:
        import ready
        sys.exit(ready.main(a[2:]))
    elif cmd == "board":
        import board
        board.main(a[1:])
    elif cmd in PARKED:
        sys.exit(f"orch.py {cmd} was parked on 2026-10-06 (skills/_parked/orchestrate-commands): "
                 f"{PARKED[cmd]}")


if __name__ == "__main__":
    main()
