---
name: orchestrate
description: Be the human for every other Claude Code session on this box - read what each is doing, answer their questions, push them along, start new sessions in herdr with the right model, and rotate any session that gets too big into a fresh one with a handoff file. Use when your human says "be the orchestrator to all my CLAUDE processes", "guide the rest of the sessions", "you kind of guide the rest", "be the compactor", "give you a handoff prompt and start a new session", "tell you my ideas and you configure other sessions", "what are all my sessions doing", or "keep them moving along". This is the one chat they talk to; it prompts the others.
---

# Orchestrate the other sessions

Your human talks only to this session. This session reads, prompts, spawns, and retires the others.
Everything lives in herdr so they can watch.

    ORCH="~/.claude/skills/orchestrate/scripts/orch.py"
    BOARD="~/.claude/skills/orchestrate/scripts/board.py"

Machine-specific values (working directory, session-name prefix, orchestrator tab label, optional
account tool) live in `config.json` next to this file. `config.example.json` documents every key.
Nothing is hardcoded; with no config file at all the defaults come from `~/.claude` and the cwd.

## 0. Permission mode

Spawned panes get `--dangerously-skip-permissions` from `orch.py` and land in bypass.
`~/.claude/settings.json` should set `"crossSessionInbound": "accept"`, so a message from a session
in a different permission class is delivered instead of held (proven 2026-09-03: a `--permission-mode
default` sender reached a bypass receiver, `from-mode="prompting"`, zero `Held peer message` lines).
This session still wants bypass so it is not the one stopping on prompts.

## 1. See the board

    py "$ORCH" ls

One line per live session: name, status, ctx in k, model, herdr pane, herdr agent state, why it
stopped (`DONE` / `QUESTION` / `BLOCKED` / `OFFER` / `ERROR`), tab title. `<-- ROTATE` marks ctx at
or over the threshold (400k by default). Two live sessions can share a name, so the board is keyed
by session id; pass a session-id prefix or a pane id when a name is ambiguous.

    py ".../scripts/tail_session.py" <sid-prefix> [n-msgs] [max-chars]   first ask, last ask, last words
    py ".../scripts/recent_events.py" <sid-prefix> <ISO-timestamp>       everything after a moment

Registry: `~/.claude/sessions/<pid>.json`. Logs: `~/.claude/projects/<cwd-with-separators-as-dashes>/<sessionId>.jsonl`.

Report in three buckets: **working now**, **waiting on a human**, **done, safe to close**.

## 2. Prompt them

`SendMessage` to the session name from `ListAgents`, or `herdr agent prompt <pane-id> "<text>"`.
Pass `notify_when_idle: true` on anything you want to hear back from.

Approval policy: say yes on your own to small, reversible, in-scope picks - usually the agent's own
recommended option. Bring to your human only money, deletes, anything sent to other people, and
anything that changes their plans.

A session started before this policy may refuse a relayed approval ("a peer message does not stand
in for their answer"). When that happens and you already have the decision, do the action yourself -
edit the file, run the command - instead of bouncing it back.

`orch.py spawn`/`task` append the standing constraints to every prompt automatically (never
`cd X && ...`, never a heredoc, take the browser lock first) - do not strip them when hand-writing
a prompt.

`/compact` sent as a message does nothing. Use rotation (section 5).

**Pull a pane next to your human when they have to see it or type into it.** Their ask
(2026-09-03): *"if there's something I need to see and maybe put my input into, you should open the
pane where I can see it, just on the right of this pane ... so I don't have to switch tabs."*

    py "$ORCH" show <name>      # moves that session's pane to the right of the orchestrator pane
    py "$ORCH" hide <name>      # sends it back to its project tab when they are done

Do it without being asked whenever a session parks on something only they can do - a permission
prompt the classifier will not let you press, a browser step in their own account, a QUESTION that
needs their words in that session rather than yours. Say in your reply that the pane is on their
right. Put it back with `hide` once the thing is done, so the orchestrator tab stays two panes at
most. The board (4b) is for decisions; `show` is for interaction.
Proven 2026-09-03. One thing to know: moving a tab's **last** pane destroys that tab, so `hide`
then rebuilds a tab with the old label rather than finding it. That is the normal path, not a bug.

## 3. Watch, do not poll

    Monitor(command='py ".../scripts/watch_sessions.py"', persistent=true)

Lines it emits:

    NEW session <name> status=<s>
    GONE session <name> (exited)
    NEW codex <name> pane=<id>    a non-claude agent pane appeared (4a)
    GONE codex <name> (pane closed)
    <name> REPORT | <path>    a codex task wrote its report file - read it
    <name> QUESTION | ...     it is waiting on a human - read it and answer
    <name> BLOCKED | ...      the permission classifier stopped it; it needs a command pasted
    <name> OFFER | ...        it offered to do more - say yes or close it
    <name> ERROR | ...        last tool result was an error, or it ended on a failure
    <name> DONE | ...         finished clean
    <name> PROMPT | ...       pane parked on a permission prompt - `py fix_mode.py <pane>`
    <name> MODE auto | ...    pane is in auto mode when the skill expects bypass
    <name> TEACH-DIALOG dismissed | ...   the "Teach auto mode about your environment?" dialog
    <name> CTX 4xxk - rotate  once per session, at or over the threshold
    <name> STALE 30m          once, a busy session whose log stopped moving
    REAP: <n> candidates      at most hourly, only when some qualify - run `py "$ORCH" reap`

To see the quiet lines fire on a normal day, lower the thresholds for one run:
`WATCH_REAP_HOURS=0 WATCH_STALE_MIN=1 py ".../watch_sessions.py"`.

## 3b. After a restart

The orchestrator's own process dies more often than anything it manages - it restarted four times
on 2026-09-03 (two Claude auto-updates, two herdr restarts). Every restart silently drops the
Monitors, the board read, and any builder that was mid-task. One command re-arms all of it:

    py "$ORCH" resume

It prints, and changes nothing by itself:

1. the exact `Monitor(...)` lines for `watch_sessions.py`, `board_watch.py`, and - only if some pane
   needs it - `auto_accept.py` with those pane ids already filled in;
2. the board's open questions, straight out of `state.json`;
3. every live session, with the dead ones (`<synthetic>` model, `ctx=0k`) marked `DEAD` - kill and
   respawn those;
4. panes needing a keypress: a pending prompt, `auto` mode where bypass was expected, or the teach
   dialog;
5. sessions sitting idle mid-task - nudge each with "continue where you left off".

Then run `py "$ORCH" doctor` for one red/green line per moving part: herdr's API socket, the peer
message pipes, `claude auth status`, a ping to the API, both settings files' `permissions.defaultMode`,
`DISABLE_AUTOUPDATER`, and the real **used** percentage from `accounts/usage/*.json`.

## 4. Start work from their ideas

An idea in one sentence, straight to a running session:

    py "$ORCH" task <label> --goal "<what they want>" --done "<what proves it>" \
        [--out PATH] [--model tier] [--group "<tab>"] [--cwd DIR]

Writes `~/.claude/handoffs/task-<label>-<stamp>.md` from `templates/task.md` and spawns a session on
`Read <file> and do exactly what it says.` Prefer this over `spawn` for anything longer than two
sentences - a long prompt risks truncation in transit, a file cannot be truncated.

Raw spawn, when the prompt really is one line or a handoff file:

    py "$ORCH" spawn <pane-label> "<prompt or @handoff-file>" --model <tier> --group "<tab label>"

- **Tab = project, pane = task.** `--group` splits into the tab with that label, or makes it. All
  the work for one project in one tab. Rename with `herdr tab rename <tab> <label>` and
  `herdr pane rename <pane> <label>`. This session's tab is the one named by `orchestrator_tab` in
  config.json - that label is how `orch.py` finds the orchestrator.
- **Model by difficulty.** `fable` for the genuinely hard (architecture, a bug that survived its own
  fix, a race). `opus` for hard but ordinary. `sonnet` (default) for normal building. `haiku` for
  trivial mechanical passes. Say which tier you picked when you report.
- Every spawned prompt carries the line telling the new session that the orchestrator speaks for the
  human on small reversible calls, so it acts on your answers instead of parking on them.

**Anything that has to outlive one turn is a pane, not a subagent.** The Agent tool is for a search
that finishes inside a couple of minutes and hands back one answer - a docs lookup, a transcript, a
grep across repos. A tutor, a long build, a watcher, anything you will come back to or your human
might want to watch - `orch.py spawn` it. In-process subagents are children of this session's
process and every one of them died with each of 2026-09-03's four restarts; a pane survives, and
`resume` finds it again. Confirmed by the human 2026-09-04 ("keep 1 i like it") when asked whether
quick lookups should be panes too: no, panes for anything over a couple of minutes, subagents for
quick lookups. Say which you used when it matters.

## 4a. Codex and other kinds

A pane can run codex-cli instead of claude. Same tab, same board, same watcher:

    py "$ORCH" task <label> --kind codex --goal "..." --done "..." [--report PATH] [--group "<tab>"]
    py "$ORCH" spawn <label> "<prompt>" --kind codex [--report PATH] [--group "<tab>"]
    py "$ORCH" kill <label>          # same command; it finds the pane by label or pane id

`spawn --kind codex` opens the pane, runs `codex --dangerously-bypass-approvals-and-sandbox`
(codex's own bypass - the TUI header then reads `permissions: YOLO mode`), clears codex's two
first-open dialogs, then prompts through `herdr agent prompt <pane>`.

**The report file is the whole contract.** Codex has no `SendMessage`, so `task --kind codex`
writes into the task file: *write your final report to `<path>`, then print `DONE` on its own line
as the last line of your reply*. Default path `~/.claude/handoffs/report-<label>.md`; override with
`--report`. `watch_sessions.py` prints `<label> REPORT | <path> (<n> bytes)` the moment it lands -
that is the line to wait for, then read the file.

What does not carry over:

- **No `SendMessage`, in either direction.** You cannot message it and it cannot message you. To
  say something mid-task, use `herdr agent prompt <pane-id> "<text>"`.
- **No ctx and no rotate.** There is no session id, no registry entry and no `.jsonl`, so `ls`
  prints `ctx=   -` and `rotate` cannot run. A Codex pane that fills up gets killed and re-tasked.
- **No `reap`.** Reaping reads log timestamps; it skips these panes entirely.
- **No model tier.** `--model` is ignored; codex picks its own.

`ls`, the watcher's `NEW`/`GONE`/`DONE`/`QUESTION`/`ERROR` lines, and the board all work, built
from herdr plus the last 40 lines of the pane's screen, run through the same word rules that judge
a Claude session's last words (`orchlib.classify_words`). `board/agent-panes.json` remembers each
pane's label and report path - herdr already reports the kind, so nothing else is stored.

Proven end to end 2026-09-04: `task hello-codex --kind codex` -> report file in 35 s, then
`hello-codex REPORT | ...report-hello-codex.md (1059 bytes)`, `hello-codex DONE | • DONE`,
`GONE codex hello-codex (pane closed)`.

## 4b. Podium - the one page they read

Podium opens in the Lavish editor (`lavish-axi`) and has four sections: **Needs you**, **Show**,
**Sessions**, **Done since your last look**.
`board/state.json` is the source of truth; `board/board.html` is generated and Podium re-reads the
state every 5 s, so a change lands without reopening.

**A question carries the real thing, never a summary of it.** Their words: *"This needs to be like
give the full context of the question here."* Put the actual code block, the actual picture, the
actual table or diagram in `--context-file`. `context_md` renders mermaid fences, `$$math$$`,
images and GFM tables. A single `$` is deliberately not a math delimiter - money comes up too often.

    py "$BOARD" add-question --id q5 --title "..." --context-file x.md \
        --option A="..." --option B="..." --pick A --why "..." --from cg4-askbox
    py "$BOARD" answer q5 "A"                    # moves it to Done
    py "$BOARD" show --caption "..." --file x.md [--for q5]
    py "$BOARD" sessions --from-ls               # fill the table from the live board
    py "$BOARD" done "text"    |  render  |  open  |  prune --days 1  |  digest

`orch.py board <...>` dispatches to the same CLI. Schema: `board/schema.md`.
Podium needs `lavish-axi` on PATH; everything else in this skill works without it.

**Only human-class questions go on the board.** Money, deletes, anything sent to other people,
anything that changes their plans. Everything else you answer yourself (section 2) - a board full of
small reversible picks is the failure mode, not the goal.

**Podium has tabs, one per project group.** A `group` field on every question, show block, session
row and done line sorts it onto the tab named after the herdr tab it came from - the same string
`--group` files a pane under at spawn time (`web-app`, `ideas-pipeline`, ...). `add-question`, `show`
and `done` also take an explicit `--group` for items with no session behind them; leave it off and
board.py looks the pane up itself, falling back to `""` (All only) when nothing live matches. The
**Needs you** strip is pinned above the tabs and always lists every open question regardless of
which tab is selected, each with its group badge, so a question never hides in a tab nobody opened.
The selected tab lives in a JS variable plus `localStorage`, so neither the 5 s poll nor a later
page open resets it.

Watch their answers the same way you watch sessions:

    Monitor(command='py ".../scripts/board_watch.py"', persistent=true)

    ANSWER q4 | A: Ask box, who gets to use it?   already applied - the page moved it to Done
    NOTE <selector> | ...                         an annotation on one element
    MESSAGE | ...                                 freeform text they typed
    LAYOUT n warnings                             fix the overflow before involving them
    SESSION ended                                 they closed the Lavish session

`watch_sessions.py` also posts by itself: a builder's `QUESTION` becomes a board question with the
builder's own last message as context and no options; a `BLOCKED` becomes a board question whose
context is that message **plus the exact one-line command to paste with the `!` prefix**, pulled out
of the builder's own fenced block; an `ERROR` becomes a Show block captioned `<name> hit an error`.
All dedup on pane + the first 80 characters. Read the question, then edit the options and the pick
in before your human sees it.

Traps, both measured 2026-09-03:

- **Lavish reloads the iframe whenever the served HTML changes on disk**, and a reload throws away
  whatever they are half-way through typing. That is why `board.html` holds no content at all and is
  only rewritten when the skeleton itself changes; content travels in `state.js` / `state.json`.
  Never put a timestamp or a counter into the skeleton.
- **Inside Lavish the artifact runs in a sandboxed iframe with an opaque origin**, so `fetch` of a
  sibling file is CORS-refused even though the server answers 200 - and `location.origin` still
  reads as the real origin there, so the sandbox cannot be detected up front. The page therefore
  reads `state.js` through a `<script>` tag, which is not CORS-checked and also works over
  `file://`. `state.json` stays the source of truth on disk; `state.js` is the same object,
  written by the same `save()`.

## 4c. The browser lock

One session drives the real browser at a time. Two drove it at once on 2026-09-03 and one typed over
the other's half-written draft. The lock is a file both sides can read, not a message anyone has to
remember to send:

    py "$ORCH" chrome take <label>     # exit 0 = it is yours, exit 1 = who has it and since when
    py "$ORCH" chrome who              # holder, or "free"
    py "$ORCH" chrome free <label>     # `free force` to break a lock left behind by a dead session

Every spawned prompt already tells the new session to take the lock before driving the browser and
free it straight after. If a session dies holding it, `chrome free force` clears it.

## 5. Rotate a big session (the "compactor")

Trigger: ctx at or over ~400k. Not a crash line (window is 1M), a speed and quality line.

    py "$ORCH" rotate <name> [--model tier] [--group "<tab>"]

One command does all of it: resolves the pane, asks that session for a handoff written to
`templates/handoff.md`, waits up to 10 minutes (`--until done --until blocked`), checks the file is
over 300 bytes, spawns the replacement in the same tab on `@<handoff>`, then closes the old pane.
If the handoff never lands it prints the pane's last 30 lines and exits 1 **without killing
anything**. The old log stays on disk.

Proven end to end 2026-09-03: a session wrote two of three files, `rotate` handed off, and the
replacement finished the third from the handoff alone with no other context.

## 6. Rotate yourself

    py "$ORCH" rotate-self --notes <file> [--dry-run]
    py "$ORCH" rotate-self --no-notes [--dry-run]

The board alone is not the handoff. `board/state.json` never held *your* knowledge - why a builder
is held, what the human actually asked today, who holds the browser lock, whose edits are
uncommitted, which login is ambient - so `rotate-self` refuses to run without `--notes <file>` and
prints the template path to fill: `templates/orch-notes.md` (`templates/handoff.md`'s shape plus
three sections: **Sessions and what each waits on** - a table of name/pane/next step/why
waiting/files it owns; **The human's asks today, not yet done** - their words; **Environment** -
ambient login and usage trap, browser lock holder, uncommitted edits by owner). Pass `--no-notes`
to rotate on the board alone when there is truly nothing to say.

The exact sequence:

    py "$BOARD" ...                                            # fill in what's known if needed
    # write a narrative file from templates/orch-notes.md by hand, e.g.
    #   ~/.claude/handoffs/orch-notes-<stamp>.md
    py "$ORCH" rotate-self --notes ~/.claude/handoffs/orch-notes-<stamp>.md

`rotate-self` splices that file above a **trimmed** board section - open questions in full, Show
captions, session rows, and only Done lines from the last 24 hours, not the whole of `state.json`;
the replacement is told the full file is still on disk for anything older. It then spawns the
replacement with `--model fable` in the orchestrator tab and stops. The replacement's first Next
step is `py "$ORCH" kill <my-name>`, so it retires you; its second and third are `resume` and
`doctor`.

`--dry-run` prints the path and the whole assembled file without writing it, spawning, or killing
anything.

`rotate` cannot do this to itself - it would be waiting on its own turn to finish.

## 6b. Accounts (optional)

Only if `accounts_tool` is set in `config.json` - a script that prints an OAuth token for a named
account (`<tool> token <name>`). With it unset, account switching is off and every pane uses the
ambient login, which is the normal single-account setup.

    py "$ORCH" account                      # what new panes start on
    py "$ORCH" account <name>|ambient       # change it for every later spawn and rotate

The switch is per process through `CLAUDE_CODE_OAUTH_TOKEN`. `herdr agent start` drops `--env`, so
`orch.py` opens a pane shell with the env, types the `claude` command into it, and waits for herdr
to detect the agent. That is why a spawn on a named account takes 10-20 s.

Traps, all hit 2026-09-03:

- **A usage ledger is only as good as the last sighting.** `seen never` plus `headroom 100%` means
  nothing was ever measured, not that the account is fresh. Two accounts hit their 5 h limit at the
  same time and the ledger still said 100% for the one nobody had used through it. `doctor` reads
  `accounts/usage/*.json` and reports **used** percent, which is the number that moves; the pane
  status line shows remaining, and reading one for the other burned a day.
- A statusline writing `accounts/usage/ambient.json` for token-env panes too cannot tell the
  accounts apart.
- `/login` inside a running session swaps that session's credentials but not the env var, so panes
  it spawns still carry the old token until `account ambient` (or a new token) is set.
- A spawn on a dead account leaves a pane with a `<synthetic>` model and `ctx=0k` on the board.
  `resume` lists those as DEAD; kill and respawn after fixing the account.

## 7. Close finished sessions

    py "$ORCH" reap [--hours 6] [--dry-run]

Closes every session that is idle, whose last assistant message is over N hours old, and whose last
words classify as `DONE`. Never `QUESTION`, `BLOCKED`, `OFFER`, `ERROR`, never a session with no
readable last words, and never the session in the orchestrator tab. One line per close with its
title and last words. Run `--dry-run` first when the count looks high.

Idleness is measured from the last assistant message in the log, not the file's mtime - a peer
message or an idle-notice subscription touches mtime too, so mtime never goes stale even on a truly
finished session.

## Traps

- **A cwd claude has never opened on this machine shows a "Do you trust this folder?" dialog**
  before anything else, and `herdr agent start` reports `agent_not_ready`. Three CourseGrid panes
  stalled on it 2026-09-03. `orch.py spawn` now reads the pane, picks "Yes, I trust this folder",
  waits for the agent, and prompts by pane id. For a pane started by hand: Down, Enter.
- **Shell quoting eats backticks in `--goal`.** `` `orchestrator` `` inside a double-quoted goal ran
  as a command and vanished from four task files. Write session names bare, or use the task file.
- **During a rotation two panes share the `orchestrator` tab and label.** `orchestrator_sid()` now
  prefers the pane whose session title matches the tab and skips any pane labelled `orch-*`, and
  `rotate-self` relabels the retiring seat `orch-retiring` before spawning. Before that, four task
  files told builders to report to the seat that was about to be killed.
- **`--allow-dangerously-skip-permissions` only PERMITS bypass; `--dangerously-skip-permissions`
  enters it.** A pane started with the wrong one comes up in default mode and stalls silently on the
  first "Do you want to proceed?". `orch.py spawn` passes the right flag and confirms bypass within
  10 s. For a pane started by hand, or one that drifted: `py ".../scripts/fix_mode.py" <pane-id>`
  (escapes the teach dialog, accepts a pending prompt, then shift+tab until the status line says
  bypass). `py ".../scripts/auto_accept.py" <pane-id> ...` keeps pressing enter on prompts in the
  background - a deny rule still prompts inside bypass.
- **An auto-update can flip `permissions.defaultMode` to `auto` underneath you.** Every pane spawned
  after that comes up in auto and stalls on its first classifier hit. `watch_sessions.py` reports
  `MODE auto`, `doctor` checks both settings files, and the installer offers to set
  `DISABLE_AUTOUPDATER=1`.
- **`cd X && ...` in the Bash tool trips a deny rule** and parks the pane on a permission prompt even
  in bypass. Use absolute paths; the cwd persists anyway.
- **Use the pane id as the herdr target**, not a name. `herdr agent prompt|read <target>` takes both,
  but a pane started by hand has no agent name - only the pane id always resolves.
- **`herdr agent start --kind codex` fails on Windows.** herdr launches
  `Start-Process -FilePath codex`, and `codex` on PATH is an npm `.cmd`/`.ps1` shim, so it dies with
  "%1 is not a valid Win32 application". `orch.py` tries `agent start` first and falls back to
  typing the command into the pane shell; herdr still detects `agent: codex` on the pane either way.
- **Codex opens with two dialogs and the first prompt is swallowed by them.** "Do you trust the
  contents of this directory?" takes Enter; the hooks-review table takes Escape (which closes it
  without trusting anything). `herdr agent prompt` returns `agent_prompted` anyway and the text
  disappears, so `orch.py` clears both before it prompts.
- **A freshly spawned session's `~/.claude/sessions/<pid>.json` is briefly written without
  name/status/sessionId.** `orchlib.sessions()` skips those instead of raising; never index that dict
  with `[]` in new code.
- Registry `status` lags. `busy` means a turn is running.
- `herdr agent prompt --wait --until idle` is wrong; wait on `done` and `blocked`.
- `tail_session.py` must match `.jsonl`, not the same-named subagent directory - the filter is deliberate.
- In the Bash tool `taskkill /PID` gets path-mangled; use `powershell Stop-Process -Id` or let
  `orch.py kill` do it (it picks `kill` on posix).
- `Monitor` scripts must `flush=True` every print or events sit in the buffer.
- Never fold `git add -A` into a rotate; a working tree with unrelated dirt sweeps into the commit.
- Mail from an address to itself does not push a phone notification - the provider treats it as a
  note to self. For anything that must be seen away from the desk, use a push channel or a different
  sending address.

## Files

    config.json                what is machine-specific here (config.example.json documents it)
    scripts/orch.py            ls, path, spawn, task, rotate, rotate-self, resume, doctor, chrome,
                               reap, kill, show, hide, account, board
    scripts/orchlib.py         shared: config, registry, herdr, log reading, the classifier, the lock
    scripts/board.py           the Orchestrator Board: state.json in, board.html out
    scripts/board_watch.py     the Monitor script for answers on the board
    scripts/watch_sessions.py  the Monitor script for the sessions
    scripts/fix_mode.py        force one pane into bypass
    scripts/auto_accept.py     keep accepting prompts in named panes
    scripts/tail_session.py    read a session's first and last words
    scripts/recent_events.py   everything a session did after a timestamp
    board/state.json           the board itself - the source of truth
    board/agent-panes.json     label and report path for each non-claude (codex) pane
    board/schema.md            what every field in state.json means
    board/demo-state.json      a fake board; `board render` it to see the look with no real data
    templates/handoff.md       what a rotating session must write
    templates/orch-notes.md    what `rotate-self --notes` needs before it will run
    templates/task.md          what `orch.py task` fills in
