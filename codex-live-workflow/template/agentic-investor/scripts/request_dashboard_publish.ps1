param(
    [string]$Reason = "manual-dashboard-publish-request"
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$OpsDir = Join-Path $ProjectRoot "data\ops"
$TriggerPath = Join-Path $OpsDir "publish-request.json"

New-Item -ItemType Directory -Force -Path $OpsDir | Out-Null

$payload = @{
    requestedAt = (Get-Date).ToString("o")
    reason = $Reason
    advisoryOnly = $true
}

$payload | ConvertTo-Json -Depth 4 | Set-Content -Path $TriggerPath -Encoding UTF8
Write-Host "Publish request written: $TriggerPath"
