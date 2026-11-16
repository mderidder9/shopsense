@echo off
REM One-time setup for the ShopSense backend. Double-click this file.
cd /d "%~dp0"

set PY=
where py >nul 2>nul && set PY=py -3
if not defined PY ( where python >nul 2>nul && set PY=python )
if not defined PY (
  echo.
  echo  Python was not found. Install it first - see README step 1 - then run setup.bat again.
  pause & exit /b 1
)

echo Creating the project's private Python environment (.venv)...
%PY% -m venv .venv || ( echo Could not create .venv & pause & exit /b 1 )

echo Installing libraries (anthropic, flask, python-dotenv)...
.venv\Scripts\python.exe -m pip install --disable-pip-version-check -q -r requirements.txt || ( echo Install failed & pause & exit /b 1 )

if not exist .env (
  copy .env.example .env >nul
  echo Created .env - Notepad will open it now. Paste your API key after the = sign, save, close.
  notepad .env
) else (
  echo .env already exists - leaving it alone.
)

echo.
echo  Setup done. Double-click start.bat to run ShopSense.
pause
