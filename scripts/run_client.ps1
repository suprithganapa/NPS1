# NPS LAB EL - Client (ICMP knock + download + decrypt)
# Run in Terminal 2 as Administrator (unless -Simulation)
# Start the server first.
#
# Usage:
#   .\scripts\run_client.ps1
#   .\scripts\run_client.ps1 -Simulation
#   .\scripts\run_client.ps1 -Output artifacts\my_video.mp4

param(
    [switch]$Simulation,
    [string]$Config = "config\generated\knocker.yaml",
    [string]$Output = "artifacts\received_video.mp4"
)

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot\..

$env:PYTHONIOENCODING = "utf-8"
$env:PYTHONUTF8 = "1"

if (-not (Test-Path $Config)) {
    Write-Error "Missing $Config. Run .\scripts\run_server.ps1 once first (it generates configs), or: python scripts\generate_lab_configs.py --server-ip 172.20.10.2 --knocker-ip 172.20.10.1"
}

$pyArgs = @("scripts\video_receiver.py", "--config", $Config, "--output", $Output)
if ($Simulation) { $pyArgs += "--simulation" }

Write-Host "Starting CLIENT  config=$Config  output=$Output" -ForegroundColor Green
python @pyArgs
