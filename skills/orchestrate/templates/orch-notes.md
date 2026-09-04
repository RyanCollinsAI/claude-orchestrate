# Orchestrator notes: <one-line summary of today>

Written by <session name> at <timestamp>. The session reading this is the replacement
orchestrator; it has none of the earlier context and must not ask the human to repeat anything
written below. Its first action is always `py orch.py kill <this session's name>`.

## Goal (the human's words)

<Quote what they actually asked for today, their phrasing, not a summary in your words.>

## Constraints and preferences

<Standing rules that still bind the replacement. "(none)" is a valid answer.>

## Key decisions

<Every settled call today, one line each, with its reason AND the option that lost. A decision
without its rejected alternative gets re-argued by the replacement.>

- **<what was decided>**: because <why> - rejected <alternative> because <why not>

## Sessions and what each waits on

<One row per live session you are tracking. The board's Sessions table only carries a state pill
and a one-line "doing" - this is the part that explains why each one is actually stuck.>

| name | pane | next step | why waiting | files it owns |
|---|---|---|---|---|
| <name> | <pane id> | <what it should do next> | <why it hasn't, or "-" if not waiting> | <path(s)> |

## The human's asks today, not yet done

<Their words, not a summary. Empty is a valid answer.>

## Environment

- **Ambient login / usage**: <which account is active on new spawns, any usage-limit trap hit today>
- **Browser lock**: <who holds it and since when, or "free">
- **Uncommitted edits**: <owner - path - what's dirty, or "none known">

## In progress right now

<What was mid-flight when this file was written. Name the file, the function, and the next edit.>

## Open questions

<Only real human-class blockers not already on the board. Empty is a valid answer.>

## Traps hit

<Anything that cost a wasted turn today: a flag that behaves differently than documented, a path
that does not exist, a tool that hangs.>

## Files that matter

<The short list to read first for today's specific work. Absolute paths, one clause each on why.>
