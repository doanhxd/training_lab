@echo off
setlocal

set "PROJECT_ROOT=%~dp0"
if not exist "%PROJECT_ROOT%configs\strategies\rsiqui\final_trailing_c_m5_demo.json" set "PROJECT_ROOT=%USERPROFILE%\Desktop\trading_lab\"

cd /d "%PROJECT_ROOT%"

title RSIQUI FINAL_TRAILING_C - XAUUSDc - 0.20 lot - $5,000

set "PYTHON=%PROJECT_ROOT%.venv\Scripts\python.exe"
set "CONFIG=%PROJECT_ROOT%configs\strategies\rsiqui\final_trailing_c_m5_demo.json"
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
echo RSIQUI FINAL_TRAILING_C / XAUUSDc / M5
echo Profile : $5,000 account contract
echo Contract: 0.20 lot / SL $240 / TP $100 / spread cap $0.40
echo Trailing: trigger +2.0 price / lock $3 / step 0.5 price
echo Magic   : 573505
echo Symbol  : XAUUSDc (fallback XAUUSD)
echo Account : runner uses the currently logged-in MT5 terminal
echo Project : %PROJECT_ROOT%
echo Safety  : this launcher starts the bot; it does not place a test order
echo ================================================================

"%PYTHON%" -m trading_lab.runners.mt5.rsiqui_final_trailing_demo --config "%CONFIG%" %*
set "EXIT_CODE=%ERRORLEVEL%"

echo.
echo Runner exited with code %EXIT_CODE%.
pause
exit /b %EXIT_CODE%
