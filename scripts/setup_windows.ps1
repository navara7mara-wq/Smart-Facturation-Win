param(
    [switch]$SkipNode,
    [switch]$SkipTests
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $Root

Write-Host "== PhoEniX BPU setup ==" -ForegroundColor Cyan

if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
    throw "Python is not installed or not available in PATH."
}

python -m pip install --upgrade pip
python -m pip install -r requirements.txt

if (-not $SkipNode) {
    if (Get-Command npm -ErrorAction SilentlyContinue) {
        npm install
    } else {
        Write-Warning "npm was not found. PDF generation and visual tests require Node.js/npm."
    }
}

python scripts/init_db.py

if (-not $SkipTests) {
    python -m pytest -q
}

Write-Host ""
Write-Host "Setup complete." -ForegroundColor Green
Write-Host "Desktop: .\run-desktop.cmd"
Write-Host "Web mode: powershell -ExecutionPolicy Bypass -File scripts/run_app.ps1"
