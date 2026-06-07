@echo off
REM Monthly forward-validation re-run of the spot-BTC-ETF net-flow edge screen (WS3b).
REM Appends a timestamped result block to reports\etf_flow_screen_history.txt so the
REM 10d/10d whisper can be watched as out-of-sample months accrue. NO refitting.
cd /d D:\Downloads\Trading_Bot
set PYTHONIOENCODING=utf-8
if not exist reports mkdir reports
echo. >> reports\etf_flow_screen_history.txt
echo ================ ETF re-check %DATE% %TIME% ================ >> reports\etf_flow_screen_history.txt
venv\Scripts\python.exe scripts\run_etf_flow_edge_screen.py >> reports\etf_flow_screen_history.txt 2>&1
