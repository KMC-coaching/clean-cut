<#
  One paste, for Windows. Downloads clean-cut and installs everything.

      irm https://raw.githubusercontent.com/KMC-coaching/clean-cut/main/install.ps1 | iex

  No git needed, no administrator password. If Python is missing it installs
  it into your own user account with winget.
#>

$ErrorActionPreference = 'Stop'
$ProgressPreference    = 'SilentlyContinue'   # the progress bar makes downloads crawl

$Repo = 'KMC-coaching/clean-cut'
$Dest = if ($env:CLEAN_CUT_SRC) { $env:CLEAN_CUT_SRC } else { Join-Path $HOME 'clean-cut' }
$Url  = "https://codeload.github.com/$Repo/zip/refs/heads/main"

function Say  { param($m) Write-Host "`n== $m" -ForegroundColor Cyan }
function Info { param($m) Write-Host "   $m" }
function Bad  { param($m) Write-Host "`n!! $m" -ForegroundColor Red }

Write-Host 'clean-cut installer for Windows'

# ------------------------------------------------------------------ python
# `python` on a clean Windows is often a 0-byte Store stub that opens the
# Microsoft Store rather than running anything, so ask for the version
# instead of trusting that the name exists.
function Find-Python {
    foreach ($cmd in @('py -3', 'python3', 'python')) {
        $parts = $cmd.Split(' ')
        if (-not (Get-Command $parts[0] -ErrorAction SilentlyContinue)) { continue }
        $extra = if ($parts.Count -gt 1) { $parts[1..($parts.Count - 1)] } else { @() }
        try {
            $ok = & $parts[0] @extra -c "import sys; print(1 if sys.version_info >= (3,9) else 0)" 2>$null
        } catch { continue }
        if ($ok -eq '1') { return , (@($parts[0]) + $extra) }
    }
    return $null
}

Say 'python'
$py = Find-Python
if (-not $py) {
    Info 'Python 3.9 or newer is not installed.'
    if (Get-Command winget -ErrorAction SilentlyContinue) {
        Info 'Installing it now, into your own account. No password needed.'
        winget install --exact --id Python.Python.3.12 --scope user `
            --accept-source-agreements --accept-package-agreements --silent
        $env:Path = [Environment]::GetEnvironmentVariable('Path', 'User') + ';' +
                    [Environment]::GetEnvironmentVariable('Path', 'Machine')
        $py = Find-Python
    }
}
if (-not $py) {
    Bad 'clean-cut needs Python 3.9 or newer.'
    Write-Host ''
    Write-Host '   Install it from  https://www.python.org/downloads/windows/'
    Write-Host "   On the installer's FIRST screen, tick 'Add python.exe to PATH'."
    Write-Host '   You do not need to be an administrator.'
    Write-Host ''
    Write-Host '   Then close this window, open a NEW PowerShell, and paste the'
    Write-Host '   install command again.'
    exit 1
}
Info ("using " + ($py -join ' '))

# ------------------------------------------------------------------ download
Say 'downloading clean-cut'
$zip = Join-Path $env:TEMP 'clean-cut.zip'
$tmp = Join-Path $env:TEMP 'clean-cut-unzip'
try {
    Invoke-WebRequest -Uri $Url -OutFile $zip -UseBasicParsing
} catch {
    Bad "could not download from $Url"
    Info 'Check the internet connection and try again.'
    exit 1
}
if (Test-Path $tmp) { Remove-Item $tmp -Recurse -Force }
Expand-Archive -Path $zip -DestinationPath $tmp -Force
$inner = Get-ChildItem $tmp -Directory | Select-Object -First 1

if (Test-Path $Dest) {
    Info "replacing the previous copy at $Dest"
    Remove-Item $Dest -Recurse -Force
}
Move-Item $inner.FullName $Dest
Remove-Item $zip, $tmp -Recurse -Force -ErrorAction SilentlyContinue
Info "into $Dest"

# ------------------------------------------------------------------ install
Say 'installing (this is the long part - 20 minutes or so)'
Info 'it fetches ffmpeg, python packages and the b-roll clips'
Info 'nothing here asks for a password'
& powershell -ExecutionPolicy Bypass -File (Join-Path $Dest 'clean-cut\scripts\setup.ps1') -Yes
if ($LASTEXITCODE -ne 0) { Bad "setup finished with problems (code $LASTEXITCODE)"; }

# ------------------------------------------------------------------ finish
$exe = $py[0]
$pre = if ($py.Count -gt 1) { ($py[1..($py.Count - 1)] -join ' ') + ' ' } else { '' }
$run = Join-Path $Dest 'clean-cut\scripts\run.py'

Say 'done'
Write-Host ''
Write-Host '   To edit a video, paste this and change the filename:'
Write-Host ''
Write-Host "       $exe $pre`"$run`" `"my-video.mp4`"" -ForegroundColor Green
Write-Host ''
Write-Host '   The finished file appears next to the original, named'
Write-Host '   my-video-edited.mp4.'
Write-Host ''
