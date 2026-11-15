@echo off
rem Starts the overlay. It lives in the system tray (bottom-right); right-click the icon for options.
rem On the first run it sets everything up automatically.
cd /d "%~dp0"
set "NEEDSETUP="
if not exist ".venv\Scripts\python.exe" set "NEEDSETUP=1"
for %%M in (riparia gondwa panjura) do if not exist "data\maps\%%M.webp" set "NEEDSETUP=1"
if defined NEEDSETUP (
    echo First run: setting things up...
    call setup.bat --quiet
    if errorlevel 1 exit /b 1
)
rem Newer version of the overlay on GitHub? Installs it first (quick; skipped when offline).
".venv\Scripts\python.exe" -m tools.self_update
".venv\Scripts\python.exe" -m potoverlay
if errorlevel 1 pause
