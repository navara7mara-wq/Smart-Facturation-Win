param(
    [Parameter(Mandatory = $true)]
    [string]$CertificateThumbprint,
    [string]$TimestampUrl = "http://timestamp.digicert.com",
    [string]$Version = "2.5.2"
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$certificate = Get-ChildItem Cert:\CurrentUser\My, Cert:\LocalMachine\My -CodeSigningCert -ErrorAction SilentlyContinue |
    Where-Object { $_.Thumbprint -eq $CertificateThumbprint } |
    Select-Object -First 1
if (-not $certificate) {
    throw "Code-signing certificate not found: $CertificateThumbprint"
}

$signtool = Get-ChildItem "${env:ProgramFiles(x86)}\Windows Kits\10\bin" -Filter signtool.exe -Recurse -ErrorAction SilentlyContinue |
    Sort-Object FullName -Descending |
    Select-Object -First 1 -ExpandProperty FullName
if (-not $signtool) {
    throw "signtool.exe was not found. Install the Windows SDK signing tools."
}

$files = @(
    (Join-Path $Root "dist\desktop\PhoEniX BPU\PhoEniX BPU.exe"),
    (Join-Path $Root "dist\installer\PhoEniX_BPU_Setup_$Version.exe")
)
foreach ($file in $files) {
    if (-not (Test-Path -LiteralPath $file)) {
        throw "Release file not found: $file"
    }
    & $signtool sign /sha1 $CertificateThumbprint /fd SHA256 /tr $TimestampUrl /td SHA256 $file
    if ($LASTEXITCODE -ne 0) {
        throw "Signing failed: $file"
    }
    & $signtool verify /pa /v $file
    if ($LASTEXITCODE -ne 0) {
        throw "Signature verification failed: $file"
    }
}

Write-Host "Release binaries signed and verified." -ForegroundColor Green
