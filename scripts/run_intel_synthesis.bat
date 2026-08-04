@echo off
REM Daily Intel Synthesis — run by TradingBot-IntelSynthesis scheduled task.
REM Writes reports\intel_synthesis_<date>.md. Do NOT pass --email here:
REM email is operator opt-in only (`python scripts\run_intel_synthesis.py --email`).
cd /d "%~dp0.."
if exist "venv\Scripts\python.exe" (
  "venv\Scripts\python.exe" scripts\run_intel_synthesis.py > logs\intel_synthesis.log 2>&1
) else (
  python scripts\run_intel_synthesis.py > logs\intel_synthesis.log 2>&1
)
