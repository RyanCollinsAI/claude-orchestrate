# Memory per session (measured 2026-10-05)

Free RAM fell to 4 GB of 62 GB with 45 claude sessions up.
Each claude.exe is about 0.45 GB.
Its helper servers add about 0.47 GB more, so one full session costs about 0.9 GB.

Helpers measured under 18 sessions (8.5 GB of descendants):

| Helper | Procs | MB | Source |
|---|---|---|---|
| playwright (npx + node + cmd) | 72 | 3350 | plugin `playwright@claude-plugins-official` |
| context7 (npx + node + cmd) | 52 | 2370 | user MCP in `~/.claude.json` |
| review-dashboard `mcp_server.py` | 9 | 826 | `Vault\.mcp.json` |
| baf-data `mcp-prototype\server.py` | 7 | 635 | `Vault\.mcp.json` |
| conhost for the above | 59 | 390 | Windows |

A short worker never calls any of these.

## The rule

- `orch.py spawn` and `orch.py task` take `--mcp full|min`.
- Default is `min` for workers: `--strict-mcp-config --mcp-config orchestrate\mcp-min.json` (an empty server list).
- Default is `full` for `--lead` and `--domain` mates.
- Pass `--mcp full` to a worker that needs Playwright, Vercel, context7 or the review dashboard.
- Spawn refuses when free RAM is under 6 GB.
  Set `ORCH_MIN_FREE_GB` to change the floor for one call.

## Not orphans

- `poll_keeper.ps1` (11 pwsh, about 0.8 GB) keeps Bridge pages polled.
  Kill one only after you check its page is closed.
- `review-dashboard\server.py` is one 1.2 GB process with no claude parent.
  It is Ryan's server, not an orphan.
