"""Tests for TPProbeAgent — the geometric TP-accuracy probe (2026-07-05).

Pins the load-bearing property: TP distance = SL distance / 3.5, which is
what makes the theoretical TP-hit rate ≈ 77.8% (p = s/(s+t)) on any symbol —
inside the owner's 75-80% goal band. Also pins momentum-siding, the hourly
per-symbol cooldown, RiskAgent survival, and fail-closed ctx handling.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from core.agents.risk_agent import RiskAgent
from core.agents.tp_probe_agent import (
    SL_ATR_MULT,
    TP_FRACTION_OF_SL,
    TPProbeAgent,
)


def _frame(closes):
    closes = np.asarray(closes, dtype=float)
    return pd.DataFrame(
        {
            "open": closes,
            "high": closes * 1.004,
            "low": closes * 0.996,
            "close": closes,
            "volume": np.full(len(closes), 1000.0),
        }
    )


def _ctx(closes, symbol="SUI/USDT:USDT", venue="binance"):
    return {"symbol": symbol, "venue": venue, "ohlcv_15m": _frame(closes)}


UP = list(np.linspace(100.0, 110.0, 60))     # steady uptrend -> e9 > e21
DOWN = list(np.linspace(110.0, 100.0, 60))   # steady downtrend -> e9 < e21


def test_geometry_pins_75_80_pct_theoretical_hit_band():
    p = TPProbeAgent().propose(_ctx(UP))
    assert p is not None and p.side == "buy"
    s_dist = p.entry - p.sl
    t_dist = p.tp - p.entry
    assert s_dist > 0 < t_dist
    assert t_dist == pytest.approx(s_dist * TP_FRACTION_OF_SL, rel=1e-9)
    theoretical_hit = s_dist / (s_dist + t_dist)
    assert 0.75 <= theoretical_hit <= 0.80  # the owner's goal band, by geometry


def test_momentum_sided_short_with_mirrored_geometry():
    p = TPProbeAgent().propose(_ctx(DOWN))
    assert p is not None and p.side == "sell"
    s_dist = p.sl - p.entry
    t_dist = p.entry - p.tp
    assert s_dist > 0 < t_dist
    assert t_dist == pytest.approx(s_dist * TP_FRACTION_OF_SL, rel=1e-9)


def test_sl_distance_is_atr_anchored():
    agent = TPProbeAgent()
    p = agent.propose(_ctx(UP))
    from core.agents.indicators import atr

    expected = SL_ATR_MULT * float(atr(_frame(UP), 14).iloc[-1])
    assert (p.entry - p.sl) == pytest.approx(expected, rel=1e-9)


def test_cooldown_one_probe_per_symbol_per_hour():
    agent = TPProbeAgent()
    assert agent.propose(_ctx(UP)) is not None
    assert agent.propose(_ctx(UP)) is None  # same symbol, within cooldown
    assert agent.propose(_ctx(UP, symbol="DOGE/USDT:USDT")) is not None


def test_survives_risk_agent_review(tmp_path):
    p = TPProbeAgent().propose(_ctx(UP))
    verdict = RiskAgent(risk_state_path=tmp_path / "risk_state.json").review(p)
    assert bool(verdict) is True, verdict.reason


def test_fail_closed_on_missing_or_short_ctx():
    agent = TPProbeAgent()
    assert agent.propose({"symbol": "X/USDT:USDT"}) is None
    assert agent.propose(_ctx(UP[:10])) is None
    assert agent.propose({"ohlcv_15m": _frame(UP)}) is None  # no symbol


def test_proposal_carries_resolver_fields():
    p = TPProbeAgent().propose(_ctx(UP, venue="bybit"))
    assert p.venue == "bybit"
    assert p.timeframe == "15m"
    assert p.horizon_bars == 32
