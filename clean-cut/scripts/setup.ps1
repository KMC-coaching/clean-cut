<#
.SYNOPSIS
  clean-cut setup for Windows.

.DESCRIPTION
  Run this in PowerShell, from the clean-cut folder:

      powershell -ExecutionPolicy Bypass -File scripts\setup.ps1

  It needs no administrator password. If Python is missing it offers to
  install it for you with winget, into your own user account. Everything
  after that - including ffmpeg - is handled by setup.py, which is the same
  file macOS and Linux run. This wrapper only finds a Python and hands over.

.PARAMETER SkipBroll
  Leave the 403 MB b-roll library for later.

.PARAMETER Yes
  Do not ask before installing Python; just do it.
#>
[CmdletBinding()]
param(
    [switch]$SkipBroll,
    [switch]$Yes
)

$ErrorActionPreference = 'Stop'
$here = Split-Path -Parent $MyInvocation.MyCommand.Path

function Write-Step { param([string]$m) Write-Host "`n== $m" -ForegroundColor Cyan }
function Write-Info { param([string]$m) Write-Host "   $m" }
function Write-Bad  { param([string]$m) Write-Host "!! $m" -ForegroundColor Red }

# ---------------------------------------------------------------- find python
# `python` on a clean Windows is often a 0-byte App Execution Alias that opens
# the Microsoft Store instead of running anything, so the version has to be
# asked for rather than assumed from the name existing.
function Find-Python {
    foreach ($cmd in @('py -3', 'python3', 'python')) {
        $parts = $cmd.Split(' ')
        $exe = Get-Command $parts[0] -ErrorAction SilentlyContinue
        if (-not $exe) { continue }
        $extra = if ($parts.Count -gt 1) { $parts[1..($parts.Count - 1)] } else { @() }
        try {
            $ok = & $parts[0] @extra -c "import sys; print(1 if sys.version_info >= (3,9) else 0)" 2>$null
        } catch { continue }
        if ($ok -eq '1') { return , (@($parts[0]) + $extra) }
    }
    return $null
}

Write-Host "clean-cut setup for Windows"

Write-Step "python"
$py = Find-Python

if (-not $py) {
    Write-Info "Python 3.9 or newer is not installed (or is the Store placeholder)."
    $winget = Get-Command winget -ErrorAction SilentlyContinue
    if ($winget) {
        $go = $Yes
        if (-not $go) {
            Write-Host ""
            Write-Host "   I can install Python for you now. It goes in your own user"
            Write-Host "   account, so it will not ask for an administrator password."
            $answer = Read-Host "   Install Python 3.12? [Y/n]"
            $go = ($answer -eq '' -or $answer -match '^[Yy]')
        }
        if ($go) {
            Write-Info "installing Python 3.12 (a few minutes)"
            winget install --exact --id Python.Python.3.12 --scope user `
                --accept-source-agreements --accept-package-agreements --silent
            # winget puts it on PATH for NEW shells, so refresh this one.
            $env:Path = [Environment]::GetEnvironmentVariable('Path', 'User') + ';' +
                        [Environment]::GetEnvironmentVariable('Path', 'Machine')
            $py = Find-Python
        }
    } else {
        Write-Info "winget is not available on this machine either."
    }
}

if (-not $py) {
    Write-Bad "clean-cut needs Python 3.9 or newer."
    Write-Host ""
    Write-Host "   Install it from  https://www.python.org/downloads/windows/"
    Write-Host "   On the first screen of the installer, TICK 'Add python.exe to PATH'."
    Write-Host "   You do not need to be an administrator."
    Write-Host ""
    Write-Host "   Then close this window, open a new PowerShell, and run this again."
    Write-Host "   Nothing else is needed - setup fetches ffmpeg itself."
    exit 1
}

Write-Info ("using " + ($py -join ' '))

# ---------------------------------------------------------------- hand over
$exe = $py[0]
$pre = if ($py.Count -gt 1) { $py[1..($py.Count - 1)] } else { @() }
$args = @((Join-Path $here 'setup.py'))
if ($SkipBroll) { $args += '--skip-broll' }

& $exe @pre @args
exit $LASTEXITCODE
