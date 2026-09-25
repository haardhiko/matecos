@echo off
setlocal
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1

echo ======================================================================
echo Running MATECOS Test Suite (249 tests)...
echo ======================================================================

set "PY_EXE=%~dp0.venv\Scripts\python.exe"
set "UV_EXE=%LOCALAPPDATA%\hermes\bin\uv.exe"

if exist "%PY_EXE%" (
    "%PY_EXE%" -m pytest tests/unit tests/security tests/contract tests/end_to_end
    goto end
)

if exist "%UV_EXE%" (
    "%UV_EXE%" run python -m pytest tests/unit tests/security tests/contract tests/end_to_end
    goto end
)

python -m pytest tests/unit tests/security tests/contract tests/end_to_end

:end
pause
