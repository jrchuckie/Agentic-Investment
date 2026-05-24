@echo off
setlocal
cd /d "%~dp0\.."
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0install_dashboard_publisher_task.ps1"
echo.
echo If you see "Installed scheduled task", setup is complete.
pause
endlocal
