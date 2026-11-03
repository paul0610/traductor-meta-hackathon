# Install.bat runs this: Windows 11 on x64, then uv, then Python 3.13 and the exact packages in uv.lock, then the
# setup assistant (python -m interprete.setup), which gets the rest ready. Arguments go to the assistant.
# ASCII only: Windows PowerShell 5.1 reads a script without a BOM in the ANSI code page. Exit codes are checked by
# hand: with ErrorActionPreference Stop, PowerShell 5.1 stops on any line a tool writes to stderr once it is piped.

Set-Location (Split-Path -Parent $PSScriptRoot)

function Stop-Setup([string]$Why) {
    Write-Host ''
    Write-Host $Why -ForegroundColor Red
    exit 1
}

function Find-Uv {
    $cmd = Get-Command uv -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    $link = Join-Path $env:LOCALAPPDATA 'Microsoft\WinGet\Links\uv.exe'
    if (Test-Path $link) { return $link }
    return $null
}

Write-Host 'Interprete ES-EN setup'
Write-Host ''

$build = [Environment]::OSVersion.Version.Build
if ($build -lt 22000) { Stop-Setup "It needs Windows 11, build 22000 or later; this PC has build $build." }
if ($env:PROCESSOR_ARCHITECTURE -ne 'AMD64') { Stop-Setup "It needs a 64-bit Intel or AMD processor; this PC reports $env:PROCESSOR_ARCHITECTURE." }
Write-Host "[1/3] Windows 11, build $build, x64"

$uv = Find-Uv
if (-not $uv) {
    if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
        Stop-Setup 'uv is missing, and so is winget to install it. Install uv from https://docs.astral.sh/uv/ and run Install.bat again.'
    }
    Write-Host '[2/3] Installing uv, the Python package manager, with winget'
    winget install --id=astral-sh.uv -e --accept-source-agreements --accept-package-agreements
    $env:Path = [Environment]::GetEnvironmentVariable('Path', 'Machine') + ';' + [Environment]::GetEnvironmentVariable('Path', 'User')
    $uv = Find-Uv
    if (-not $uv) { Stop-Setup 'uv did not install. Install it from https://docs.astral.sh/uv/ and run Install.bat again.' }
}
Write-Host "[2/3] uv: $uv"

Write-Host '[3/3] Python 3.13 and the exact packages in uv.lock; the first time it downloads about 1 GB'
& $uv sync --locked
if ($LASTEXITCODE -ne 0) { Stop-Setup 'The packages did not install. Check the internet connection and run Install.bat again.' }

Write-Host ''
& $uv run --locked python -m interprete.setup @args
exit $LASTEXITCODE
