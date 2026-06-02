@echo off
REM Daily Crypto Market Intelligence Brief — run by the TradingBot-MarketIntel scheduled task.
REM Writes reports\market_intel_<date>.md (the durable artifact) + appends a snapshot to
REM data\market_intel_history.jsonl. Log is OVERWRITTEN each run (>) so it can't grow
REM unbounded — the per-run output is self-contained and history lives in the .md/.jsonl.
cd /d D:\Downloads\Trading_Bot
D:\Downloads\Trading_Bot\venv\Scripts\python.exe scripts\market_intel_report.py > logs\market_intel.log 2>&1
