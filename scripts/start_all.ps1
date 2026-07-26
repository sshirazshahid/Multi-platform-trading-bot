# Launch persistent paper/read-only collectors without duplicate workers.
# TradingBot.bat owns bot supervision; this script owns auxiliary startup only.
$ErrorActionPreference = 'SilentlyContinue'
$root = Split-Path $PSScriptRoot -Parent
Set-Location $root
$py = Join-Path $root 'venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $py)) {
  Write-Host "[start_all] venv python not found: $py"
  exit 1
}

# Serialize discovery plus launch. This closes the race between two launchers
# that both observe a collector as absent before either has started it.
$mutex = [Threading.Mutex]::new($false, 'Local\TradingBot.StartAll')
$ownsMutex = $false
try {
  try {
    $ownsMutex = $mutex.WaitOne([TimeSpan]::FromSeconds(30))
  } catch [Threading.AbandonedMutexException] {
    $ownsMutex = $true
  }
  if (-not $ownsMutex) {
    Write-Host '[start_all] another startup pass is active; not starting workers'
    exit 0
  }

  $procs = @()
  if (Test-Path -LiteralPath (Join-Path $root 'run_confluence_paper.py')) {
    $procs += @{ name = 'confluence-paper'; script = 'run_confluence_paper.py' }
  } else {
    Write-Host '[start_all] confluence-paper skipped (run_confluence_paper.py not present)'
  }
  $procs += @(
    @{ name = 'liq-harvester';  script = 'scripts\harvest_liquidations.py' },
    @{ name = 'skew-harvester'; script = 'scripts\harvest_skew.py' },
    @{ name = 'l2-harvester';   script = 'scripts\harvest_l2.py' },
    @{ name = 'tv-harvester';   script = 'scripts\harvest_tv.py' }
  )

  try {
    $running = @(Get-CimInstance Win32_Process `
                 -Filter "name='python.exe' or name='pythonw.exe'" -ErrorAction Stop |
                 Select-Object -ExpandProperty CommandLine)
  } catch {
    Write-Host '[start_all] cannot verify existing workers; refusing auxiliary startup'
    throw
  }

  foreach ($proc in $procs) {
    $scriptPath = Join-Path $root $proc.script
    if (-not (Test-Path -LiteralPath $scriptPath)) {
      Write-Host ("[start_all] {0,-18} skipped (script missing)" -f $proc.name)
      continue
    }

    $base = Split-Path $scriptPath -Leaf
    $isRunning = $false
    foreach ($commandLine in $running) {
      if ($commandLine -and $commandLine -match [regex]::Escape($base)) {
        $isRunning = $true
        break
      }
    }
    if ($isRunning) {
      Write-Host ("[start_all] {0,-18} already running" -f $proc.name)
      continue
    }

    $quotedScript = '"{0}"' -f $scriptPath
    $child = Start-Process -FilePath $py -ArgumentList $quotedScript -WindowStyle Hidden `
                           -WorkingDirectory $root -PassThru
    if ($null -ne $child) {
      $running += "$py $quotedScript"
      Write-Host ("[start_all] {0,-18} STARTED (pid {1})" -f $proc.name, $child.Id)
    } else {
      Write-Host ("[start_all] {0,-18} failed to start" -f $proc.name)
    }
  }
  Write-Host '[start_all] done. main.py is owned by TradingBot.bat.'
} finally {
  if ($ownsMutex) {
    $mutex.ReleaseMutex()
  }
  $mutex.Dispose()
}
