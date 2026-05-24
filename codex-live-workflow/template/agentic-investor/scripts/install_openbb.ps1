$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$BundledPython = Join-Path $env:USERPROFILE ".cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
$Python = if (Test-Path -LiteralPath $BundledPython) { $BundledPython } else { "python" }
$Cache = Join-Path $ProjectRoot "vendor\pip-cache"
$Wheelhouse = Join-Path $Cache "wheelhouse"
$VendorPython = Join-Path $ProjectRoot "vendor\python"

function Invoke-Native {
  param(
    [Parameter(Mandatory = $true)]
    [string]$FilePath,

    [Parameter(Mandatory = $true)]
    [string[]]$Arguments
  )

  & $FilePath @Arguments
  if ($LASTEXITCODE -ne 0) {
    throw "Command failed with exit code ${LASTEXITCODE}: $FilePath $($Arguments -join ' ')"
  }
}

New-Item -ItemType Directory -Force -Path $Cache | Out-Null

$PipSourceArgs = @("--cache-dir", $Cache, "--upgrade")
if (Test-Path -LiteralPath $Wheelhouse) {
  $PipSourceArgs = @("--no-index", "--find-links", $Wheelhouse) + $PipSourceArgs
}

if (Test-Path -LiteralPath $VendorPython) {
  $OpenBbItems = Get-ChildItem -LiteralPath $VendorPython -Force | Where-Object {
    $_.Name -eq "openbb" -or $_.Name -like "openbb-*" -or $_.Name -like "openbb_*"
  }
  foreach ($Item in $OpenBbItems) {
    try {
      Remove-Item -LiteralPath $Item.FullName -Recurse -Force
    } catch {
      Write-Warning "Could not remove stale OpenBB vendor item '$($Item.FullName)': $($_.Exception.Message). Continuing because vendor\python is no longer loaded globally."
    }
  }
}

$PreviousPythonPath = $env:PYTHONPATH
$PreviousVendorFlag = $env:AGENTIC_ENABLE_VENDOR_PYTHON
try {
  $env:PYTHONPATH = $PreviousPythonPath
  $env:AGENTIC_ENABLE_VENDOR_PYTHON = "0"

  if ($env:AGENTIC_OPENBB_UPGRADE_PIP -eq "1") {
    Invoke-Native $Python @("-m", "pip", "install", "--upgrade", "pip")
  }
  Invoke-Native $Python (@("-m", "pip", "install", "openbb", "--no-deps") + $PipSourceArgs)
  Invoke-Native $Python (@(
    "-m", "pip", "install",
    "openbb-core",
    "openbb-equity",
    "openbb-index",
    "openbb-fixedincome",
    "openbb-currency",
    "openbb-federal-reserve",
    "openbb-fred",
    "openbb-yfinance"
  ) + $PipSourceArgs)

  Invoke-Native $Python @("-c", "import openbb; build = getattr(openbb, 'build', None); build() if callable(build) else None")
  Invoke-Native $Python @((Join-Path $ProjectRoot "scripts\run_task.py"), "openbb_smoke")
} finally {
  $env:PYTHONPATH = $PreviousPythonPath
  $env:AGENTIC_ENABLE_VENDOR_PYTHON = $PreviousVendorFlag
}
