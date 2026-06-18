@echo off
rem Launches the Local Manager via the canonical entry point (run.py).
rem Do NOT use `python -m app.main` — it causes a circular import.
cd /d "%~dp0"
python run.py
