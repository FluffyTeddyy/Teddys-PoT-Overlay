@echo off
cd /d "%~dp0"
".venv\Scripts\python.exe" -m tools.quest_editor
if errorlevel 1 pause
