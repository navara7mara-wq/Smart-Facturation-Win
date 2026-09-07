param()

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path))
$TestRoot = Join-Path $Root "build\upgrade-smoke"
$InstallDir = Join-Path $TestRoot "program"
$UserDataDir = Join-Path $TestRoot "user-data"
$OldInstaller = Join-Path $Root "dist\installer\PhoEniX_BPU_Setup_2.5.0.exe"
$NewInstaller = Join-Path $Root "dist\installer\PhoEniX_BPU_Setup_2.5.1.exe"
$MarkerTool = Join-Path $Root "quality\scripts\upgrade_marker.py"
$Report = Join-Path $TestRoot "upgrade-smoke.json"

function Assert-WorkspacePath([string]$Path) {
    $resolvedRoot = [IO.Path]::GetFullPath($Root).TrimEnd('\') + '\'
    $resolvedPath = [IO.Path]::GetFullPath($Path)
    if (-not $resolvedPath.StartsWith($resolvedRoot, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing path outside workspace: $resolvedPath"
    }
}

function Run-Process([string]$FilePath, [string]$Arguments, [hashtable]$Environment = @{}) {
    $info = New-Object System.Diagnostics.ProcessStartInfo
    $info.FileName = $FilePath
    $info.Arguments = $Arguments
    $info.UseShellExecute = $false
    $info.CreateNoWindow = $true
    foreach ($key in $Environment.Keys) { $info.EnvironmentVariables[$key] = $Environment[$key] }
    $process = [System.Diagnostics.Process]::Start($info)
    $process.WaitForExit()
    if ($process.ExitCode -ne 0) { throw "Exit $($process.ExitCode): $FilePath $Arguments" }
}

Assert-WorkspacePath $TestRoot
if (-not (Test-Path -LiteralPath $OldInstaller)) { throw "2.5.0 installer missing" }
if (-not (Test-Path -LiteralPath $NewInstaller)) { throw "2.5.1 installer missing" }
if (Test-Path -LiteralPath $TestRoot) { Remove-Item -LiteralPath $TestRoot -Recurse -Force }
New-Item -ItemType Directory -Path $TestRoot -Force | Out-Null

$installArguments = "/VERYSILENT /SUPPRESSMSGBOXES /NORESTART /SP- /NOICONS /DIR=`"$InstallDir`""
Run-Process $OldInstaller $installArguments
$InstalledExe = Join-Path $InstallDir "PhoEniX BPU.exe"
Run-Process $InstalledExe "--smoke-test" @{"PHOENIX_USER_DATA_DIR" = $UserDataDir}
$OldSmoke = Get-Content (Join-Path $UserDataDir "desktop-smoke-test.json") -Raw | ConvertFrom-Json
$Database = Join-Path $UserDataDir "data\pos_ai.sqlite3"
$Seed = & python $MarkerTool seed $Database | ConvertFrom-Json
if ($LASTEXITCODE -ne 0) { throw "Unable to seed 2.5.0 business marker" }

Run-Process $NewInstaller $installArguments
Run-Process $InstalledExe "--smoke-test" @{"PHOENIX_USER_DATA_DIR" = $UserDataDir}
$NewSmoke = Get-Content (Join-Path $UserDataDir "desktop-smoke-test.json") -Raw | ConvertFrom-Json
$Verification = & python $MarkerTool verify $Database | ConvertFrom-Json
if ($LASTEXITCODE -ne 0) { throw "Unable to verify migrated business marker" }

$Uninstaller = Join-Path $InstallDir "unins000.exe"
Run-Process $Uninstaller "/VERYSILENT /SUPPRESSMSGBOXES /NORESTART"
if (Test-Path -LiteralPath $InstalledExe) { throw "Application remained after uninstall" }
if (-not (Test-Path -LiteralPath $Database)) { throw "User database was removed by uninstall" }

[ordered]@{
    from_version = "2.5.0"
    to_version = "2.5.1"
    old_smoke = $OldSmoke
    seeded = $Seed
    new_smoke = $NewSmoke
    verified = $Verification
    uninstall = "ok"
    user_database_preserved = $true
} | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $Report -Encoding utf8
Write-Host "Upgrade smoke passed: $Report" -ForegroundColor Green
