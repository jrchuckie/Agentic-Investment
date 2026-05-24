param(
    [string]$TaskName = "AgenticInvestor Dashboard Publisher",
    [int]$WaitSeconds = 180
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$LogDir = Join-Path $ProjectRoot "logs\publisher"

$task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if (-not $task) {
    throw "Scheduled task not found: $TaskName. Run scripts\install_dashboard_publisher_task.ps1 first."
}

$before = @()
if (Test-Path $LogDir) {
    $before = @(Get-ChildItem -Path $LogDir -Filter "publish_dashboard_*.log" -File | Sort-Object LastWriteTime)
}

Start-ScheduledTask -TaskName $TaskName
Write-Host "Started scheduled task: $TaskName"

$deadline = (Get-Date).AddSeconds($WaitSeconds)
$lastState = ""
do {
    Start-Sleep -Seconds 3
    $info = Get-ScheduledTaskInfo -TaskName $TaskName
    $task = Get-ScheduledTask -TaskName $TaskName
    $lastState = [string]$task.State
    Write-Host ("State: {0}; LastTaskResult: {1}" -f $lastState, $info.LastTaskResult)
    if ($lastState -ne "Running") {
        break
    }
} while ((Get-Date) -lt $deadline)

$after = @()
if (Test-Path $LogDir) {
    $after = @(Get-ChildItem -Path $LogDir -Filter "publish_dashboard_*.log" -File | Sort-Object LastWriteTime)
}

$newLogs = @($after | Where-Object { $before.FullName -notcontains $_.FullName })
$latest = if ($newLogs.Count) { $newLogs[-1] } elseif ($after.Count) { $after[-1] } else { $null }

if ($latest) {
    Write-Host "Latest log: $($latest.FullName)"
    Get-Content -Path $latest.FullName -Tail 80
} else {
    Write-Host "No publisher log found yet."
}

if ($lastState -eq "Running") {
    throw "Publish task is still running after $WaitSeconds seconds."
}
