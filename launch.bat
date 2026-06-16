@echo off
rem ZillaSoft Local Manager launcher
rem Opens a server console (shows the auth token; close it to stop the server)
rem and then opens the UI in your default browser.
cd /d "%~dp0"
start "ZillaSoft Local Manager" cmd /k ".venv\Scripts\python.exe run.py"
timeout /t 3 >nul
start "" "http://localhost:5555"
