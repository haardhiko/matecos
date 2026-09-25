@echo off
setlocal
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1

echo ======================================================================
echo Launching MATECOS FastAPI Application Server...
echo ======================================================================

set "PY_EXE=%~dp0.venv\Scripts\python.exe"
set "UV_EXE=%LOCALAPPDATA%\hermes\bin\uv.exe"

if exist "%PY_EXE%" (
    echo Using Virtualenv Python: %PY_EXE%
    "%PY_EXE%" scripts\run_server.py
    goto end
)

if exist "%UV_EXE%" (
    echo Using uv runner: %UV_EXE%
    "%UV_EXE%" run python scripts\run_server.py
    goto end
)

echo Warning: Virtual environment not detected at .venv. Attempting system python...
python scripts\run_server.py

:end
pause
