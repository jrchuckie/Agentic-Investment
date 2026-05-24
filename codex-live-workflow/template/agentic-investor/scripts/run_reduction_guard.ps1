$ErrorActionPreference = "Stop"

$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $ProjectRoot

$Python = Join-Path $ProjectRoot ".venv-openbb\Scripts\python.exe"
if (-not (Test-Path $Python)) {
  $Python = "python"
}

& $Python "scripts\reduction_guard.py" @args
