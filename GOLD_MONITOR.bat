@echo off
setlocal
rem Standalone GOLD Monitor: Local MT5 observer only.
rem No strategy config, runner, Telegram, MT5 login, or trade order path.
set "PROJECT_ROOT=%~dp0"
if not exist "%PROJECT_ROOT%monitoring\gold_monitor\app.py" (
    echo Khong tim thay GOLD Monitor source:
    echo %PROJECT_ROOT%monitoring\gold_monitor\app.py
    pause
    exit /b 1
)

rem Prefer the project environment when present.
set "VENV_DIR=%PROJECT_ROOT%.venv311"
if not exist "%VENV_DIR%\Scripts\pythonw.exe" set "VENV_DIR=%PROJECT_ROOT%.venv"
if exist "%VENV_DIR%\Scripts\pythonw.exe" (
    set "PYTHONPATH=%PROJECT_ROOT%..;%PYTHONPATH%"
    start "GOLD Monitor" /b "%VENV_DIR%\Scripts\pythonw.exe" -m training_lab.monitoring.gold_monitor.app
    exit /b 0
)

rem Portable fallback for another PC: use the installed Python launcher/interpreter.
where py >nul 2>&1
if %errorlevel%==0 (
    set "PYTHON_CMD=py -3"
    goto :check_dependencies
)
where python >nul 2>&1
if %errorlevel%==0 (
    set "PYTHON_CMD=python"
    goto :check_dependencies
)

echo Khong tim thay Python 3.
echo Cai Python 3.11+ tu https://www.python.org/downloads/ roi chay lai file nay.
pause
exit /b 1

:check_dependencies
set "PYTHONPATH=%PROJECT_ROOT%..;%PYTHONPATH%"
%PYTHON_CMD% -c "import MetaTrader5, psutil" >nul 2>&1
if not %errorlevel%==0 (
    echo Thieu dependency GOLD Monitor tren may nay.
    echo Chay lenh sau trong Command Prompt:
    echo %PYTHON_CMD% -m pip install -r "%PROJECT_ROOT%requirements-gold-monitor.txt"
    pause
    exit /b 1
)

rem Console mode is intentional in fallback mode so startup errors remain visible.
%PYTHON_CMD% -m training_lab.monitoring.gold_monitor.app
if not %errorlevel%==0 pause
exit /b %errorlevel%
