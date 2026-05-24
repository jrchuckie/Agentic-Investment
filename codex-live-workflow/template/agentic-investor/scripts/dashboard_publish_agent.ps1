param(
    [switch]$AutoMarketHours,
    [int]$AutoEveryMinutes = 15,
    [switch]$SkipValuation
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$OpsDir = Join-Path $ProjectRoot "data\ops"
$LogDir = Join-Path $ProjectRoot "logs\publisher_agent"
$TriggerPath = Join-Path $OpsDir "publish-request.json"
$StatePath = Join-Path $OpsDir "publish-agent-state.json"
$LockPath = Join-Path $OpsDir "publish-agent.lock"
$PublishScript = Join-Path $ProjectRoot "scripts\publish_dashboard_now.ps1"

New-Item -ItemType Directory -Force -Path $OpsDir | Out-Null
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

$LogFile = Join-Path $LogDir ("publish_agent_{0}.log" -f (Get-Date -Format "yyyyMMdd"))

function Write-AgentLog {
    param([string]$Message)
    $line = "[{0}] {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $Message
    Add-Content -Path $LogFile -Value $line -Encoding UTF8
    Write-Host $line
}

function Read-State {
    if (-not (Test-Path $StatePath)) {
        return @{}
    }
    try {
        $json = Get-Content -Path $StatePath -Raw | ConvertFrom-Json
        $state = @{}
        foreach ($property in $json.PSObject.Properties) {
            $state[$property.Name] = $property.Value
        }
        return $state
    } catch {
        return @{}
    }
}

function Write-State {
    param([hashtable]$State)
    $State | ConvertTo-Json -Depth 6 | Set-Content -Path $StatePath -Encoding UTF8
}

function Test-MarketWindow {
    $now = Get-Date
    $minutes = $now.Hour * 60 + $now.Minute
    $start = 21 * 60 + 25
    $end = 5 * 60 + 15
    return ($minutes -ge $start) -or ($minutes -le $end)
}

function Invoke-DashboardPublish {
    param([string]$Reason)
    if (Test-Path $LockPath) {
        $lockAge = (Get-Date) - (Get-Item $LockPath).LastWriteTime
        if ($lockAge.TotalMinutes -lt 30) {
            Write-AgentLog "Skip publish; another publish appears active. Reason=$Reason"
            return
        }
        Remove-Item -LiteralPath $LockPath -Force -ErrorAction SilentlyContinue
    }

    Set-Content -Path $LockPath -Value (Get-Date).ToString("o") -Encoding UTF8
    try {
        Write-AgentLog "Start publish. Reason=$Reason"
        $args = @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", "`"$PublishScript`"")
        if ($SkipValuation) {
            $args += "-SkipValuation"
        }
        $process = Start-Process -FilePath "powershell.exe" -ArgumentList $args -WorkingDirectory $ProjectRoot -Wait -PassThru -WindowStyle Hidden
        $state = Read-State
        $state["lastPublishAt"] = (Get-Date).ToString("o")
        $state["lastReason"] = $Reason
        $state["lastExitCode"] = $process.ExitCode
        Write-State $state
        Write-AgentLog "Publish finished. ExitCode=$($process.ExitCode)"
    } finally {
        Remove-Item -LiteralPath $LockPath -Force -ErrorAction SilentlyContinue
    }
}

$state = Read-State
$shouldPublish = $false
$reason = ""

if (Test-Path $TriggerPath) {
    $shouldPublish = $true
    $reason = "manual-trigger"
    try {
        $trigger = Get-Content -Path $TriggerPath -Raw | ConvertFrom-Json
        if ($trigger.reason) {
            $reason = [string]$trigger.reason
        }
    } catch {
        $reason = "manual-trigger"
    }
    Remove-Item -LiteralPath $TriggerPath -Force -ErrorAction SilentlyContinue
}

if (-not $shouldPublish -and $AutoMarketHours -and (Test-MarketWindow)) {
    $last = $null
    if ($state["lastAutoPublishAt"]) {
        try {
            $last = [datetime]::Parse([string]$state["lastAutoPublishAt"])
        } catch {
            $last = $null
        }
    }
    if (-not $last -or ((Get-Date) - $last).TotalMinutes -ge $AutoEveryMinutes) {
        $shouldPublish = $true
        $reason = "auto-market-hours"
        $state["lastAutoPublishAt"] = (Get-Date).ToString("o")
        Write-State $state
    }
}

if ($shouldPublish) {
    Invoke-DashboardPublish -Reason $reason
} else {
    Write-AgentLog "No publish needed."
}
