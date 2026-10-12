# Files

Moved out of SKILL.md on 2026-09-10 (size budget) - a straight command/file reference, not a rule.

    config.json                what is machine-specific here (config.example.json documents it)
    scripts/orch.py            ls, path, spawn, task,
                               resume, doctor, chrome, reap, kill, show, hide, account, quota, board
    scripts/orchlib.py         shared: config, registry, herdr, log reading, the classifier, the lock
    scripts/acp_client.py      ACP wire + permission policy; acp_pane.py is one `--acp` pane's life
                               (references/acp.md: pins, policy, kill switch board/acp.off, rollback)
    scripts/quota.py           `orch.py quota`: text, JSON, or `--tui` view of every plan's headroom,
                               from quota-axi plus local Claude accounts (quota-axi cannot see those)
    scripts/board.py           the Orchestrator Board: state.json in, board-calm.html (default) and board.html (old) out
    scripts/board_watch.py     the Monitor script for answers on the board
    scripts/podium_watch.py    the Monitor script that polls every open Bridge (was Podium) page nobody else is polling (feedback is never left in a queue)
    scripts/watch_sessions.py  the Monitor script for the sessions
    scripts/fix_mode.py        force one pane into bypass
    scripts/auto_accept.py     keep accepting prompts in named panes
    scripts/tail_session.py    read a session's first and last words
    scripts/recent_events.py   everything a session did after a timestamp
    board/state.json           the board itself - the source of truth
    board/agent-panes.json     label, owner, model and report path for each non-claude pane
                               (codex and pi). For a pi pane this is the ONLY record it exists -
                               herdr never flags one as an agent; see references/pi-panes.md
    board/owners.json          who spawned each session; what `kill` and `reap` check
    board/schema.md            what every field in state.json means
    board/demo-state.json      a fake board; `board render` it to see the look with no real data
    herdr-plugin/herdr-plugin.toml  the `[[startup]]` hook that runs `orch.py space --order` once
                               herdr's server is up - inert until `herdr plugin link|enable`
    templates/handoff.md       the shape of a brief for a new pane on a new task
    templates/task.md          what `orch.py task` fills in
    references/history.md      the dated incident behind every rule in SKILL.md
