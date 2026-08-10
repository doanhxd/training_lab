@echo off
setlocal

rem GOLD Trader read-only monitor launcher.
rem Telegram settings are read from the environment/config; no order is sent by this GUI.
set "PROJECT_ROOT=%~dp0"
if not exist "%PROJECT_ROOT%configs\strategies\rsiqui\final_x_m5.json" set "PROJECT_ROOT=%USERPROFILE%\Desktop\training_lab\"

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
start "GOLD Trader Monitor" /b "%PYTHONW%" -m training_lab.monitoring.rsiqui.monitor_gui --config "%PROJECT_ROOT%configs\strategies\rsiqui\final_x_m5.json" --symbol XAUUSD
exit /b 0
