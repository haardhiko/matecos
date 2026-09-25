@echo off
setlocal
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1

:menu
cls
echo ======================================================================
echo           MATECOS - Multi-Agent Tool Ecosystem Control Launcher
echo ======================================================================
echo.
echo   [1] Start FastAPI Application Server (http://localhost:8000)
echo   [2] Run Multi-Agent End-to-End Pipeline Demo (CLI Simulation)
echo   [3] Run 4-Pillar Comprehensive Live Showcase
echo   [4] Run Full Test Suite (249 unit, security, contract, e2e tests)
echo   [5] Open Interactive Python Shell with MATECOS Environment
echo   [6] Exit
echo.
echo ======================================================================
set /p choice="Select an option [1-6]: "

if "%choice%"=="1" (
    call run_server.bat
    goto menu
)
if "%choice%"=="2" (
    call run_demo.bat
    goto menu
)
if "%choice%"=="3" (
    call run_showcase.bat
    goto menu
)
if "%choice%"=="4" (
    call run_tests.bat
    goto menu
)
if "%choice%"=="5" (
    "%~dp0.venv\Scripts\python.exe"
    goto menu
)
if "%choice%"=="6" (
    exit /b 0
)

echo Invalid choice. Please enter 1, 2, 3, 4, 5, or 6.
timeout /t 2 >nul
goto menu
