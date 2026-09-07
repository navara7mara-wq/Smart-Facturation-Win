param(
    [string]$Python = "C:\Python314\python.exe",
    [string]$Version = "1.1.0"
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$DistPath = Join-Path $Root "dist\publisher"
$WorkPath = Join-Path $Root "build\license-studio"
$SpecPath = Join-Path $Root "build\license-studio-spec"
$OutputExe = Join-Path $DistPath "PhoEniX License Studio.exe"

foreach ($path in @($DistPath, $WorkPath, $SpecPath)) {
    $fullRoot = [IO.Path]::GetFullPath($Root).TrimEnd('\') + '\'
    $fullPath = [IO.Path]::GetFullPath($path)
    if (-not $fullPath.StartsWith($fullRoot, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to modify a path outside the workspace: $fullPath"
    }
    if (Test-Path -LiteralPath $path) {
        Remove-Item -LiteralPath $path -Recurse -Force
    }
}

if (-not (Test-Path -LiteralPath $Python)) {
    throw "Python runtime not found: $Python"
}

& $Python (Join-Path $Root "scripts\build_icon.py")
if ($LASTEXITCODE -ne 0) {
    throw "Icon generation failed."
}

$arguments = @(
    "-m", "PyInstaller",
    "--noconfirm",
    "--clean",
    "--onefile",
    "--windowed",
    "--noupx",
    "--name", "PhoEniX License Studio",
    "--distpath", $DistPath,
    "--workpath", $WorkPath,
    "--specpath", $SpecPath,
    "--paths", $Root,
    "--icon", (Join-Path $Root "installer\phoenix-bpu.ico"),
    (Join-Path $Root "license_studio.py")
)

& $Python @arguments
if ($LASTEXITCODE -ne 0) {
    throw "License Studio build failed with exit code $LASTEXITCODE."
}
if (-not (Test-Path -LiteralPath $OutputExe)) {
    throw "License Studio executable was not created: $OutputExe"
}

Write-Host "Publisher tool created: $OutputExe" -ForegroundColor Green
Write-Host "The private signing key is not embedded in this executable." -ForegroundColor Yellow
