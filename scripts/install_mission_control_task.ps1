[CmdletBinding(SupportsShouldProcess = $true)]
param(
  [string]$TaskName = 'TradingBot-MissionControl',
  [switch]$AtLogOn,
  [switch]$StartNow
)

$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$python = Join-Path $root 'venv\Scripts\python.exe'
$runner = Join-Path $root 'scripts\run_mission_control.py'
$envFile = Join-Path $root '.env'

if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
  throw "Virtual-environment Python not found: $python"
}
if (-not (Test-Path -LiteralPath $runner -PathType Leaf)) {
  throw "Mission Control runner not found: $runner"
}
if (-not (Test-Path -LiteralPath $envFile -PathType Leaf)) {
  throw '.env is missing; copy .env.example and set MISSION_CONTROL_TOKEN first.'
}

$tokenLine = Get-Content -LiteralPath $envFile | Where-Object {
  $_ -match '^\s*MISSION_CONTROL_TOKEN\s*='
} | Select-Object -Last 1
$token = if ($tokenLine) { ($tokenLine -split '=', 2)[1].Trim() } else { '' }
if (-not $token) {
  throw 'MISSION_CONTROL_TOKEN is empty in .env; refuse to register Mission Control task.'
}

# .env is the activation surface (token/host/port). Never bake secrets into the
# task action — the runner load_dotenv's .env at start.
$actionArgs = '"{0}"' -f $runner
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

if ($PSCmdlet.ShouldProcess($TaskName, 'register Mission Control recovery task')) {
  try {
    Register-ScheduledTask `
      -TaskName $TaskName `
      -Description 'Localhost Mission Control WebUI (127.0.0.1:8787); token from .env' `
      -Action $action `
      -Trigger $trigger `
      -Settings $settings `
      -Principal $principal `
      -Force `
      -ErrorAction Stop | Out-Null
    $registered = Get-ScheduledTask -TaskName $TaskName -ErrorAction Stop
    if ($null -eq $registered) {
      throw 'Task Scheduler did not return the newly registered task.'
    }
  } catch {
    $guidance = if ($AtLogOn) {
      'Task Scheduler denied the per-user logon fallback.'
    } else {
      ('Task Scheduler denied boot-time registration. Re-run from an ' +
       'Administrator PowerShell, or add -AtLogOn for per-user logon recovery.')
    }
    throw ($guidance + ' No Mission Control task was registered. Windows said: ' +
      $_.Exception.Message)
  }
  $triggerLabel = if ($AtLogOn) { 'at user logon' } else { 'at Windows startup' }
  Write-Host "Registered $TaskName for $identity ($triggerLabel)."

  if ($StartNow) {
    Start-ScheduledTask -TaskName $TaskName
    Write-Host "Started $TaskName."
  }
}
