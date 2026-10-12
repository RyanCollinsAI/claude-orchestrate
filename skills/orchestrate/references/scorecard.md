# `scorecard.py` - how the seat knows it is doing a good job

Ryan asked the orchestrator seat, 2026-09-09: *"how do you know you are doing a good job?"*
Before that day the honest answer was "I don't - I feel like it is going fine."
This file is the answer: every number the seat is allowed to judge itself by, where it comes from,
and what a bad one looks like.

```
py "C:\Users\YOURNAME\.claude\skills\orchestrate\scripts\scorecard.py" [--days 7] [--md] [--json] [--snapshot]
```

`board.py digest` embeds the `--md` version, so the morning digest carries it without anyone
running it by hand.

## The rule this file exists to enforce

**Measure first, judge second.** Every number below is counted from a file that already existed
before the scorecard did - `board/state.json`, the `*.jsonl` transcripts, the git logs. Nothing is
instrumented for the scorecard's benefit, which is what lets it be run against a week that has
already happened. If a future number needs new instrumentation, that is a reason to be suspicious
of the number, not a reason to add the instrumentation.

## Where each number comes from

| source | what it can answer | what it cannot |
|---|---|---|
| `board/state.json` | questions open right now, `done[]` lines from the last ~24h | anything older: `board.py prune` deletes answered questions and old Done lines every hour |
| `*.jsonl` transcripts under `~/.claude/projects/*/` | every command the seat typed, every `SendMessage` it sent, when each pane was active, each pane's final context | anything a python process did without a Claude turn - the watcher's own auto-posted questions have no transcript |
| `git log` across `digest_repos` | commits authored in the window | which pane authored them (nothing stamps a pane onto a commit) |
| the herdr session registry | who is alive right now | history - the registry holds live sessions only |

The scan is one streaming pass. A line is only handed to `json.loads` when a substring test already
proved it matters, which is what keeps ~1.1 GB of transcript to a few seconds.

## Every measure, and what a bad one means

### Questions

| measure | meaning | bad looks like |
|---|---|---|
| asked | distinct question ids created in the window | a jump with no matching rise in decisions: the seat is forwarding instead of deciding |
| answered by Ryan | closed by a human | - |
| auto-picked | closed by `board.py autopick` after `auto_after` passed | rising while `answered by Ryan` falls: questions are being marked `--auto` that should not be |
| withdrawn / duplicate | answer text says withdrawn, superseded, duplicate, obsolete, moot, stale | more than ~1 in 5. The seat is posting questions it could have answered from the board, and Ryan is paying to close them |
| still open | open right now | more than ~10. The board stops being one screen and questions start hiding |
| wait median (h) | half of answered questions waited less than this | - |
| **wait p90 (h)** | the slowest tenth waited at least this | over ~24h. Something Ryan cares about sat a full day |
| wait not computable | answered, but no `add-question` command exists to date it | a large share means the watcher is posting most questions itself; the median is then only about the seat's own questions |
| oldest open | the single question that has waited longest, and for how long | anything past a day that is not deliberately parked |

### Decisions

| measure | meaning | bad looks like |
|---|---|---|
| messages the seat sent | every `SendMessage` from an `orchestrator*` transcript | - |
| approvals it gave itself | messages whose first 400 characters carry an approval phrase | near zero: the seat is a relay, not an orchestrator, and Ryan is the bottleneck by design |
| reverted within 24h | a later message to the same pane that takes it back | - |
| **revert rate (%)** | reverted / approvals | over ~15%. The seat is approving before it has the facts |

**Read the printed pairs before believing this rate.** It is a word match, so it names suspects, not
verdicts. The plain-text output prints up to six approval/revert pairs underneath the table for
exactly this reason.

A bare `stop` is deliberately **not** a revert marker. Measured 2026-09-09: "two more jobs, then
stop", "stop only if a shared-tools edit becomes necessary" and "right to stop" made 34 of 35
matches false, and the rate read 37.6% when the true figure was near 14%. `stop` now counts only as
the first word of a message, which is how a real recall actually reads ("STOP now.").

### Panes

`spawned` / `killed` are counted from `orch.py` verbs in the transcripts.
`transcripts in window` counts every log file with an entry in the window, including one-shot
subagent and skill runs, so it is much larger than the number of panes Ryan ever saw. It is a
denominator, not a headcount.

### Pane time

**The watcher keeps no agent-state history.** `watch_sessions.py` samples context into `ctx_hist`,
but that is an in-memory dict trimmed to the last hour and never written to disk, so there is no
record of which pane was idle when. Pane time is therefore reconstructed from transcript gaps:

- a gap of **5 minutes or less** between two entries counts as **working** (tool call, model
  latency, a subagent finishing)
- anything longer counts as **waiting**
- a single gap is **capped at 8 hours**, so one pane parked overnight cannot swamp the column

`waiting (%)` is the share of pane-hours spent sitting still. It runs high by nature - a pane that
finished and is waiting to be killed is counted as waiting. Watch the trend, not the level. A rise
means panes are finishing and nobody is retiring them, or leads are blocking on the seat.

To make this a measured number instead of a reconstructed one, the watcher would have to append its
`ctx_hist` samples plus each pane's `agent_status` to a file. That is the one piece of new
instrumentation worth adding here.

### Rework

Two independent signals, reported separately because they catch different failures:

- **panes whose last words admit a redo**: the pane's own final text says "again", "re-run", "redo",
  "fixed my", "my mistake", "second pass". This is the pane redoing its own work.
- **labels spawned more than once**: the same label root handed out twice in the window
  (`app-week-rail` and `app-week-rail-2` share the root). This is the *seat* redoing the pane.

The second is the more damning of the two: a respawned label usually means the first pane was given
a brief it could not finish. Over ~20% of spawns, the briefs are too big.

### Shipped

Commits across `digest_repos`, authored in the window, `--all --no-merges`.

`.git` is a **file**, not a directory, in any repo whose git dir was separated - the vault is one
(`C:/Users/YOURNAME/vault-repos/vault-notes.git`). An `os.path.isdir` test silently drops it, which is
how `board.py digest` reported zero vault commits until 2026-09-09. `scorecard.is_repo()` asks git
instead, and `git_since` now calls it.

### Tokens

`context carried at retire (k)` sums each retired pane's final context. It is the closest available
proxy for what a pane cost: that window was re-read on every turn until the seat retired it. A pane
killed at 700k cost far more than one killed at 90k doing the same work.

## The three numbers to watch

Printed under the table, each with a week-over-week delta once two runs of the same window length
exist:

1. **p90 question wait (h)** - how long the slowest question left Ryan blocked
2. **revert rate (%)** - approvals the seat gave itself and then took back
3. **pane time waiting (%)** - share of pane-hours spent sitting still

They were chosen because each one gets worse when the seat fails in a different way: too slow to
Ryan, too fast on its own, or not keeping panes moving. A seat can look busy while all three rot.

## Week-over-week

`--snapshot` appends the run to `board/scorecard-history.json` (last 52 runs kept). `judge()` reads
the newest earlier run **with the same window length**, and only one that ended **at least half a
window ago** - a 1-day run never compares itself against a 7-day one, and two runs of the same week
taken an hour apart are not a week-over-week delta. They cover almost the same days, so their delta
is near zero and would print "flat" when nothing was measured at all. With no eligible earlier run
the deltas are simply absent; nothing is faked.

## Known limits, stated so nobody re-derives them

- A question the watcher posted through `add_question_dict` has no shell command, so its asked time
  is unknown. It counts as asked, but its wait lands in `wait not computable` rather than skewing
  the median.
- Nothing links a commit to a pane, so `commits` is a whole-window number, never per-seat.
- `approvals` and `reverted_24h` are word matches over message text. Read the printed pairs.
- `transcripts in window` counts subagent and skill runs, so `redo rate (%)` has a large denominator
  and reads low. Compare it against itself week to week, never against an absolute target.
