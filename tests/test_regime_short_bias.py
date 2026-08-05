"""Tests for prereg-61 F&G+liq SHORT-bias log-only evaluator."""
from __future__ import annotations

import json
from pathlib import Path

from core.regime_short_bias import (
    PREREG_ID,
    completed_hour_start,
    evaluate_short_bias,
    sum_long_liq_usd,
)


def test_evaluate_fires_only_when_fear_and_liq(tmp_path: Path) -> None:
    quiet = evaluate_short_bias(fng_value=55, long_usd_24h=300_000_000)
    assert quiet["any_cell_fired"] is False
    assert quiet["live_short_authorized"] is False
    assert quiet["narrative"] == "LIQ_ONLY"

    fear_only = evaluate_short_bias(fng_value=28, long_usd_24h=1_000_000)
    assert fear_only["fng_ok"] is True
    assert fear_only["any_cell_fired"] is False
    assert fear_only["narrative"] == "FEAR_ONLY"

    both = evaluate_short_bias(fng_value=28, long_usd_24h=208_000_000)
    assert both["any_cell_fired"] is True
    assert both["narrative"] == "SHORT_BIAS_ENV"
    assert both["live_short_authorized"] is False
    assert both["prereg_id"] == PREREG_ID
    fired = [c for c in both["cells"] if c["fired"]]
    assert len(fired) >= 3  # 25/50/100M; 200M may or may not


def test_sum_long_liq_window(tmp_path: Path) -> None:
    end = 1_700_000_000
    p = tmp_path / "liq.jsonl"
    rows = [
        {"hour": end - 3600 * 2, "symbol": "ALL", "long_usd": 10e6, "short_usd": 1e6, "count": 3},
        {"hour": end - 3600, "symbol": "ALL", "long_usd": 5e6, "short_usd": 0, "count": 2},
        {"hour": end, "symbol": "ALL", "long_usd": 7e6, "short_usd": 0, "count": 1},
        {"hour": end, "symbol": "BTCUSDT", "long_usd": 999e6, "short_usd": 0, "count": 9},
        {"hour": end - 3600 * 30, "symbol": "ALL", "long_usd": 999e6, "short_usd": 0, "count": 1},
    ]
    p.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    out = sum_long_liq_usd(p, end_hour_inclusive=end, window_hours=24)
    assert out["long_usd_24h"] == 22e6
    assert out["hours_present"] == 3
    assert out["missing_history"] is False


def test_completed_hour_is_previous_bucket() -> None:
    # Mid-hour → last fully completed UTC hour bucket (not the in-progress one).
    mid = 1_699_999_200 + 30 * 60  # 30m into hour starting 1699999200
    assert completed_hour_start(mid) == 1_699_999_200 - 3600
