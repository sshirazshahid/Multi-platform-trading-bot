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

# Pick a Python: prefer venv, fall back to system python.
$Python = Join-Path $Root "venv\Scripts\python.exe"
if (-not (Test-Path $Python)) {
    $Python = "python"
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
    param([string]$Description, [string[]]$Args)
    Log "BEGIN $Description"
    Log ("  cmd: $Python " + ($Args -join ' '))
    & $Python @Args 2>&1 | Tee-Object -FilePath $LogFile -Append
    if ($LASTEXITCODE -ne 0) {
        Log "FAIL  $Description (exit=$LASTEXITCODE) — aborting weekly retrain"
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

# 4. Summary — read latest pointer per market so the log reflects the outcome.
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
