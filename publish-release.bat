@echo off
rem Publish a new version to GitHub. Friends' overlays install it the next time they start run.bat.
cd /d "%~dp0"
set /p NOTES="What changed in this version? "
if "%NOTES%"=="" (
    echo Nothing entered, nothing published.
    pause
    exit /b 1
)
".venv\Scripts\python.exe" -m tools.publish_release "%NOTES%"
pause
