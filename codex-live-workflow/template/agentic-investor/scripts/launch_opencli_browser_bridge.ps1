$ErrorActionPreference = "Stop"

$OpenCli = Join-Path $env:USERPROFILE "Documents\Codex\.tools\opencli.cmd"
$Chrome = "C:\Program Files\Google\Chrome\Application\chrome.exe"
$Extension = Join-Path $env:USERPROFILE "Documents\Codex\.tools\opencli-extension\unpacked"
$Profile = Join-Path $env:USERPROFILE "Documents\New project\agentic-investor\vendor\opencli-chrome-profile"

if (!(Test-Path $OpenCli)) {
  throw "OpenCLI command not found: $OpenCli"
}
if (!(Test-Path $Chrome)) {
  throw "Chrome not found: $Chrome"
}
if (!(Test-Path (Join-Path $Extension "manifest.json"))) {
  throw "OpenCLI extension folder not found: $Extension"
}

New-Item -ItemType Directory -Force -Path $Profile | Out-Null

Write-Host "Restarting OpenCLI daemon..."
& $OpenCli daemon restart | Out-Host
Start-Sleep -Seconds 2

Write-Host "Launching Chrome with OpenCLI Browser Bridge extension..."
Start-Process -FilePath $Chrome -ArgumentList @(
  "--user-data-dir=$Profile",
  "--load-extension=$Extension",
  "--disable-extensions-except=$Extension",
  "--no-first-run",
  "--no-default-browser-check",
  "https://x.com"
)

Write-Host ""
Write-Host "A Chrome window should open. Keep it open."
Write-Host "If Chrome asks about the extension, allow/keep OpenCLI enabled."
Write-Host "After the page loads, run this check from the project folder:"
Write-Host "& '$OpenCli' doctor"
