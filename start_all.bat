@echo off
rem Compatibility helper entrypoint for persistent paper/read-only collectors.
rem TradingBot.bat and this shim both delegate to scripts\start_all.ps1.
rem The PowerShell helper serializes startup and skips workers already running.
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start_all.ps1"
exit /b %ERRORLEVEL%
