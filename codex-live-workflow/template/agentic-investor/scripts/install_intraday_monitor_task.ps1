param(
    [string]$TaskName = "Agentic Investor Intraday Monitor",
    [string]$StartTime = "21:30",
    [int]$IntervalMinutes = 15,
    [int]$DurationHours = 7
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$Runner = Join-Path $ProjectRoot "scripts\run_intraday_monitor_now.ps1"
$LauncherScript = Join-Path $ProjectRoot "scripts\run_hidden_powershell.vbs"
if (-not (Test-Path $Runner)) {
    throw "Runner not found: $Runner"
}
if (-not (Test-Path $LauncherScript)) {
    throw "Hidden launcher script not found: $LauncherScript"
}

$action = New-ScheduledTaskAction `
    -Execute "wscript.exe" `
    -Argument "//B //Nologo `"$LauncherScript`" `"$Runner`""

$start = [DateTime]::ParseExact($StartTime, "HH:mm", $null)
$runCount = [Math]::Floor(($DurationHours * 60) / $IntervalMinutes) + 1
$triggers = @()
for ($i = 0; $i -lt $runCount; $i++) {
    $time = $start.AddMinutes($i * $IntervalMinutes).ToString("HH:mm")
    $triggers += New-ScheduledTaskTrigger -Daily -At $time
}

$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -MultipleInstances IgnoreNew

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $action `
    -Trigger $triggers `
    -Settings $settings `
    -Description "Refresh Agentic Investor market data, paper fills, dashboard snapshot, and Firestore private snapshot in advisory-only paper-trade mode." `
    -Force | Out-Null

$lastTime = $start.AddMinutes(($runCount - 1) * $IntervalMinutes).ToString("HH:mm")
Write-Host "Installed Windows Scheduled Task: $TaskName"
Write-Host "Schedule: daily from $StartTime to $lastTime, every $IntervalMinutes minutes."
Write-Host "Window: hidden via wscript launcher"
Write-Host "Runner: $Runner"
Write-Host "Logs: $(Join-Path $ProjectRoot 'logs\intraday')"
