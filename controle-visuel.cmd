@echo off
setlocal
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "scripts\run_visual_tests.ps1"
if errorlevel 1 (
  echo.
  echo Le controle visuel a rencontre une erreur.
  pause
  exit /b 1
)
start "" "output\visual-regression\report.html"
exit /b 0
