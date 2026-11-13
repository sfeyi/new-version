@echo off
setlocal
cd /d "%~dp0"
set "GUIDE_PY=python"
if exist "..\.venv\Scripts\python.exe" set "GUIDE_PY=..\.venv\Scripts\python.exe"
if exist ".venv\Scripts\python.exe" set "GUIDE_PY=.venv\Scripts\python.exe"
"%GUIDE_PY%" app.py
if errorlevel 1 pause
