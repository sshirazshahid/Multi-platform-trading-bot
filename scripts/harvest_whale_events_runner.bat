@echo off
REM Hourly-friendly one-shot whale/large-transfer harvest (log-only).
REM Never places orders. Optional WHALE_ALERT_API_KEY for labeled CEX flow.
cd /d "%~dp0.."
set PYTHONIOENCODING=utf-8
if not exist logs mkdir logs
if exist venv\Scripts\python.exe (
  venv\Scripts\python.exe scripts\harvest_whale_events.py --once >> logs\whale_harvest.log 2>&1
) else (
  python scripts\harvest_whale_events.py --once >> logs\whale_harvest.log 2>&1
)
