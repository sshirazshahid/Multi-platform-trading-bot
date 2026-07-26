@echo off
REM Keep the liquidation harvester running: restart it if it ever exits (sleep/network).
REM Started now + scheduled ONLOGON so the forward-harvest survives reboots.
REM Single-instance: harvest_liquidations.py holds data/runtime_locks/harvest_liquidations.lock;
REM a second python exits immediately — do NOT also launch a system-Python copy.
cd /d D:\Downloads\Trading_Bot
set PYTHONIOENCODING=utf-8
if not exist logs mkdir logs
:loop
venv\Scripts\python.exe scripts\harvest_liquidations.py >> logs\liquidations_harvester.log 2>&1
timeout /t 30 /nobreak >nul
goto loop
