from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_env_template_is_redacted_and_fail_closed():
    template = (ROOT / ".env.example").read_text(encoding="utf-8")

    assert "OPERATING_MODE=PAPER" in template
    assert "CONTROLLED_LIVE_ENABLED=false" in template
    assert "ENTRY_POLICY=SHADOW_ONLY" in template
    assert "MODEL_GATE_SHADOW=true" in template
    assert "your_" not in template.lower()
    for key in (
        "BINANCE_API_KEY",
        "BINANCE_SECRET_KEY",
        "BYBIT_API_KEY",
        "BYBIT_SECRET_KEY",
        "BITGET_API_KEY",
        "BITGET_SECRET_KEY",
        "BITGET_PASSPHRASE",
        "ANTHROPIC_API_KEY",
    ):
        assert f"{key}=\n" in template


def test_windows_startup_task_uses_only_the_canonical_safe_supervisor():
    source = (ROOT / "scripts" / "install_24x7_task.ps1").read_text(
        encoding="utf-8"
    ).lower()

    assert "launcher_supervisor.py" in source
    assert "run --restart" in source
    # Action args must be flag-less (.env is the only profile surface).
    assert '"{0}" run --restart' in (ROOT / "scripts" / "install_24x7_task.ps1").read_text(
        encoding="utf-8"
    )
    assert "[string]$PaperProfile" not in (ROOT / "scripts" / "install_24x7_task.ps1").read_text(
        encoding="utf-8"
    )
    assert "-atstartup" in source
    assert "paper" in source and "observation" in source
    assert "controlled_live" not in source
    assert "main.py" not in source
    assert "api_key" not in source
    assert "-allowstartifonbatteries" in source
    assert "-dontstopifgoingonbatteries" in source
    assert "-waketorun" in source
    assert "administrator powershell" in source
    assert "no tradingbot task was registered" in source
    assert "-erroraction stop" in source
    assert "get-scheduledtask -taskname" in source
    assert "-atlogon" in source


def test_weekly_retrain_pins_the_project_virtual_environment():
    source = (ROOT / "scripts" / "retrain_weekly.ps1").read_text(
        encoding="utf-8"
    ).lower()

    assert "venv\\scripts\\python.exe" in source
    assert "project virtual-environment python not found" in source
    assert "--auto-promote" in source
