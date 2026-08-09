param(
    [string]$Version = "1.4.0"
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$IssPath = Join-Path $Root "installer\PhoEniX_BPU.iss"

$candidates = @(
    "$env:ProgramFiles(x86)\Inno Setup 6\ISCC.exe",
    "$env:ProgramFiles\Inno Setup 6\ISCC.exe"
) | Where-Object { $_ -and (Test-Path $_) }

if (-not $candidates) {
    throw "Inno Setup 6 is required. Install it, then rerun this script."
}

$env:PHOENIX_VERSION = $Version
& $candidates[0] $IssPath
if ($LASTEXITCODE -ne 0) {
    throw "Inno Setup failed with exit code $LASTEXITCODE."
}

Write-Host "Installer created in dist\installer" -ForegroundColor Green
