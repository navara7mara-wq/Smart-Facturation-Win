param(
    [string]$Version = "2.5.2"
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$TestRoot = Join-Path $Root "build\installer-smoke"
$InstallDir = Join-Path $TestRoot "program"
$UserDataDir = Join-Path $TestRoot "user-data"
$Installer = Join-Path $Root "dist\installer\PhoEniX_BPU_Setup_${Version}_Test.exe"
$ReportPath = Join-Path $TestRoot "installer-smoke.json"

function Assert-WorkspacePath([string]$Path) {
    $resolvedRoot = [IO.Path]::GetFullPath($Root).TrimEnd('\') + '\'
    $resolvedPath = [IO.Path]::GetFullPath($Path)
    if (-not $resolvedPath.StartsWith($resolvedRoot, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to modify a path outside the workspace: $resolvedPath"
    }
}

function Run-Process([string]$FilePath, [string]$Arguments, [hashtable]$Environment = @{}) {
    $processInfo = New-Object System.Diagnostics.ProcessStartInfo
    $processInfo.FileName = $FilePath
    $processInfo.Arguments = $Arguments
    $processInfo.UseShellExecute = $false
    $processInfo.CreateNoWindow = $true
    foreach ($key in $Environment.Keys) {
        $processInfo.EnvironmentVariables[$key] = $Environment[$key]
    }
    $process = [System.Diagnostics.Process]::Start($processInfo)
    $process.WaitForExit()
    if ($process.ExitCode -ne 0) {
        throw "Process failed with exit code $($process.ExitCode): $FilePath $Arguments"
    }
}

Assert-WorkspacePath $TestRoot
if (Test-Path -LiteralPath $TestRoot) {
    Remove-Item -LiteralPath $TestRoot -Recurse -Force
}
New-Item -ItemType Directory -Path $TestRoot -Force | Out-Null

& (Join-Path $Root "scripts\build_installer.ps1") -Version $Version -SkipDesktopBuild -TestBuild
if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $Installer)) {
    throw "Test installer build failed."
}

$installArguments = "/VERYSILENT /SUPPRESSMSGBOXES /NORESTART /SP- /NOICONS /DIR=`"$InstallDir`""
Run-Process $Installer $installArguments
$InstalledExe = Join-Path $InstallDir "PhoEniX BPU.exe"
if (-not (Test-Path -LiteralPath $InstalledExe)) {
    throw "Installed executable was not found."
}
Run-Process $InstalledExe "--smoke-test" @{"PHOENIX_USER_DATA_DIR" = $UserDataDir}
$firstSmokeResult = Get-Content (Join-Path $UserDataDir "desktop-smoke-test.json") -Raw | ConvertFrom-Json
if ($firstSmokeResult.fresh_install_empty -ne "ok") {
    throw "Fresh installation is not empty."
}
if ($firstSmokeResult.default_admin -ne "secure-first-run-required") {
    throw "Fresh installation does not require secure administrator setup."
}

$MarkerPath = Join-Path $UserDataDir "update-preservation.marker"
Set-Content -LiteralPath $MarkerPath -Value "preserve" -Encoding ascii
Run-Process $Installer $installArguments
Run-Process $InstalledExe "--smoke-test" @{"PHOENIX_USER_DATA_DIR" = $UserDataDir}
if (-not (Test-Path -LiteralPath $MarkerPath)) {
    throw "User data was not preserved during update."
}

$Uninstaller = Join-Path $InstallDir "unins000.exe"
if (-not (Test-Path -LiteralPath $Uninstaller)) {
    throw "Uninstaller was not created."
}
Run-Process $Uninstaller "/VERYSILENT /SUPPRESSMSGBOXES /NORESTART"
if (Test-Path -LiteralPath $InstalledExe) {
    throw "Application files remain after uninstall."
}
if (-not (Test-Path -LiteralPath $MarkerPath)) {
    throw "User data was removed by uninstall."
}

$smokeResult = Get-Content (Join-Path $UserDataDir "desktop-smoke-test.json") -Raw | ConvertFrom-Json
$report = [ordered]@{
    version = $Version
    install = "ok"
    first_smoke = $firstSmokeResult
    update = "ok"
    data_preserved = $true
    uninstall = "ok"
    runtime = $smokeResult
}
$report | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $ReportPath -Encoding utf8
Write-Host "Installer smoke test passed: $ReportPath" -ForegroundColor Green
