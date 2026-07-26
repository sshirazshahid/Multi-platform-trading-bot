[CmdletBinding(SupportsShouldProcess = $true)]
param(
  [string]$TaskName = 'TradingBot-24x7',
  [switch]$AtLogOn,
  [switch]$StartNow
)

$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$python = Join-Path $root 'venv\Scripts\python.exe'
$launcher = Join-Path $root 'scripts\launcher_supervisor.py'
$envFile = Join-Path $root '.env'

if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
  throw "Virtual-environment Python not found: $python"
}
if (-not (Test-Path -LiteralPath $launcher -PathType Leaf)) {
  throw "Canonical launcher not found: $launcher"
}
if (-not (Test-Path -LiteralPath $envFile -PathType Leaf)) {
  throw '.env is missing; copy .env.example and complete PAPER setup first.'
}

$modeLine = Get-Content -LiteralPath $envFile | Where-Object {
  $_ -match '^\s*OPERATING_MODE\s*='
} | Select-Object -Last 1
$mode = if ($modeLine) { ($modeLine -split '=', 2)[1].Trim().ToUpperInvariant() } else { 'PAPER' }
if ($mode -notin @('PAPER', 'OBSERVATION')) {
  throw "Refusing scheduled startup in $mode mode. This task is PAPER/OBSERVATION only."
}

# Profile lives ONLY in .env (PAPER_TRADING_PROFILE). Never bake --paper-profile
# into the schtask — that silently overrode MAX_FLOW_BAND (2026-07-22 incident).
$actionArgs = '"{0}" run --restart' -f $launcher
$action = New-ScheduledTaskAction `
  -Execute $python `
  -Argument $actionArgs `
  -WorkingDirectory $root
$trigger = if ($AtLogOn) {
  New-ScheduledTaskTrigger -AtLogOn -User ([System.Environment]::UserName)
} else {
  New-ScheduledTaskTrigger -AtStartup
}
$settings = New-ScheduledTaskSettingsSet `
  -StartWhenAvailable `
  -AllowStartIfOnBatteries `
  -DontStopIfGoingOnBatteries `
  -WakeToRun `
  -RestartCount 999 `
  -RestartInterval (New-TimeSpan -Minutes 1) `
  -ExecutionTimeLimit ([TimeSpan]::Zero) `
  -MultipleInstances IgnoreNew
$identity = if ($AtLogOn) {
  [System.Environment]::UserName
} else {
  [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
}
$principal = New-ScheduledTaskPrincipal `
  -UserId $identity `
  -LogonType $(if ($AtLogOn) { 'Interactive' } else { 'S4U' }) `
  -RunLevel Limited

if ($PSCmdlet.ShouldProcess($TaskName, "register recovery task for $mode mode")) {
  try {
    Register-ScheduledTask `
      -TaskName $TaskName `
      -Description 'Fail-closed PAPER/OBSERVATION trading bot supervisor' `
      -Action $action `
      -Trigger $trigger `
      -Settings $settings `
      -Principal $principal `
      -Force `
      -ErrorAction Stop | Out-Null
    $registered = Get-ScheduledTask -TaskName $TaskName -ErrorAction Stop
    if ($null -eq $registered) {
      throw "Task Scheduler did not return the newly registered task."
    }
  } catch {
    $guidance = if ($AtLogOn) {
      'Task Scheduler denied the per-user logon fallback.'
    } else {
      ('Task Scheduler denied boot-time registration. Re-run this exact script ' +
       'from an Administrator PowerShell, or add -AtLogOn for a per-user ' +
       'recovery fallback that begins only after sign-in.')
    }
    throw ($guidance + ' No TradingBot task was registered. Windows said: ' +
      $_.Exception.Message)
  }
  $triggerLabel = if ($AtLogOn) { 'at user logon' } else { 'at Windows startup' }
  Write-Host "Registered $TaskName for $identity ($mode, $triggerLabel)."

  if ($StartNow) {
    Start-ScheduledTask -TaskName $TaskName
    Write-Host "Started $TaskName."
  }
}
