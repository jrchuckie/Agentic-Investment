param()

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$LogDir = Join-Path $ProjectRoot "logs\intraday"
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
$LogFile = Join-Path $LogDir ("intraday_monitor_{0}.log" -f (Get-Date -Format "yyyyMMdd_HHmmss"))

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

Set-Location $ProjectRoot
$PythonExe = Find-Python
"[{0}] Start intraday monitor. Project={1}; Python={2}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $ProjectRoot, $PythonExe | Tee-Object -FilePath $LogFile

$pythonExitCode = 0
$oldErrorActionPreference = $ErrorActionPreference
try {
    # Windows PowerShell surfaces native stderr as non-terminating errors. For our read-only monitor,
    # treat stderr as log output and rely on the process exit code for failure.
    $ErrorActionPreference = "Continue"
    & $PythonExe "scripts\run_task.py" "intraday_monitor" 2>&1 | Tee-Object -FilePath $LogFile -Append
    $pythonExitCode = $LASTEXITCODE
} finally {
    $ErrorActionPreference = $oldErrorActionPreference
}
if ($pythonExitCode -ne 0) {
    throw "intraday_monitor failed with exit code $pythonExitCode. See log: $LogFile"
}

"[{0}] Intraday monitor completed." -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss") | Tee-Object -FilePath $LogFile -Append
