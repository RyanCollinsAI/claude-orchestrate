<#
.SYNOPSIS
  Install the orchestrate skill into ~/.claude/skills/orchestrate.

.DESCRIPTION
  Copies the skill, writes a config.json if there is not one already, checks the dependencies, and
  offers two settings.json changes. Nothing is overwritten without being told to: an existing
  config.json is left alone, and an existing hooks array is appended to, never replaced.

.PARAMETER Cwd
  The working directory new sessions start in. Defaults to the current directory.

.PARAMETER NoHooks
  Skip the settings.json hook offer entirely.

.PARAMETER Bypass
  Also set permissions.defaultMode to bypassPermissions. Off by default - read the README first.

.EXAMPLE
  .\install.ps1 -Cwd C:\Users\me\my-project
#>
[CmdletBinding()]
param(
  [string]$Cwd = (Get-Location).Path,
  [switch]$NoHooks,
  [switch]$Bypass
)

$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $MyInvocation.MyCommand.Path
$claude = if ($env:CLAUDE_CONFIG_DIR) { $env:CLAUDE_CONFIG_DIR } else { Join-Path $env:USERPROFILE '.claude' }
$dest = Join-Path $claude 'skills\orchestrate'

function Say($ok, $label, $detail) {
  $tag = if ($ok -eq $true) { 'OK  ' } elseif ($ok -eq $false) { 'MISS' } else { '--  ' }
  Write-Host ("  {0} {1,-16} {2}" -f $tag, $label, $detail)
}

Write-Host "claude-orchestrate installer"
Write-Host "  repo   $repo"
Write-Host "  target $dest"
Write-Host ""

# ---------------------------------------------------------------- 1. copy the skill
New-Item -ItemType Directory -Force -Path $dest | Out-Null
foreach ($sub in 'scripts', 'templates', 'board') {
  New-Item -ItemType Directory -Force -Path (Join-Path $dest $sub) | Out-Null
}
Copy-Item (Join-Path $repo 'skills\orchestrate\SKILL.md') $dest -Force
Copy-Item (Join-Path $repo 'skills\orchestrate\scripts\*.py')   (Join-Path $dest 'scripts')   -Force
Copy-Item (Join-Path $repo 'skills\orchestrate\templates\*.md') (Join-Path $dest 'templates') -Force
Copy-Item (Join-Path $repo 'skills\orchestrate\board\*')        (Join-Path $dest 'board')     -Force
Copy-Item (Join-Path $repo 'config.example.json') $dest -Force
Write-Host "Skill copied."

# ---------------------------------------------------------------- 2. config.json
$configPath = Join-Path $dest 'config.json'
if (Test-Path $configPath) {
  Write-Host "config.json already exists - left untouched. Delete it to regenerate."
} else {
  $full = [System.IO.Path]::GetFullPath($Cwd)
  $cfg = [ordered]@{
    default_cwd      = $full
    session_prefix   = (Split-Path -Leaf $full).ToLower()
    orchestrator_tab = 'orchestrator'
    accounts_tool    = ''
    rotate_at_k      = 400
    digest_repos     = @()
  }
  $cfg | ConvertTo-Json -Depth 5 | Set-Content -Path $configPath -Encoding utf8
  Write-Host "Wrote $configPath"
  Write-Host "  default_cwd    $full"
  Write-Host "  session_prefix $($cfg.session_prefix)"
}

# ---------------------------------------------------------------- 3. dependencies
Write-Host ""
Write-Host "Dependencies"
$py = Get-Command py, python -ErrorAction SilentlyContinue | Select-Object -First 1
if ($py) {
  $v = & $py.Source -c "import sys;print('%d.%d' % sys.version_info[:2])"
  $okv = [version]$v -ge [version]'3.11'
  Say $okv 'python' "$v at $($py.Source)$(if (-not $okv) { '  <-- needs 3.11 or newer' })"
} else {
  Say $false 'python' 'not on PATH - nothing in this skill runs without it'
}

$herdr = Get-Command herdr -ErrorAction SilentlyContinue
if ($herdr) {
  Say $true 'herdr' $herdr.Source
} else {
  Say $false 'herdr' 'REQUIRED. It is the terminal multiplexer every command drives: ls, spawn, rotate, reap, show/hide all go through it. Without herdr the skill does nothing. https://github.com/anthropics/herdr'
}

$lavish = Get-Command lavish-axi -ErrorAction SilentlyContinue
if ($lavish) {
  Say $true 'lavish-axi' $lavish.Source
} else {
  Say $null 'lavish-axi' 'optional - only `board open` and board_watch.py need it. Everything else works without it.'
}

$pwshCmd = Get-Command pwsh -ErrorAction SilentlyContinue
if ($pwshCmd) {
  Say $true 'pwsh' $pwshCmd.Source
} else {
  Say $null 'pwsh' 'optional - only needed if you point `accounts_tool` at a PowerShell account switcher.'
}

$gh = Get-Command claude -ErrorAction SilentlyContinue
Say ($null -ne $gh) 'claude' $(if ($gh) { $gh.Source } else { 'not on PATH - `doctor` cannot check auth' })

# ---------------------------------------------------------------- 4. settings.json
if (-not $NoHooks) {
  $settingsPath = Join-Path $claude 'settings.json'
  $settings = if (Test-Path $settingsPath) {
    Get-Content $settingsPath -Raw | ConvertFrom-Json -AsHashtable
  } else { @{} }

  Write-Host ""
  Write-Host "settings.json ($settingsPath)"
  Write-Host "  1. PreToolUse hook that blocks the five command shapes that park a pane on a prompt"
  Write-Host "     (heredocs, ``cd X && ...``, git checkout --, git restore, backticks in a commit -m)."
  Write-Host "  2. DISABLE_AUTOUPDATER=1 - a mid-work Claude update reloads every pane at once."
  if ($Bypass) {
    Write-Host "  3. permissions.defaultMode = bypassPermissions (you passed -Bypass)."
  }
  $ans = Read-Host "Apply these? [y/N]"
  if ($ans -match '^[Yy]') {
    # Copy the hook next to the other hooks, then register it without touching what is there.
    $hookDir = Join-Path $claude 'hooks'
    New-Item -ItemType Directory -Force -Path $hookDir | Out-Null
    $hookDest = Join-Path $hookDir 'block-known-traps.py'
    Copy-Item (Join-Path $repo 'hooks\block-known-traps.py') $hookDest -Force

    $entry = @{
      matcher = 'Bash|PowerShell'
      hooks   = @(@{ type = 'command'; command = "py `"$($hookDest -replace '\\','/')`"" })
    }
    if (-not $settings.ContainsKey('hooks')) { $settings['hooks'] = @{} }
    if (-not $settings['hooks'].ContainsKey('PreToolUse')) { $settings['hooks']['PreToolUse'] = @() }
    $already = @($settings['hooks']['PreToolUse']) | Where-Object {
      ($_ | ConvertTo-Json -Depth 5) -match 'block-known-traps'
    }
    if ($already) {
      Write-Host "  hook already registered - left as is"
    } else {
      $settings['hooks']['PreToolUse'] = @($settings['hooks']['PreToolUse']) + $entry
      Write-Host "  hook appended to PreToolUse (existing entries kept)"
    }

    if (-not $settings.ContainsKey('env')) { $settings['env'] = @{} }
    $settings['env']['DISABLE_AUTOUPDATER'] = '1'
    Write-Host "  env.DISABLE_AUTOUPDATER = 1"

    if ($Bypass) {
      if (-not $settings.ContainsKey('permissions')) { $settings['permissions'] = @{} }
      $settings['permissions']['defaultMode'] = 'bypassPermissions'
      Write-Host "  permissions.defaultMode = bypassPermissions"
    }

    if (Test-Path $settingsPath) {
      Copy-Item $settingsPath "$settingsPath.bak" -Force
      Write-Host "  backup written to $settingsPath.bak"
    }
    $settings | ConvertTo-Json -Depth 12 | Set-Content -Path $settingsPath -Encoding utf8
    Write-Host "  settings.json updated"
  } else {
    Write-Host "  skipped"
  }
}

# ---------------------------------------------------------------- 5. done
Write-Host ""
Write-Host "Installed. Next:"
Write-Host "  py `"$dest\scripts\orch.py`" doctor     one red/green line per moving part"
Write-Host "  py `"$dest\scripts\orch.py`" ls         every live Claude Code session"
Write-Host "Then, in the session you want to drive the others: `"be the orchestrator`"."
