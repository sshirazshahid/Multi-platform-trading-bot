@echo off
rem Compatibility entrypoint. TradingBot.bat owns all launch and restart behavior.
call "%~dp0TradingBot.bat" --supervise
exit /b %ERRORLEVEL%
