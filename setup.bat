@echo off
rem First-time setup: finds (or installs) Python, creates .venv, installs the packages and
rem downloads the map images. Safe to run again; it skips whatever is already done.
setlocal
cd /d "%~dp0"
title PoT Quest Overlay setup

if exist ".venv\Scripts\python.exe" goto :packages

echo Looking for Python 3.11 - 3.13...
set "PYEXE="
set "PYARG="
rem 1) The "py" launcher that comes with python.org installs.
for %%V in (3.12 3.13 3.11) do (
    if not defined PYEXE (
        py -%%V -c "import sys" >nul 2>nul && (set "PYEXE=py" & set "PYARG=-%%V")
    )
)
rem 2) The usual per-user install folders.
for %%V in (312 313 311) do (
    if not defined PYEXE if exist "%LOCALAPPDATA%\Programs\Python\Python%%V\python.exe" set "PYEXE=%LOCALAPPDATA%\Programs\Python\Python%%V\python.exe"
)
rem 3) "python" on PATH (ignoring the Microsoft Store placeholder, which fails this check).
if not defined PYEXE (
    python -c "import sys; sys.exit(0 if (3, 11) <= sys.version_info[:2] <= (3, 13) else 1)" >nul 2>nul && set "PYEXE=python"
)

if defined PYEXE goto :venv

echo.
echo Python 3.12 is needed but wasn't found.
choice /c YN /m "Install Python 3.12 now with winget (official python.org build, about 25 MB)"
if errorlevel 2 goto :nopython
winget install --id Python.Python.3.12 -e --scope user --accept-package-agreements --accept-source-agreements
set "PYEXE=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
if not exist "%PYEXE%" goto :nopython

:venv
echo.
echo Creating the Python environment (.venv)...
"%PYEXE%" %PYARG% -m venv .venv
if not exist ".venv\Scripts\python.exe" goto :venvfail

:packages
echo.
echo Installing packages (about 200 MB download the first time, a few minutes)...
".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -q --upgrade pip
".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -r requirements.txt
if errorlevel 1 goto :pipfail

set "NEEDMAPS="
for %%M in (riparia gondwa panjura) do if not exist "data\maps\%%M.webp" set "NEEDMAPS=1"
if not defined NEEDMAPS goto :done
echo.
echo Downloading map images from Potservers (about 21 MB)...
".venv\Scripts\python.exe" -m tools.fetch_potservers --images-only
if errorlevel 1 goto :mapfail

:done
echo.
echo ============================================================
echo  Setup done. Start the overlay with run.bat.
echo  Screen positions are worked out for your resolution. Only if
echo  markers look off, run calibrate.bat once with the game open.
echo ============================================================
if not "%~1"=="--quiet" pause
exit /b 0

:nopython
echo.
echo Python 3.12 is required. Get it from https://www.python.org/downloads/
echo (tick "Add python.exe to PATH" in the installer), then run setup.bat again.
pause
exit /b 1

:venvfail
echo.
echo Could not create the .venv folder.
echo If this folder is inside Documents, Desktop or Pictures, Windows "Controlled folder
echo access" may be blocking Python. Move the folder somewhere like C:\Games\pot-quest-overlay
echo and run setup.bat again.
pause
exit /b 1

:pipfail
echo.
echo Installing packages failed. Check your internet connection and run setup.bat again.
pause
exit /b 1

:mapfail
echo.
echo Downloading the maps failed. Check your internet connection and run setup.bat again.
pause
exit /b 1
