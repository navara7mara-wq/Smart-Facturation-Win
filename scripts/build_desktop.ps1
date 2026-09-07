param(
    [string]$Version = "2.5.2",
    [string]$Python = "python",
    [string]$NodeRuntimeRoot = ""
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$BuildRoot = Join-Path $Root "build"
$RuntimeStage = Join-Path $BuildRoot "desktop-runtime"
$WorkPath = Join-Path $BuildRoot "pyinstaller"
$SpecPath = Join-Path $BuildRoot "spec"
$DistPath = Join-Path $Root "dist\desktop"
$OutputExe = Join-Path $DistPath "PhoEniX BPU\PhoEniX BPU.exe"

function Assert-WorkspacePath([string]$Path) {
    $resolvedRoot = [IO.Path]::GetFullPath($Root).TrimEnd('\') + '\'
    $resolvedPath = [IO.Path]::GetFullPath($Path)
    if (-not $resolvedPath.StartsWith($resolvedRoot, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to modify a path outside the workspace: $resolvedPath"
    }
}

foreach ($path in @($RuntimeStage, $WorkPath, $SpecPath, $DistPath)) {
    Assert-WorkspacePath $path
    if (Test-Path -LiteralPath $path) {
        Remove-Item -LiteralPath $path -Recurse -Force
    }
}

& $Python (Join-Path $Root "scripts\build_icon.py")
if ($LASTEXITCODE -ne 0) {
    throw "Application icon generation failed."
}

if (-not $NodeRuntimeRoot) {
    if ($env:PHOENIX_NODE_RUNTIME_SOURCE) {
        $NodeRuntimeRoot = $env:PHOENIX_NODE_RUNTIME_SOURCE
    } else {
        $NodeRuntimeRoot = Join-Path $env:USERPROFILE ".cache\codex-runtimes\codex-primary-runtime\dependencies\node"
    }
}
$NodeExe = Join-Path $NodeRuntimeRoot "bin\node.exe"
$NodeModules = Join-Path $NodeRuntimeRoot "node_modules"
if (-not (Test-Path -LiteralPath $NodeExe)) {
    throw "Node runtime not found: $NodeExe"
}
foreach ($module in @("playwright", "playwright-core")) {
    if (-not (Test-Path -LiteralPath (Join-Path $NodeModules $module))) {
        throw "Required Node module not found: $module"
    }
}

$RuntimeNode = Join-Path $RuntimeStage "node"
$RuntimeModules = Join-Path $RuntimeNode "node_modules"
New-Item -ItemType Directory -Path $RuntimeModules -Force | Out-Null
Copy-Item -LiteralPath $NodeExe -Destination (Join-Path $RuntimeNode "node.exe")
foreach ($module in @("playwright", "playwright-core")) {
    Copy-Item -LiteralPath (Join-Path $NodeModules $module) -Destination $RuntimeModules -Recurse
}

$arguments = @(
    "-m", "PyInstaller",
    "--noconfirm",
    "--clean",
    "--onedir",
    "--windowed",
    "--noupx",
    "--name", "PhoEniX BPU",
    "--distpath", $DistPath,
    "--workpath", $WorkPath,
    "--specpath", $SpecPath,
    "--paths", $Root,
    "--icon", (Join-Path $Root "installer\phoenix-bpu.ico"),
    "--version-file", (Join-Path $Root "installer\version_info.txt"),
    "--hidden-import", "scripts.export_invoice_template",
    "--add-data", ((Join-Path $Root "static") + ";static"),
    "--add-data", ((Join-Path $Root "templates") + ";templates"),
    "--add-data", ((Join-Path $Root "database\schema.sql") + ";database"),
    "--add-data", ((Join-Path $Root "resources") + ";resources"),
    "--add-data", ((Join-Path $Root "docs") + ";docs"),
    "--add-data", ((Join-Path $Root "config\license_public_key.pem") + ";config"),
    "--add-data", ((Join-Path $Root "scripts\render_pdf.js") + ";scripts"),
    "--add-data", ($RuntimeStage + ";runtime"),
    (Join-Path $Root "desktop.py")
)

& $Python @arguments
if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller failed with exit code $LASTEXITCODE."
}
if (-not (Test-Path -LiteralPath $OutputExe)) {
    throw "Desktop executable was not created: $OutputExe"
}

Write-Host "Desktop package created: $OutputExe" -ForegroundColor Green
