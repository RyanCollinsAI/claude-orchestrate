# TASK {label}

**Goal:** {goal_line}
**Done when:** {done_line}
**Report to:** {owner_line}

## Goal

{goal}

## Why

{why}

Ryan's words reach you through voice-to-text. If a request reads oddly, check the known mishears in `{voice_mishears}` before you act on it.

## Done when

{done}

## Out of scope

{out_of_scope}

## Output file

{out}

## Read first

{read_first}

## Constraints

- Standing rules for every pane (Chrome, shell, git, privacy, approvals, asking Ryan): `{pane_rules}`. Read it once now.
- Work in `{cwd}`.
- Ship path for this repo: {ship_mode}. Modes: no-mistakes = full gate then PR, direct-PR = push and open a PR, local-only = ship_main.py to main; yolo on = the agent may merge green work. This only describes the repo. Do not change it. feature-lifecycle step 7 names it in the report.
- Do not start work outside this file's Goal. If the scope looks wrong, say so and keep building.
- {shell_rule}
- Right before your final report, put it on the board so it counts as done: `py "C:\Users\YOURNAME\.claude\skills\orchestrate\scripts\board_done.py" record --session {label} --outcome "<one line: what shipped>"`.

## Report to

{report_to}

The only report shape is three lines: `Done: <what finished>`, `Proof: <output, path or commit>`, `Next: <what happens next, or none>`.
For anything Ryan will see or use (a page, a hotkey, a print, a sent email, a restart), a commit or a test count is not proof.
Prove it where he looks: a screenshot path of it in his browser or window, the Sent-folder read-back, or the live URL loaded the way he loads it (same browser, same scheme).
If you could not check it there, write `Proof: not seen from Ryan's side` so the orchestrator does not pass it on as done.
Never send an idle or status ping ("are you there", "still working?"). The orchestrator reads `orch.py ls`; a message that only says you are idle is dropped.
Crew panes end the report with two more lines after Next: `Blast radius: contained|wide - <reason>` and `Lifecycle: crew - <lead> runs review and ship`.
