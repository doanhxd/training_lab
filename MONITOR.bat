@echo off
setlocal

rem GOLD Trader read-only monitor launcher.
rem Telegram settings are read from the environment/config; no order is sent by this GUI.
set "PROJECT_ROOT=%~dp0"
set "PYTHONPATH=%PROJECT_ROOT%..;%PYTHONPATH%"
if not exist "%PROJECT_ROOT%configs\strategies\rsiqui\final_m5.json" set "PROJECT_ROOT=%USERPROFILE%\Desktop\training_lab\"

set "PYTHONW=%PROJECT_ROOT%.venv\Scripts\pythonw.exe"
if not exist "%PYTHONW%" (
    echo Khong tim thay project venv: %PYTHONW%
    echo Hay cai dependencies cua training_lab truoc.
    exit /b 1
)

cd /d "%PROJECT_ROOT%"
start "GOLD Trader Monitor" /b "%PYTHONW%" -m training_lab.monitoring.rsiqui.monitor_gui --config "%PROJECT_ROOT%configs\strategies\rsiqui\final_m5.json" --symbol XAUUSD
exit /b 0
