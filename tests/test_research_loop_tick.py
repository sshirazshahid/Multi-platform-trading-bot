"""Tests for scripts/run_research_loop_tick.py — fail-closed research ops tick."""

from __future__ import annotations

import json
from pathlib import Path

from scripts.run_research_loop_tick import run_tick


def test_research_loop_tick_dry_run_refuses_install(tmp_path: Path) -> None:
    (tmp_path / "data").mkdir()
    (tmp_path / ".env").write_text(
        "OPERATING_MODE=PAPER\nPAPER_TRADING_PROFILE=MAX_FLOW_BAND\n"
        "MCP_DIRECTIONAL_ECONOMIC_GATE_MODE=paper_fallback\n",
        encoding="utf-8",
    )
    payload = run_tick(root=tmp_path, dry_run=True)
    assert payload["dry_run"] is True
    assert "refuse_auto_strategy_install" in payload["refusals"]
    assert "refuse_controlled_live_enable" in payload["refusals"]
    assert not (tmp_path / "data" / "research_loop_tick_latest.json").exists()
    assert "goal_progress.json missing" in " ".join(payload["warnings"])


def test_research_loop_tick_writes_latest(tmp_path: Path) -> None:
    data = tmp_path / "data"
    data.mkdir()
    (tmp_path / ".env").write_text("OPERATING_MODE=PAPER\n", encoding="utf-8")
    (data / "goal_progress.json").write_text(
        json.dumps({"lanes": [], "generated_utc": "2026-08-11T00:00:00Z"}),
        encoding="utf-8",
    )
    (data / "promotion_funnel.json").write_text(
        json.dumps({"lanes": [], "generated_utc": "2026-08-11T00:00:00Z"}),
        encoding="utf-8",
    )
    payload = run_tick(root=tmp_path, dry_run=False)
    out = data / "research_loop_tick_latest.json"
    assert out.exists()
    disk = json.loads(out.read_text(encoding="utf-8"))
    assert disk["s0"]["operating_mode_env"] == "PAPER"
    assert payload["honesty"]
    assert "s4_stack_audit" in payload
    assert payload["s4_stack_audit"]["live_trade_authorized"] is False
    # tmp_path lacks full repo tree → audit should fail closed
    assert payload["s4_stack_audit"]["ok"] is False
    assert any("paper_stack_audit FAILED" in w for w in payload["warnings"])
    assert "s5_exit_geometry" in payload
    assert payload["s5_exit_geometry"]["live_trade_authorized"] is False
    assert "s6_mature_cohort" in payload
    assert payload["s6_mature_cohort"]["live_trade_authorized"] is False
