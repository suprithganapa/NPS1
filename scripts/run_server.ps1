# NPS LAB EL - Server (ICMP sniffer + HTTP video)
# Run in Terminal 1 as Administrator (unless -Simulation)
#
# Usage:
#   .\scripts\run_server.ps1
#   .\scripts\run_server.ps1 -Simulation
#   .\scripts\run_server.ps1 -HttpOnly

param(
    [switch]$Simulation,
    [switch]$HttpOnly,
    [string]$Config = "config\generated\server.yaml"
)

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot\..

$env:PYTHONIOENCODING = "utf-8"
$env:PYTHONUTF8 = "1"

if (-not (Test-Path $Config)) {
    Write-Host "Missing $Config - generating lab configs from config\config_ip.yaml ..." -ForegroundColor Yellow
    python scripts\generate_lab_configs.py `
        --server-ip 172.20.10.2 `
        --knocker-ip 172.20.10.1 `
        --server-video-port 8765
}

$pyArgs = @("scripts\run_lab_server.py", "--config", $Config)
if ($Simulation) { $pyArgs += "--simulation" }
if ($HttpOnly)   { $pyArgs += "--http-only" }

Write-Host "Starting SERVER  config=$Config" -ForegroundColor Cyan
python @pyArgs
