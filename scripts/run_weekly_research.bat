@echo off
REM Weekly research automation (TradingBot-WeeklyResearch task):
REM   1. accumulate perp OHLCV history (idempotent backfill — grows the dataset)
REM   2. re-run the FROZEN edge screen on it and log the verdict
REM The screen stays "immature / NO_EDGE" until the perps have enough co-existing
REM history (months out); this auto-checks every week and the log shows the verdict,
REM so if an edge ever genuinely appears it is recorded rather than missed. Log is
REM overwritten each run (no unbounded growth); the cache + reports are the artifacts.
cd /d D:\Downloads\Trading_Bot
echo ===== Weekly research run %DATE% %TIME% ===== > logs\weekly_research.log
echo --- backfill perps --- >> logs\weekly_research.log
D:\Downloads\Trading_Bot\venv\Scripts\python.exe scripts\backfill_perps_ohlcv.py >> logs\weekly_research.log 2>&1
echo --- edge screen (1h) --- >> logs\weekly_research.log
D:\Downloads\Trading_Bot\venv\Scripts\python.exe scripts\run_perp_alpha_screen.py --timeframe 1h >> logs\weekly_research.log 2>&1
echo --- sweet spots (trade-history, IS/OOS) --- >> logs\weekly_research.log
D:\Downloads\Trading_Bot\venv\Scripts\python.exe scripts\find_sweet_spots.py >> logs\weekly_research.log 2>&1
echo --- calendar patterns (seasonality, after-cost + OOS-significant) --- >> logs\weekly_research.log
D:\Downloads\Trading_Bot\venv\Scripts\python.exe scripts\find_patterns.py >> logs\weekly_research.log 2>&1
echo --- per-pair strategy sweep (mom/MR/breakout, IS/OOS + Bonferroni) --- >> logs\weekly_research.log
D:\Downloads\Trading_Bot\venv\Scripts\python.exe scripts\strategy_pattern_search.py >> logs\weekly_research.log 2>&1
