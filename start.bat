@echo off
rem Launches the Local Manager via the canonical entry point (run.py).
rem Do NOT use `python -m app.main` — it causes a circular import.
cd /d "%~dp0"
echo Starting ZillaSoft Local Manager...
echo Once it says "Uvicorn running", open http://localhost:5555 in your browser.
echo Close this window to stop the server.
echo.
python run.py
echo.
echo Server stopped. Press any key to close this window.
pause >nul
