@echo off
setlocal EnableDelayedExpansion
title Trading Bot
set "BOT_DIR=%~dp0"
set "VENV_DIR=%BOT_DIR%venv"
set "VENV_PYTHON=%VENV_DIR%\Scripts\python.exe"
set "VENV_PIP=%VENV_DIR%\Scripts\pip.exe"
set "SETUP_FLAG=%BOT_DIR%data\.setup_done"
set "HELPER=bot_helper.py"
if not exist "%BOT_DIR%data"                        mkdir "%BOT_DIR%data" 2>nul
if not exist "%BOT_DIR%logs"                        mkdir "%BOT_DIR%logs" 2>nul
if not exist "%BOT_DIR%data\research"               mkdir "%BOT_DIR%data\research" 2>nul
if not exist "%BOT_DIR%data\profiles"               mkdir "%BOT_DIR%data\profiles" 2>nul
if not exist "%BOT_DIR%data\arbitrage"              mkdir "%BOT_DIR%data\arbitrage" 2>nul
if not exist "%BOT_DIR%data\profiles\conservative"  mkdir "%BOT_DIR%data\profiles\conservative" 2>nul
if not exist "%BOT_DIR%data\profiles\moderate"      mkdir "%BOT_DIR%data\profiles\moderate" 2>nul
if not exist "%BOT_DIR%data\profiles\aggressive"    mkdir "%BOT_DIR%data\profiles\aggressive" 2>nul
if not exist "%SETUP_FLAG%" goto :wizard
goto :menu
:wizard
cls
echo.
echo  TRADING BOT - First Time Setup
echo.
pause
cls
echo  Step 1 - Checking Python
echo.
set "PYTHON_CMD="
where py >nul 2>&1
if not errorlevel 1 set "PYTHON_CMD=py" & goto :py_ok
where python3 >nul 2>&1
if not errorlevel 1 set "PYTHON_CMD=python3" & goto :py_ok
where python >nul 2>&1
if not errorlevel 1 set "PYTHON_CMD=python" & goto :py_ok
echo  ERROR: Python not found. Install from https://python.org
pause
goto :menu
:py_ok
for /f "tokens=2" %%V in ('%PYTHON_CMD% --version 2^>^&1') do set "PY_VER=%%V"
echo  [OK] Python %PY_VER% found.
pause
cls
echo  Step 2 - Creating virtual environment and installing packages
echo.
cd /d "%BOT_DIR%"
if exist "%VENV_DIR%" goto :venv_ok
%PYTHON_CMD% -m venv "%VENV_DIR%"
if errorlevel 1 goto :venv_fail
echo  [OK] venv created.
goto :venv_done
:venv_fail
echo  ERROR: Could not create virtual environment.
pause
goto :menu
:venv_ok
echo  venv already exists - skipping creation.
:venv_done
"%VENV_PYTHON%" -m pip install --upgrade pip --quiet
"%VENV_PIP%" install -r requirements.txt
if errorlevel 1 goto :pkg_fail
echo  [OK] Packages installed.
pause
goto :step3
:pkg_fail
echo  ERROR: pip install failed. Check your internet connection.
pause
goto :menu
:step3
cls
echo  Step 3 - Copy .env.example to .env
echo.
if not exist "%BOT_DIR%.env" (
    copy "%BOT_DIR%.env.example" "%BOT_DIR%.env" >nul
    echo  [OK] .env created from template.
    echo.
    echo  IMPORTANT: Open .env in a text editor and add your API keys.
    echo  Press any key to open .env in Notepad now...
    pause >nul
    notepad "%BOT_DIR%.env"
) else (
    echo  .env already exists - skipping.
)
echo done > "%SETUP_FLAG%"
echo.
echo  Setup complete. Starting menu...
timeout /t 2 >nul
goto :menu
:menu
cls
echo.
echo  =====================================================================
echo   TRADING BOT - Binance + MEXC + Bybit + Bitget
echo   Crypto + Gold + Silver + Oil - Spot + Futures + Arbitrage
echo  =====================================================================
echo.
cd /d "%BOT_DIR%"
set "IS_LIVE=0"
findstr /i "DRY_RUN=false" .env >nul 2>&1
if not errorlevel 1 set "IS_LIVE=1"
if "!IS_LIVE!"=="1" (
    echo   Run Mode : LIVE TRADING
) else (
    echo   Run Mode : DRY RUN - paper trading, no real money
)
echo.
echo  -----------------------------------------------------------------------
echo   [1] Start bot (single profile)
echo   [2] Live dashboard
echo   [3] Backtest
echo   [4] Show open positions
echo   [5] View logs
echo   [6] Edit API keys (.env)
echo   [A] Toggle DRY RUN / LIVE mode
echo   [B] Re-run setup wizard
echo   [C] Clear Python cache
echo  -----------------------------------------------------------------------
echo   [L] Replicate Live Wallet to DRY RUN
echo   [M] Multi-Profile Learning (all 3 profiles)
echo   [R] Multi-profile comparison report
echo   [J] Fix ghost/phantom positions
echo  -----------------------------------------------------------------------
echo   [Z] View arbitrage opportunities
echo   [X] Send email report now
echo   [E] Run Claude AI analysis
echo   [G] View last Claude AI report
echo  -----------------------------------------------------------------------
echo   [0] Exit
echo  -----------------------------------------------------------------------
echo.
set /p "CHOICE=  Enter choice: "
if /i "!CHOICE!"=="0" exit /b 0
if /i "!CHOICE!"=="1" goto :run_bot
if /i "!CHOICE!"=="2" goto :dashboard
if /i "!CHOICE!"=="3" goto :backtest
if /i "!CHOICE!"=="4" goto :status
if /i "!CHOICE!"=="5" goto :logs
if /i "!CHOICE!"=="6" notepad "%BOT_DIR%.env" & goto :menu
if /i "!CHOICE!"=="A" goto :tog_live
if /i "!CHOICE!"=="B" del "%SETUP_FLAG%" >nul 2>&1 & goto :wizard
if /i "!CHOICE!"=="C" goto :clr
if /i "!CHOICE!"=="L" goto :wallet_replicate
if /i "!CHOICE!"=="M" goto :mprofile
if /i "!CHOICE!"=="R" goto :mreport
if /i "!CHOICE!"=="J" goto :fix_ghosts
if /i "!CHOICE!"=="Z" goto :arb_view
if /i "!CHOICE!"=="X" goto :email_send
if /i "!CHOICE!"=="E" goto :ai_run
if /i "!CHOICE!"=="G" goto :ai_view
goto :menu
:run_bot
cls
echo.
echo  Starting bot...
echo.
cd /d "%BOT_DIR%"
if not exist "%VENV_PYTHON%" goto :err_setup
if not exist ".env" goto :err_env
"%VENV_PYTHON%" clear_cache.py >nul 2>&1
set "IS_LIVE=0"
findstr /i "DRY_RUN=false" .env >nul 2>&1
if not errorlevel 1 set "IS_LIVE=1"
if "!IS_LIVE!"=="0" goto :bot_start
echo   WARNING: LIVE TRADING - REAL MONEY AT RISK
echo.
set /p "CONFIRM=  Type YES to confirm: "
if /i not "!CONFIRM!"=="YES" goto :bot_cancel
:bot_start
echo  Running. Press Ctrl+C to stop.
echo.
"%VENV_PYTHON%" main.py
echo.
echo  Bot stopped.
pause
goto :menu
:bot_cancel
echo  Cancelled.
pause
goto :menu
:dashboard
cls
echo.
echo  Opening live dashboard (refreshes every 10s)...
echo  Press Ctrl+C to return to menu.
echo.
cd /d "%BOT_DIR%"
if not exist "%VENV_PYTHON%" goto :err_setup
"%VENV_PYTHON%" dashboard.py
pause
goto :menu
:wallet_replicate
cls
echo.
echo  ===================================================
echo   REPLICATE LIVE WALLET TO DRY RUN
echo  ===================================================
echo.
echo  Scans live balances and mirrors them into the
echo  DRY RUN paper wallets for all 3 profiles.
echo.
echo  - Coins converted to USDT at current price
echo  - Real money is NOT affected
echo.
set /p "CONF=  Replicate now? [Y/N]: "
if /i not "!CONF!"=="Y" goto :menu
echo.
cd /d "%BOT_DIR%"
if not exist "%VENV_PYTHON%" goto :err_setup
"%VENV_PYTHON%" -c "from core.wallet_replicator import replicate_live_to_dry; replicate_live_to_dry()"
echo.
pause
goto :menu
:mprofile
cls
echo.
echo  Multi-Profile Learning
echo.
echo  Conservative + Moderate + Aggressive run simultaneously
echo  Crypto + Gold + Silver + Oil  (DRY RUN only)
echo.
cd /d "%BOT_DIR%"
if not exist "%VENV_PYTHON%" goto :err_setup
if not exist ".env" goto :err_env
set "IS_LIVE=0"
findstr /i "DRY_RUN=false" .env >nul 2>&1
if not errorlevel 1 set "IS_LIVE=1"
if "!IS_LIVE!"=="1" goto :mp_err
"%VENV_PYTHON%" clear_cache.py >nul 2>&1
echo  Starting... Press Ctrl+C to stop.
echo.
"%VENV_PYTHON%" multi_profile_main.py
echo.
echo  Session ended.
pause
goto :menu
:mp_err
echo  ERROR: Multi-profile only runs in DRY RUN. Use Option [A] first.
pause
goto :menu
:mreport
cls
echo.
cd /d "%BOT_DIR%"
if not exist "%VENV_PYTHON%" goto :err_setup
"%VENV_PYTHON%" multi_profile_main.py --report
echo.
pause
goto :menu
:fix_ghosts
cls
echo.
echo  Scanning for ghost positions...
echo.
cd /d "%BOT_DIR%"
if not exist "%VENV_PYTHON%" goto :err_setup
"%VENV_PYTHON%" fix_ghost_positions.py
echo.
pause
goto :menu
:arb_view
cls
echo.
echo  Arbitrage Opportunities (last scan)
echo.
cd /d "%BOT_DIR%"
if not exist "%VENV_PYTHON%" goto :err_setup
"%VENV_PYTHON%" show_arb.py
echo.
pause
goto :menu
:email_send
cls
echo.
echo  Sending email report...
echo.
cd /d "%BOT_DIR%"
if not exist "%VENV_PYTHON%" goto :err_setup
"%VENV_PYTHON%" -c "from core.report_emailer import ReportEmailer; ReportEmailer().send_daily(force=True)"
echo.
pause
goto :menu
:ai_run
cls
echo.
echo  Claude AI Analysis
echo.
cd /d "%BOT_DIR%"
if not exist "%VENV_PYTHON%" goto :err_setup
set "KEY_OK=0"
for /f "usebackq tokens=*" %%L in (".env") do (
    set "ELINE=%%L"
    if "!ELINE:~0,19!"=="ANTHROPIC_API_KEY=sk" set "KEY_OK=1"
)
if "!KEY_OK!"=="1" (
    echo  Live API key found. Running...
) else (
    echo  No active API key - using embedded Claude (free).
)
echo.
"%VENV_PYTHON%" claude_ai_runner.py
echo.
pause
goto :menu
:ai_view
cls
echo.
echo  Claude AI - Last Report
echo.
cd /d "%BOT_DIR%"
if not exist "%VENV_PYTHON%" goto :err_setup
"%VENV_PYTHON%" claude_ai_runner.py --report
echo.
set "HTML_RPT=%BOT_DIR%data\claude_analysis.html"
if exist "!HTML_RPT!" start "" "!HTML_RPT!"
pause
goto :menu
:backtest
cls
echo.
echo  Backtest - 70%% Train / 30%% Test
echo.
echo  [1] supertrend  [2] meanreversion  [3] multitf
echo  [4] trend       [5] grid           [6] scalping
echo.
cd /d "%BOT_DIR%"
if not exist "%VENV_PYTHON%" goto :err_setup
set "STRAT="
set "SC="
set /p "SC=  Choose [1-6]: "
if "!SC!"=="1" set "STRAT=supertrend"
if "!SC!"=="2" set "STRAT=meanreversion"
if "!SC!"=="3" set "STRAT=multitf"
if "!SC!"=="4" set "STRAT=trend"
if "!SC!"=="5" set "STRAT=grid"
if "!SC!"=="6" set "STRAT=scalping"
if "!STRAT!"=="" (
    echo  Invalid. Enter 1-6.
    pause
    goto :menu
)
set "SYM=BTC/USDT"
set /p "SYM=  Symbol [BTC/USDT]: "
if "!SYM!"=="" set "SYM=BTC/USDT"
set "DAYS=60"
set /p "DAYS=  Days [60]: "
if "!DAYS!"=="" set "DAYS=60"
echo.
echo  [1] Binance  [2] MEXC  [3] Bybit  [4] Bitget  [5] ALL
echo.
set "EX=1"
set /p "EX=  Exchange [1-5, default 1]: "
if "!EX!"=="" set "EX=1"
if "!EX!"=="5" goto :bt_all
set "EXNAME=binance"
if "!EX!"=="2" set "EXNAME=mexc"
if "!EX!"=="3" set "EXNAME=bybit"
if "!EX!"=="4" set "EXNAME=bitget"
"%VENV_PYTHON%" backtest.py --strategy "!STRAT!" --symbol "!SYM!" --exchange "!EXNAME!" --days "!DAYS!"
echo.
pause
goto :menu
:bt_all
"%VENV_PYTHON%" backtest.py --strategy "!STRAT!" --symbol "!SYM!" --all-exchanges --days "!DAYS!"
echo.
pause
goto :menu
:status
cls
echo.
cd /d "%BOT_DIR%"
if not exist "%VENV_PYTHON%" goto :err_setup
"%VENV_PYTHON%" main.py --status
echo.
pause
goto :menu
:logs
cls
echo.
echo  [1] Follow latest log   [2] Open logs folder
echo.
if not exist "%BOT_DIR%logs\" goto :no_logs
set /p "LC=  Choice: "
if "!LC!"=="1" goto :log_tail
if "!LC!"=="2" explorer "%BOT_DIR%logs\" & goto :menu
goto :menu
:log_tail
set "LFILE="
for /f %%F in ('dir /b /o-d "%BOT_DIR%logs\bot_*.log" 2^>nul') do if not defined LFILE set "LFILE=%BOT_DIR%logs\%%F"
if not defined LFILE goto :no_log_f
echo  Following !LFILE! ... Ctrl+C to stop
powershell -Command "Get-Content '!LFILE!' -Wait -Tail 50"
goto :menu
:no_logs
echo  No logs yet.
pause
goto :menu
:no_log_f
echo  No log files yet.
pause
goto :menu
:tog_live
cls
echo.
cd /d "%BOT_DIR%"
set "IS_LIVE=0"
findstr /i "DRY_RUN=false" .env >nul 2>&1
if not errorlevel 1 set "IS_LIVE=1"
if "!IS_LIVE!"=="1" goto :to_dry
echo  Current: DRY RUN
echo.
echo  WARNING: LIVE = REAL MONEY AT RISK.
echo.
set /p "CONF=  Type YES to confirm LIVE mode: "
if /i not "!CONF!"=="YES" goto :tog_cancel
"%VENV_PYTHON%" -c "
import re, pathlib
p = pathlib.Path('.env')
t = p.read_text()
t = re.sub(r'DRY_RUN=true', 'DRY_RUN=false', t, flags=re.I)
p.write_text(t)
print('Switched to LIVE. Restart bot to apply.')
"
pause
goto :menu
:to_dry
"%VENV_PYTHON%" -c "
import re, pathlib
p = pathlib.Path('.env')
t = p.read_text()
t = re.sub(r'DRY_RUN=false', 'DRY_RUN=true', t, flags=re.I)
p.write_text(t)
print('Switched to DRY RUN.')
"
pause
goto :menu
:tog_cancel
echo  Cancelled.
pause
goto :menu
:clr
cls
echo  Clearing cache...
cd /d "%BOT_DIR%"
if exist "%VENV_PYTHON%" "%VENV_PYTHON%" clear_cache.py
echo.
pause
goto :menu
:err_setup
echo  ERROR: Not installed. Run Option [B] first.
pause
goto :menu
:err_env
echo  ERROR: .env missing. Run Option [B] first.
pause
goto :menu
