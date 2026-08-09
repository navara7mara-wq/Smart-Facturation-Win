param(
    [string]$Version = "1.0.0",
    [switch]$SkipInstaller
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$Dist = Join-Path $Root "dist"
$Stage = Join-Path $Dist "PhoEniX_BPU_$Version"
$ZipPath = Join-Path $Dist "PhoEniX_BPU_$Version.zip"

if (Test-Path $Stage) {
    Remove-Item -LiteralPath $Stage -Recurse -Force
}
New-Item -ItemType Directory -Path $Stage | Out-Null
New-Item -ItemType Directory -Path $Dist -Force | Out-Null

$items = @(
    "app.py",
    "db.py",
    "requirements.txt",
    "package.json",
    "README.md",
    "RELEASE_CHECKLIST.md",
    "setup.cmd",
    "run.cmd",
    "controle-visuel.cmd",
    "visual.config.json",
    "installer",
    "docs",
    "database",
    "scripts",
    "services",
    "static",
    "templates",
    "tests"
)

foreach ($item in $items) {
    $source = Join-Path $Root $item
    if (Test-Path $source) {
        Copy-Item -LiteralPath $source -Destination $Stage -Recurse
    }
}

Get-ChildItem -Path $Stage -Recurse -Directory -Filter "__pycache__" | Remove-Item -Recurse -Force
Get-ChildItem -Path $Stage -Recurse -Directory -Filter ".pytest_cache" | Remove-Item -Recurse -Force
Get-ChildItem -Path $Stage -Recurse -File -Include "*.pyc","*.pyo","*.log" | Remove-Item -Force

New-Item -ItemType Directory -Path (Join-Path $Stage "data") -Force | Out-Null
New-Item -ItemType Directory -Path (Join-Path $Stage "uploads") -Force | Out-Null
New-Item -ItemType Directory -Path (Join-Path $Stage "exports") -Force | Out-Null
New-Item -ItemType Directory -Path (Join-Path $Stage "output") -Force | Out-Null

if (Test-Path $ZipPath) {
    Remove-Item -LiteralPath $ZipPath -Force
}
Compress-Archive -Path (Join-Path $Stage "*") -DestinationPath $ZipPath

Write-Host "Release archive created: $ZipPath" -ForegroundColor Green

if (-not $SkipInstaller) {
    try {
        powershell -ExecutionPolicy Bypass -File (Join-Path $Root "scripts\build_installer.ps1") -Version $Version
    } catch {
        Write-Warning "Installer EXE was not created: $($_.Exception.Message)"
    }
}
