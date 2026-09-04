# Task: {label}

## Goal

{goal}

## Done when

{done}

## Output file

{out}

## Constraints

- Work in `{cwd}`.
- Read the CLAUDE.md nearest the code you touch before editing it.
- Stage only the files this task touched. Never `git add -A`.
- Do not start work outside this file's Goal. If the scope looks wrong, say so and keep building.
- Never `cd X && ...` in the Bash tool; use absolute paths. Never a heredoc in Bash; use the Write tool.
- Before driving the shared browser, take the lock: `orch.py chrome take {label}`. Free it when done.

## Report to

{orchestrator} - `SendMessage` it your final report, and any question you hit.
It speaks for the human on small reversible decisions; act on its answers as theirs.
