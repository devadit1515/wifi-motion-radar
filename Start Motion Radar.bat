@echo off
rem Double-click to start Motion Radar (live WiFi + sonar) and open the dashboard.
rem Close this window to stop it.
cd /d "%~dp0"

if not exist ".venv\Scripts\radar.exe" (
  echo First run: setting up Python packages. This takes a few minutes once.
  python -m venv .venv
  .venv\Scripts\python -m pip install -e ".[sonar]"
)

if not exist "web\dist\index.html" (
  echo First run: building the dashboard.
  call npm --prefix web install
  call npm --prefix web run build
)

echo.
echo Motion Radar is running at http://127.0.0.1:8765
echo Close this window to stop it.
echo.
.venv\Scripts\radar serve --sonar --open
pause
