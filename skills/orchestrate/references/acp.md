# ACP panes (A1 claude, A2 codex + pi, 2026-10-09)

`orch.py spawn|task --acp` runs a claude, codex or pi pane as a protocol child instead of a screen.
The client is picked by `--kind` (`--harness`): claude-agent-acp for claude, codex-acp for codex, `pi --mode rpc` for pi.
All three go through the same `acp_pane.py`, the same state file, the same inbox, the same policy file and the same kill switch.
The default is still the send-text path: no `--acp`, no change.
ACP is Agent Client Protocol: the agent runs as a child program and we exchange JSON lines on its stdin and stdout.
A prompt is a line in.
Progress is lines out.
"May I run this?" is a line out that we answer with a line in.
Nothing types into a terminal and nothing reads one back.
The design and its measurements are `acp/DESIGN.md` (A0, reviewed SHIP).
This page is the pins, the commands, the policy, the kill switch and the rollback.

## Status

Opt-in per pane, behind a flag.
The default path is unchanged: no flag, no ACP.
No existing pane and not the seat is ACP.
A2 built the codex and pi clients behind the same flag and did not flip a default: the claude canary has 3 tasks to complete first (A2 ran tasks 2 and 3; see the end of this page).
Login is subscription only (decision D2): Claude Max for claude, ChatGPT for codex, and for pi whatever `pi` is logged in to, with an Anthropic model refused.
There is no API key anywhere in this path.

## Pinned versions

| Thing | Exact version | Where it is pinned |
|---|---|---|
| `@agentclientprotocol/claude-agent-acp` | **0.88.0** | `acp/package.json` and `acp/package-lock.json`; install `npm ci --omit=optional` |
| `@agentclientprotocol/codex-acp` | **2.1.1** (Apache-2.0) | `acp/codex/package.json` and `acp/codex/package-lock.json`; install `npm ci --omit=optional` in `acp/codex`. A separate folder, so the claude install is untouched |
| codex native binary | the global npm install's `codex.exe` (codex-cli 0.154.0 here) | `CODEX_PATH`, set by `acp_codex.child_env`; the adapter's bundled copy is not fetched |
| `pi` | the global npm install (0.87.0 here), run as `node …\pi-coding-agent\dist\bundle\cli.js --mode rpc` | `acp_pi.pi_command`; nothing to install |
| `@agentclientprotocol/sdk` (transitive) | 1.7.0 | lockfile |
| `@anthropic-ai/claude-agent-sdk` (transitive) | 0.3.293 | lockfile |
| ACP protocol | 1 | `acp_client.PROTOCOL` |
| node | >= 22 (24.13.0 here) | checked at start by `acp_pane.py` |
| claude | 2.1.295 at `C:\Users\YOURNAME\.local\bin\claude.exe` | `CLAUDE_CODE_EXECUTABLE`, set by `child_env` |
| Python | 3.14 (`py`), standard library only | none needed |
| `agent-client-protocol` (PyPI) | **0.12.1** is the version evaluated; **not installed and not imported** | nothing |

The adapter ships several versions a week.
A bump is deliberate: change `acp/package.json`, run `npm ci --omit=optional`, run `py acp/spike.py` and the real-adapter run below, commit the lockfile.
Never `npx -y`: npx resolves whatever is newest on a bad day.
The Python SDK is asyncio based and adds a dependency for five methods that fit in `acp_client.py`.
If it is ever adopted, pin `agent-client-protocol==0.12.1` in a requirements file the same day and delete this sentence.
That is a deviation from the A1 brief ("the Python package at an exact version"), taken from DESIGN section 6, which the review passed.

## Files

| File | What it is |
|---|---|
| `scripts/acp_client.py` | the wire: JSON-RPC over the child's stdio, the Windows job object, the D2 guard, the permission policy (`decide`) |
| `scripts/acp_pane.py` | one pane's life: start, prompt, cancel, resume, tee, state file, board asks. `--harness claude|codex|pi` |
| `scripts/acp_codex.py` | codex-acp: where the adapter and `codex.exe` are, the env (no API key), the session dials (mode, model, reasoning effort) |
| `scripts/acp_pi.py` | `PiRpcClient`: pi's own RPC records dressed in the same surface, plus the extension-dialog bridge |
| `scripts/fake_acp_agent.py` | a Python stub that speaks ACP (`FAKE_FLAVOR=codex` makes it look like codex-acp), for tests |
| `scripts/fake_pi_rpc.py` | a Python stub that speaks `pi --mode rpc`, for tests |
| `scripts/test_acp.py`, `scripts/test_acp_harnesses.py` | the tests |
| `board/acp-policy.json` | the permission policy; a missing or broken file falls back to the same defaults in code |
| `board/acp.off` | the kill switch (a file; its text is the reason) |
| `state/acp/state/<key>.json` | what `ls` reads: state, word, sessionId, ctx_k, last_message, open_ask |
| `state/acp/log/<key>.log` | the tee: what the pane's screen shows; `ACP_WIRE_LOG=1` adds `<key>.wire.log` |
| `state/acp/inbox/<key>/` | `*.md` one prompt per file, `*.cancel`, `stop` |
| `state/acp/prompts/<key>.md` | the spawn prompt |
| `acp/acp-pane-real-run.txt` | proof: a real-adapter run (hook fires, policy allows, board ask answered) |

`<key>` is the pane id with `:` turned into `-` (`w3:pK3` is `w3-pK3`).
`board/agent-panes.json` gets one row per ACP pane with `kind: "acp"`; that row is what marks a pane as ACP.

## Commands

    py orch.py spawn NAME @prompt.md --rule N --model sonnet --cwd DIR --worktree --acp
    py orch.py task  NAME --goal "..." --done "..." --rule N --model sonnet --cwd DIR --worktree --acp
    py orch.py prompt NAME "text"            # an ACP pane: an inbox file, never the screen
    py orch.py prompt NAME --cancel          # the Escape key: ends the open turn, the pane stays up
    py orch.py ls --format text              # HARNESS column says acp; STATUS, CTX and KIND come from the state file
    py orch.py kill NAME                     # closes the pane; the job object takes the adapter and claude with it

    py orch.py task  NAME --goal "..." --done "..." --rule N --kind codex --model gpt-6-astra --effort high --cwd DIR --worktree --acp
    py orch.py task  NAME --goal "..." --done "..." --rule N --kind pi --model gpt-6-astra --effort high --cwd DIR --worktree --acp

`--acp` runs claude, codex and pi, and is never a domain mate (a mate arms Monitors, which need the screen path).
grok, agy and cursor keep their own path.
Everything else a pane does is unchanged for claude: the report still arrives by `SendMessage` (the child has it) and the task file's Done/Proof/Next.
A codex or pi ACP pane reports the way its TUI pane does (a report file, `DONE` on the last line), and its client also tells the owner through `orch.py prompt <owner>` when a turn ends, because no watcher reads an ACP pane's screen.

## The three clients

| | claude | codex | pi |
|---|---|---|---|
| child | `claude-agent-acp` (node) | `codex-acp` (node) | `pi --mode rpc` |
| wire | ACP, JSON-RPC | ACP, JSON-RPC (the same `AcpClient`) | pi's records: `{"type":"prompt"}` in, `{"type":"message_update"}` out (`PiRpcClient`) |
| session mode | `acceptEdits` | `workspace-write` (edits the workspace; asks before writing outside it or using the network) | none |
| model, effort | `session/set_config_option` `model`, `effort` | `model`, `reasoning_effort` | `--model`, `--thinking` on the command line; `set_thinking_level` |
| login check | adapter says `kind: account` | adapter says `kind: account` (ChatGPT). A logged-out codex stops the pane; the client never calls `authenticate`, which would open a browser | pi has none to read; an Anthropic provider is refused |
| permission question | `session/request_permission`, answered by `decide` | the same; option ids are `allow_once`, `allow_for_session`, `decline`, `cancel`, picked by kind | an extension dialog: `select` or `confirm` |
| turn ends at | `session/prompt` answers | `session/prompt` answers | `agent_settled` (after retries and compaction), not `agent_end` |
| cancel | `session/cancel` | `session/cancel` | `abort` |
| resume | `session/load <sessionId>` | `session/load <sessionId>` | `switch_session <sessionFile>` (the state file keeps `sessionFile`) |

Pi never asks before a tool runs, so the protocol cannot gate a pi tool call and this client does not pretend to.
The only questions pi raises are extension dialogs.
A `select` dialog's options are mapped to yes and no by the `dialog_allow` and `dialog_reject` regexes in the policy file.
The dialog text goes through the same deny and Ryan-only lists as a command, so a dialog that asks about sending mail becomes a board ask.
An option that means neither (`main`, `dev`) is never picked: the dialog is cancelled.
A `confirm` dialog is yes or no by the same rules.
`input` and `editor` dialogs are cancelled, because nobody types at that pane.
Codex and pi ACP panes each answered a nonce live on 2026-10-10 (gpt-5.6-luna, effort high): transcript `acp/acp-live-proof-2026-10-10.txt`.
Longer codex work on Windows is still a canary of its own.

## How the pane runs

1. `_spawn_acp` opens a normal herdr pane, writes the sidecar row, writes the prompt to a file, and types one line into the pane: `py -u acp_pane.py --label ... --prompt-file ...`.
2. `acp_pane.py` refuses to start if `ANTHROPIC_API_KEY` or `ANTHROPIC_AUTH_TOKEN` is set.
3. It starts `node <acp>/node_modules/@agentclientprotocol/claude-agent-acp/dist/index.js` inside a Windows job object with KILL_ON_JOB_CLOSE, so a dead client never leaves a claude running.
4. `initialize` advertises no client file system and no client terminal, so Claude Code uses its own tools and every hook sees every call, exactly like a normal pane.
5. It waits for the adapter's `_auth/status_update` and stops unless `kind` is `account` (the Claude subscription).
6. `session/new` carries the pane label as `claude --name`, then `session/set_mode acceptEdits`, then `model` and `effort` through `session/set_config_option`.
7. The loop sends prompts as `session/prompt`, queues files that arrive mid-turn, sends `session/cancel` on a cancel file, and answers permission requests.

`bypassPermissions` is deliberately not used.
In bypass Claude Code approves before the callback, so there would be nothing to route.
`acceptEdits` approves edits in the cwd and asks for the rest, and the policy decides those.

## Permission policy (`board/acp-policy.json`)

A request is decided in this order:

1. Hooks already ran. A command a hook blocks never reaches the permission check (measured).
2. `deny_commands`: force push, `reset --hard`, `clean -f`, Chrome cookies. Answered `reject_once` at once.
3. `ask_commands`, `delete_commands`, `ask_paths`: the Ryan-only list from `approval-policy.md` (money, deletes, anything sent to other people, credentials, publishing, a school submission). The client calls `asks.add(...)` with the command as `--action`, sets the state to `question`, and waits.
   Ryan's pick on the board is read back from `board/asks.json` by ask id: "Allow once" answers `allow_once`, anything else answers `reject_once`.
   The board's reply prompt (`[answer to q-...]`) is swallowed by the client, not sent to the model.
4. Reads and searches: allowed.
5. Writes: allowed inside the pane's cwd and `write_roots`; anything else is a board ask. A recursive delete is a board ask unless every target is a plain relative path.
6. Everything else: `default` (allow). This is what a bypass pane allows today; the policy only subtracts.

Options are chosen by `kind`, never by id, and never `allow_always` (that writes a permission rule into Ryan's settings for good).
Nothing waits forever: an unanswered ask is rejected after `ask_timeout_s` (30 min), the turn goes on, and the state keeps `QUESTION` until the next turn.
Shutdown or a cancel answers every open request `cancelled`.

## Kill switch

    echo "adapter 0.88.1 broke permission requests" > board/acp.off     # spawn --acp now refuses and prints this
    del board\acp.off                                                    # allow ACP spawns again

The switch stops NEW ACP panes of all three harnesses (claude, codex, pi) with the same message.
A live ACP pane that misbehaves: `py orch.py kill NAME`, then respawn without `--acp` from the same task file.

## Rollback

1. Stop using the flag: spawn without `--acp`. Every old path is intact, so this alone is a full rollback for new work.
2. Kill any live ACP pane: `py orch.py kill NAME`.
3. To remove the code: copy the backups over the edited files and restart one pane.
   The merge made `scripts/orch.py.bak-<date>` and `scripts/orchlib.py.bak-<date>` beside the originals (house pattern; A1 made `.bak-2026-10-09-a1` copies in its worktree before the first edit).
   Copy `orch.py.bak-<date>` over `orch.py` and `orchlib.py.bak-<date>` over `orchlib.py`.
   The new files (`acp_client.py`, `acp_pane.py`, `fake_acp_agent.py`, `test_acp.py`, `board/acp-policy.json`) are inert once nothing imports them.
4. `acp/` (the pinned adapter) stays in place; it is additive.

## Proof

- `py -m pytest test_acp.py -q`: the wire, the policy, a pane's whole life against a Python stub, and the orch.py routing.
- `acp/acp-pane-real-run.txt`: a real claude-agent-acp run through `acp_pane.py` on the subscription (`auth: account/Claude Max`). `git checkout -- <file>` was blocked by `block-known-traps` with `"mode":"acceptEdits"` in the hook log (hooks fire in the ACP child). A routine command was allowed by policy. A command on the Ryan-only list became board ask `q-...`, was answered "Allow once", and ran.

## Probes from DESIGN section 9

| # | Question | Answer |
|---|---|---|
| 1 | `extraArgs: {name}` reaches `claude --name` | Yes. The registry row carries the pane label. |
| 2 | `session/load` continues the transcript | Yes. After a restart the session recalled a code word given before it. The replay is not echoed to the pane. |
| 3 | `HERDR_*` in the child env makes herdr mark the pane `agent: claude` with the child's session id | **No** (measured live by the A1 reviewer). herdr does not link the child's session to the pane, so the child's registry row has pane `-`. `ls` therefore matches the registry row to the pane by the session id in the client's state file. The client's own `report-agent` stays off unless `ACP_HERDR_REPORT=1`; turn it on in the canary if the herdr sidebar should show the pane's state. |
| 4 | `CLAUDE_CODE_OAUTH_TOKEN` still reports `kind: account` | **No.** The adapter reports `kind: none`, "Not logged in", for a per-process token, although the login works (a real haiku turn ran on it). `require_subscription` therefore passes `none` only when the env carries an `sk-ant-oat` token and no API key variable. Any other `none`, and every `api_key`, `gateway` and `external`, is refused. |
| 5 | A job object around the adapter | Done (`KillOnCloseJob`). A test kills the client with no `/T` and the adapter dies with it. |
| 6 | Advertise elicitation so AskUserQuestion exists | Not done. A pane that needs a pick runs `orch.py ask`, which is proven. |
| 7 | `tool None:` lines in the tee | A `tool_call_update` with no status and no content is skipped. |

## Known gaps

- Done by I3: `watch_sessions.py`, `restart_idle.py` and `restart_pane.py` skip `kind: acp` rows (they type into a terminal, and an ACP pane's terminal is the client).
  The ACP resume is `py acp_pane.py ... --resume <sessionId>` (`session/load`), or for pi `--resume <sessionFile>`. Nothing calls it yet.
- Open: `orch_watchdog.py` still reads the screen. For an ACP pane the state file (`L.acp_state`) is the better source for STALLED and the last words.
- A2: a codex or pi ACP pane is left out of `L.other_panes()`, so the screen-based watchers never see it. The cost: no `REPORT |` watcher line for it. The client's `tell_owner` replaces that.
- Open: pi tool calls cannot be gated (pi never asks). Codex and pi ran one nonce turn live (2026-10-10); longer runs are canary items.
- Done by A3 (2026-10-10): `L.panes_by_sid()` now maps an ACP child's sessionId (from the client's state file) to its pane, so its callers see ACP panes. A caller that types into a pane must still skip `L.acp_pane_ids()`.
- The `acp-policy.json` regexes were written for shell commands. A pi dialog is matched on its title plus its message, so a dialog worded in a way the regexes do not know is allowed, as a claude command nobody listed is.

## Canary

Run only after the lead merges.
One real small task on an `--acp` pane, its report arriving by the normal path, the transcript path in the report.
A pane that completes 3 such tasks cleanly is what A2 needs.
Watch `state/acp/log/<key>.log`, `board/asks.json`, `py orch.py ls --format text`, and the hook log for `"mode":"acceptEdits"` blocks.
