# crew-dispatch.json - which harness, model, and effort a pane gets

The file is `crew-dispatch.json` at the root of this skill.
Its shape is kunchenguid/firstmate's `config/crew-dispatch.json` (MIT; the example is `docs/examples/crew-dispatch.json` there, and the real one is on screen at minute 8 of "High-Throughput Agentic Engineering with Kun", 2026-09).
The rules are Ryan's.

## Shape

```json
{
  "rules": [
    { "when": "<plain-language kind of task>",
      "use": [ { "harness": "claude|pi|codex", "model": "<optional>", "effort": "<optional>" } ],
      "why": "<dated rationale, Ryan's words when it is his rule>" }
  ],
  "runwayFloor": 10,
  "default": [ { "harness": "claude", "model": "sonnet", "effort": "high" } ]
}
```

`when` and `use` are required.
`use` and `default` take one profile or a list.
**Status: not live in `orch.py dispatch` yet.**
`runwayFloor`, `spendPriority` and `--explain` are built in `scripts/dispatch_rule.py`, but `orch.py` still picks by most weekly headroom alone until I2 applies `references/g8-orch-patch-for-i2.md`.
Until then `--explain` can show a pick that `orch.py dispatch` does not make.
`schema_errors()` is also unused until that patch.

A list is a quota pick, three steps (quota raid dispatch, from firstmate's quota-array-dispatch, MIT):

1. Eligibility and reasoning class are the rule itself: the seat judged them when it chose the rule, and a rule only lists entries that fit.
2. Runway floor. `runwayFloor` (optional, top level, a weekly percent 0-100) drops any entry whose harness is **known** to have less than that left, while another entry is not under it.
   A harness with no reading stays in and sorts last.
   If every entry is under the floor, the floor lifts and the most headroom wins (nothing has room, so spend the least empty bucket).
   No `runwayFloor` means no floor.
3. Rank. `spendPriority` (optional number on a USE entry, default 0, higher wins) comes first.
   Ties go to the most **weekly** quota left, then to the order written.
   With no `spendPriority` anywhere this is the old "most weekly headroom" rule.

`spendPriority` and `runwayFloor` never change which combos a rule allows: the `--rule` gate and the G-10 hook read only harness, model and effort.
`why` is optional and is where a standing preference lives, dated, in Ryan's own words.
`_notes` is ignored by code and read by people.

## How it is used

    py "$ORCH" dispatch            # the table, each rule resolved to one pick
    py "$ORCH" dispatch --refresh  # let quota-axi refresh vendor sessions first

    py "$SKILL/scripts/dispatch_rule.py" --explain 5                       # why each USE entry of rule 5 won or lost, live quota
    py "$SKILL/scripts/dispatch_rule.py" --explain 5 --headroom claude=40,pi=0   # what-if numbers

`--explain` takes a rule number or `default`.
Each entry prints WON or LOST with its weekly left, its `spendPriority` and the reason (under the floor, lower spendPriority, less headroom, or written later).

Nothing in code matches a task to a rule.
The orchestrator reads the `when` lines, picks the rule that fits with judgement, and passes that rule's resolved pick as flags:

    py "$ORCH" spawn <label> "<prompt>" --rule 5 --harness pi --model gpt-6-astra --effort xhigh --cwd DIR
    py "$ORCH" task  <label> --goal ... --done ... --rule 3 --harness claude --model fable --effort xhigh --cwd DIR
    py "$ORCH" review <lead> --diff <range> --rule 6 --harness pi --model gpt-5.6-sol --effort medium

`spawn` and `task` refuse without `--model` and `--cwd` (2026-09-23) - `review` still resolves both itself.

Since 2026-10-08 all three also refuse without `--rule <n>` (the number `dispatch` prints) and refuse a `--harness/--model/--effort` that is not one of that rule's USE entries, else-fallbacks included.
The G-10 hook in `plugins/orchestrator-guardrails` checks before the Bash call, and `orch.py` checks again, so both agree (`scripts/dispatch_rule.py`).
The refusal prints the exact allowed combos.
`--rule default` uses the table's default list.
`--rule override --reason "<why>"` skips the match and is logged.
Every spawn is one row in `state/dispatch-log.jsonl` (ts, decision, rule, model, harness, effort, label, caller); `jq`/`grep override` audits the off-table picks.
`ORCH_DISPATCH_HOOK=off` (env, or as a prefix on one command) turns the check off.
The scripts never read the `when` text: the seat judges, the hook checks consistency.
A live session needs a plugin reload (or restart) to load G-10; `orch.py` enforces the same rule without it.

Restore (undo all of this): copy each `*.bak-2026-10-08` back over its file (`orch.py`, `orch_watchdog.py`, `test_orch.py`, `SKILL.md`, `references/dispatch.md`, and in the plugin `hooks/hooks.js.bak-2026-10-08-dispatch`, `test/unit.mjs`, `.claude-plugin/plugin.json`, `AGENTS.md`), delete `scripts/dispatch_rule.py` and `scripts/test_dispatch_rule.py`, reload the plugin.
Quick off without restoring: set `guardDispatchRule` false in the plugin config (hook) and `ORCH_DISPATCH_HOOK=off` in the environment (CLI).

That is firstmate's contract too: natural-language rules stay with the first mate, scripts only ever receive concrete axes.

## Asking Jev: `dispatch --pick`

    py "$ORCH" dispatch --pick "<task text>"

Opt-in.
It asks Jev (TypeSafe's decision model, `scripts/jev_pick.py`) which rule fits, and prints one line.
On a clear answer: `rule=3 confidence=0.82`.
Under 0.6, or on a timeout (5 s) or any error: `ambiguous (...)`, and the seat decides as today.
Off: `jev off: ...`, and no network call is made.
Jev never passes `--rule` for you.
The seat still passes it, and the G-10 hook checks it as before.

On only when `TYPESAFE_API_KEY` is set, or `AI_GATEWAY_API_KEY` plus `TYPESAFE_BASE` (the Vercel gateway, `https://ai-gateway.vercel.sh/typesafe`).
A `NAME=` prefix on the key is dropped, because the vault stores the whole line.
The key is never printed or logged.
Only the task text goes over the wire: no quota numbers, no secrets, no file contents.
Keep school and personal tasks out; a `--pick` is the seat's choice per task.

Every pick (not an off) adds one row to `state/dispatch-log.jsonl`:
`{"ts", "decision": "pick", "source": "cli", "caller", "sub": "pick", "picker": "jev", "status": "clear|ambiguous", "rule", "confidence", "backend", "tokens", "task_chars"}`.
The row keeps the task length, not its text.

Accuracy is low (2026-10-09, 20 real task rows against the seat's rule): 7 of 20 match, and 3 of the 8 picks at or over 0.6.
Cost is about $0.004 per 100 picks.
Treat a clear answer as a hint to check, not as the answer.
Replay it yourself: `py scripts/jev_pick.py replay -n 20 [--short]`.

## Effort

One dial, three spellings, mapped by `orch.py`:

| Harness | Flag on the command line |
|---|---|
| claude | `--effort <level>` |
| pi | `--thinking <level>` |
| codex | `-c model_reasoning_effort="<level>"` |

Levels: `low`, `medium`, `high`, `xhigh`.
`max` is refused on purpose.
Kun measured Sonnet 5 on max costing more than Fable because a model that is not smart enough for the task burns cycles instead of solving it, and Ryan's rule is `xhigh` only for a genuinely hard one.
No `--effort` means the harness default (claude: the session's `effortLevel`, pi: the model's default thinking).

## Headroom

`quota_headroom()` reads `quota.collect()` rows:

- `claude` = the **active** account's weekly-left (that is the login a spawned pane inherits), else the first non-ambient claude row.
- `pi` = the `codex` row, because a pi pane here runs the ChatGPT plan (pi-panes.md). Anthropic models never run on pi.
- A harness with no reading sorts last, never first.

## Review on pi

`review --harness pi` spawns the reviewer as a pi pane.
It has no `SendMessage`, so its brief says: write the report file, end with `DONE`.
The orchestrator reads `handoffs/review-<lead>-<stamp>.md` and relays the verdict to the lead.
Everything else in the brief (use the feature the way Ryan would, own worktree, never touch the shared checkout) is unchanged.

## Editing the table

Ryan does not edit it by hand.
When he states a preference ("always use X for Y"), the orchestrator adds or rewrites one rule, with the date and his words in `why`, and commits it in claude-config.
A rule that turns out wrong is rewritten or removed the same way.
Tests: `scripts/test_dispatch.py`, `scripts/test_dispatch_rule.py`.
