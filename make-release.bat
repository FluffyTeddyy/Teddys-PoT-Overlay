@echo off
rem Builds dist\PoT-Quest-Overlay-<date>.zip (about 1 MB) to send to friends.
cd /d "%~dp0"
".venv\Scripts\python.exe" -m tools.make_release
if not errorlevel 1 explorer "%~dp0dist"
pause
