@echo off
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup_panel.ps1"
set "result=%errorlevel%"
pause
exit /b %result%
