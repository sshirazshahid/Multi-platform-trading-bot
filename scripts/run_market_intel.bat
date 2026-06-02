@echo off
REM Daily Crypto Market Intelligence Brief — run by the TradingBot-MarketIntel scheduled task.
REM Writes reports\market_intel_<date>.md and appends a run line to logs\market_intel.log.
cd /d D:\Downloads\Trading_Bot
D:\Downloads\Trading_Bot\venv\Scripts\python.exe scripts\market_intel_report.py >> logs\market_intel.log 2>&1
