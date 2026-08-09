param(
    [int]$Port = 8000,
    [string]$HostName = "127.0.0.1",
    [switch]$NoBrowser
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $Root

$env:PHOENIX_HOST = $HostName
$env:PHOENIX_PORT = [string]$Port
$Url = "http://${HostName}:${Port}"

if (-not $NoBrowser) {
    Start-Job -ScriptBlock {
        param($TargetUrl)
        Start-Sleep -Seconds 2
        Start-Process $TargetUrl
    } -ArgumentList $Url | Out-Null
}

Write-Host "Starting PhoEniX BPU on $Url"
python app.py
