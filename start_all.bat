@echo off
:: start_all.bat — start the bot's persistent auxiliary processes (confluence paper + liq/skew/L2
:: harvesters), dedup-safe. Called by auto_restart.bat and TradingBot.bat; can also be run directly.
:: Does NOT start main.py (the caller manages the bot). See scripts\start_all.ps1.
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start_all.ps1"
