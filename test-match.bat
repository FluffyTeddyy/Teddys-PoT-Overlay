@echo off
rem Checks the minimap matcher against data\calibration.png (saved by calibrate.bat)
rem and writes data\locator_debug.png showing where it thinks you are.
cd /d "%~dp0"
".venv\Scripts\python.exe" -m tools.test_locator %*
pause
