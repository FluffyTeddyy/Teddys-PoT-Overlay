@echo off
rem Updates quests (and map images if they changed) from Potservers after a game patch.
rem Takes about 15 minutes: every quest is re-downloaded, slowly, to respect the site's limits.
rem Quests you added yourself in the quest editor are kept.
cd /d "%~dp0"
echo ============================================================
echo  Updating quest data (about 15 minutes).
echo  You can keep playing: the overlay keeps working meanwhile.
echo  Easier: the overlay's tray menu has "Update quest data",
echo  which does this in the background and switches by itself.
echo ============================================================
echo.
if not exist ".venv\Scripts\python.exe" call setup.bat --quiet
".venv\Scripts\python.exe" -m tools.fetch_potservers --refresh
if errorlevel 1 (
    echo.
    echo Update failed - check your internet connection and run update-data.bat again.
    echo It continues where it stopped.
    pause
    exit /b 1
)
echo.
echo ============================================================
echo  Quest data updated. If the overlay is running, right-click
echo  its tray icon and pick "Reload config and quests".
echo ============================================================
pause
