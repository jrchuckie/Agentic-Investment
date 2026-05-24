param(
    [string]$TaskName = "AgenticInvestor Dashboard Publisher",
    [int]$EveryMinutes = 15,
    [switch]$NoSchedule,
    [switch]$SkipValuation,
    [switch]$TriggerWatcherOnly
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$AgentScript = Join-Path $ProjectRoot "scripts\dashboard_publish_agent.ps1"
$LauncherScript = Join-Path $ProjectRoot "scripts\run_hidden_powershell.vbs"
$LogDir = Join-Path $ProjectRoot "logs\publisher"
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

if (-not (Test-Path $AgentScript)) {
    throw "Publish agent script not found: $AgentScript"
}
if (-not (Test-Path $LauncherScript)) {
    throw "Hidden launcher script not found: $LauncherScript"
}

$publishArgs = @(
    "//B",
    "//Nologo",
    "`"$LauncherScript`"",
    "`"$AgentScript`""
)
if (-not $TriggerWatcherOnly) {
    $publishArgs += "-AutoMarketHours"
}
if ($EveryMinutes) {
    $publishArgs += @("-AutoEveryMinutes", [string]$EveryMinutes)
}
if ($SkipValuation) {
    $publishArgs += "-SkipValuation"
}
$argument = $publishArgs -join " "

$action = New-ScheduledTaskAction -Execute "wscript.exe" -Argument $argument -WorkingDirectory $ProjectRoot
$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 25)

$triggers = @()
if (-not $NoSchedule) {
    $trigger = New-ScheduledTaskTrigger `
        -Once `
        -At (Get-Date).AddMinutes(1) `
        -RepetitionInterval (New-TimeSpan -Minutes 1) `
        -RepetitionDuration (New-TimeSpan -Days 3650)
    $triggers += $trigger
}

$description = "Runs Agentic Investor dashboard publish agent from normal Windows Task Scheduler context. The agent watches data\ops\publish-request.json and auto-publishes during US market hours."

$existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($existing) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
}

if ($triggers.Count -gt 0) {
    Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $triggers -Settings $settings -Description $description | Out-Null
} else {
    Register-ScheduledTask -TaskName $TaskName -Action $action -Settings $settings -Description $description | Out-Null
}

Write-Host "Installed scheduled task: $TaskName"
Write-Host "Project: $ProjectRoot"
Write-Host "Command: wscript.exe $argument"
Write-Host "Window: hidden via wscript launcher"
if ($NoSchedule) {
    Write-Host "Schedule: on-demand only"
} else {
    Write-Host "Schedule: every 1 minute; publishes on request and during US market hours every $EveryMinutes minutes"
}
Write-Host "Logs: $LogDir"
