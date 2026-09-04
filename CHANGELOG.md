# Changelog

## 0.2.0 - 2026-09-03

First public release. Everything below was built and proven on one machine over one long day, then
made portable.

### Added

- **`orch.py resume`** - re-arms everything after the orchestrator's own process restarts: the exact
  `Monitor(...)` lines to run, the board's open questions, dead sessions to respawn, panes needing a
  keypress, and sessions sitting idle mid-task. Changes nothing by itself.
- **`orch.py doctor`** - one red/green line per moving part: herdr's API socket, the peer-message
  pipes, `claude auth status`, an API ping, both settings files' `permissions.defaultMode`,
  `DISABLE_AUTOUPDATER`, and the real *used* percentage from the usage feed.
- **`orch.py chrome take|free|who`** - a single-driver lock on the shared browser, held as a file
  both sides can read rather than a message someone has to remember to send.
- **`BLOCKED`** as a stop reason. A session halted by the permission classifier becomes a board
  question carrying the exact one-line command to paste, pulled out of the session's own fenced
  code block.
- **Permission-mode drift detection.** `watch_sessions.py` reports a pane sitting in `auto` when
  bypass was expected, and escapes the "Teach auto mode about your environment?" dialog before
  reporting it.
- **`config.json`** - `default_cwd`, `session_prefix`, `orchestrator_tab`, `accounts_tool`,
  `rotate_at_k`, `digest_repos`, plus the four directory overrides. Every key optional, every one
  overridable for a single run with `ORCH_<KEY>`.
- **`install.ps1` / `install.sh`** - copy the skill, write a `config.json`, report which
  dependencies are present and what still works without the missing ones, and offer the PreToolUse
  hook and `DISABLE_AUTOUPDATER=1`. An existing `config.json` is never overwritten; an existing
  hooks array is appended to, never replaced; `settings.json` is backed up first.
- **`hooks/block-known-traps.py`** - the five checks this skill relies on, with the reason for each
  written into the file.
- **`board/demo-state.json`** - a fake board you can render to see the look with no real data.

### Changed

- **The log directory is computed, not hardcoded.** `orchlib.project_slug()` builds Claude Code's
  own project-directory name from `default_cwd` (every `:`, `\` and `/` becomes a `-`), so the
  skill reads the right logs on any machine and for any project.
- **Accounts are optional and off by default.** With `accounts_tool` unset, every pane uses the
  ambient login and `orch.py account` says so.
- **Process kills pick their platform**: `Stop-Process` on Windows, `kill` elsewhere.
- `tail_session.py` and `recent_events.py` read their log directory from the config instead of a
  hardcoded path.
- SKILL.md gains section 3b (after a restart) and 4c (the browser lock), and the rule that anything
  outliving one turn is a pane, not an in-process subagent.

### Fixed

- `doctor` no longer calls an absent `permissions.defaultMode` a failure - an absent key simply does
  not override the mode.

## 0.1.0 - internal

`ls`, `spawn`, `task`, `rotate`, `rotate-self`, `reap`, `kill`, `show`/`hide`, the Orchestrator
Board, and the two Monitor scripts.
