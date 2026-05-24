param(
  [string]$WorkspaceRoot = (Join-Path $env:USERPROFILE "Documents\New project\agentic-investor"),
  [string]$CodexWorkspace = (Join-Path $env:USERPROFILE "Documents\Codex")
)

$ErrorActionPreference = "Continue"

function Test-CommandName {
  param([string]$Name)
  return [bool](Get-Command $Name -ErrorAction SilentlyContinue)
}

$pythonPath = Join-Path $WorkspaceRoot ".venv-openbb\Scripts\python.exe"
if (-not (Test-Path $pythonPath)) { $pythonPath = "python" }

$checks = [ordered]@{
  workspace_exists = Test-Path $WorkspaceRoot
  skill_exists = Test-Path (Join-Path $env:USERPROFILE ".codex\skills\agentic-investor-live-workflow\SKILL.md")
  node_available = Test-CommandName "node"
  codex_available = Test-CommandName "codex.exe"
  python_candidate = $pythonPath
  moomoo_opend_11111 = $false
  weixin_send_script = Test-Path (Join-Path $CodexWorkspace "weixin-send.mjs")
  codex_push_weixin_script = Test-Path (Join-Path $CodexWorkspace "codex-push-weixin.mjs")
  weixin_latest_target = Test-Path (Join-Path $env:USERPROFILE ".codex\weixin-bridge\latest-target.json")
  real_account_snapshot = Test-Path (Join-Path $WorkspaceRoot "data\broker\moomoo\real_account_latest.json")
}

try {
  $checks.moomoo_opend_11111 = [bool](Test-NetConnection -ComputerName "127.0.0.1" -Port 11111 -WarningAction SilentlyContinue -InformationLevel Quiet)
} catch {
  $checks.moomoo_opend_11111 = $false
}

$warnings = @()
if (-not $checks.moomoo_opend_11111) { $warnings += "OpenD is not reachable at 127.0.0.1:11111." }
if (-not $checks.weixin_latest_target) { $warnings += "WeChat latest-target.json is missing; send the bot one message first." }
if (-not $checks.real_account_snapshot) { $warnings += "No local real_account_latest.json yet; run read-only moomoo refresh after OpenD login." }

Write-Output (@{
  status = if ($warnings.Count) { "needs_attention" } else { "ok" }
  checks = $checks
  warnings = $warnings
  policy = "advisory-only; read-only moomoo; no unlock; no broker orders"
} | ConvertTo-Json -Depth 5)
