from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _engine_source() -> str:
    return (ROOT / "core" / "bot_engine.py").read_text(encoding="utf-8")


def test_heartbeat_is_published_atomically_and_failures_are_visible():
    source = _engine_source()
    block = source[source.index("def _write_heartbeat"):source.index("def _try_reconnect")]

    assert "atomic_write_json" in block
    assert "heartbeat write failed" in block
    assert '"schema_version": 1' in block
    assert '"paper_trading_profile"' in block
    assert '"risk_profile"' in block
    assert '"aggregate_open_risk_pct"' in block
    assert '"operating_mode"' in block
    assert 'Path("data/heartbeat.json").write_text' not in block


def test_daily_health_and_summary_jobs_are_explicitly_utc():
    source = _engine_source()

    assert source.count('schedule.every().day.at("00:00", "UTC")') == 2
