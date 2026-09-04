# `board/state.json`

The board's only source of truth.
`board.py render` turns this into `board.html`; the page re-reads this file every 5 s and re-renders in the browser.
Never hand-edit `board.html` - it is overwritten by the next `board` command.

Written atomically (`.tmp` then `os.replace`) because the page polls the same file.

```json
{
  "updated": "2026-09-03T17:05:00-07:00",
  "header": {"usage": "5h 50%, resets 6:00 PM", "note": ""},
  "questions": [...],
  "show": [...],
  "sessions": [...],
  "done": [...]
}
```

## `updated`

Local ISO timestamp with offset, rewritten on every save.
The browser compares this string to decide whether to re-render, so nothing else has to change for an update to land.

## `questions[]`

One thing your human has to answer. Everything else on the board is read-only.

| field | type | meaning |
|---|---|---|
| `id` | `"q5"` | lowercase `q` + a number. Shown uppercase (`Q5`) and used as the Lavish `queueKey`. |
| `title` | string | one line, the question itself |
| `from` | string | the pane that asked, or `""` when the orchestrator wrote it |
| `context_md` | markdown | **the real thing**, never a summary of it - see the rule below |
| `options[]` | `{"value": "A", "label": "..."}` | renders as radios. Empty = free-text answer. |
| `pick` | `"A"` | the recommended option; that radio comes pre-checked and outlined |
| `pick_why` | string | one line under the options saying why |
| `inputs[]` | `{"name": "a", "label": "a =", "width": 70}` | small text boxes instead of radios |
| `group` | string | which tab this lands on; `""` shows only in **All**. See "Tabs" below. |
| `created` | ISO | |
| `answered` | ISO or `null` | non-null hides it from "Needs you" |
| `answer` | string or `null` | what they said |

Options and inputs are exclusive in practice: options make radios, inputs make text boxes, neither makes one textarea.

### Tabs (set 2026-09-04)

Every item in `questions[]`, `show[]`, `sessions[]` and `done[]` carries a `group`: the herdr tab
label the item's session lives in, the same string `--group` filed the pane under at spawn time
(`web-app`, `ideas-pipeline`, `video-pipeline`, `routines`, `orchestrator`, ...). The board builds an **All**
tab plus one tab per distinct `group` it finds, and filters **Show**, **Sessions** and **Done** by
whichever tab is selected. `""` (no live pane resolved, or none given) only ever shows under **All**.

**Needs you never filters by tab.** Every open question shows there regardless of the selected tab,
each carrying its own group as a small badge, so a question can never hide in a tab nobody opened.

`add-question`, `show` and `done` take an explicit `--group`; when it is left off, `board.py` looks
up the pane named by `--from` (or the session `show --for`'s question belongs to) through herdr and
uses its tab, falling back to `""`. `sessions --from-ls` and `session <pane>` always derive `group`
from the live pane, since a session row has one by definition.

### The context rule (set 2026-09-03)

> "This needs to be like give the full context of the question here."

`context_md` carries the actual artefact, not a description of it:

- ` ```mermaid ` fences become `<pre class="mermaid">` and render as diagrams
- `$$ ... $$` is left alone for KaTeX auto-render. Inline math is `\( ... \)`. A single `$` is **deliberately not** a math delimiter: money comes up constantly, and single-`$` delimiters turn the text between two dollar amounts into an equation.
- `![alt](file.png)` - the file is **copied into `board/`** by `add-question` and the link rewritten to the bare filename. Lavish serves the HTML's own folder; a leading `/` never resolves.
- other fences become `<pre class="code">`
- GFM tables render as tables

## `show[]`

Something to look at, no answer wanted.

| field | meaning |
|---|---|
| `id` | `"s3"` |
| `caption` | the line above the block: why he is looking at this |
| `body_md` | same markdown rules as `context_md` |
| `for` | question id this supports, or `""` |
| `group` | which tab this lands on; default is the `for` question's group. See "Tabs" above. |
| `created` | ISO |

Newest first.

## `sessions[]`

The table of what every other pane is doing. `board sessions --from-ls` rebuilds it from the live registry through `orchlib`, so it is normally not hand-written.

| field | meaning |
|---|---|
| `pane` | herdr pane label, else the session display name |
| `doing` | terminal title, else the last thing it said |
| `model` | short model name from the log |
| `state` | `working` \| `waiting` \| `done` \| `error` - drives the pill colour |
| `note` | context size, `<-- ROTATE` at 400k |
| `group` | the pane's herdr tab. See "Tabs" above. |

`state` comes from `orchlib.classify()`: `QUESTION`/`OFFER` -> `waiting`, `ERROR` -> `error`, `DONE` -> `done`, and a registry status of `busy` always wins as `working`.

## `updates[]` (set 2026-09-04)

`{"ts": ISO, "pane": "...", "kind": "DONE"|"QUESTION"|"BLOCKED"|"ERROR"|"REPORT", "text": "...", "group": "..."}`,
newest first, capped at 50. The live event feed - `watch_sessions.py` writes one entry per stop-reason
line and report-file landing through `board.sync_board(new_updates=[...])`, so it shows up on the board
without anyone running a `board` command by hand. Not for human-curated milestones; that's `done[]`.

## `next_qid`

Integer, next question id to hand out. Monotonic - `next_qid()` only ever increases it, even past an
id `prune` later removes from `questions[]`, so two different questions can never carry the same
small number within one board's lifetime.

## `done[]`

`{"ts": ISO, "text": "...", "group": "..."}`, newest first.
`board answer` appends one automatically, inheriting the answered question's `group`.
`board prune --days 1` drops lines and answered questions older than that.
