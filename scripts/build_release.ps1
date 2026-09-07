param(
    [string]$Version = "2.5.2",
    [switch]$SkipInstaller
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$Dist = Join-Path $Root "dist"
$DesktopDir = Join-Path $Dist "desktop\PhoEniX BPU"
$PortableZip = Join-Path $Dist "PhoEniX_BPU_Portable_$Version.zip"
$HashFile = Join-Path $Dist "SHA256SUMS.txt"
$PublisherTool = Join-Path $Dist "publisher\PhoEniX License Studio.exe"

& (Join-Path $Root "scripts\build_desktop.ps1") -Version $Version
if ($LASTEXITCODE -ne 0) {
    throw "Desktop package build failed."
}

& (Join-Path $Root "scripts\build_license_studio.ps1")
if ($LASTEXITCODE -ne 0) {
    throw "License Studio build failed."
}

if (Test-Path -LiteralPath $PortableZip) {
    Remove-Item -LiteralPath $PortableZip -Force
}
Compress-Archive -Path (Join-Path $DesktopDir "*") -DestinationPath $PortableZip -CompressionLevel Optimal
Write-Host "Portable archive created: $PortableZip" -ForegroundColor Green

if (-not $SkipInstaller) {
    & (Join-Path $Root "scripts\build_installer.ps1") -Version $Version -SkipDesktopBuild
    if ($LASTEXITCODE -ne 0) {
        throw "Installer build failed."
    }
}

$artifacts = @($PortableZip)
if (Test-Path -LiteralPath $PublisherTool) {
    $artifacts += $PublisherTool
}
$Installer = Join-Path $Dist "installer\PhoEniX_BPU_Setup_$Version.exe"
if (Test-Path -LiteralPath $Installer) {
    $artifacts += $Installer
}
$hashLines = foreach ($artifact in $artifacts) {
    $hash = Get-FileHash -LiteralPath $artifact -Algorithm SHA256
    "$($hash.Hash.ToLowerInvariant())  $([IO.Path]::GetFileName($artifact))"
}
Set-Content -LiteralPath $HashFile -Value $hashLines -Encoding ascii
Write-Host "SHA-256 manifest created: $HashFile" -ForegroundColor Green
