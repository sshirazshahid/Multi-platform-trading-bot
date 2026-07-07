@echo off
title Confluence Strategy - PAPER Forward-Test (no real orders)
cd /d "%~dp0"
if not exist "run_confluence_paper.py" (
  echo run_confluence_paper.py is not present in this checkout. Confluence paper runner is disabled.
  exit /b 0
)
:loop
for /d /r %%d in (__pycache__) do @if exist "%%d" rd /s /q "%%d" 2>nul
venv\Scripts\python.exe run_confluence_paper.py
echo Runner exited (code %ERRORLEVEL%). Restarting in 10s... Ctrl+C twice to quit.
timeout /t 10 /nobreak
goto loop
