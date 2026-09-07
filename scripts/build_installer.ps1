param(
    [string]$Version = "2.5.2",
    [switch]$SkipDesktopBuild,
    [switch]$TestBuild
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$IssPath = Join-Path $Root "installer\PhoEniX_BPU.iss"
$DesktopExe = Join-Path $Root "dist\desktop\PhoEniX BPU\PhoEniX BPU.exe"

if (-not $SkipDesktopBuild -and -not (Test-Path -LiteralPath $DesktopExe)) {
    & (Join-Path $Root "scripts\build_desktop.ps1") -Version $Version
    if ($LASTEXITCODE -ne 0) {
        throw "Desktop package build failed."
    }
}
if (-not (Test-Path -LiteralPath $DesktopExe)) {
    throw "Desktop executable is missing: $DesktopExe"
}

$candidates = @(@(
    (Get-Command ISCC.exe -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Source -ErrorAction SilentlyContinue),
    "$env:ProgramFiles(x86)\Inno Setup 6\ISCC.exe",
    "$env:ProgramFiles\Inno Setup 6\ISCC.exe",
    "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe",
    "D:\Program Files\Inno Setup 6\ISCC.exe"
) | Where-Object { $_ -and (Test-Path $_) })

if (-not $candidates) {
    $registryLocations = @(
        "HKLM:\Software\Microsoft\Windows\CurrentVersion\Uninstall\*",
        "HKLM:\Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\*",
        "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\*"
    )
    $installLocation = Get-ItemProperty $registryLocations -ErrorAction SilentlyContinue |
        Where-Object { $_.DisplayName -like "*Inno Setup*" -and $_.InstallLocation } |
        Select-Object -First 1 -ExpandProperty InstallLocation
    if ($installLocation) {
        $candidate = Join-Path $installLocation "ISCC.exe"
        if (Test-Path $candidate) {
            $candidates = @($candidate)
        }
    }
}

if (-not $candidates) {
    throw "Inno Setup 6 is required. Install it, then rerun this script."
}

$env:PHOENIX_VERSION = $Version
if ($TestBuild) {
    $env:PHOENIX_APP_ID = "{{3A267D89-9114-46D2-B775-B2B5370DD7C6}"
    $env:PHOENIX_OUTPUT_SUFFIX = "_Test"
} else {
    $env:PHOENIX_APP_ID = ""
    $env:PHOENIX_OUTPUT_SUFFIX = ""
}
& $candidates[0] $IssPath
if ($LASTEXITCODE -ne 0) {
    throw "Inno Setup failed with exit code $LASTEXITCODE."
}

Write-Host "Installer created in dist\installer" -ForegroundColor Green
