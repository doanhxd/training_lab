@echo off
setlocal

rem Standalone GOLD Monitor: Local MT5 observer only.
rem No strategy config, runner, Telegram, MT5 login, or trade order path.
set "PROJECT_ROOT=%~dp0"
if not exist "%PROJECT_ROOT%monitoring\gold_monitor\app.py" set "PROJECT_ROOT=%USERPROFILE%\Desktop\training_lab\"

set "VENV_DIR=%PROJECT_ROOT%.venv311"
if not exist "%VENV_DIR%\Scripts\pythonw.exe" set "VENV_DIR=%PROJECT_ROOT%.venv"
set "PYTHONW=%VENV_DIR%\Scripts\pythonw.exe"
if not exist "%PYTHONW%" (
    echo Khong tim thay project venv: %PYTHONW%
    echo Hay cai dependencies cua training_lab truoc.
    exit /b 1
)

cd /d "%PROJECT_ROOT%"
set "PYTHONPATH=%PROJECT_ROOT%..;%PYTHONPATH%"
start "GOLD Monitor" /b "%PYTHONW%" -m training_lab.monitoring.gold_monitor.app
exit /b 0
