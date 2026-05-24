$ErrorActionPreference = "Stop"

$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $ProjectRoot

$Label = if ($args.Count -gt 0) { $args -join " " } else { "15m decision" }
$Python = Join-Path $ProjectRoot ".venv-openbb\\Scripts\\python.exe"

function Invoke-PythonWithTimeout {
  param(
    [Parameter(Mandatory = $true)][string[]]$Argv,
    [int]$TimeoutSec = 180
  )

  $stdoutPath = Join-Path $env:TEMP ("ai_ps_stdout_{0}.log" -f ([Guid]::NewGuid().ToString("n")))
  $stderrPath = Join-Path $env:TEMP ("ai_ps_stderr_{0}.log" -f ([Guid]::NewGuid().ToString("n")))

  $result = [ordered]@{
    ok = $true
    timed_out = $false
    exit_code = $null
    error = ""
    stdout_tail = ""
    stderr_tail = ""
  }

  try {
    $p = Start-Process -FilePath $Python -ArgumentList $Argv -NoNewWindow -PassThru -RedirectStandardOutput $stdoutPath -RedirectStandardError $stderrPath
    $exited = $false
    try { $exited = $p.WaitForExit([Math]::Max(0, $TimeoutSec) * 1000) } catch { $exited = $false }
    if (-not $exited) {
      try {
        # Stop-Process can hang in some pathological cases; use taskkill as a best-effort fallback.
        cmd /c "taskkill /PID $($p.Id) /T /F" | Out-Null
      } catch {
        try { Stop-Process -Id $p.Id -Force -ErrorAction SilentlyContinue } catch {}
      }
      $result.ok = $false
      $result.timed_out = $true
      $result.error = "timeout after ${TimeoutSec}s (likely OpenD not reachable)"
    } else {
      $p.Refresh()
      $result.exit_code = $p.ExitCode
      if ($p.ExitCode -ne 0) {
        $result.ok = $false
        $result.error = "exit code $($p.ExitCode)"
      }
    }
  } catch {
    $result.ok = $false
    $result.error = "exception: $($_.Exception.Message)"
  }

  try {
    if (Test-Path $stdoutPath) { $result.stdout_tail = (Get-Content -Raw -Path $stdoutPath -ErrorAction SilentlyContinue) }
    if (Test-Path $stderrPath) { $result.stderr_tail = (Get-Content -Raw -Path $stderrPath -ErrorAction SilentlyContinue) }
  } catch {}

  if ($result.stdout_tail.Length -gt 2000) { $result.stdout_tail = $result.stdout_tail.Substring($result.stdout_tail.Length - 2000) }
  if ($result.stderr_tail.Length -gt 2000) { $result.stderr_tail = $result.stderr_tail.Substring($result.stderr_tail.Length - 2000) }

  return $result
}

$accountRefresh = [ordered]@{ ok = $false; error = ""; timed_out = $false; exit_code = $null }
$intradayMonitor = [ordered]@{ ok = $false; error = ""; timed_out = $false; exit_code = $null }
$wechatPush = [ordered]@{ ok = $false; error = ""; timed_out = $false; exit_code = $null }

if (-not (Test-Path $Python)) {
  $msg = "Python runtime not found: $Python"
  Write-Warning $msg
  $accountRefresh.ok = $false
  $accountRefresh.error = $msg
  $intradayMonitor.ok = $false
  $intradayMonitor.error = $msg
  $wechatPush.ok = $false
  $wechatPush.error = $msg
} else {
  # Safety boundary: read-only REAL account mode (do not unlock / place orders).
  $env:MOOMOO_REAL_ACCOUNT_READ = "1"

  $openDHost = $env:MOOMOO_OPEND_HOST
  if ([string]::IsNullOrWhiteSpace($openDHost)) { $openDHost = "127.0.0.1" }
  $openDHost = $openDHost.ToString().Trim()
  if (-not $openDHost) { $openDHost = "127.0.0.1" }

  $openDPortRaw = $env:MOOMOO_OPEND_PORT
  if ([string]::IsNullOrWhiteSpace($openDPortRaw)) { $openDPortRaw = "11111" }
  $openDPortRaw = $openDPortRaw.ToString().Trim()
  if (-not $openDPortRaw) { $openDPortRaw = "11111" }
  $openDPort = 11111
  try { $openDPort = [int]$openDPortRaw } catch { $openDPort = 11111 }

  $tnc = $null
  try { $tnc = Test-NetConnection -ComputerName $openDHost -Port $openDPort -WarningAction SilentlyContinue -InformationLevel Quiet } catch { $tnc = $false }
  if (-not $tnc) {
    $msg = "OpenD not reachable at ${openDHost}:${openDPort} (skip refresh; will still push conclusion)"
    Write-Warning $msg
    $accountRefresh = [ordered]@{ ok = $false; timed_out = $false; exit_code = $null; error = $msg; stdout_tail = ""; stderr_tail = "" }
    $intradayMonitor = [ordered]@{ ok = $false; timed_out = $false; exit_code = $null; error = $msg; stdout_tail = ""; stderr_tail = "" }
  } else {
    # 1) Refresh REAL account snapshot (best-effort, time-bounded)
    Write-Host "[1/3] Refreshing REAL account snapshot..." -ForegroundColor Cyan
    $accountRefresh = Invoke-PythonWithTimeout -Argv @("scripts\\refresh_moomoo_real_account_fast.py") -TimeoutSec 60
    if (-not $accountRefresh.ok) { Write-Warning ("REAL account refresh failed: {0}" -f $accountRefresh.error) }

    # 2) Refresh intraday monitor outputs (best-effort, time-bounded)
    Write-Host "[2/3] Refreshing intraday monitor..." -ForegroundColor Cyan
    $intradayMonitor = Invoke-PythonWithTimeout -Argv @("scripts\\run_task.py", "intraday_monitor") -TimeoutSec 90
    if (-not $intradayMonitor.ok) { Write-Warning ("Intraday monitor failed: {0}" -f $intradayMonitor.error) }
  }
}

$runtimeDir = Join-Path $ProjectRoot "data\\runtime"
New-Item -ItemType Directory -Force -Path $runtimeDir | Out-Null
$ctxStamp = Get-Date -Format "yyyyMMdd_HHmmss"
$ctxPath = Join-Path $runtimeDir ("opening_decision_push_context_{0}_{1}.json" -f $ctxStamp, ([Guid]::NewGuid().ToString("n")))
$ctxLatestPath = Join-Path $runtimeDir "opening_decision_push_context_latest.json"

$ctx = @{
  timestamp = (Get-Date).ToString("s")
  label = $Label
  account_refresh = $accountRefresh
  intraday_monitor = $intradayMonitor
  wechat_push = $wechatPush
}

$ctx | ConvertTo-Json -Depth 10 | Set-Content -Path $ctxPath -Encoding UTF8
Copy-Item -Force -LiteralPath $ctxPath -Destination $ctxLatestPath

Write-Host "[3/3] Sending WeChat push..." -ForegroundColor Cyan
try {
  node "scripts\\opening_decision_push.mjs" "--context" $ctxPath
  $exitCode = $LASTEXITCODE
  $errMsg = ""
  if ($exitCode -ne 0) { $errMsg = "exit code $exitCode" }
  $wechatPush = [ordered]@{
    ok = ($exitCode -eq 0)
    timed_out = $false
    exit_code = $exitCode
    error = $errMsg
    stdout_tail = ""
    stderr_tail = ""
  }
} catch {
  $wechatPush = [ordered]@{
    ok = $false
    timed_out = $false
    exit_code = $null
    error = "exception: $($_.Exception.Message)"
    stdout_tail = ""
    stderr_tail = ""
  }
}

Write-Host ("[3/3] WeChat push result: ok={0} exit_code={1} timed_out={2}" -f $wechatPush.ok, $wechatPush.exit_code, $wechatPush.timed_out) -ForegroundColor DarkGray

$ctx.wechat_push = $wechatPush
$ctx | ConvertTo-Json -Depth 10 | Set-Content -Path $ctxPath -Encoding UTF8
Copy-Item -Force -LiteralPath $ctxPath -Destination $ctxLatestPath

if (-not $wechatPush.ok) {
  Write-Warning ("WeChat push failed: {0}" -f $wechatPush.error)
  if ($wechatPush.stdout_tail) { Write-Output $wechatPush.stdout_tail }
  if ($wechatPush.stderr_tail) { Write-Output $wechatPush.stderr_tail }
  $exitCode = 1
  if ($wechatPush.timed_out) { $exitCode = 124 }
  elseif ($wechatPush.exit_code -ne $null) { $exitCode = $wechatPush.exit_code }
  exit $exitCode
}

if ($wechatPush.stdout_tail) { Write-Output $wechatPush.stdout_tail }
if ($wechatPush.stderr_tail) { Write-Output $wechatPush.stderr_tail }

Write-Output (ConvertTo-Json ([ordered]@{
  task = "opening_decision_push"
  timestamp = (Get-Date).ToString("s")
  label = $Label
  context = $ctxPath
  context_latest = $ctxLatestPath
  account_refresh = $accountRefresh
  intraday_monitor = $intradayMonitor
  wechat_push = $wechatPush
}) -Depth 10)

$exitCode = 0
if ($wechatPush.exit_code -ne $null) { $exitCode = $wechatPush.exit_code }
exit $exitCode
