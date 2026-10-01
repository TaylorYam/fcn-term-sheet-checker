@echo off
if not exist "%~dp0.venv\Scripts\pythonw.exe" (
  echo Please run setup_panel.cmd first.
  pause
  exit /b 1
)
start "" /D "%~dp0" "%~dp0.venv\Scripts\pythonw.exe" "%~dp0launch_panel.pyw"
