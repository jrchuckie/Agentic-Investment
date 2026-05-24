param(
    [switch]$SkipMarket,
    [switch]$SkipValuation,
    [switch]$SkipOptions,
    [switch]$SkipNews,
    [switch]$SkipCommittee,
    [switch]$SkipSnapshot,
    [switch]$SkipFirestore,
    [switch]$SkipHosting,
    [switch]$StrictFirestore,
    [switch]$ForcePublish
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$LogDir = Join-Path $ProjectRoot "logs\publisher"
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
$LogFile = Join-Path $LogDir ("publish_dashboard_{0}.log" -f (Get-Date -Format "yyyyMMdd_HHmmss"))

function Write-Step {
    param([string]$Message)
    $line = "[{0}] {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $Message
    Write-Host $line
    Add-Content -Path $LogFile -Value $line -Encoding UTF8
}

function Find-Python {
    $candidates = @()
    if ($env:AGENTIC_INVESTOR_PYTHON) {
        $candidates += $env:AGENTIC_INVESTOR_PYTHON
    }
    $codexPython = Join-Path $env:USERPROFILE ".cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
    $candidates += $codexPython
    $pythonCommand = Get-Command python -ErrorAction SilentlyContinue
    if ($pythonCommand) {
        $candidates += $pythonCommand.Source
    }

    foreach ($candidate in $candidates) {
        if (-not $candidate) {
            continue
        }
        if (Test-Path $candidate) {
            return (Resolve-Path $candidate).Path
        }
    }

    throw "Python not found. Set AGENTIC_INVESTOR_PYTHON to the Python executable used by this project."
}

function Invoke-PythonStep {
    param(
        [string]$Name,
        [string[]]$PythonArgs
    )
    Write-Step $Name
    & $script:PythonExe @PythonArgs 2>&1 | Tee-Object -FilePath $LogFile -Append
    if ($LASTEXITCODE -ne 0) {
        throw "$Name failed with exit code $LASTEXITCODE. See log: $LogFile"
    }
}

function Invoke-PythonStepSoft {
    param(
        [string]$Name,
        [string[]]$PythonArgs
    )
    Write-Step $Name
    & $script:PythonExe @PythonArgs 2>&1 | Tee-Object -FilePath $LogFile -Append
    if ($LASTEXITCODE -ne 0) {
        Write-Step "$Name finished with warning exit code $LASTEXITCODE. Continue publish flow; dashboard will show valuation data gap if needed."
    }
}

function Test-NetworkPreflight {
    Write-Step "Network preflight for market data and Firebase APIs"
    $null = & $script:PythonExe "scripts\network_preflight.py" 2>&1 | Tee-Object -FilePath $LogFile -Append
    if ($LASTEXITCODE -eq 0) {
        Write-Step "Network preflight passed."
        return $true
    }
    Write-Step "Network preflight failed. Skip network refresh and preserve last good market/valuation cache."
    return $false
}

Set-Location $ProjectRoot
$script:PythonExe = Find-Python
Write-Step "Project: $ProjectRoot"
Write-Step "Python: $script:PythonExe"
Write-Step "Log: $LogFile"

$networkReady = Test-NetworkPreflight

if (-not $SkipMarket) {
    if ($networkReady) {
        Invoke-PythonStepSoft "Refresh market snapshot" -PythonArgs @("scripts\run_task.py", "market_snapshot")
    } else {
        Write-Step "Skip market refresh because network preflight failed."
    }
}

if (-not $SkipValuation) {
    if ($networkReady) {
        Invoke-PythonStepSoft "Refresh valuation and analyst consensus snapshot" -PythonArgs @("scripts\run_task.py", "valuation_snapshot")
    } else {
        Write-Step "Skip valuation refresh because network preflight failed."
    }
}

if (-not $SkipOptions) {
    if ($networkReady) {
        Invoke-PythonStepSoft "Refresh option chain snapshot" -PythonArgs @("scripts\run_task.py", "options_snapshot")
    } else {
        Write-Step "Skip option chain refresh because network preflight failed."
    }
}

if (-not $SkipNews) {
    if ($networkReady) {
        Invoke-PythonStepSoft "Refresh breaking news and event radar" -PythonArgs @("scripts\run_task.py", "intel_monitor")
        Invoke-PythonStepSoft "Refresh news/social sentiment feed" -PythonArgs @("scripts\run_task.py", "social_sentiment_feed")
    } else {
        Write-Step "Skip news/social refresh because network preflight failed."
    }
}

if (-not $SkipCommittee) {
    Invoke-PythonStepSoft "Run research committee gate" -PythonArgs @("scripts\run_task.py", "research_committee")
}

if (-not $SkipSnapshot) {
    Invoke-PythonStep "Refresh local dashboard snapshot" -PythonArgs @("scripts\run_task.py", "dashboard_snapshot")
}

if (-not $SkipFirestore) {
    if ((-not $networkReady) -and (-not $ForcePublish)) {
        if ($StrictFirestore) {
            throw "Network preflight failed; StrictFirestore is enabled so Firestore publish is blocked. See log: $LogFile"
        }
        Write-Step "Skip Firestore publish because network preflight failed."
    } elseif ($StrictFirestore) {
        Invoke-PythonStep "Publish private Firestore snapshot" -PythonArgs @("scripts\run_task.py", "firebase_publish_snapshot")
    } else {
        Invoke-PythonStepSoft "Publish private Firestore snapshot" -PythonArgs @("scripts\run_task.py", "firebase_publish_snapshot")
    }
}

if (-not $SkipHosting) {
    if ((-not $networkReady) -and (-not $ForcePublish)) {
        Write-Step "Skip Firebase Hosting publish because network preflight failed."
    } else {
        Invoke-PythonStep "Publish Firebase Hosting static dashboard" -PythonArgs @("scripts\deploy_firebase_hosting_rest.py")
    }
}

Write-Step "Publish flow completed."
