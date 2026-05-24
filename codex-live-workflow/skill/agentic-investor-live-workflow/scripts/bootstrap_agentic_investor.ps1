param(
  [string]$WorkspaceRoot = (Join-Path $env:USERPROFILE "Documents\New project\agentic-investor"),
  [string]$CodexWorkspace = (Join-Path $env:USERPROFILE "Documents\Codex"),
  [switch]$Force
)

$ErrorActionPreference = "Stop"

$SkillRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$PackageRoot = (Resolve-Path (Join-Path $SkillRoot "..\..\..")).Path
$TemplateRoot = Join-Path $PackageRoot "template\agentic-investor"
$IntegrationRoot = Join-Path $PackageRoot "integrations\weixin"
$CodexSkillRoot = Join-Path $env:USERPROFILE ".codex\skills\agentic-investor-live-workflow"

function Copy-Directory {
  param(
    [Parameter(Mandatory = $true)][string]$Source,
    [Parameter(Mandatory = $true)][string]$Destination
  )
  if ((Test-Path $Destination) -and -not $Force) {
    Write-Host "Exists: $Destination (use -Force to overwrite)"
    return
  }
  New-Item -ItemType Directory -Force -Path $Destination | Out-Null
  Get-ChildItem -LiteralPath $Source -Force | ForEach-Object {
    Copy-Item -LiteralPath $_.FullName -Destination (Join-Path $Destination $_.Name) -Recurse -Force
  }
}

Copy-Directory -Source $SkillRoot -Destination $CodexSkillRoot
Copy-Directory -Source $TemplateRoot -Destination $WorkspaceRoot

New-Item -ItemType Directory -Force -Path $CodexWorkspace | Out-Null
foreach ($name in @("weixin-send.mjs", "codex-push-weixin.mjs", "weixin-codex-bridge.mjs")) {
  $src = Join-Path $IntegrationRoot $name
  if (Test-Path $src) {
    $dst = Join-Path $CodexWorkspace $name
    if ((Test-Path $dst) -and -not $Force) {
      Write-Host "Exists: $dst (use -Force to overwrite)"
    } else {
      Copy-Item -LiteralPath $src -Destination $dst -Force
    }
  }
}

$envExample = Join-Path $WorkspaceRoot ".env.example"
$envFile = Join-Path $WorkspaceRoot ".env"
if ((Test-Path $envExample) -and -not (Test-Path $envFile)) {
  Copy-Item -LiteralPath $envExample -Destination $envFile
}

$stateExample = Join-Path $WorkspaceRoot "state.example.json"
$stateFile = Join-Path $WorkspaceRoot "state.json"
if ((Test-Path $stateExample) -and -not (Test-Path $stateFile)) {
  Copy-Item -LiteralPath $stateExample -Destination $stateFile
}

Write-Output (@{
  status = "installed"
  skill = $CodexSkillRoot
  workspace = $WorkspaceRoot
  codex_workspace = $CodexWorkspace
  next_steps = @(
    "Fill .env with local private paths and credentials.",
    "Log in to moomoo/OpenD and confirm 127.0.0.1:11111.",
    "Log in to OpenClaw WeChat and send one message to create latest-target.json.",
    "Run self_check.ps1."
  )
} | ConvertTo-Json -Depth 4)
