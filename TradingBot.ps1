# TradingBot.ps1 — PowerShell launcher
# Right-click -> "Run with PowerShell"

$Host.UI.RawUI.WindowTitle = "Trading Bot Launcher"
$BOT_DIR     = Split-Path -Parent $MyInvocation.MyCommand.Path
$VENV_PYTHON = Join-Path $BOT_DIR "venv\Scripts\python.exe"

function Show-Header {
    Clear-Host
    Write-Host ""
    Write-Host "  ============================================================" -ForegroundColor Cyan
    Write-Host "    Trading Bot  |  Binance + Bybit + Bitget" -ForegroundColor Cyan
    Write-Host "    Trend Following  /  Grid Trading  /  Scalping" -ForegroundColor DarkCyan
    Write-Host "  ============================================================" -ForegroundColor Cyan
    Write-Host ""
}

function Find-Python {
    if (Test-Path $VENV_PYTHON) { return $VENV_PYTHON }
    foreach ($cmd in @("py", "python3", "python")) {
        try {
            $path = (Get-Command $cmd -ErrorAction SilentlyContinue).Source
            if ($path) { return $cmd }
        } catch {}
    }
    Write-Host "  [ERROR] Python not found." -ForegroundColor Red
    Write-Host "  Download from: https://www.python.org/downloads/" -ForegroundColor Yellow
    Read-Host "  Press Enter to exit"
    exit 1
}

function Install-Bot {
    Show-Header
    Write-Host "  Installing dependencies..." -ForegroundColor Yellow
    Write-Host ""
    $basePython = Find-Python

    if (-not (Test-Path (Join-Path $BOT_DIR "venv"))) {
        Write-Host "  [1/3] Creating virtual environment..." -ForegroundColor Cyan
        & $basePython -m venv (Join-Path $BOT_DIR "venv")
    } else {
        Write-Host "  [1/3] Venv already exists — skipping" -ForegroundColor Green
    }

    Write-Host "  [2/3] Upgrading pip..." -ForegroundColor Cyan
    & $VENV_PYTHON -m pip install --upgrade pip --quiet

    Write-Host "  [3/3] Installing packages..." -ForegroundColor Cyan
    & (Join-Path $BOT_DIR "venv\Scripts\pip.exe") install -r (Join-Path $BOT_DIR "requirements.txt")

    Write-Host ""
    Write-Host "  Running setup wizard..." -ForegroundColor Cyan
    Set-Location $BOT_DIR
    & $VENV_PYTHON (Join-Path $BOT_DIR "setup.py")

    Write-Host ""
    Write-Host "  Done! Now edit .env with your API keys (Option 5)." -ForegroundColor Green
    Read-Host "  Press Enter to return to menu"
}

function Run-Bot {
    Show-Header
    if (-not (Test-Path $VENV_PYTHON)) {
        Write-Host "  [ERROR] Run Install first (Option 1)." -ForegroundColor Red
        Read-Host "  Press Enter"; return
    }
    if (-not (Test-Path (Join-Path $BOT_DIR ".env"))) {
        Write-Host "  [ERROR] .env not found. Run Install first." -ForegroundColor Red
        Read-Host "  Press Enter"; return
    }

    $envContent = Get-Content (Join-Path $BOT_DIR ".env") -ErrorAction SilentlyContinue
    $dryRunLine = $envContent | Where-Object { $_ -match "^DRY_RUN\s*=" }
    $isLive     = $dryRunLine -match "=\s*false"

    if ($isLive) {
        Write-Host "  [WARNING] DRY_RUN=false — REAL TRADES WILL BE PLACED!" -ForegroundColor Red
        Write-Host ""
        $confirm = Read-Host "  Type YES to confirm live trading"
        if ($confirm -ne "YES") {
            Write-Host "  Cancelled." -ForegroundColor Yellow
            Read-Host "  Press Enter"; return
        }
    } else {
        Write-Host "  Mode: DRY RUN (paper trading)" -ForegroundColor Green
    }

    Write-Host ""
    Write-Host "  Starting bot... Ctrl+C to stop." -ForegroundColor Cyan
    Write-Host "  Logs: $BOT_DIR\logs\" -ForegroundColor DarkGray
    Write-Host ""
    Set-Location $BOT_DIR
    & $VENV_PYTHON (Join-Path $BOT_DIR "main.py")
    Write-Host ""
    Read-Host "  Bot stopped. Press Enter to return"
}

function Run-Backtest {
    Show-Header
    if (-not (Test-Path $VENV_PYTHON)) {
        Write-Host "  [ERROR] Run Install first." -ForegroundColor Red
        Read-Host "  Press Enter"; return
    }

    Write-Host "  Strategies:" -ForegroundColor Cyan
    Write-Host "    [1] Trend Following"
    Write-Host "    [2] Scalping"
    Write-Host "    [3] Grid"
    Write-Host ""
    $s        = Read-Host "  Choose strategy [1-3]"
    $stratMap = @{"1"="trend"; "2"="scalping"; "3"="grid"}
    $strat    = $stratMap[$s]
    if (-not $strat) { Write-Host "  Invalid." -ForegroundColor Red; Read-Host; return }

    $symbol = Read-Host "  Symbol [BTC/USDT]"
    if (-not $symbol) { $symbol = "BTC/USDT" }

    $days = Read-Host "  Days of history [90]"
    if (-not $days) { $days = "90" }

    $ex = Read-Host "  Exchange: binance, bybit, or bitget [binance]"
    if (-not $ex) { $ex = "binance" }

    Write-Host ""
    Write-Host "  Running backtest: $strat on $symbol ($ex, $days days)" -ForegroundColor Cyan
    Write-Host ""
    Set-Location $BOT_DIR
    & $VENV_PYTHON (Join-Path $BOT_DIR "backtest.py") --strategy $strat --symbol $symbol --exchange $ex --days $days
    Write-Host ""
    Read-Host "  Press Enter to return"
}

function Show-Status {
    Show-Header
    if (-not (Test-Path $VENV_PYTHON)) {
        Write-Host "  [ERROR] Run Install first." -ForegroundColor Red
        Read-Host "  Press Enter"; return
    }
    Set-Location $BOT_DIR
    & $VENV_PYTHON (Join-Path $BOT_DIR "main.py") --status
    Write-Host ""
    Read-Host "  Press Enter to return"
}

function Edit-Config {
    Show-Header
    $envFile = Join-Path $BOT_DIR ".env"
    if (-not (Test-Path $envFile)) {
        Copy-Item (Join-Path $BOT_DIR ".env.example") $envFile -ErrorAction SilentlyContinue
        Write-Host "  Created .env from .env.example" -ForegroundColor Green
    }
    Write-Host "  [1] Edit .env      (API keys, DRY_RUN toggle)"
    Write-Host "  [2] Edit config.py (strategy parameters)"
    Write-Host "  [3] Back"
    Write-Host ""
    $c = Read-Host "  Choice"
    if ($c -eq "1") { notepad $envFile }
    if ($c -eq "2") { notepad (Join-Path $BOT_DIR "config.py") }
}

function View-Logs {
    Show-Header
    $logsDir = Join-Path $BOT_DIR "logs"
    if (-not (Test-Path $logsDir)) {
        Write-Host "  No logs yet — run the bot first." -ForegroundColor Yellow
        Read-Host "  Press Enter"; return
    }
    Write-Host "  [1] Tail latest log (live)"
    Write-Host "  [2] Open logs folder"
    Write-Host "  [3] View error log"
    Write-Host "  [4] Back"
    Write-Host ""
    $c = Read-Host "  Choice"

    if ($c -eq "1") {
        $latest = Get-ChildItem "$logsDir\bot_*.log" | Sort-Object LastWriteTime -Descending | Select-Object -First 1
        if ($latest) {
            Write-Host "  Following $($latest.Name) — Ctrl+C to stop" -ForegroundColor Cyan
            Get-Content $latest.FullName -Wait -Tail 40
        } else {
            Write-Host "  No log files found." -ForegroundColor Yellow
            Read-Host "  Press Enter"
        }
    }
    if ($c -eq "2") { Start-Process explorer $logsDir }
    if ($c -eq "3") {
        $errLog = Join-Path $logsDir "errors.log"
        if (Test-Path $errLog) { Get-Content $errLog | More }
        else { Write-Host "  No errors logged." -ForegroundColor Green }
        Read-Host "  Press Enter"
    }
}

# -- Main loop
while ($true) {
    Show-Header
    Write-Host "  [1]  Install / Update dependencies"
    Write-Host "  [2]  Run the bot"
    Write-Host "  [3]  Backtest a strategy"
    Write-Host "  [4]  Show bot status"
    Write-Host "  [5]  Edit configuration"
    Write-Host "  [6]  View logs"
    Write-Host "  [7]  Exit"
    Write-Host ""
    $choice = Read-Host "  Enter choice [1-7]"
    switch ($choice) {
        "1" { Install-Bot }
        "2" { Run-Bot }
        "3" { Run-Backtest }
        "4" { Show-Status }
        "5" { Edit-Config }
        "6" { View-Logs }
        "7" { exit 0 }
        default { Write-Host "  Invalid choice." -ForegroundColor Red; Start-Sleep 1 }
    }
}
