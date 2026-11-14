@echo off
setlocal
cd /d "%~dp0"
set "HOST=0.0.0.0"
set "PORT=7862"
set "GUIDE_PY=python"
if exist "..\.venv\Scripts\python.exe" set "GUIDE_PY=..\.venv\Scripts\python.exe"
if exist ".venv\Scripts\python.exe" set "GUIDE_PY=.venv\Scripts\python.exe"
"%GUIDE_PY%" -X utf8 launch.py
if errorlevel 1 pause
