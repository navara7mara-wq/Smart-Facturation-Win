param(
    [switch]$NoCapture,
    [switch]$Strict,
    [string]$Page = ""
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$python = "C:\Users\Administrateur\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"

if (-not (Test-Path -LiteralPath $python)) {
    $python = "python"
}

$pythonArgs = @((Join-Path $PSScriptRoot "visual_regression.py"))
if ($NoCapture) { $pythonArgs += "--no-capture" }
if ($Strict) { $pythonArgs += "--strict" }
if ($Page) { $pythonArgs += @("--page", $Page) }

& $python @pythonArgs
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}

$report = Join-Path $root "output\visual-regression\report.html"
Write-Host "Rapport visuel: $report"
