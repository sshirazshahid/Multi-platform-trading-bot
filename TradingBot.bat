@echo off
setlocal EnableDelayedExpansion
title Trading Bot
set "BOT_DIR=%~dp0"
set "VENV_DIR=%BOT_DIR%venv"
set "VENV_PYTHON=%VENV_DIR%\Scripts\python.exe"
set "VENV_PIP=%VENV_DIR%\Scripts\pip.exe"
set "SETUP_FLAG=%BOT_DIR%data\.setup_done"
set "HELPER=bot_helper.py"
if not exist "%BOT_DIR%data"            mkdir "%BOT_DIR%data" 2>nul
if not exist "%BOT_DIR%logs"            mkdir "%BOT_DIR%logs" 2>nul
if not exist "%BOT_DIR%data\research"   mkdir "%BOT_DIR%data\research" 2>nul
if not exist "%BOT_DIR%data\profiles"   mkdir "%BOT_DIR%data\profiles" 2>nul
if not exist "%BOT_DIR%data\arbitrage"  mkdir "%BOT_DIR%data\arbitrage" 2>nul
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
echo  ERROR: Python not found.
pause
goto :menu
:py_ok
for /f "tokens=2" %%V in ('%PYTHON_CMD% --version 2^>^&1') do set "PY_VER=%%V"
echo  [OK] Python %PY_VER% found.
pause
cls
echo  Step 2 - Installing Packages
echo.
cd /d "%BOT_DIR%"
if exist "%VENV_DIR%" goto :venv_ok
%PYTHON_CMD% -m venv "%VENV_DIR%"
if errorlevel 1 goto :venv_fail
echo  [OK] venv created.
goto :venv_done
:venv_fail
echo  ERROR: venv failed.
pause
goto :menu
:venv_ok
echo  venv already exists.
:venv_done
"%VENV_PYTHON%" -m pip install --upgrade pip --quiet
"%VENV_PIP%" install -r requirements.txt
if errorlevel 1 goto :pkg_fail
echo  [OK] Packages installed.
pause
goto :step3
:pkg_fail
echo  ERROR: pip install failed.
pause
goto :menu
:step3
cls
echo  Step 3 - Binance API Keys (Enter to skip)
echo.
set "BIN_KEY=" & set "BIN_SEC="
set /p "BIN_KEY=  Binance API Key    : "
set /p "BIN_SEC=  Binance Secret Key : "
if "!BIN_KEY!"=="" set "BIN_KEY=your_binance_api_key_here" & set "BIN_SEC=your_binance_secret_key_here"
set "BIN_TESTNET=false"
cls
echo  Step 4 - Bybit API Keys (Enter to skip)
echo.
set "BY_KEY=" & set "BY_SEC="
set /p "BY_KEY=  Bybit API Key    : "
set /p "BY_SEC=  Bybit Secret Key : "
cls
echo  Step 5 - Bitget API Keys (Enter to skip)
echo.
set "BG_KEY=" & set "BG_SEC=" & set "BG_PASS="
set /p "BG_KEY=  Bitget API Key    : "
set /p "BG_SEC=  Bitget Secret Key : "
set /p "BG_PASS= Bitget Passphrase : "
cls
echo  Step 6 - Email + Risk Profile
echo.
set "GM_SEND=" & set "GM_PASS2=" & set "GM_RECV="
set /p "GM_SEND=  Gmail address (Enter to skip): "
if "!GM_SEND!"=="" goto :wiz_risk
set /p "GM_PASS2=  App Password: "
set /p "GM_RECV=  Recipient email: "
:wiz_risk
echo.
echo  Risk: [1] CONSERVATIVE  [2] MODERATE  [3] AGGRESSIVE
echo.
set /p "RISK_C=  Choose [1/2/3]: "
set "R_PCT=0.02" & set "R_SL=0.010" & set "R_TP=0.030" & set "R_DL=0.02" & set "R_POS=4" & set "R_LEV=2"
if "!RISK_C!"=="2" set "R_PCT=0.03" & set "R_SL=0.015" & set "R_TP=0.045" & set "R_DL=0.03" & set "R_POS=6" & set "R_LEV=3"
if "!RISK_C!"=="3" set "R_PCT=0.05" & set "R_SL=0.020" & set "R_TP=0.060" & set "R_DL=0.05" & set "R_POS=8" & set "R_LEV=3"
echo.
echo  Operating Mode:
echo    [1] PAPER       - Simulated trades, no real orders (recommended)
echo    [2] OBSERVATION - Data collection only, no trades at all
echo    [3] LIVE        - Real orders with real money
echo.
set /p "MODE_C=  Choose [1/2/3]: "
set "OP_MODE=PAPER"
if "!MODE_C!"=="2" set "OP_MODE=OBSERVATION"
if "!MODE_C!"=="3" set "OP_MODE=CONTROLLED_LIVE"
cd /d "%BOT_DIR%"
"%VENV_PYTHON%" %HELPER% write_env "!BIN_KEY!" "!BIN_SEC!" "!BIN_TESTNET!" "" "" "" "!GM_SEND!" "!GM_PASS2!" "!GM_RECV!" "false"
"%VENV_PYTHON%" %HELPER% apply_risk "!R_PCT!" "!R_POS!" "!R_DL!" "!R_SL!" "!R_TP!" "!R_LEV!"
if not "!BY_KEY!"=="" "%VENV_PYTHON%" %HELPER% update_keys bybit "!BY_KEY!" "!BY_SEC!"
if not "!BG_KEY!"=="" "%VENV_PYTHON%" %HELPER% update_keys bitget "!BG_KEY!" "!BG_SEC!" "!BG_PASS!"
"%VENV_PYTHON%" -c "from bot_helper import set_env_var; set_env_var('OPERATING_MODE', '!OP_MODE!'); set_env_var('CONTROLLED_LIVE_ENABLED', 'true' if '!OP_MODE!'=='CONTROLLED_LIVE' else 'false')" 2>nul
echo done > "%SETUP_FLAG%"
echo  Setup complete.
timeout /t 3 >nul
goto :menu
:menu
cls
echo.
echo  =====================================================================
echo   TRADING BOT - Binance + Bybit + Bitget
echo   Crypto Spot + Futures - 30 Coins - MCP Brain Scoring Engine
echo  =====================================================================
echo.
cd /d "%BOT_DIR%"
set "OP_MODE=PAPER"
set "IS_LIVE=0"
findstr /i "OPERATING_MODE=CONTROLLED_LIVE" .env >nul 2>&1
if not errorlevel 1 (
    set "OP_MODE=CONTROLLED_LIVE"
    set "IS_LIVE=1"
)
findstr /i "OPERATING_MODE=OBSERVATION" .env >nul 2>&1
if not errorlevel 1 set "OP_MODE=OBSERVATION"
set "TM=usdt_only"
findstr /i "TRADING_MODE=portfolio" .env >nul 2>&1
if not errorlevel 1 set "TM=portfolio"
findstr /i "TRADING_MODE=all" .env >nul 2>&1
if not errorlevel 1 set "TM=all"
if "!OP_MODE!"=="CONTROLLED_LIVE" goto :lbl_live
if "!OP_MODE!"=="OBSERVATION" goto :lbl_obs
echo   Operating Mode : PAPER (simulated trades)
goto :lbl_tm
:lbl_live
echo   Operating Mode : CONTROLLED_LIVE (real orders)
echo   Safety Nets    : 3x max leverage, 1%% sizing, $2 max loss/trade
goto :lbl_tm
:lbl_obs
echo   Operating Mode : OBSERVATION (data collection only)
:lbl_tm
if "!TM!"=="portfolio" goto :lbl_port
if "!TM!"=="all" goto :lbl_all
echo   Trade Mode     : USDT ONLY
goto :lbl_snap
:lbl_port
echo   Trade Mode     : PORTFOLIO
goto :lbl_snap
:lbl_all
echo   Trade Mode     : ALL (30 coins, Spot + Futures)
:lbl_snap
echo.
set "HAS_DATA=0"
if exist "data\positions.json"                        set "HAS_DATA=1"
if exist "data\profiles\conservative\positions.json"  set "HAS_DATA=1"
if exist "data\profiles\moderate\positions.json"      set "HAS_DATA=1"
if exist "data\profiles\aggressive\positions.json"    set "HAS_DATA=1"
if "!HAS_DATA!"=="1" if exist "%VENV_PYTHON%" "%VENV_PYTHON%" %HELPER% snapshot
echo.
echo  -----------------------------------------------------------------------
echo   [1] Start bot          [2] Dashboard (60s)    [3] Switch trade mode
echo   [4] Scan portfolio     [5] Backtest            [6] Open positions
echo   [7] View logs          [8] Edit API keys       [9] Risk profile
echo   [A] Switch mode        [B] Re-run wizard       [C] Clear cache
echo   [D] Build exe          [M] Multi-Profile       [R] Multi report
echo  -----------------------------------------------------------------------
echo   WALLET
echo   [L]  Replicate Live Wallet to DRY RUN
echo   [K]  View last wallet snapshot
echo  -----------------------------------------------------------------------
echo   MAINTENANCE
echo   [J]  Fix ghost/phantom positions
echo  -----------------------------------------------------------------------
echo   [Z] Arbitrage opps     [Y] Arbitrage backtest
echo   [X] Send email report  [W] Test email
echo   [E] Claude AI now      [F] Set Claude key      [G] View Claude report
echo  -----------------------------------------------------------------------
echo   [0] Exit
echo  -----------------------------------------------------------------------
echo.
set /p "CHOICE=  Enter choice: "
if /i "!CHOICE!"=="0" exit /b 0
if /i "!CHOICE!"=="1" goto :run_bot
if /i "!CHOICE!"=="2" goto :dashboard
if /i "!CHOICE!"=="3" goto :sw_mode
if /i "!CHOICE!"=="4" goto :scan_p
if /i "!CHOICE!"=="5" goto :backtest
if /i "!CHOICE!"=="6" goto :status
if /i "!CHOICE!"=="7" goto :logs
if /i "!CHOICE!"=="8" goto :keys
if /i "!CHOICE!"=="9" goto :risk
if /i "!CHOICE!"=="A" goto :tog_mode
if /i "!CHOICE!"=="B" del "%SETUP_FLAG%" >nul 2>&1 & goto :wizard
if /i "!CHOICE!"=="C" goto :clr
if /i "!CHOICE!"=="D" goto :build
if /i "!CHOICE!"=="M" goto :mprofile
if /i "!CHOICE!"=="R" goto :mreport
if /i "!CHOICE!"=="L" goto :wallet_replicate
if /i "!CHOICE!"=="K" goto :wallet_show
if /i "!CHOICE!"=="J" goto :fix_ghosts
if /i "!CHOICE!"=="Z" goto :arb_view
if /i "!CHOICE!"=="Y" goto :arb_bt
if /i "!CHOICE!"=="X" goto :email_send
if /i "!CHOICE!"=="W" goto :email_test
if /i "!CHOICE!"=="E" goto :ai_run
if /i "!CHOICE!"=="F" goto :ai_key
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
"%VENV_PYTHON%" -c "import shutil,os;[shutil.rmtree(os.path.join(r,d)) for r,ds,_ in os.walk('.') if 'venv' not in r for d in ds if d=='__pycache__']" >nul 2>&1
set "IS_LIVE=0"
findstr /i "OPERATING_MODE=CONTROLLED_LIVE" .env >nul 2>&1
if not errorlevel 1 set "IS_LIVE=1"
if "!IS_LIVE!"=="0" goto :bot_start
echo   ************************************************************
echo   *  WARNING: CONTROLLED_LIVE MODE - REAL MONEY AT RISK      *
echo   *  Safety: 3x max leverage, 1%% sizing, $2 max loss/trade  *
echo   ************************************************************
echo.
set /p "CONFIRM=  Type YES to confirm: "
if /i not "!CONFIRM!"=="YES" goto :bot_cancel
:bot_start
echo  Running. Ctrl+C to stop.
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
echo  Opening dashboard (60s refresh)...
echo  Press Ctrl+C to return.
echo.
cd /d "%BOT_DIR%"
if not exist "%VENV_PYTHON%" goto :err_setup
"%VENV_PYTHON%" dashboard.py --refresh 60
pause
goto :menu
:wallet_replicate
cls
echo.
echo  ===================================================
echo   REPLICATE LIVE WALLET TO DRY RUN
echo  ===================================================
echo.
echo  Scans live balances from all exchanges and mirrors
echo  them into your DRY RUN paper wallets.
echo.
echo  - Coins converted to USDT at current price
echo  - Real money is NOT affected
echo.
set /p "CONF=  Replicate now? [Y/N]: "
if /i not "!CONF!"=="Y" goto :menu
echo.
cd /d "%BOT_DIR%"
if not exist "%VENV_PYTHON%" goto :err_setup
"%VENV_PYTHON%" %HELPER% replicate_wallet
echo.
pause
goto :menu
:wallet_show
cls
echo.
echo  Last Wallet Snapshot
echo.
cd /d "%BOT_DIR%"
if not exist "%VENV_PYTHON%" goto :err_setup
"%VENV_PYTHON%" %HELPER% show_wallet_snapshot
echo.
pause
goto :menu
:fix_ghosts
cls
echo.
echo  Fix Ghost / Phantom Positions
echo.
echo  Scans position files and removes
echo  any open positions with invalid symbols.
echo.
cd /d "%BOT_DIR%"
if not exist "%VENV_PYTHON%" goto :err_setup
"%VENV_PYTHON%" %HELPER% fix_ghosts
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
:arb_bt
cls
echo.
echo  Arbitrage Backtest - All Exchanges
echo.
cd /d "%BOT_DIR%"
if not exist "%VENV_PYTHON%" goto :err_setup
set "SYM=BTC/USDT"
set /p "SYM=  Symbol [BTC/USDT]: "
if "!SYM!"=="" set "SYM=BTC/USDT"
set "DAYS=30"
set /p "DAYS=  Days [30]: "
if "!DAYS!"=="" set "DAYS=30"
echo.
"%VENV_PYTHON%" backtest.py --strategy arbitrage --symbol "!SYM!" --days "!DAYS!"
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
set "EMAIL_OK=0"
findstr /i "GMAIL_SENDER=" .env >nul 2>&1
if not errorlevel 1 set "EMAIL_OK=1"
if "!EMAIL_OK!"=="0" goto :email_nokey
"%VENV_PYTHON%" -m core.report_emailer --send
echo.
pause
goto :menu
:email_nokey
echo  No Gmail credentials in .env
pause
goto :menu
:email_test
cls
echo.
echo  Testing email credentials...
echo.
cd /d "%BOT_DIR%"
if not exist "%VENV_PYTHON%" goto :err_setup
"%VENV_PYTHON%" -m core.report_emailer --test
echo.
pause
goto :menu
:ai_run
cls
echo.
echo  Claude AI Analysis - Run Now
echo.
cd /d "%BOT_DIR%"
if not exist "%VENV_PYTHON%" goto :err_setup
set "KEY_OK=0"
for /f "usebackq tokens=*" %%L in (".env") do (
    set "ELINE=%%L"
    if "!ELINE:~0,19!"=="ANTHROPIC_API_KEY=sk" set "KEY_OK=1"
)
if "!KEY_OK!"=="1" goto :ai_live
echo  No active API key - using embedded Claude (free).
echo.
"%VENV_PYTHON%" claude_ai_runner.py
pause
goto :menu
:ai_live
"%VENV_PYTHON%" -c "import shutil,os;[shutil.rmtree(os.path.join(r,d)) for r,ds,_ in os.walk('.') if 'venv' not in r for d in ds if d=='__pycache__']" >nul 2>&1
echo  Live API key found. Running...
echo.
"%VENV_PYTHON%" claude_ai_runner.py
echo.
pause
goto :menu
:ai_key
cls
echo.
echo  Set Claude AI API Key
echo  Get one at: https://console.anthropic.com
echo.
cd /d "%BOT_DIR%"
if not exist "%VENV_PYTHON%" goto :err_setup
"%VENV_PYTHON%" claude_ai_runner.py --setkey
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
:mprofile
cls
echo.
echo  Multi-Profile Learning
echo.
echo  3 profiles on all exchanges simultaneously
echo  Arb every 2min, Claude every 15min, Email daily
echo.
echo  TIP: Run [L] first to mirror your real balance.
echo.
cd /d "%BOT_DIR%"
if not exist "%VENV_PYTHON%" goto :err_setup
if not exist ".env" goto :err_env
set "IS_LIVE=0"
findstr /i "OPERATING_MODE=CONTROLLED_LIVE" .env >nul 2>&1
if not errorlevel 1 set "IS_LIVE=1"
if "!IS_LIVE!"=="1" goto :mp_err
"%VENV_PYTHON%" -c "import shutil,os;[shutil.rmtree(os.path.join(r,d)) for r,ds,_ in os.walk('.') if 'venv' not in r for d in ds if d=='__pycache__']" >nul 2>&1
echo  Starting... Press Ctrl+C to stop.
echo.
"%VENV_PYTHON%" multi_profile_main.py
echo.
echo  Session ended.
pause
goto :menu
:mp_err
echo  ERROR: Multi-profile needs PAPER mode. Switch to PAPER first via [A].
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
:sw_mode
cls
echo.
echo  [1] USDT ONLY   [2] PORTFOLIO   [3] ALL
echo.
echo  USDT ONLY  - Trade configured USDT pairs only
echo  PORTFOLIO  - Scan your wallet + trade what you hold
echo  ALL        - All 30 coins, Spot + Futures on all exchanges
echo.
cd /d "%BOT_DIR%"
set /p "TMC=  Choose [1/2/3]: "
if "!TMC!"=="1" "%VENV_PYTHON%" %HELPER% set_trading_mode usdt_only & pause & goto :menu
if "!TMC!"=="2" "%VENV_PYTHON%" %HELPER% set_trading_mode portfolio & pause & goto :menu
if "!TMC!"=="3" "%VENV_PYTHON%" %HELPER% set_trading_mode all & pause & goto :menu
goto :menu
:scan_p
cls
echo  Scanning wallets...
echo.
cd /d "%BOT_DIR%"
if not exist "%VENV_PYTHON%" goto :err_setup
"%VENV_PYTHON%" %HELPER% scan_portfolio
echo.
pause
goto :menu
:tog_mode
cls
echo.
echo  Switch Operating Mode
echo.
echo  Current: !OP_MODE!
echo.
echo  [1] PAPER             - Simulated trades (safe, no real orders)
echo  [2] OBSERVATION       - Data collection only (no trades)
echo  [3] CONTROLLED_LIVE   - Real orders (requires signed checklist)
echo.
cd /d "%BOT_DIR%"
set /p "MC=  Choose [1/2/3]: "
if "!MC!"=="1" (
    "%VENV_PYTHON%" -c "from bot_helper import set_env_var; set_env_var('OPERATING_MODE', 'PAPER'); set_env_var('CONTROLLED_LIVE_ENABLED', 'false')"
    echo  Switched to PAPER mode.
    pause
    goto :menu
)
if "!MC!"=="2" (
    "%VENV_PYTHON%" -c "from bot_helper import set_env_var; set_env_var('OPERATING_MODE', 'OBSERVATION'); set_env_var('CONTROLLED_LIVE_ENABLED', 'false')"
    echo  Switched to OBSERVATION mode.
    pause
    goto :menu
)
if "!MC!"=="3" (
    echo.
    echo  WARNING: CONTROLLED_LIVE = REAL MONEY AT RISK
    echo  Safety nets: 3x max leverage, 1%% sizing, $2 max loss/trade
    echo.
    set /p "CONF=  Type YES to confirm: "
    if /i not "!CONF!"=="YES" goto :tog_cancel2
    "%VENV_PYTHON%" -c "from bot_helper import set_env_var; set_env_var('OPERATING_MODE', 'CONTROLLED_LIVE'); set_env_var('CONTROLLED_LIVE_ENABLED', 'true')"
    echo  Switched to CONTROLLED_LIVE mode.
    pause
    goto :menu
)
goto :menu
:tog_cancel2
echo  Cancelled.
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
echo  [1] Binance  [2] Bybit  [3] Bitget  [4] ALL
echo.
set "EX=1"
set /p "EX=  Exchange [1-4, default 1]: "
if "!EX!"=="" set "EX=1"
if "!EX!"=="4" goto :bt_all
set "EXNAME=binance"
if "!EX!"=="2" set "EXNAME=bybit"
if "!EX!"=="3" set "EXNAME=bitget"
echo.
"%VENV_PYTHON%" backtest.py --strategy "!STRAT!" --symbol "!SYM!" --exchange "!EXNAME!" --days "!DAYS!"
echo.
pause
goto :menu
:bt_all
echo.
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
echo  [1] Follow live  [2] Open folder  [3] Last errors
echo.
if not exist "%BOT_DIR%logs\" goto :no_logs
set /p "LC=  Choice: "
if "!LC!"=="1" goto :log_tail
if "!LC!"=="2" explorer "%BOT_DIR%logs\" & goto :menu
if "!LC!"=="3" goto :log_err
goto :menu
:log_tail
set "LFILE="
for /f %%F in ('dir /b /o-d "%BOT_DIR%logs\bot_*.log" 2^>nul') do if not defined LFILE set "LFILE=%BOT_DIR%logs\%%F"
if not defined LFILE goto :no_log_f
echo  Following !LFILE! ... Ctrl+C to stop
powershell -Command "Get-Content '!LFILE!' -Wait -Tail 50"
goto :menu
:log_err
if not exist "%BOT_DIR%logs\errors.log" goto :no_err_f
powershell -Command "Get-Content '%BOT_DIR%logs\errors.log' -Tail 50"
pause
goto :menu
:no_logs
echo  No logs yet.
pause
goto :menu
:no_log_f
echo  No log files yet.
pause
goto :menu
:no_err_f
echo  No errors logged.
pause
goto :menu
:keys
cls
echo.
echo  Edit API Keys
echo.
echo  [1] Binance  [2] Bybit  [3] Bitget
echo  [4] Email    [5] Open .env in Notepad  [6] Back
echo.
set /p "EKC=  Choice: "
cd /d "%BOT_DIR%"
if "!EKC!"=="1" goto :k_bin
if "!EKC!"=="2" goto :k_bybit
if "!EKC!"=="3" goto :k_bitget
if "!EKC!"=="4" goto :k_em
if "!EKC!"=="5" notepad "%BOT_DIR%.env" & goto :menu
goto :menu
:k_bin
echo.
set /p "BK=  Binance API Key    : "
set /p "BS=  Binance Secret Key : "
"%VENV_PYTHON%" %HELPER% update_keys binance "!BK!" "!BS!"
pause
goto :menu
:k_bybit
echo.
set /p "BYK=  Bybit API Key    : "
set /p "BYS=  Bybit Secret Key : "
"%VENV_PYTHON%" %HELPER% update_keys bybit "!BYK!" "!BYS!"
pause
goto :menu
:k_bitget
echo.
set /p "BGK=  Bitget API Key    : "
set /p "BGS=  Bitget Secret Key : "
set /p "BGP=  Bitget Passphrase : "
"%VENV_PYTHON%" %HELPER% update_keys bitget "!BGK!" "!BGS!" "!BGP!"
pause
goto :menu
:k_em
echo.
set /p "GS=  Gmail sender    : "
set /p "GP=  App Password    : "
set /p "GR=  Recipient email : "
"%VENV_PYTHON%" %HELPER% update_email "!GS!" "!GP!" "!GR!"
pause
goto :menu
:risk
cls
echo.
echo  [1] CONSERVATIVE  [2] MODERATE  [3] AGGRESSIVE
echo.
echo  Note: Max leverage is capped at 3x across all profiles.
echo.
set /p "RC=  Choose [1/2/3]: "
set "R_PCT=0.02" & set "R_SL=0.010" & set "R_TP=0.030" & set "R_DL=0.02" & set "R_POS=4" & set "R_LEV=2"
if "!RC!"=="2" set "R_PCT=0.03" & set "R_SL=0.015" & set "R_TP=0.045" & set "R_DL=0.03" & set "R_POS=6" & set "R_LEV=3"
if "!RC!"=="3" set "R_PCT=0.05" & set "R_SL=0.020" & set "R_TP=0.060" & set "R_DL=0.05" & set "R_POS=8" & set "R_LEV=3"
cd /d "%BOT_DIR%"
"%VENV_PYTHON%" %HELPER% apply_risk "!R_PCT!" "!R_POS!" "!R_DL!" "!R_SL!" "!R_TP!" "!R_LEV!"
echo  Profile applied.
pause
goto :menu
:clr
cls
echo.
echo  Clearing cache...
echo.
cd /d "%BOT_DIR%"
if not exist "%VENV_PYTHON%" goto :err_setup
"%VENV_PYTHON%" -c "import shutil,os;n=sum(1 for r,ds,_ in os.walk('.') if 'venv' not in r for d in ds if d=='__pycache__' and not shutil.rmtree(os.path.join(r,d)));print(f'Cleared {n} __pycache__ dirs')" 2>nul
echo  Cache cleared.
echo.
pause
goto :menu
:build
cls
echo.
if not exist "%VENV_PYTHON%" goto :err_setup
set /p "BC=  Build exe? [Y/N]: "
if /i not "!BC!"=="Y" goto :menu
cd /d "%BOT_DIR%"
"%VENV_PYTHON%" -c "import shutil,os;[shutil.rmtree(os.path.join(r,d)) for r,ds,_ in os.walk('.') if 'venv' not in r for d in ds if d=='__pycache__']" >nul 2>&1
"%VENV_PIP%" install pyinstaller --quiet
"%VENV_DIR%\Scripts\pyinstaller.exe" --onefile --name "TradingBot" --add-data "config.py;." --add-data "bot_helper.py;." --hidden-import "ccxt" --hidden-import "loguru" --hidden-import "dotenv" --hidden-import "schedule" --collect-all "ccxt" main.py
if not errorlevel 1 goto :build_ok
echo  Build failed.
pause
goto :menu
:build_ok
echo  Done: %BOT_DIR%dist\TradingBot.exe
explorer "%BOT_DIR%dist"
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
