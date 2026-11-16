@echo off
REM Starts the ShopSense backend and opens the prototype. Close this window to stop it.
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (
  echo Run setup.bat first.
  pause & exit /b 1
)
start "" http://localhost:8000/
.venv\Scripts\python.exe app.py
pause
