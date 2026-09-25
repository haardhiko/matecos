@echo off
setlocal
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1

echo ======================================================================
echo Running MATECOS Multi-Agent Pipeline Demo...
echo ======================================================================

set "PY_EXE=%~dp0.venv\Scripts\python.exe"
set "UV_EXE=%LOCALAPPDATA%\hermes\bin\uv.exe"

if exist "%PY_EXE%" (
    "%PY_EXE%" scripts\demo_run.py
    goto end
)

if exist "%UV_EXE%" (
    "%UV_EXE%" run python scripts\demo_run.py
    goto end
)

python scripts\demo_run.py

:end
pause
