@echo off
setlocal EnableDelayedExpansion
title Trading Bot
set "BOT_DIR=%~dp0"
set "VENV_DIR=%BOT_DIR%venv"
set "VENV_PYTHON=%VENV_DIR%\Scripts\python.exe"
set "VENV_PIP=%VENV_DIR%\Scripts\pip.exe"
set "SETUP_FLAG=%BOT_DIR%data\.setup_done"
set "HELPER=bot_helper.py"
if /i "%~1"=="--supervise" goto :supervise
if not exist "%BOT_DIR%data"            mkdir "%BOT_DIR%data" 2>nul
if not exist "%BOT_DIR%logs"            mkdir "%BOT_DIR%logs" 2>nul
if not exist "%BOT_DIR%data\research"   mkdir "%BOT_DIR%data\research" 2>nul
if not exist "%BOT_DIR%data\profiles"   mkdir "%BOT_DIR%data\profiles" 2>nul
if not exist "%BOT_DIR%data\arbitrage"  mkdir "%BOT_DIR%data\arbitrage" 2>nul
if not exist "%SETUP_FLAG%" goto :wizard
goto :menu
:supervise
cd /d "%BOT_DIR%"
if not exist "%VENV_PYTHON%" (
    echo  ERROR: venv Python not found. Run TradingBot.bat to complete setup.
    exit /b 2
)
if not exist ".env" (
    echo  ERROR: .env missing. Run TradingBot.bat to complete setup.
    exit /b 2
)
"%VENV_PYTHON%" "%BOT_DIR%scripts\launcher_supervisor.py" run --restart
exit /b %ERRORLEVEL%
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
echo  Step 3 - Credentials
echo.
echo  Credential input is hidden and never passed on a process command line.
echo.
cd /d "%BOT_DIR%"
"%VENV_PYTHON%" "%BOT_DIR%scripts\launcher_supervisor.py" secure-setup
if errorlevel 1 goto :secure_setup_fail
cls
echo  Step 4 - Risk Profile
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
echo.
set /p "MODE_C=  Choose [1/2]: "
set "OP_MODE=PAPER"
if "!MODE_C!"=="2" set "OP_MODE=OBSERVATION"
cd /d "%BOT_DIR%"
"%VENV_PYTHON%" %HELPER% apply_risk "!R_PCT!" "!R_POS!" "!R_DL!" "!R_SL!" "!R_TP!" "!R_LEV!"
"%VENV_PYTHON%" "%BOT_DIR%scripts\launcher_supervisor.py" set-mode "!OP_MODE!"
if errorlevel 1 goto :secure_setup_fail
echo done > "%SETUP_FLAG%"
echo  Setup complete.
timeout /t 3 >nul
goto :menu
:secure_setup_fail
echo.
echo  ERROR: Secure setup did not complete.
pause
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
echo   [A] Switch safe mode   [B] Re-run wizard       [D] Build exe
echo   [M] Multi-Profile      [R] Multi report
echo  -----------------------------------------------------------------------
echo   WALLET
echo   [L]  Replicate Live Wallet to DRY RUN
echo   [K]  View last wallet snapshot
echo  -----------------------------------------------------------------------
echo   MAINTENANCE
echo   [J]  Fix ghost/phantom positions
echo  -----------------------------------------------------------------------
echo   [Y] Arbitrage backtest
echo   [X] Send email report  [W] Test email
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
if /i "!CHOICE!"=="D" goto :build
if /i "!CHOICE!"=="M" goto :mprofile
if /i "!CHOICE!"=="R" goto :mreport
if /i "!CHOICE!"=="L" goto :wallet_replicate
if /i "!CHOICE!"=="K" goto :wallet_show
if /i "!CHOICE!"=="J" goto :fix_ghosts
if /i "!CHOICE!"=="Y" goto :arb_bt
if /i "!CHOICE!"=="X" goto :email_send
if /i "!CHOICE!"=="W" goto :email_test
goto :menu
:run_bot
cls
echo.
echo  Starting bot...
echo.
cd /d "%BOT_DIR%"
if not exist "%VENV_PYTHON%" goto :err_setup
if not exist ".env" goto :err_env
echo  Running. Ctrl+C to stop.
echo.
"%VENV_PYTHON%" "%BOT_DIR%scripts\launcher_supervisor.py" run --restart
echo.
echo  Bot stopped.
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
"%VENV_PYTHON%" -m dashboard --refresh 60
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
echo.
echo  CONTROLLED_LIVE cannot be activated or started from this launcher.
echo.
cd /d "%BOT_DIR%"
set /p "MC=  Choose [1/2]: "
if "!MC!"=="1" (
    "%VENV_PYTHON%" "%BOT_DIR%scripts\launcher_supervisor.py" set-mode PAPER
    echo  Switched to PAPER mode.
    pause
    goto :menu
)
if "!MC!"=="2" (
    "%VENV_PYTHON%" "%BOT_DIR%scripts\launcher_supervisor.py" set-mode OBSERVATION
    echo  Switched to OBSERVATION mode.
    pause
    goto :menu
)
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
"%VENV_PYTHON%" "%BOT_DIR%scripts\launcher_supervisor.py" secure-keys binance
pause
goto :menu
:k_bybit
echo.
"%VENV_PYTHON%" "%BOT_DIR%scripts\launcher_supervisor.py" secure-keys bybit
pause
goto :menu
:k_bitget
echo.
"%VENV_PYTHON%" "%BOT_DIR%scripts\launcher_supervisor.py" secure-keys bitget
pause
goto :menu
:k_em
echo.
"%VENV_PYTHON%" "%BOT_DIR%scripts\launcher_supervisor.py" secure-email
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
:build
cls
echo.
if not exist "%VENV_PYTHON%" goto :err_setup
set /p "BC=  Build exe? [Y/N]: "
if /i not "!BC!"=="Y" goto :menu
cd /d "%BOT_DIR%"
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
