"""T4 — MAX_FLOW_BAND paper profile (max-flow band engine, 2026-07-19).

New PAPER-only research profile MAX_FLOW_BAND: identical risk envelope to
AGGRESSIVE_RESEARCH (6 slots, 0.125% risk/trade, same aggregate risk, gross
exposure, and leverage) — the profile is a cohort/epoch label plus the gate
key for the T3 geometry guard, NOT a risk expansion. Explicit mode_profile_for
calls still fail closed outside PAPER; config/launcher coerce leftover .env
profiles to STANDARD under OBSERVATION so set-mode does not brick startup.

Env selection: the launcher resolves the default profile from the .env
PAPER_TRADING_PROFILE line (unset -> standard, today's behavior) so the T5
.env block is the single activation surface; the CLI flag still wins.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from core.entry_policy import (
    AGGRESSIVE_RESEARCH_PAPER_PROFILE,
    MAX_FLOW_BAND_PAPER_PROFILE,
    mode_profile_for,
    normalize_paper_profile,
)


def test_profile_name_normalization():
    assert MAX_FLOW_BAND_PAPER_PROFILE == "MAX_FLOW_BAND"
    assert normalize_paper_profile("max-flow-band") == "MAX_FLOW_BAND"
    assert normalize_paper_profile("MAX_FLOW_BAND") == "MAX_FLOW_BAND"


def test_profile_copies_aggressive_research_params():
    band = mode_profile_for("PAPER", MAX_FLOW_BAND_PAPER_PROFILE)
    agg = mode_profile_for("PAPER", AGGRESSIVE_RESEARCH_PAPER_PROFILE)
    assert band.name == "PAPER_MAX_FLOW_BAND"
    assert band.max_open_positions == agg.max_open_positions
    assert band.risk_per_trade_pct == agg.risk_per_trade_pct
    assert band.aggregate_open_risk_pct == agg.aggregate_open_risk_pct
    assert band.gross_exposure_pct == agg.gross_exposure_pct
    assert band.max_leverage == agg.max_leverage


def test_profile_fails_closed_outside_paper():
    for mode in ("CONTROLLED_LIVE", "OBSERVATION"):
        with pytest.raises(ValueError):
            mode_profile_for(mode, MAX_FLOW_BAND_PAPER_PROFILE)


def test_config_coerces_research_profile_under_observation():
    """OBSERVATION + leftover MAX_FLOW_BAND in env must import, not ValueError."""
    env = os.environ.copy()
    for key in (
        "OPERATING_MODE",
        "ENTRY_POLICY",
        "APPROVED_PAPER_STRATEGIES",
        "APPROVED_LIVE_STRATEGIES",
        "CONTROLLED_LIVE_ENABLED",
        "PAPER_TRADING_PROFILE",
        "PAPER_PROFILE_STARTED_AT",
    ):
        env.pop(key, None)
    env["PYTHON_DOTENV_DISABLED"] = "1"
    env["OPERATING_MODE"] = "OBSERVATION"
    env["PAPER_TRADING_PROFILE"] = "MAX_FLOW_BAND"
    root = Path(__file__).resolve().parents[1]
    code = (
        "import config\n"
        "assert config.OPERATING_MODE == 'OBSERVATION'\n"
        "assert config.PAPER_TRADING_PROFILE == 'STANDARD'\n"
    )
    completed = subprocess.run(
        [sys.executable, "-c", code],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr


def test_launcher_worker_env_accepts_profile_in_paper_only(tmp_path):
    from scripts import launcher_supervisor

    (tmp_path / ".env").write_text("OPERATING_MODE=PAPER\n", encoding="utf-8")
    child = launcher_supervisor._safe_worker_env(
        tmp_path, environ={"PAPER_TRADING_PROFILE": "max-flow-band"}
    )
    assert child["PAPER_TRADING_PROFILE"] == "MAX_FLOW_BAND"
    assert child["OPERATING_MODE"] == "PAPER"
    assert child["CONTROLLED_LIVE_ENABLED"] == "false"

    (tmp_path / ".env").write_text("OPERATING_MODE=OBSERVATION\n", encoding="utf-8")
    # Coerce (don't raise): leftover research profile in env must not brick OBSERVATION.
    obs = launcher_supervisor._safe_worker_env(
        tmp_path, environ={"PAPER_TRADING_PROFILE": "max-flow-band"}
    )
    assert obs["OPERATING_MODE"] == "OBSERVATION"
    assert obs["PAPER_TRADING_PROFILE"] == "STANDARD"


def test_launcher_cli_accepts_max_flow_band_choice():
    from scripts import launcher_supervisor

    args = launcher_supervisor._build_parser().parse_args(
        ["run", "--paper-profile", "max-flow-band"]
    )
    assert args.paper_profile == "max-flow-band"


def test_launcher_default_profile_resolves_from_env_file(tmp_path):
    from scripts import launcher_supervisor

    # Unset in .env -> today's behavior (STANDARD).
    (tmp_path / ".env").write_text("OPERATING_MODE=PAPER\n", encoding="utf-8")
    assert (
        normalize_paper_profile(launcher_supervisor._default_paper_profile(tmp_path))
        == "STANDARD"
    )
    # .env carries the profile -> the T5 env block alone activates it.
    (tmp_path / ".env").write_text(
        "OPERATING_MODE=PAPER\nPAPER_TRADING_PROFILE=MAX_FLOW_BAND\n",
        encoding="utf-8",
    )
    assert (
        normalize_paper_profile(launcher_supervisor._default_paper_profile(tmp_path))
        == "MAX_FLOW_BAND"
    )
    # An explicit CLI flag must still win over the .env default.
    args = launcher_supervisor._build_parser().parse_args(
        ["run", "--paper-profile", "standard"]
    )
    assert args.paper_profile == "standard"


def test_supervisor_boot_sets_profile_and_fresh_epoch(tmp_path, monkeypatch):
    """Heartbeat inputs: supervise() must export PAPER_TRADING_PROFILE=
    MAX_FLOW_BAND + a fresh PAPER_PROFILE_STARTED_AT for the worker, which
    bot_engine's heartbeat then reports (clean cohort epoch)."""
    from scripts import launcher_supervisor

    (tmp_path / ".env").write_text("OPERATING_MODE=PAPER\n", encoding="utf-8")
    monkeypatch.setenv("PAPER_TRADING_PROFILE", "STANDARD")
    monkeypatch.delenv("PAPER_PROFILE_STARTED_AT", raising=False)
    observed = []

    class Lock:
        def close(self):
            pass

    result = launcher_supervisor.supervise(
        root=tmp_path,
        paper_profile="max-flow-band",
        acquire_lock=lambda *args, **kwargs: Lock(),
        worker_is_running=lambda *_: False,
        start_helpers=lambda *_: 0,
        run_worker=lambda *_: observed.append(
            (
                os.environ["PAPER_TRADING_PROFILE"],
                float(os.environ["PAPER_PROFILE_STARTED_AT"]),
            )
        )
        or 0,
    )
    assert result == 0
    assert observed[0][0] == "MAX_FLOW_BAND"
    assert observed[0][1] > 0
    assert os.environ["PAPER_TRADING_PROFILE"] == "STANDARD"
    assert "PAPER_PROFILE_STARTED_AT" not in os.environ
