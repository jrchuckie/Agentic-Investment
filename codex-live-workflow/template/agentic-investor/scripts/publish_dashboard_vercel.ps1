param(
    [switch]$Prod,
    [switch]$SkipSnapshot
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$DashboardRoot = Join-Path $ProjectRoot "dashboard"
$PythonExe = Join-Path $env:USERPROFILE ".cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"

if (-not (Test-Path $PythonExe)) {
    $pythonCommand = Get-Command python -ErrorAction SilentlyContinue
    if (-not $pythonCommand) {
        throw "Python not found. Set up the Codex runtime or add python to PATH."
    }
    $PythonExe = $pythonCommand.Source
}

Set-Location $ProjectRoot

if (-not $SkipSnapshot) {
    & $PythonExe "scripts\run_task.py" dashboard_snapshot
    if ($LASTEXITCODE -ne 0) {
        throw "dashboard_snapshot failed with exit code $LASTEXITCODE"
    }
}

$argsList = @("vercel", "deploy", $DashboardRoot, "--yes")
if ($Prod) {
    $argsList += "--prod"
}

& npx.cmd @argsList
if ($LASTEXITCODE -ne 0) {
    throw "Vercel deploy failed with exit code $LASTEXITCODE"
}
