# Traps

- **A cwd claude has never opened on this machine shows a "Do you trust this folder?" dialog**
  before anything else, and `herdr agent start` reports `agent_not_ready`. Three WebApp panes
  stalled on it 2026-09-03. `orch.py spawn` now reads the pane, picks "Yes, I trust this folder",
  waits for the agent, and prompts by pane id. For a pane started by hand: Down, Enter.
- **A nested repo asks "Allow external CLAUDE.md file imports?"** once per project, because its
  CLAUDE.md reaches the vault's `@AGENTS.md`, which sits outside that repo. Unanswered, herdr says
  `agent_not_ready` and the spawn is abandoned (sc-reactionbox, and family-slack twice, 2026-09-23).
  `orch.py spawn` answers Yes only when every listed import is under `~/Vault` or `~/.claude`.
- **Shell quoting eats backticks in `--goal`.** `` `orchestrator` `` inside a double-quoted goal ran
  as a command and vanished from four task files. Write session names bare, or use the task file.
- **Two panes can share the `orchestrator` tab and label.** `orchestrator_sid()` prefers the
  pane whose session title matches the tab and skips any pane labelled `orch-*`. Before that, four
  task files told builders to report to the wrong seat.
- **`--allow-dangerously-skip-permissions` only PERMITS bypass; `--dangerously-skip-permissions`
  enters it.** A pane started with the wrong one comes up in default mode and stalls silently on the
  first "Do you want to proceed?". `orch.py spawn` passes the right flag and confirms bypass within
  10 s. For a pane started by hand, or one that drifted: `py ".../scripts/fix_mode.py" <pane-id>`
  (escapes the teach dialog, accepts a pending prompt, then shift+tab until the status line says
  bypass). `py ".../scripts/auto_accept.py" <pane-id> ...` keeps pressing enter on prompts in the
  background - a deny rule still prompts inside bypass.
- **A pane started by hand with NEITHER bypass flag can never reach bypass**: shift+tab only cycles
  default -> auto -> plan there, so `fix_mode.py` comes back with "plan mode on" (app-week-rail,
  2026-09-07, respawned by a lead by hand). Respawn it through `orch.py spawn`, or leave it in auto
  with `auto_accept.py <pane>` armed. To pick a numbered choice on a parked prompt, send the digit
  and Enter as TWO calls: `herdr agent send-keys <pane> "2"` then `... "Enter"` - the digit alone
  does not commit, and the failure looks exactly like the tool doing nothing.
- **An auto-update can flip `permissions.defaultMode` to `auto` underneath you.** Every pane spawned
  after that comes up in auto and stalls on its first classifier hit. `watch_sessions.py` reports
  `MODE auto`, `doctor` checks both settings files, and the installer offers to set
  `DISABLE_AUTOUPDATER=1`.
- **`cd X && ...` in the Bash tool trips a deny rule** and parks the pane on a permission prompt even
  in bypass. Use absolute paths; the cwd persists anyway.
- **Use the pane id as the herdr target**, not a name. `herdr agent prompt|read <target>` takes both,
  but a pane started by hand has no agent name - only the pane id always resolves.
- **`herdr agent start --kind codex` fails on Windows.** herdr launches
  `Start-Process -FilePath codex`, and `codex` on PATH is an npm `.cmd`/`.ps1` shim, so it dies with
  "%1 is not a valid Win32 application". `orch.py` tries `agent start` first and falls back to
  typing the command into the pane shell; herdr still detects `agent: codex` on the pane either way.
- **Codex auto-updates on launch, prints "Please restart Codex" and EXITS to the shell** - and herdr
  still reports a live idle `codex` agent on that pane, so spawn called it a success and the prompt
  went nowhere (2026-09-06). `_codex_ready` now waits for codex's own composer line *below* the last
  update notice, and `_codex_open` relaunches once when it sees the notice.
- **`herdr pane close` leaves the codex process running**, and it keeps the thread's writer lock, so
  a later `codex resume <id>` dies with `thread <id> already has an active writer (code -32600)`.
  Spawn records the pids it started in the sidecar; `resume` and `kill` stop that tree first.
- **Codex writes its rollout file on the FIRST TURN, not when the TUI draws** - so the thread id can
  only be read after `agent prompt`, never before. Match on `thread_source: user`: the same folder
  also holds subagent and Desktop threads, and `codex resume --last` would pick whichever is newest
  across the whole box, not this pane's.
- **Codex opens with dialogs and the first prompt is swallowed by them.** "Do you trust the
  contents of this directory?" takes Enter; the hooks-review dialog takes Escape (which closes it
  without trusting anything). `herdr agent prompt` returns `agent_prompted` anyway and the text
  disappears, so `orch.py` clears both before it prompts. The hooks dialog was "press t to trust
  all" in 0.153.2 and a numbered menu headed "Hooks need review" in 0.153.4 - `CODEX_HOOKS_MARKS`
  matches both, and any new wording has to be added there or the pane hangs on it.
- **A herdr agent NAME is not the pane LABEL, and `agent start` refuses a name a live agent still
  owns (`agent_name_taken`).** A pane renamed `orch-retiring` kept the agent name
  `orchestrator`, so six spawns on 2026-09-07 died as
  `claude did not come up in pane w3:p78: None` (herdr's error was on stderr and `herdr()` dropped
  it), each leaving a bare shell pane and an overflow tab `orchestrator-2/-3/-4`. Now: `herdr()`
  reads stderr JSON too, and `spawn` picks `<label>-2` when the name is taken and closes the pane
  on any refusal instead of waiting 120 s on the trust dialog. Renaming a pane is not enough -
  `herdr agent rename <pane> <newname>` has to run too.
- **A freshly spawned session's `~/.claude/sessions/<pid>.json` is briefly written without
  name/status/sessionId.** `orchlib.sessions()` skips those instead of raising; never index that dict
  with `[]` in new code. The visible side of this: `ls` run within the first seconds of a spawn
  omits the new pane - a FALSE NEGATIVE, and the natural response (spawn again) puts two panes on
  one branch, which is worse than the missing pane. app-lead-4 nearly did this 2026-09-07 18:05.
  Give `ls` a few seconds after a spawn, and look twice before ever re-spawning a name.
- Registry `status` lags. `busy` means a turn is running.
- `herdr agent prompt --wait --until idle` is wrong; wait on `done` and `blocked`.
- `tail_session.py` must match `.jsonl`, not the same-named subagent directory - the filter is deliberate.
- In the Bash tool `taskkill /PID` gets path-mangled; use `powershell Stop-Process -Id` or let
  `orch.py kill` do it (it picks `kill` on posix).
- `Monitor` scripts must `flush=True` every print or events sit in the buffer.
- **Never write `board/state.json` in place** (`open(p, "w")` from an inline script). Three
  writers share it - the watcher, the hourly prune, any pane's `board.py` - and an in-place write
  racing an atomic one left "Extra data" after the JSON on 2026-09-06 23:14, which killed the
  watcher. Go through `board.py` (its `save()` writes to `.tmp` and `_replace`s with a retry), or
  copy that pattern.
- Never fold `git add -A` into a scripted commit; a working tree with unrelated dirt sweeps into it.
- Mail from an address to itself does not push a phone notification - the provider treats it as a
  note to self. For anything that must be seen away from the desk, use a push channel or a different
  sending address.
- **`L.tabs()`/`L.panes()` list every workspace, not just the caller's.** Since each domain mate got
  its own herdr workspace (2026-09-23), a `--group` tab search that does not filter by
  `workspace_id` can match a same-named tab living in a DIFFERENT mate's space. `_new_pane` now
  scopes that search (and the `<group>-N` overflow) to the resolved target workspace; any new code
  walking `L.tabs()`/`L.panes()` for a specific pane's siblings must filter by `workspace_id` too.
- **Moving a pane across workspaces changes its pane id.** herdr's own agent-guide: "A pane moved
  into another workspace receives a new workspace-qualified pane ID." `show` used to key its
  `shown.json` row on the PRE-move id; `hide` then looked the pane up fresh (by name/label, so it
  found the new id) and never matched a saved home, rebuilding "parked" instead of sending it back.
  Fixed by reading `.result.move_result.pane.pane_id` off the move response and keying on THAT.
- **`herdr workspace create` always makes a root pane** for its first tab (agent-guide:
  "`workspace create` returns `.result.workspace`, `.result.tab`, and `.result.root_pane`"). A
  caller that then does its own `tab create`/`pane split` for the real work leaves that root pane
  behind as a stray empty shell. `spawn`'s domain-mate path reuses it directly instead
  (`_new_pane(..., reuse_pane=...)`); `space`, which is moving EXISTING panes into a fresh
  workspace and has no new agent to put there, closes it instead.
