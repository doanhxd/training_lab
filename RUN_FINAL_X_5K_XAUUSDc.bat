@echo off
setlocal

set "PROJECT_ROOT=%~dp0"
if not exist "%PROJECT_ROOT%configs\strategies\rsiqui\final_x_m5.json" set "PROJECT_ROOT=%USERPROFILE%\Desktop\training_lab\"

cd /d "%PROJECT_ROOT%"

title RSIQUI FINAL_X - XAUUSDc - 0.10 lot - $5,000

set "VENV_DIR=%PROJECT_ROOT%.venv311"
if not exist "%VENV_DIR%\Scripts\python.exe" set "VENV_DIR=%PROJECT_ROOT%.venv"
set "PYTHON=%VENV_DIR%\Scripts\python.exe"
set "CONFIG=%PROJECT_ROOT%configs\strategies\rsiqui\final_x_m5.json"
set "PYTHONPATH=%PROJECT_ROOT%.."

if not exist "%PYTHON%" (
    echo [ERROR] Project Python not found:
    echo         %PYTHON%
    echo Expected the project repository at:
    echo         %PROJECT_ROOT%
    echo Create the project .venv first, then run this launcher again.
    pause
    exit /b 1
)

if not exist "%CONFIG%" (
    echo [ERROR] Profile config not found:
    echo         %CONFIG%
    pause
    exit /b 1
)

echo ================================================================
echo RSIQUI FINAL_X / XAUUSDc / M5
echo Profile : $5,000 account contract
echo Contract: 0.10 lot / SL $100 / TP $30 / spread cap $0.30
echo Trailing: disabled (fixed SL/TP)
echo Magic   : 573505
echo Symbol  : XAUUSDc (fallback XAUUSD)
echo Account : runner uses the currently logged-in MT5 terminal
echo Project : %PROJECT_ROOT%
echo Safety  : this launcher starts the bot; it does not place a test order
echo ================================================================

"%PYTHON%" -m training_lab.runners.mt5.rsiqui_final_trailing --config "%CONFIG%" %*
set "EXIT_CODE=%ERRORLEVEL%"

echo.
echo Runner exited with code %EXIT_CODE%.
pause
exit /b %EXIT_CODE%
