# start_all.ps1 — launch the trading bot's PERSISTENT auxiliary processes (dedup-safe).
#
# Started by start_all.bat, which is called from auto_restart.bat (before its restart loop)
# and TradingBot.bat (menu option [1] Start bot). Does NOT start main.py — each entry point
# manages the bot itself (auto_restart.bat = crash-restart loop; TradingBot.bat = foreground).
#
# Dedup: a script is only launched if no running python.exe already has it in its command
# line, so clicking the launcher twice (or having a scheduled task already up) will NOT spawn
# duplicates (two harvesters would double-count the same hour bucket).
#
# Periodic research (market-intel / weekly / etf-monthly / daily-scan) is NOT here — those run
# on their own scheduled-task cadence; running weekly jobs on every click would be wrong.
$ErrorActionPreference = 'SilentlyContinue'
$root = Split-Path $PSScriptRoot -Parent
Set-Location $root
$py = Join-Path $root 'venv\Scripts\python.exe'
if (-not (Test-Path $py)) { Write-Host "[start_all] venv python not found: $py"; exit 1 }

# (label, script path relative to repo root) — persistent loops only
$procs = @(
  @{ name = 'confluence-paper'; script = 'run_confluence_paper.py' },
  @{ name = 'liq-harvester';    script = 'scripts\harvest_liquidations.py' },
  @{ name = 'skew-harvester';   script = 'scripts\harvest_skew.py' },
  @{ name = 'l2-harvester';     script = 'scripts\harvest_l2.py' },
  @{ name = 'tv-harvester';     script = 'scripts\harvest_tv.py' }
)

$running = @(Get-CimInstance Win32_Process -Filter "name='python.exe'" -ErrorAction SilentlyContinue |
             Select-Object -ExpandProperty CommandLine)

foreach ($p in $procs) {
  $base = Split-Path $p.script -Leaf
  $isUp = $false
  foreach ($cl in $running) { if ($cl -and $cl -match [regex]::Escape($base)) { $isUp = $true; break } }
  if ($isUp) {
    Write-Host ("[start_all] {0,-18} already running" -f $p.name)
  } else {
    Start-Process -FilePath $py -ArgumentList $p.script -WindowStyle Hidden -WorkingDirectory $root
    Write-Host ("[start_all] {0,-18} STARTED" -f $p.name)
  }
}
Write-Host "[start_all] done. (main.py is started by the caller: auto_restart.bat / TradingBot.bat [1].)"
