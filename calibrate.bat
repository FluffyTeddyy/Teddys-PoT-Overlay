@echo off
rem Start this, then switch to Path of Titans within 5 seconds. A screenshot opens where you
rem drag a box around the minimap and then around the quest list.
cd /d "%~dp0"
".venv\Scripts\python.exe" -m tools.calibrate %*
pause
