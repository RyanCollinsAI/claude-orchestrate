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
| `created` | ISO | |
| `answered` | ISO or `null` | non-null hides it from "Needs you" |
| `answer` | string or `null` | what they said |

Options and inputs are exclusive in practice: options make radios, inputs make text boxes, neither makes one textarea.

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

`state` comes from `orchlib.classify()`: `QUESTION`/`OFFER` -> `waiting`, `ERROR` -> `error`, `DONE` -> `done`, and a registry status of `busy` always wins as `working`.

## `done[]`

`{"ts": ISO, "text": "..."}`, newest first.
`board answer` appends one automatically.
`board prune --days 1` drops lines and answered questions older than that.
