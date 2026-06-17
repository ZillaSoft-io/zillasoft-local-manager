@echo off
cd /d "%~dp0"
start /min python -m app.main
timeout /t 3 /nobreak > nul
start http://localhost:5555
