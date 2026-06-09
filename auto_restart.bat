@echo off
title Trading Bot - 24/7 Auto-Restart
cd /d "%~dp0"

:: Start persistent auxiliary processes once (confluence paper + liq/skew/L2 harvesters),
:: dedup-safe. main.py itself is run by the restart loop below.
call "%~dp0start_all.bat"

:loop
echo.
echo ============================================
echo  Starting bot... (auto-restart on crash)
echo  Time: %date% %time%
echo ============================================
echo.

:: Clear __pycache__ before every start so fixes always load
for /d /r %%d in (__pycache__) do @if exist "%%d" rd /s /q "%%d" 2>nul

venv\Scripts\python.exe main.py

:: Check exit code: 0 = clean shutdown, non-zero = crash
if %ERRORLEVEL% EQU 0 (
    echo.
    echo ============================================
    echo  Bot exited cleanly at %date% %time%
    echo  NOT restarting (clean exit code 0).
    echo  Run this script again to restart manually.
    echo ============================================
    pause
    goto :eof
)

echo.
echo ============================================
echo  Bot CRASHED at %date% %time%
echo  Exit code: %ERRORLEVEL%
echo  Restarting in 10 seconds...
echo  Press Ctrl+C twice to quit permanently
echo ============================================
timeout /t 10 /nobreak
goto loop
