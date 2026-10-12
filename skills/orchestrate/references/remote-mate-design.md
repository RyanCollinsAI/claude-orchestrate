# Remote mate on the laptop: design (M0)

Status: design only, written 2026-10-09.
Sections 1 to 11 are the M0 design. Section 12, "Built in M1", lists what was built and where it differs.
The live laptop proof is parked until Ryan accepts the laptop step (section 9 and the steps in section 12).

## Contents

1. The idea, and options rejected
2. The SSH path (host, user, key, `~/.ssh/config`)
3. Where things live (board, handoffs, vault)
4. How reports and files come back (`scp`)
5. The `orch.py --host` shape (subcommands, `ls` merge, prompt delivery)
6. What herdr does on the laptop
7. Failure modes and what the seat sees
8. What stays impossible
9. The one laptop step for Ryan
10. Size estimate for M1
11. Proof: ssh round trip (parked)
12. Built in M1 (what exists, the Ryan steps, the parked proof)

## 1. The idea in one paragraph

A remote mate is a normal domain mate (SKILL.md 4d) whose herdr runs on the Dell XPS 15 instead of host.
It is a Claude Code session in the laptop's own herdr.
It has its own `~/.claude`, its own `sessions\`, its own `handoffs\`, and its own crew.
The seat on host reaches it with plain OpenSSH over Tailscale.
The seat runs the laptop's own `orch.py` over ssh, so no herdr protocol is rewritten on the seat side.
Files cross as whole files with `scp`.
Nothing else crosses.

This is the opposite of today.
Today the laptop is a thin client that remotes INTO host (`tools\herdr-anywhere.ps1`).
The new path is host into the laptop.
It needs sshd on the laptop and one key line.
That is the Ryan step in section 9.

### Why this shape, and what was rejected

| Option | Verdict | Why |
|---|---|---|
| Run the laptop's `orch.py` over ssh (chosen) | Pick | The laptop already gets `~/.claude` from claude-config (`laptop-abroad-setup.ps1` step 5), so the same `orch.py` is there. One script, no second protocol. |
| `herdr --remote <ssh-target>` | No | `tools\herdr-remote.ps1` records that it cannot target Windows. |
| Tailscale SSH | No | The Tailscale SSH server does not run on Windows (memory `hardware-laptop-dell-xps-15`). |
| Taildrive (Tailscale file share) | No | Needs a change in the Tailscale admin ACL. Tailscale settings are off limits. |
| rsync | No | Not on Windows by default. Needs MSYS or WSL. Whole-file `scp` is enough. |
| PowerShell remoting (WinRM) | No | Heavier than ssh and a second auth system. |
| ACP over ssh (`ssh laptop claude-agent-acp`) | Later | A1 already speaks ACP. Stdio over ssh gives a pane with no screen. Worth an M2 look, not M1. |
| First Mate's bash (Kun, 6,400 lines) | No | Needs macOS or Linux on the far side. Nothing is ported. |

## 2. The SSH path

Facts read from this box on 2026-10-09:

- Laptop Tailscale name: `laptop` (device name `laptop`).
- Tailscale IP: `100.x.y.z`.
- MagicDNS name: `your-host.your-tailnet.ts.net`.
- Key expiry: none set (disabled 2026-10-04).
- This box has OpenSSH 10.5, a key `~/.ssh/id_ed25519`, and no `~/.ssh/config` at all.
- Windows user on the laptop: expected `YOURNAME`.
  `laptop-abroad-setup.ps1` warns when the laptop user is not `YOURNAME`, so confirm it once.

Put this in `C:\Users\YOURNAME\.ssh\config` on host (M1 or Ryan adds it, not this task):

    Host laptop
        HostName 100.x.y.z
        User YOURNAME
        IdentityFile ~/.ssh/id_ed25519
        IdentitiesOnly yes
        BatchMode yes
        ConnectTimeout 8
        ServerAliveInterval 15
        ServerAliveCountMax 3
        StrictHostKeyChecking accept-new

Why each line:

- `HostName` is the IP, not the MagicDNS name, so a DNS hiccup is not a second way to fail.
  The IP changes only if the device is removed and re-added in Tailscale.
  That re-add also resets the key expiry to 180 days, so redo the expiry setting then.
- `BatchMode yes` means ssh never stops to ask for a password.
  A missing key fails at once with `Permission denied`, and the seat reads that text.
  No password is ever typed or stored.
- `ConnectTimeout 8` caps a dead laptop at 8 seconds, and `ls` gives a laptop that connects but hangs 10 more seconds, so `ls` never hangs the seat for long.
- `StrictHostKeyChecking accept-new` trusts the laptop on first contact only.
  A changed host key is refused, never auto-accepted.
- Do not set `ControlMaster`.
  Windows OpenSSH does not multiplex reliably.
  Each call is a fresh handshake, about a second, which is fine for `ls` and `prompt`.

The key is the one already on this box.
No new key is made.
The laptop needs the matching public line in its authorized keys (section 9).
On Windows the file depends on the account type: `%USERPROFILE%\.ssh\authorized_keys` for a normal user, `C:\ProgramData\ssh\administrators_authorized_keys` for an administrator.
host already works this way, so copy what host does.

Two Windows traps that host already taught us, both apply to the laptop:

- Windows sshd kills the logon session's processes when the connection drops (`tools\herdr-remote.ps1`).
  So herdr's server is never started from ssh.
  It starts at the laptop's own desktop logon through a scheduled task (the `HerdrStart` pattern from host).
  Ssh only TALKS to a server that is already up, and panes that server creates survive the disconnect.
- `Programs\Herdr\bin` is a junction that an sshd logon cannot see through.
  The remote call finds `herdr.exe` in `~\.herdr\packages\standalone\releases\<newest>` first, the way `herdr-remote.ps1` does.

## 3. Where things live

| Thing | Where | Notes |
|---|---|---|
| The seat, the one board Ryan sees | host (`board\state.json`) | One board. Ryan talks to the seat only. |
| The remote mate | A Claude session in the laptop's herdr | Domain mate. Never spawns a mate (depth cap holds). |
| Its crew | Panes in the laptop's herdr | Spawned by the laptop's own `orch.py`, on the laptop's own Claude login. |
| Its session registry | `C:\Users\YOURNAME\.claude\sessions\` on the laptop | The seat never reads it over a share. It asks `orch.py ls`. |
| Task files the seat sends | Seat: `handoffs\task-*.md`. Laptop: `handoffs\inbox\` | Pushed by `scp`. |
| Reports the mate writes | Laptop: `handoffs\outbox\report-*.md` | Pulled by `scp` into the seat's `handoffs\remote\laptop\`. |
| Questions for Ryan | A line `ASK: <question>` in the report | See below. |
| The laptop's `~/.claude` | Git clone of claude-config | Updated on the laptop with `git pull`. The seat never pushes code to it by `scp`. |
| The vault | Never crosses | The laptop has its own clone of vault-notes. Nothing syncs it. |

`handoffs\` is gitignored in claude-config (the `/*` allow-list in `.gitignore`), so git cannot carry these files.
That is why they cross by `scp`.

Why questions travel as an `ASK:` line:
`orch.py ask` on the laptop writes the LAPTOP's board, which Ryan never opens.
So a remote mate does not use `ask`.
It writes `ASK: <text>` in its report, and the pull step raises it on the seat's board.
Anything that needs an approval id stays on host, because `approval check` reads host's approvals file.
A remote mate cannot submit schoolwork, send a message to other people, or pay.

## 4. How reports and files come back

Tool: `scp`, whole files only, no rsync, no Taildrive.

Pull (laptop to seat), one command per host:

    scp -p -o BatchMode=yes "laptop:.claude/handoffs/outbox/*" "C:/Users/YOURNAME/.claude/handoffs/remote/laptop/"

Push (seat to laptop), one file per task:

    scp -o BatchMode=yes "C:/Users/YOURNAME/.claude/handoffs/task-<name>.md" "laptop:.claude/handoffs/inbox/"

Rules:

- Only `handoffs\inbox\` and `handoffs\outbox\` are named in any command. There is no recursive copy of a tree.
- Pulled files land in `handoffs\remote\laptop\`, a folder of their own. A pull can never overwrite a local file.
- "New" means "the file name was not seen before", kept in a small `seen` list.
  It does not mean "newer mtime", because the two clocks may disagree (section 7).
- After a pull the seat deletes nothing on the laptop.
  The mate moves a report to `outbox\sent\` only after the seat's next `ls --host` shows the pull succeeded.
  M1 can skip this and let the folder grow, because reports are small.
- Reports follow the normal shape: `Done:`, `Proof:`, `Next:`.
  Proof from the laptop is text or a file path ON THE LAPTOP.
  A screenshot proof is pulled as a file into the same folder, and the seat opens it from there.

A pull is on demand: `orch.py pull --host laptop`.
The existing Monitor (`watch_sessions.py`) runs it every 60 seconds while a remote task is open, and stops when none is open.

## 5. The `orch.py --host` shape

One new flag, `--host <name>`, and one new file, `scripts\remote_host.py`.
`<name>` is a key under a new `hosts` block in `config.json`:

    "hosts": {
      "laptop": {
        "ssh": "laptop",
        "orch": "C:\\Users\\YOURNAME\\.claude\\skills\\orchestrate\\scripts\\orch.py",
        "py": "py"
      }
    }

`ssh` is the `Host` alias from section 2.
A command with `--host laptop` becomes `ssh laptop <py> <orch> <same command>` with ssh's own BatchMode and timeout.
`remote_host.py` reads the result.
`orch.py` only gains a dispatch branch that sends `--host` commands there.

### Which subcommands gain `--host`

| Subcommand | With `--host` | Note |
|---|---|---|
| `ls` | Runs `ls --format toon` on the laptop, merges rows | See the merge rules below. Also runs bare `ls` with no flag as a merged view when `hosts` is set. |
| `prompt` | Sends a prompt to a remote pane | See "how a prompt reaches a pane". |
| `task` | Pushes the task file to `inbox\`, then runs `task` on the laptop | The laptop's `orch.py` spawns the pane. The seat adds `owner` and `host` to its own sidecar. |
| `pull` (new) | Pulls `outbox\` | Section 4. |
| `doctor` | Adds a laptop check: reachable, sshd, herdr up, `claude --version`, clock skew | Prints one line per failure mode in section 7. |
| `hold`, `unhold`, `go`, `ungo`, `cancel`, `kill` | Later (M2) | They are the same ssh wrapper. Left out so M1 stays small. |
| `spawn`, `review`, `dispatch`, `quota`, `account`, `chrome`, `seat`, `board`, `approval` | Refused with `not remote-capable` | They need host state: quota, accounts, Chrome, the board, approvals. |

### How `ls` merges remote rows

- The laptop's `ls --format toon` rows come back over ssh and are parsed with the same `toon_format` the seat already uses.
- Each remote name becomes `name@laptop`, and one column `HOST` is added (`-` for local rows).
- Rows are ordered by the owner tree as today, with the remote mate under the seat and its crew under it.
- The last good remote snapshot is cached at `state\remote\laptop-ls.json` with its time.
- If the laptop does not answer, the seat shows the cached rows marked `stale <age>` plus one line saying why (section 7).
  It does NOT print an error and exit, because `ls` is the first thing the seat runs and must always answer.
- The remote call also prints the laptop's `epoch` so the seat can work out clock skew with no extra round trip.
- Pane ids are per host, so a remote pane id is always shown as `laptop:<pane>`, never as a bare `w1:p3` that could clash with a local one.

### How a prompt reaches a remote pane

The text never goes on the ssh command line.
Windows quoting through ssh and the laptop's default shell will mangle quotes, backticks and newlines.
So the text goes over stdin:

    ssh laptop py <orch> prompt <name> --stdin --sender axi-lead@host   (text piped in)

Two small additions on the laptop's `orch.py` (the same file, delivered by `git pull`):

- `--stdin` reads the prompt text from stdin.
- `--sender` sets the `[from ...]` stamp, so the mate sees `[from axi-lead@host]`.
  Without it the laptop would stamp its own caller name and the mate could mistake the seat for an attacker, the exact failure that `cmd_prompt` documents (2026-09-16).

The laptop's `cmd_prompt` then does what it already does: the pi inbox file, the ACP inbox file, or `herdr agent prompt` with the "pane moved" check.
The seat prints the laptop's own result line, prefixed with `laptop:`.
A prompt to an unreachable laptop is refused with the reason.
It is not queued, because a queued prompt can arrive hours later out of order.
A task is different: it is a file, so `task --host` can be retried safely.

## 6. What herdr does on the laptop

- The laptop runs its own herdr (0.8.x), which `laptop-abroad-setup.ps1` step 7 already configures from host's `config.toml`.
- Its server starts from a scheduled task at logon (the `HerdrStart` pattern), never from ssh.
- The remote mate and its crew are panes in that server, so closing the ssh connection kills nothing.
- Ryan can still sit at the laptop, run `herdr`, and watch the mate work, because it is a normal local herdr.
- The existing `host` launcher on the laptop does the opposite job (laptop attaches to host).
  It is unchanged, and its fallback to the laptop's own herdr is the same server the remote mate lives in.
- Herdr does not register a pi pane as an agent on Windows (`PI_NO_HERDR_AGENT`).
  The remote `ls` therefore reads the laptop's pi sidecar the same way the local one does.

## 7. Failure modes, and what the seat sees

The seat gets the failure from ssh's own text, classified by `remote_host.py`.
Each one is one line in `ls`, `doctor` and the refusal message.

| Failure | What ssh prints | What the seat shows |
|---|---|---|
| Laptop asleep, lid closed, or powered off | `connect to host ... port 22: Connection timed out` | `laptop: unreachable (timeout 8s). Tailscale: offline, last seen <age>.` Cached rows shown stale. This is what happened on 2026-10-09. |
| Tailscale down on the laptop (service stopped, signed out) | Same timeout as above | Same line. Ssh cannot tell asleep from Tailscale-down, so the line adds `tailscale ping` result and says "cannot tell which". |
| Tailscale down on host | `Network is unreachable` or `No route to host` | `this box: Tailscale is down` plus the first line of `tailscale status`. All remote rows stale. |
| Laptop up but sshd stopped | `Connection refused` | `laptop: up, sshd not listening`. Start it from the laptop, or from RDP into the laptop. |
| Ssh key missing or not authorized | `Permission denied (publickey,keyboard-interactive)` at once | `laptop: key refused. Add ~/.ssh/id_ed25519.pub to the laptop's authorized keys (Ryan step).` BatchMode makes this instant, never a password prompt. |
| Laptop host key changed | `REMOTE HOST IDENTIFICATION HAS CHANGED` | `laptop: host key changed, refusing. Check it is really the laptop before editing known_hosts.` Never auto-accepted. |
| Herdr server not running on the laptop | The remote `orch.py` exits non-zero with its own herdr error | `laptop: up, herdr not running`. Fix: sign in at the laptop (its logon task starts herdr). Ssh must not start it. |
| Laptop user is not `YOURNAME` | `orch.py` path not found | `laptop: orch.py not found at <path>. Check hosts.laptop.orch and the Windows user.` |
| `claude` not on the sshd PATH | `claude : The term 'claude' is not recognized` | `doctor` retries with `%USERPROFILE%\.local\bin\claude.exe` and says which path worked. |
| Clock skew | Ssh does not care, so nothing fails | `ls` prints `skew +95s` when the laptop's epoch differs by more than 60 seconds. File "new" checks use names, not mtimes (section 4). All timestamps written by remote tools are UTC ISO 8601. |
| Laptop in another time zone (Rome) | Nothing fails | Local clock times on the laptop differ. UTC fields are the only ones compared. |
| Laptop reconnects mid-command | `client_loop: send disconnect` | The command is read-only or a whole-file copy, so it is retried once. A `prompt` is NOT retried, because it may have landed. The seat says "unknown: check `ls --host`". |

Rule for all of them: a remote failure never blocks a local command.
`ls` still lists local panes.
`task --host` and `prompt --host` refuse.
`doctor` reports.

## 8. What stays impossible (or stays on host)

- Waking the laptop.
  Tailscale has no wake-on-LAN path, and waking or rebooting it is out of scope for this task.
  A sleeping laptop is simply unreachable until Ryan opens it.
- Starting herdr from ssh.
  Windows sshd would kill every agent when the connection drops.
- Reading the laptop's screen as a stream.
  The seat can read a pane once (`herdr pane read` through the same ssh wrapper, M2).
  It cannot watch live.
- The vault.
  The laptop's clone is its own.
  The seat never syncs notes either way.
- Ryan's logged-in Chrome profiles.
  They live on host.
  The laptop has none, so a remote mate cannot do signed-in browser work.
- The Claude login.
  Someone must run `claude` once on the laptop and sign in (`laptop-abroad-setup.ps1` already lists this as a Ryan step).
  `claude --version` passing does NOT prove the login works.
- Quota and accounts.
  `orch.py quota` and `account` see host's accounts only.
  Whether laptop work draws on the same plan window is a plan question, not something this design can read.
- Approvals.
  Submit, send, and pay stay on host with an approval id.
- A remote mate spawning a mate.
  The depth cap holds (SKILL.md 4d).
- GPU and heavy work.
  The RTX 5090 is on host.
  Renders, Playwright batches and yt-dlp stay there.
- Running with no laptop on.
  Remote tasks are files, so they survive, but nothing runs until the laptop is up.

## 9. The one laptop step for Ryan

Nothing below was done or tested by this task.
It is the list M1 asks Ryan to accept before any code is written.
It can be a short script, `tools\laptop-mate-setup.ps1` (about 60 lines, run once on the laptop, safe to re-run like the abroad script):

1. Turn on OpenSSH Server and set it to start at boot (`Add-WindowsCapability OpenSSH.Server`, `Set-Service sshd -StartupType Automatic`).
2. Set the sshd default shell to PowerShell (the same setting host has).
3. Limit the sshd firewall rule to the Tailscale range `100.64.0.0/10`, so the laptop does not listen for ssh on hotel or campus wifi.
4. Add one public key line to the right authorized keys file: the existing `~/.ssh/id_ed25519.pub` from host.
   Do not make a new key.
5. Add the logon task that starts herdr (the `HerdrStart` pattern) if the laptop does not have one.
6. Create `handoffs\inbox\` and `handoffs\outbox\`.

If Ryan does not want sshd open on the laptop, the design stops here.
There is no fallback that reaches in without it.

## 10. Size estimate for M1

Small enough to build, if step 9 is accepted.

| File | New or edit | Lines (approx) | What |
|---|---|---|---|
| `scripts\remote_host.py` | new | 170 | Resolve host, run ssh (BatchMode, timeout), classify errors (section 7), `ls` parse + merge + cache + skew, `scp` pull and push |
| `scripts\orch.py` | edit | 45 | `--host` in `SPECS`, one dispatch branch, `pull` command, `prompt --stdin --sender` |
| `scripts\test_remote_host.py` | new | 130 | A fake ssh (`ORCH_SSH_BIN`) that returns each failure text in section 7 |
| `config.example.json` | edit | 8 | The `hosts` block with its comment |
| `tools\laptop-mate-setup.ps1` | new | 60 | Section 9, for the laptop |
| `SKILL.md` | edit | 4 | One pointer to this page |

About 2 new scripts, 1 new tool, 3 small edits, around 420 lines with tests and about 285 without.
For scale: Kun's version is about 6,400 lines of bash.

Order inside M1: `remote_host.py` with the fake-ssh tests first, then the `ls` merge, then `prompt`, then `task` and `pull`, then `doctor`.
The first real run needs the laptop awake and step 9 done, so keep the real-laptop check last.

## 11. Proof: ssh round trip

Status: **PARKED.**
The laptop was offline when this was run, so the round trip did not happen.
The design is not affected.

Run from this box (host), 2026-10-09.
There is no `~/.ssh/config`, so the full target is spelled out:

    ssh -o BatchMode=yes -o ConnectTimeout=8 YOURNAME@100.x.y.z "claude --version"

Output:

    ssh: connect to host 100.x.y.z port 22: Connection timed out
    exit=255

`tailscale status`, the laptop's line:

    100.x.y.z  laptop  you@  windows  active; relay "lax"; offline, last seen 2d ago, tx 5304 rx 0

`tailscale status --json`, the laptop's peer:

    HostName: laptop   Online: False   LastSeen: 2026-10-07T00:22:30.1Z
    DNSName: your-host.your-tailnet.ts.net.   KeyExpiry: none   Relay: lax

`tailscale ping`:

    ping "100.x.y.z" timed out
    no reply

What this shows, and what it does not:

- The laptop has been off the tailnet since 2026-10-07 00:22 UTC.
  The timeout is the "asleep or off" row in section 7, seen for real.
- It does NOT show whether sshd runs on the laptop, whether `YOURNAME` is the user, or whether the key is accepted.
  Port 22 was never reached.
- When the laptop is awake, run the same command after step 9.
  Expected output is one line like `2.x.x (Claude Code)`.
  If it says `claude` is not recognized, use `& "$env:USERPROFILE\.local\bin\claude.exe" --version` and record the path.

## 12. Built in M1

Status: built 2026-10-09 against a FAKE host. The live laptop proof is **PARKED**.
This section replaces the plan in sections 5 and 10 where they differ.
What changed from the plan: the seat does not run the laptop's `orch.py` over ssh.
It runs a small host-side script, `remote.py`, so the laptop needs no herdr and no second protocol.

### What exists

| Piece | Where | What it does |
|---|---|---|
| `scripts/remote.py` | new, about 450 lines | Seat side and host side in one file. |
| `scripts/fake_host.py` | new | A local stand-in for ssh, scp and the claude CLI. Tests only. |
| `scripts/test_remote.py` | new, 24 tests | start, `ls` merge, report sync, kill, unreachable, classify, refusals. |
| `board/hosts.example.json` | new | The host table. Copy it to `board/hosts.json` (gitignored, per machine). |
| `orch.py` | edit | `task --host`, `kill <name>@<host>`, `pull`, and the `ls` merge. |
| `dispatch_rule.py` | edit, 3 lines | `task --host` needs no `--rule`: it runs on the host's own login, not the quota table. |

### The host table

`board/hosts.json`, one key per host:

    { "laptop": { "ssh": "YOURNAME@100.x.y.z", "home": "C:/Users/YOURNAME",
                  "sync": [".claude/handoffs/outbox"] } }

- `ssh`: the target. A `Host` alias from `~/.ssh/config` works too.
- `home`: the host's home, with forward slashes. Used to name the report path in the task file.
- `sync`: folders to pull, relative to the host's home. Every entry must sit under `.claude/handoffs`. Anything else is refused, so the vault can never cross.
- With no `hosts.json`, `ls` never calls ssh.

### How each command works

- Every ssh and scp call runs with `BatchMode=yes` and `ConnectTimeout=8`, so it never asks for a password and never hangs.
- `task <label> --host laptop --goal ... --done ...` writes a task file in `handoffs\`, copies it to `.claude/handoffs/inbox/` on the host, then runs `remote.py host-start` there.
- `host-start` starts `claude -p "Read <task> and do exactly what it says."` as a detached process.
  On Windows it asks for a breakaway from sshd's job object, because sshd kills a logon session's processes when the connection drops.
  It writes a state file in `.claude/remote-mates/<label>.json` on the host.
  There is no tmux on Windows, so a detached process is the equivalent.
- The mate has no SendMessage. The task file tells it to write `report-<label>.md` in `.claude/handoffs/outbox/` with `Done:`, `Proof:`, `Next:`, and `ASK: <question>` if it needs Ryan.
- `ls` runs `remote.py host-rows` and adds one row per mate: name `label@laptop`, WHERE = `laptop`, HARNESS as the host reports it, STATUS busy, done, failed or killed.
  Status comes from the process (alive) and the report file (exists).
- `pull [--host laptop]` lists the host's outbox, copies each file whose name is new into `handoffs\remote\laptop\`, and prints each `Done:` line.
  "New" is by file name and size, kept in `board/remote-seen.json`, so a re-run report is pulled again. It never uses mtimes. A file touched in the last 2 seconds waits for the next pull.
- `kill <label>@laptop` (or `kill <label> --host laptop`) stops the process tree over ssh and marks it killed.

### When the laptop does not answer

- ssh text is sorted into: `timeout`, `refused`, `key`, `hostkey`, `network`.
  `ls` prints one line saying which, plus how long ago the host was last seen.
- The last good rows (`board/remote-cache.json`) stay on the board with STATUS = **UNREACHABLE**.
  It is not VANISHED: the watchdog only flags herdr panes, and a remote mate is not one.
- After a failure `ls` does not ask that host again for 60 seconds, so a dead laptop costs one wait (8 seconds to connect, up to 18 if it connects and hangs), not one per `ls`.
- `task --host`, `pull` and `kill` stop with `UNREACHABLE: <reason>`. A prompt is not queued.
- A clock gap over 60 seconds prints `clock skew +95s`.

### Not built (still M2 or later)

- `prompt --host`, `hold`, `cancel` for a remote mate. A started mate runs one task and ends.
- `doctor --host` and the Monitor that runs `pull` every 60 seconds.
- `ASK:` lines are shown by `pull` in the report text only. They are not raised on the seat's board yet.
- `tools\laptop-mate-setup.ps1`. The steps below are by hand.
- Herdr on the laptop. A remote mate is a detached process, not a pane, so Ryan cannot watch it at the laptop's herdr. The log is `.claude/remote-mates/<label>.log` on the laptop.

### Ryan steps (nothing here was done by the build)

On the laptop, in an administrator PowerShell:

1. Install and start sshd:

       Add-WindowsCapability -Online -Name OpenSSH.Server~~~~0.0.1.0
       Set-Service sshd -StartupType Automatic
       Start-Service sshd

2. Set PowerShell as the sshd shell:

       New-ItemProperty -Path HKLM:\SOFTWARE\OpenSSH -Name DefaultShell -Value "C:\Program Files\PowerShell\7\pwsh.exe" -PropertyType String -Force

   Use `C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe` if PowerShell 7 is not installed.
3. Allow ssh only from Tailscale:

       Set-NetFirewallRule -Name OpenSSH-Server-In-TCP -RemoteAddress 100.64.0.0/10

4. Add the key. On host print it with `Get-Content ~\.ssh\id_ed25519.pub`.
   On the laptop add that one line to:
   - `C:\ProgramData\ssh\administrators_authorized_keys` if `YOURNAME` is an administrator.
     Then run `icacls C:\ProgramData\ssh\administrators_authorized_keys /inheritance:r /grant "SYSTEM:F" /grant "Administrators:F"`.
   - `C:\Users\YOURNAME\.ssh\authorized_keys` if `YOURNAME` is a normal user.
5. Pull claude-config on the laptop so `remote.py` is there, and sign in to `claude` once at the laptop.
6. If `claude` is not on the sshd PATH, make `C:\Users\YOURNAME\.claude\remote-host.json`:

       { "claude": "C:/Users/YOURNAME/.local/bin/claude.exe",
         "claude_args": ["--permission-mode", "acceptEdits"] }

   `claude_args` is the unattended permission setting. `acceptEdits` lets the mate edit files but not run most commands.
   Widen it only if Ryan agrees: a mate on the laptop has no one to click a prompt.
7. On host, `copy board\hosts.example.json board\hosts.json`, then check:

       ssh -o BatchMode=yes -o ConnectTimeout=8 YOURNAME@100.x.y.z claude --version
       py orch.py task smoke --host laptop --goal "write hello to a file" --done "report exists"
       py orch.py ls
       py orch.py pull --host laptop

### Proof: ssh round trip (M1 run, PARKED)

Run from host 2026-10-09 with the exact command in the task:

    ssh -o BatchMode=yes -o ConnectTimeout=8 YOURNAME@100.x.y.z claude --version
    ssh: connect to host 100.x.y.z port 22: Connection timed out
    exit=255

    tailscale status:  100.x.y.z  laptop  you@  windows  active; relay "lax"; offline, last seen 2d ago

Port 22 was never reached, so sshd, the user name and the key are still unproven.
The fake-host tests prove the seat code and the host code, not the laptop.
