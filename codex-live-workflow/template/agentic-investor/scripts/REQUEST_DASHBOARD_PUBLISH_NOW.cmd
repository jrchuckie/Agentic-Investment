@echo off
setlocal
cd /d "%~dp0\.."
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0request_dashboard_publish.ps1" -Reason "manual-dashboard-publish-request"
echo.
echo Publish request created. The dashboard publish agent should pick it up within about 1 minute.
pause
endlocal
