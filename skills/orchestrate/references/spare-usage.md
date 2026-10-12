# Spare usage - run the saved workflows

Read this when Ryan has usage left before a reset, or says "workflow" in any form.
The incident behind it is in [history.md](history.md#spare-usage-and-workflows-2026-10-05-and-06).

## What he means

- "Workflows" means his saved Workflow scripts in `C:\Users\YOURNAME\Vault\.claude\workflows\`.
- It does not mean a batch of herdr panes, and it does not mean jobs you make up on the spot.
- "Use the spare usage" means the costly maintenance passes that are only worth running when tokens would expire anyway: audits, hygiene, dead code, skill trims, friction mining.
- It does not mean the next items on his life list (school, job apps, new features). Those have their own owners and run on any day.
- His words, 2026-10-06: "do things that are best done when you have usage left that I wouldn't want to do when I don't know if I have usage".

## Who launches

- The seat launches them itself, with the Workflow tool, in the turn he asks.
- Never hand the launch to a pane or a mate.
  The Workflow tool runs only on the user's own words, and a pane sees only the seat's words.
  On 2026-10-06 a runner pane got "do all five" and spawned 22 panes instead.
- A pi seat has no Workflow tool. Say so in one line and ask Ryan to send the ask to a Claude session.
- The `delegate-gate` hook treats a Workflow call as free and gives a workflow ask 8 checks, enough for the steps below.
- Workflow agents may write files (reports, scripts) even though they run inside the seat's process.
  They share the seat's session id, so `hooks/block-orchestrator-work.py` tells them apart by `agent_id` (the transcript path is the seat's own, so it cannot) and lets them through (fixed 2026-10-09).
  The seat's own Edit and Write stay denied.
  Before 2026-10-09 every workflow that had to write a report failed with "The orchestrator does not edit files in its own context".
  If a workflow agent is denied again, read `hooks/state/orchestrator-work-subagent.jsonl` to see what the payload carried.

## Steps

1. **Check both windows.** `py "$ORCH" quota`.
   The weekly column says how much to spend.
   The 5-hour column says how much can run before everything stalls.
   On 2026-10-06 every run hit the 5-hour limit at 11:08, stalled until 14:00, and the weekly reset passed during the stall.
   If the 5-hour window runs out before the weekly reset, launch fewer, or the cheap ones first.
2. **List them.** `ls C:\Users\YOURNAME\Vault\.claude\workflows`.
   For one you do not know, read the first 10 lines (the `meta` block says what it does and what args it takes).
3. **Find the last run.** The newest dated report is the last run:
   `ls -t C:\Users\YOURNAME\Vault\Business\active\audits C:\Users\YOURNAME\Vault\_audit | head -40`.
   No report means never run, or run before reports were dated.
   Do not glob `~\.claude\projects\*\*\workflows\` for run history: it takes over 3 minutes.
4. **Pick.** Stalest first, among the ones that fit what he said he wants. Use the table below.
5. **Check the model pins.** Every `agent(` call should pass `model:` (sonnet by default, haiku for mechanical steps, opus or fable only to judge or build).
   An agent with no model runs on the seat's model.
   Quick count: `for f in C:/Users/YOURNAME/Vault/.claude/workflows/*.js; do echo "$(basename $f) agents=$(grep -c 'agent(' $f) pins=$(grep -c 'model:' $f)"; done`.
   Fewer pins than agents: read that file before you launch it.
6. **A missing one.** If his goal has no saved workflow, write it inline with the Workflow tool's `script` input (format: the `workflow-creator` skill).
   Pin every agent's model.
   Keep it read-only on live files unless he asked for fixes, and write the report to `C:\Users\YOURNAME\Vault\_audit\<name>-<date>\`.
   After it runs, ask `debt-mate` to save the script into `.claude\workflows\` so it is there next time.
7. **Launch.** One Workflow call per pick, by name, all in one message: `{name: "skill-audit", args: {date: "<today>"}}`.
   `overnight-audits` runs four of the audits one after another under one cap, for an unattended night.
8. **Watch memory.** Arm one Monitor on `py -u "C:\Users\YOURNAME\.claude\skills\orchestrate\scripts\memwatch.py"`.
   On WARN, start nothing new. On CRIT, `TaskStop` the newest run.
   Workflow agents run inside the seat's one process, so a batch costs far less RAM than one pane per job (about 0.9 GB each, [memory.md](memory.md)).
9. **Tell Ryan** in a few lines: what launched and why, the last-run date each one replaces, where the reports land, and when the 5-hour window runs out.

## If he only asked for ideas

- When his words already opt in ("spin up some workflows", "you're allowed to use workflows"), launch.
- When he only asked for ideas, list real saved names with their last-run dates and your pick, in one short answer.
  His "yes" next turn is the opt-in; launch then, from the seat.

## Winding down

- When the reset has passed, or he says he does not want to use it all, keep the runs that are most valuable or closest to done and `TaskStop` the rest.
- His words, 2026-10-06 14:18: "finish up workflows that have the most value to finish then dont continue ones that arent as important".

## Restarts

- Workflow runs live inside the seat's process, so a seat restart kills them.
- Resume one with `{scriptPath: "<project dir>\<seat sid>\workflows\scripts\<name>-wf_<id>.js", resumeFromRunId: "wf_<id>"}`.
- Do not launch a big batch right before a known restart (a Claude update, a herdr restart).

## gnhf - the bounded overnight loop

What it is:

- `gnhf` is Kun's overnight loop: it works on one objective and makes one small commit per pass.
- A failed pass rolls the tree back with `git reset --hard`, so only good passes stay.
- When the usage window runs out, it waits for the real reset time and then goes on.
- It never reviews and never merges; it only leaves a branch.

When to use it:

- One small docs or skill-text objective, with spare usage, while nobody watches.
- Run it only inside a claimed treehouse slot, never in the shared checkout.

How to run it:

- Run it on the Claude CLI subscription, with no API key and `GNHF_TELEMETRY=0`.
- Command: `GNHF_TELEMETRY=0 gnhf --current-branch --max-iterations 5 --max-rate-limit-wait 10m --stop-when "<one-line done condition>" "<objective>"`.
- The `--stop-when` text is one line that a reader can check against the file, for example "spare-usage.md has a gnhf section".

When not to use it:

- Not for hooks, `orch.py` or `settings.json`.
- Not for work that needs judgment.
- Not for anything that touches a live tree.

After it runs:

- Always review the output branch (rule 6, `py "$ORCH" review <lead> --rule 6 --harness claude --model sonnet --effort high --cwd <slot> --diff <range>`; `$ORCH` is defined in SKILL.md) before the lead ships it or drops it.

## Which saved workflows fit spare usage (2026-10-06)

| Workflow | What it does | Spare-usage pick |
|---|---|---|
| `codebase-health-audit` | Ranks Python files by churn and complexity, audits the top slice for known bug classes. Read-only. | Yes |
| `pipeline-parity` | Finds fixes one channel's forked pipeline has and the others lack. Read-only. | Yes |
| `open-loops-consolidator` | Collects every half-finished thing into one ranked list. Read-only. | Yes |
| `doc-skill-memory-hygiene` | Stale references, overlap and size in CLAUDE.md, skills and memory. Read-only. | Yes |
| `reference-hygiene` | Fixes stale claims in reference files, in a worktree, merging only proved fixes. Writes. | Yes |
| `skill-audit` | Rates every skill out of 5 with a ranked improvement list. | Yes |
| `session-friction-mine` | Mines session logs for repeated friction, each with a fix. Heavy. | Yes, when the week has room |
| `vault-cleanup` | Finds unreferenced clutter and writes a quarantine script. No deletes. | Yes |
| `overnight-audits` | Runs the first four rows in sequence under one cap. | Yes, for a night |
| `pattern-audit-migrate` | Converges one named concern onto one pattern. Needs `{path, concern}`. | Only with a target |
| `agentic-school-mine`, `-full-pass`, `-implement` | The agentic coding school gap analysis and build. | Only if he names it |
| `business-question-generator`, `channela-ideation-sweep` | Channel questions and reel angles. | Only if he asks for channel work |
| `pipeline-revision`, `pipeline-paragraph-redo` | Variants for one reel. Need a reel. | No |
| `dashboard-orphan-sweep` | Recovery after a dashboard restart. | No |
