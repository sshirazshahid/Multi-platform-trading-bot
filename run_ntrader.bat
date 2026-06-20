@echo off
REM ============================================================
REM  NautilusTrader rebuild (ntrader/) - runner
REM  Fully isolated from the live bot. Uses ntrader\venv_nt only;
REM  never touches the live venv, data/ state, or PM2.
REM ============================================================
setlocal
cd /d "%~dp0"

set "PY=ntrader\venv_nt\Scripts\python.exe"
set "PYTEST_ARGS=--rootdir ntrader -p no:cacheprovider --import-mode=importlib -q"

REM --- ensure the isolated venv exists (it is gitignored) ---
if not exist "%PY%" (
  echo [setup] ntrader\venv_nt not found - creating it with Python 3.12 ...
  py -3.12 -m venv ntrader\venv_nt
  if errorlevel 1 (
    echo [error] could not create the venv. Is Python 3.12 installed?  Check with: py -0
    pause
    exit /b 1
  )
  "%PY%" -m pip install --upgrade pip
  "%PY%" -m pip install -r ntrader\requirements-nt.txt pytest
  if errorlevel 1 (
    echo [error] dependency install failed.
    pause
    exit /b 1
  )
)

:menu
echo.
echo ===== NautilusTrader rebuild ^(ntrader^) =====
echo   1. Run tests        - parity gate + risk guard + disaster stop
echo   2. Phase 0 smoke    - prove NautilusTrader installs and runs
echo   3. Phase 1 harness  - SYNTHETIC data backtest
echo   4. Phase 1 harness  - REAL data backtest from data\ohlcv_cache
echo   5. Run everything   - all of 1-4
echo   6. LIVE PAPER trade - real Binance public bars + local sandbox, NO real orders
echo   0. Exit
echo.
set /p "CH=Choose [0-6]: "

if "%CH%"=="1" goto tests
if "%CH%"=="2" goto smoke
if "%CH%"=="3" goto synth
if "%CH%"=="4" goto parquet
if "%CH%"=="5" goto all
if "%CH%"=="6" goto paper
if "%CH%"=="0" exit /b 0
echo Invalid choice.
goto menu

:tests
echo.
echo --- TESTS (parity + risk) ---
"%PY%" -m pytest ntrader\tests\ %PYTEST_ARGS%
goto done

:smoke
echo.
echo --- PHASE 0 SMOKE ---
"%PY%" ntrader\backtests\phase0_smoke.py
goto done

:synth
echo.
echo --- PHASE 1 HARNESS (synthetic) ---
"%PY%" ntrader\backtests\phase1_tsmom_backtest.py --source synth --parity
goto done

:parquet
echo.
echo --- PHASE 1 HARNESS (real data) ---
"%PY%" ntrader\backtests\phase1_tsmom_backtest.py --source parquet --parity
goto done

:all
echo.
echo --- TESTS (parity + risk) ---
"%PY%" -m pytest ntrader\tests\ %PYTEST_ARGS%
echo.
echo --- PHASE 0 SMOKE ---
"%PY%" ntrader\backtests\phase0_smoke.py
echo.
echo --- PHASE 1 HARNESS (synthetic) ---
"%PY%" ntrader\backtests\phase1_tsmom_backtest.py --source synth --parity
echo.
echo --- PHASE 1 HARNESS (real data) ---
"%PY%" ntrader\backtests\phase1_tsmom_backtest.py --source parquet --parity
goto done

:paper
echo.
echo --- LIVE PAPER TRADING - zero-credential, real bars, simulated fills ---
echo Streaming REAL Binance public market data. NO API keys, NO real orders, NO funds at risk.
echo Daily TSMOM is the validated signal: it preserves capital, it does NOT make a profit.
echo It acts on DAILY closes, so it may sit flat for a while - that is expected.
echo Press Ctrl-C to stop.
echo.
"%PY%" ntrader\live\paper_trade.py --coins BTC --timeframe 1d --equity 10000
goto menu

:done
echo.
echo Done.
pause
goto menu
