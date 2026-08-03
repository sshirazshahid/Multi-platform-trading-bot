# scripts/retrain_weekly.ps1 — weekly LR+GBM ensemble retrain.
#
# Sundays 04:00 UTC via Windows Task Scheduler. Runs the full pipeline against
# the live SQLite warehouse, lets the promotion gate decide whether each new
# model replaces the *_latest.json pointer, and tees stdout+stderr to a
# timestamped log under data\retrain_logs\.
#
# Register with Task Scheduler (run once, as Administrator):
#
#   $ScriptPath = "D:\Downloads\Trading_Bot\scripts\retrain_weekly.ps1"
#   schtasks /Create /TN "TradingBot Weekly Retrain" `
#     /TR "powershell.exe -NoProfile -ExecutionPolicy Bypass -File `"$ScriptPath`"" `
#     /SC WEEKLY /D SUN /ST 04:00 /RL HIGHEST /F
#
# To change the schedule:
#   schtasks /Change /TN "TradingBot Weekly Retrain" /ST 05:00
# To run it on demand:
#   schtasks /Run /TN "TradingBot Weekly Retrain"
# To delete:
#   schtasks /Delete /TN "TradingBot Weekly Retrain" /F

$ErrorActionPreference = "Stop"

# Resolve repo root (script lives in <root>/scripts).
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

# Pin scheduled research to this repository's tested environment. An explicit
# override remains available for controlled maintenance, but an absent venv is
# a hard failure instead of silently using an unrelated PATH interpreter.
$VenvPython = Join-Path $Root "venv\Scripts\python.exe"
$Python = if ($env:TRADINGBOT_PYTHON) {
    $env:TRADINGBOT_PYTHON
} elseif (Test-Path -LiteralPath $VenvPython -PathType Leaf) {
    $VenvPython
} else {
    throw "Project virtual-environment Python not found: $VenvPython"
}

# Log file (one per run, timestamped).
$LogDir = Join-Path $Root "data\retrain_logs"
New-Item -ItemType Directory -Path $LogDir -Force | Out-Null
$Stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$LogFile = Join-Path $LogDir "retrain_$Stamp.log"

function Log {
    param([string]$Message)
    $line = "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] $Message"
    Write-Host $line
    Add-Content -Path $LogFile -Value $line
}

function RunStep {
    param([string]$Description, [string[]]$ArgList)
    Log "BEGIN $Description"
    Log ("  cmd: $Python " + ($ArgList -join ' '))
    # PS 5.1 gotcha (measured 2026-08-02/03, both runs died at the identical
    # point with no FAIL line): with $ErrorActionPreference='Stop', `2>&1` on
    # a NATIVE command wraps each stderr line in a NativeCommandError record
    # and the FIRST one becomes a TERMINATING error — python progress bars or
    # warnings killed the whole retrain mid-step, bypassing the exit-code
    # check below. Make stderr non-terminating for the call and stringify the
    # records so the log still captures them verbatim.
    $prevEAP = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        & $Python @ArgList 2>&1 |
            ForEach-Object { "$_" } |
            Tee-Object -FilePath $LogFile -Append
    } finally {
        $ErrorActionPreference = $prevEAP
    }
    if ($LASTEXITCODE -ne 0) {
        Log "FAIL  $Description (exit=$LASTEXITCODE) -- aborting weekly retrain"
        exit $LASTEXITCODE
    }
    Log "OK    $Description"
}

Log "==============================================================="
Log " Weekly retrain start  ($(Get-Date -Format o))"
Log "==============================================================="

# 1. Backfill features for any new candidates that arrived this week.
RunStep "build_features_dataset" @("scripts\build_features_dataset.py", "--print-every", "500")

# 2. Triple-barrier labels — both markets.
RunStep "build_labels (futures)" @("scripts\build_labels.py", "--market", "futures", "--print-every", "500")
RunStep "build_labels (spot)"    @("scripts\build_labels.py", "--market", "spot",    "--print-every", "500")

# 3. Train LR+GBM ensemble for both markets, run promotion gate inline.
$Tag = "weekly_$(Get-Date -Format yyyyMMdd)"
RunStep "train_models (both, --auto-promote)" @(
    "scripts\train_models.py",
    "--market", "both",
    "--tag", $Tag,
    "--auto-promote"
)

# 4. Refresh hour gate evidence from the rolling warehouse window.
RunStep "refresh_hour_gates" @(
    "scripts\refresh_hour_gates.py",
    "--lookback-days", "60"
)

# 5. Crypto signal postmortem — actionables digest for the week.
RunStep "run_signal_postmortem" @(
    "scripts\run_signal_postmortem.py",
    "--lookback-days", "7"
)

# 6. Weekly experiment scorecard — pre/post inference on every active
#    experiment in scripts/experiments.json (2026-06-11). Exit 0 even on
#    NOT_STARTED/NO_DATA (a degraded report is not a pipeline failure).
RunStep "weekly_scorecard" @(
    "scripts\weekly_scorecard.py"
)

# 7. Summary — read latest pointer per market so the log reflects the outcome.
foreach ($market in @("futures", "spot")) {
    $latest = Join-Path $Root "data\models\ensemble_${market}_latest.json"
    if (Test-Path $latest) {
        $payload = Get-Content $latest -Raw | ConvertFrom-Json
        Log ("LATEST $market -> $($payload.model_version)  promoted_at=$($payload.promoted_at)")
    } else {
        Log "LATEST $market -> (no pointer; gate has never accepted a model)"
    }
}

Log "==============================================================="
Log " Weekly retrain done  ($(Get-Date -Format o))"
Log "==============================================================="
