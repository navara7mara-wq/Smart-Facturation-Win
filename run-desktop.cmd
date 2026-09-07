@echo off
cd /d "%~dp0"
where pythonw.exe >nul 2>&1
if errorlevel 1 (
  echo Python est introuvable. Lancez setup.cmd avant de demarrer l'application.
  pause
  exit /b 1
)
start "" pythonw.exe "%~dp0desktop.py"
