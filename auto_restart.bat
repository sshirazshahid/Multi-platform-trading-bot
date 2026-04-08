@echo off
title Trading Bot - 24/7 Auto-Restart
cd /d "%~dp0"

:loop
echo.
echo ============================================
echo  Starting bot... (auto-restart on crash)
echo  Time: %date% %time%
echo ============================================
echo.

:: Clear __pycache__ before every start so fixes always load
for /d /r %%d in (__pycache__) do @if exist "%%d" rd /s /q "%%d"

venv\Scripts\python.exe main.py

echo.
echo ============================================
echo  Bot stopped/crashed at %date% %time%
echo  Restarting in 10 seconds...
echo  Press Ctrl+C twice to quit permanently
echo ============================================
timeout /t 10 /nobreak
goto loop
