"""Portfolio Expected-Shortfall soft-cap (2026-06-11).

EWMA (RiskMetrics) covariance over 1h returns -> parametric ES_97.5 of
the open book in USD -> SOFT size taper (floor 0.25, never blocks) when
the projected ES with the candidate exceeds budget_pct of equity.
Signed legs net (long A + short B offsets when correlated). Fail-OPEN
on any data gap. Supersedes the static notional-bucket corr taper
(re-armable via RISK["corr_group_taper_enabled"]).
"""
from __future__ import annotations

import math
from pathlib import Path

import pandas as pd
import pytest

BASE_MS = 1_700_000_000_000
HOUR_MS = 3_600_000


def _candles(n=250, scale=0.01, seed_phase=0):
    """Deterministic 1h candles with real variance (alternating returns)."""
    closes = [100.0]
    for i in range(1, n):
        r = scale if (i + seed_phase) % 2 == 0 else -scale
        closes.append(closes[-1] * (1.0 + r))
    return [[BASE_MS + i * HOUR_MS, c, c, c, c, 1.0]
            for i, c in enumerate(closes)]


class FakeExchange:
    """Serves the SAME candle path for every base -> perfect correlation."""

    def __init__(self, candles=None):
        self.candles = candles if candles is not None else _candles()

    def fetch_ohlcv(self, symbol, timeframe, limit=240, market_type="futures"):
        return self.candles


def _pr(equity_cfg=None):
    from core.portfolio_risk import PortfolioRisk
    cfg = {"enabled": True, "cache_ttl_sec": 0}
    cfg.update(equity_cfg or {})
    return PortfolioRisk({"fake": FakeExchange()}, cfg)


# ─── math ─────────────────────────────────────────────────────────────


def test_ewma_cov_weights_and_value():
    """Constant-magnitude returns -> EWMA variance == r^2 exactly
    (weights sum to 1)."""
    from core.portfolio_risk import ewma_cov
    rets = pd.DataFrame({"A": [0.01, -0.01] * 50})
    cov = ewma_cov(rets, 0.94)
    assert cov.shape == (1, 1)
    assert cov[0, 0] == pytest.approx(0.0001, rel=1e-9)


def test_es_multiplier_table_matches_closed_form():
    """phi(Phi^-1(q))/(1-q) for q=0.975: z=1.959964, phi(z)=0.0584..."""
    from core.portfolio_risk import ES_MULT
    z = 1.959963985
    phi = math.exp(-z * z / 2.0) / math.sqrt(2.0 * math.pi)
    assert ES_MULT[0.975] == pytest.approx(phi / 0.025, abs=2e-3)


def test_signed_netting_offsets():
    """Long A + short B on perfectly correlated series -> projected ES
    near zero -> no taper. Same-direction doubles the risk instead."""
    pr = _pr({"budget_pct": 1e-9, "floor": 0.25})  # absurdly tight budget
    open_pos = [{"symbol": "AAA/USDT:USDT", "side": "buy",
                 "entry_price": 1.0, "size": 1000.0}]
    hedged = pr.evaluate_candidate(open_pos, "BBB", "sell", 1000.0, 10_000)
    assert hedged.ok
    assert hedged.es_projected_usd == pytest.approx(0.0, abs=1e-6)
    assert hedged.factor == 1.0
    same = pr.evaluate_candidate(open_pos, "BBB", "buy", 1000.0, 10_000)
    assert same.ok
    assert same.es_projected_usd > 0
    assert same.factor == 0.25  # tight budget -> floor


def test_taper_floor_and_slack_budget():
    pr = _pr({"budget_pct": 0.005})
    open_pos = [{"symbol": "AAA/USDT:USDT", "side": "buy",
                 "entry_price": 1.0, "size": 1000.0}]
    # Huge equity -> budget dwarfs ES -> factor 1.0
    slack = pr.evaluate_candidate(open_pos, "BBB", "buy", 1000.0, 1e9)
    assert slack.factor == 1.0
    # Tiny equity -> floor engages, never below 0.25, never 0
    tight = pr.evaluate_candidate(open_pos, "BBB", "buy", 1000.0, 1.0)
    assert tight.factor == 0.25


def test_fail_open_no_data():
    """No exchanges -> no series -> factor 1.0, ok False (fail-open)."""
    from core.portfolio_risk import PortfolioRisk
    pr = PortfolioRisk({}, {"enabled": True})
    d = pr.evaluate_candidate([], "AAA", "buy", 100.0, 10_000)
    assert d.factor == 1.0
    assert d.ok is False
    assert "no_data" in d.reason


def test_empty_book_es_zero():
    pr = _pr()
    d = pr.evaluate_book([], 10_000)
    assert d.ok
    assert d.es_open_usd == 0.0
    assert d.reason == "empty_book"
    snap = pr.last_snapshot()
    assert snap is not None and snap["es_open_usd"] == 0.0


def test_es_scales_with_sqrt_horizon():
    pr1 = _pr({"horizon_hours": 1.0, "budget_pct": 0.005})
    pr4 = _pr({"horizon_hours": 4.0, "budget_pct": 0.005})
    open_pos = [{"symbol": "AAA/USDT:USDT", "side": "buy",
                 "entry_price": 1.0, "size": 1000.0}]
    e1 = pr1.evaluate_book(open_pos, 10_000).es_open_usd
    e4 = pr4.evaluate_book(open_pos, 10_000).es_open_usd
    assert e4 == pytest.approx(e1 * 2.0, rel=1e-6)


def test_position_objects_supported():
    """tracker.get_open() yields Position objects — attribute access path."""
    class P:
        symbol = "AAA/USDT:USDT"
        side = "sell"
        entry_price = 2.0
        size = 500.0
    pr = _pr()
    legs = pr._signed_legs([P()])
    assert legs == {"AAA": -1000.0}


# ─── source pins ──────────────────────────────────────────────────────


def test_es_hunk_in_sizing_chain_before_notional():
    src = Path("core/bot_engine.py").read_text(encoding="utf-8")
    i_regime = src.index("size_fraction *= _regime_size_mult")
    i_es = src.index("PORTFOLIO ES SOFT-CAP")
    i_notional = src.index("notional = mtype_bal * size_fraction")
    assert i_regime < i_es < i_notional


def test_corr_bucket_taper_flag_gated():
    """Static bucket taper superseded; re-armable via RISK flag."""
    src = Path("core/bot_engine.py").read_text(encoding="utf-8")
    assert 'RISK.get("corr_group_taper_enabled", False)' in src


def test_heartbeat_and_dashboard_surface_es():
    eng = Path("core/bot_engine.py").read_text(encoding="utf-8")
    assert '"portfolio_es"' in eng
    assert "_heartbeat_portfolio_es" in eng
    dash = Path("dashboard.py").read_text(encoding="utf-8")
    assert "Portfolio ES" in dash


def test_config_defaults():
    from config import ES_RISK
    assert ES_RISK.get("enabled") is True
    assert ES_RISK.get("q") == 0.975
    assert ES_RISK.get("budget_pct") == 0.005
    assert ES_RISK.get("floor") == 0.25
