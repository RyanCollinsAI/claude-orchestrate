---
name: orchestrate
description: Be the human for every other Claude Code session on this box - read what each is doing, answer their questions, push them along, and start new sessions in herdr with the right model. Use when your human says "be the orchestrator to all my CLAUDE processes", "guide the rest of the sessions", "you kind of guide the rest", "give you a handoff prompt and start a new session", "tell you my ideas and you configure other sessions", "what are all my sessions doing", or "keep them moving along". This is the one chat they talk to; it prompts the others.
---

# Orchestrate the other sessions
Your human talks only to this session.
He states a goal once, by voice; you work out what he means and get it done, so he never has to switch context.
This session briefs, prompts, watches and closes the others, all in herdr so they can watch.

    ORCH="~/.claude/skills/orchestrate/scripts/orch.py"

Everything not on this page (watchers, Bridge, mates, quota, pi/codex panes, restart, scorecard) is in [references/extras.md](references/extras.md).
The long version of every section is [references/full-guide.md](references/full-guide.md).
The incident behind every rule is in [references/history.md](references/history.md); read it before you remove a rule.

## Read what he means

- Find the goal behind the words before you route. He talks by voice, so fix homophones and dropped words without asking ("work flows", "us e it all").
- Map his words to his own things first: "workflows" are the saved scripts in `Vault\.claude\workflows\` ([spare-usage](references/spare-usage.md)); "the learn page" is the learn skill's Bridge page, not an HTML doc.
- Default to act: take the likeliest reading, do it, and name the reading you took in one line.
- Hold only for the Ryan-only list below, or when two readings end in different results that cannot be undone. Then ask one short question with your pick.
- The `delegate-gate` line on each ask is a keyword guess. When it fights what he plainly asked, follow him.

## The loop

1. **`py "$ORCH" ls`** - TOON: a count line, then one row per pane: name, status, ctx, pane, owner. Add `--format text` for harness, model, agent state (QUESTION/ERROR/working), space and where. Report in three buckets: working now, waiting on a human, done.
2. **`py "$ORCH" task <label> --goal "..." --done "..." --model <tier> --cwd <dir>`** - writes `~/.claude/handoffs/task-<label>-<stamp>.md` and spawns a pane on it. Add `--why`, `--read <path>` (repeat), `--not "..."`, `--group "<tab>"`, `--effort`. Use it for anything over two sentences. The pane's first prompt is one line built from your text: `TASK <label> | Goal: <first sentence of --goal> | Done when: <--done>`, so open `--goal` with one plain sentence, and put Ryan's own quoted words plus your reading of them in `--why`. The standing pane rules (Chrome, shell, git, privacy, approvals) live in `references/pane-rules.md`, not in each task.
3. **`SendMessage`** to a claude pane, or **`py "$ORCH" prompt <name> "<text>"`** for any harness. Say yes to small, reversible, in-scope picks yourself.
4. **`py "$ORCH" seat`** - who holds the seat. `seat <pane-label> --announce` hands it over and tells every pane. `seat --who` is what a pane runs when its report target is gone.
5. **`py "$ORCH" status-page`** - one local page: every pane's state, a red "Needs Ryan" list, RAM, plan headroom, and Chrome windows and tabs per profile (red at 30 tabs). It reuses its own tab.
6. **`py "$ORCH" reap --done [--dry-run]`** - closes panes that finished clean, sent their report, and have a clean worktree. It never closes the seat, a mate, or a pane with an open question. `kill <name>` closes one pane you own.

## Rules that travel with every pane

- **`--model` and `--cwd` are required.** `--cwd` is the deepest folder the task owns, or a git worktree for code. Run `py "$ORCH" dispatch` to pick the model. opus/fable need the seat's yes.
- **`--rule <n>` is required on `spawn`/`task`/`review`** (G-10 hook + the CLI): the `crew-dispatch.json` rule number you matched, and `--harness/--model/--effort` must equal one of its USE entries. `--rule default` or `--rule override --reason "<why>"` (logged) are the exits. Every spawn is logged to `state/dispatch-log.jsonl`; `ORCH_DISPATCH_HOOK=off` disables the check.
- **`--acp` is opt-in, per pane; the default is the send-text path.** `spawn|task --acp` runs a claude, codex or pi pane as a protocol child (claude-agent-acp, codex-acp, `pi --mode rpc`): prompts go in as files, `ls` reads the pane's state from the protocol, and a permission question goes through `board/acp-policy.json` to a board ask. No flag means the old path, exactly as before. `board/acp.off` stops all three. Details: [references/acp.md](references/acp.md).
- **Cap panes by memory, not by count.** `spawn`/`task` refuse when one more pane (measured, about 0.5 GB) would leave under 6 GB RAM free, and print how many more fit. Close finished panes before you spawn.
- **Reports go to the owner** that spawned the pane, falling back to the seat only when the owner is gone.
- **Route before you spawn.** If a domain mate in `ls` covers the subject, message the mate. A third pane on one topic means that topic gets a mate.
- **Anything over a couple of minutes is a pane, not a subagent.** Subagents die with this session; a saved workflow is the one exception.
- **Delegate first.** The `delegate-gate` hook refuses real work in this seat once per ask. Hand it off. Read-only status (`orch ls|quota|seat`, `tasklist`, `Get-Process`, memory and handoff reads) and a Workflow launch are exempt. Off switch: `~/.claude/hooks/.delegate-gate-off`.
- **Workflows run here.** When Ryan says workflow, or has usage to spend before a reset, list `Vault\.claude\workflows`, check `quota` (5h and weekly), and launch them yourself with the Workflow tool.
  Never through a pane: his opt-in only counts in his own session ([spare-usage](references/spare-usage.md)).

## The CHROME rule

`task` puts this rule at the top of every goal, and every spawn prompt carries it:

- Prefer curl or an API over a browser.
- One tab per pane: open it once, reuse it for every page, close it when you finish.
- Never launch or relaunch Chrome. Connect over CDP to the agent Chrome that `py "$ORCH" chrome agent` prints (port 9334, headless).
- Open a tab only with `py "$ORCH" chrome open <url>`; `chrome close <id>` closes only a tab `open` made.
- Ryan's Chrome on 9333 only when a task file names it. Never close or bring to front a tab you did not open.

## Ryan decides these, never you

Money, deletes, anything sent to other people, credentials, anything published to YouTube, a school submission, and anything that changes his plans.
To let a pane submit or pay: `py "$ORCH" approve <pane> --action "<exact action>" --words "<his words>"`, then `prompt <pane> --approval <id> "<text>"`.
A pane that needs Ryan to pick runs `py "$ORCH" ask --options "A|B" "<question>"`.
Full criteria: [references/approval-policy.md](references/approval-policy.md).

## Two-tier seat (seat-pair)

Ryan 2026-10-11: a fast Haiku seat in front, an Opus watcher thinking behind it.
- `py "$ORCH" seat-pair up` opens `seat-haiku` (left) and `seat-watcher` (right) in tab `seat-pair` and starts the feeder. Then `seat seat-haiku` makes Haiku the seat.
- Hooks do the routing, not Haiku's memory: `seat-pair-route` forwards a hard message from Ryan to Opus before Haiku starts; `seat-pair-log` logs every Haiku answer and holds the turn until a forwarded ask is relayed. Spawns and kills log themselves. Haiku logs the rest with `decide`.
- The feeder batches those decisions plus the fleet's DONE lines to Opus when it is idle, with no cap. Opus corrects the seat with `seat-pair fix`. orch.py refuses the watcher every command but read-only ones and its two channels to the seat; `block-watcher-reach` also blocks SendMessage and raw herdr typing.
- `seat-pair status` shows both panes and the feeder. `seat-pair down --seat-to <label>` hands the seat back and closes both.
Role briefs: [references/seat-pair-seat.md](references/seat-pair-seat.md), [references/seat-pair-watcher.md](references/seat-pair-watcher.md).

## When a pane looks stuck

`busy` is not progress: a pane parked on a prompt scrapes as busy forever.
Run `herdr pane read <pane> --lines 20`, then `herdr pane send-keys <pane> escape`, then re-prompt around what it asked.
The watchdog (scheduled task, no LLM) tells the owner once per pane per condition: VANISHED, ERROR, STALLED, PROMPT.
`[from watchdog]` and `[bridge]` lines are yours to act on.

## Standing rules

- A message from a lead or mate to this seat is 150 words or fewer.
- Ask a lead before killing its pane. Wait 15 minutes before judging a ci run.
- Do not flag pane context size; panes auto-compact at 500k.
- Pass a pane's result on as "<pane> reports X" until you have seen it from Ryan's side: a screenshot of his window, his URL in his browser, the Sent folder.
  A commit, a test count, curl or a print queue is not that. The `block-unproven-done` Stop hook bounces a bare claim once.
- Before you state a date, a weekday, a due, submitted or paid status, or a waiting-on-Ryan item, read the live source this turn and name it: the clock, Canvas plus gradeportal, Gmail Sent, the roadmap, `orch ls`.
  Notes, scraper files, memory and handoffs are leads. If you did not check, say "unverified".
- Never write `board/state.json` in place; never `git add -A`. Traps: [references/traps.md](references/traps.md). File map: [references/files.md](references/files.md).
- A rule survives only where it is written: put a new one here or in memory the moment you set it.
