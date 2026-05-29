"""Phase 15.3 — Layers A/B/C/D (2026-05-03).

A: Time-decay TP / pre-AGE_LIMIT trailing-activation lowering
B: Per-side PAIR_OVERRIDES (long/short asymmetric)
C: Volatility-adaptive SL multiplier (ATR-magnitude-based)
D: Confidence-scaled SL multiplier (score-based)
"""
from __future__ import annotations

import time
from unittest.mock import MagicMock

# ─── Layer A — pre-AGE_LIMIT trailing activation ────────────────


def _mk_pos(open_minutes_ago: float, side: str = "buy"):
    p = MagicMock()
    p.open_time = time.time() - open_minutes_ago * 60
    p.atr_pct = 0
    p.symbol = "BTC/USDT:USDT"
    p.id = "test1"
    p.side = side
    p.entry_price = 100.0
    p.stop_loss = 98.5
    p.take_profit = 103.0
    p.market_type = "futures"
    return p


def test_age_aware_activation_under_50min_uses_base():
    from core.trailing_stop_manager import TrailingStopManager
    mgr = TrailingStopManager()
    pos = _mk_pos(open_minutes_ago=10)
    base = mgr._adaptive_activation(pos)
    # Should be near the configured RISK trailing_activation (default Phase 15: 1.2%)
    from config import RISK
    expected_base = RISK.get("trailing_activation", 0.012)
    assert abs(base - expected_base) < 1e-6


def test_age_aware_activation_50_to_65min_lowers_to_05pct_or_base():
    from core.trailing_stop_manager import TrailingStopManager
    mgr = TrailingStopManager()
    pos = _mk_pos(open_minutes_ago=55)
    act = mgr._adaptive_activation(pos)
    # Capped at min(base, 0.005) — so for Phase 15 base 0.012, drops to 0.005
    assert act <= 0.005


def test_age_aware_activation_over_65min_drops_to_near_zero():
    from core.trailing_stop_manager import TrailingStopManager
    mgr = TrailingStopManager()
    pos = _mk_pos(open_minutes_ago=70)
    act = mgr._adaptive_activation(pos)
    # Final 10 minutes — activate at any positive PnL
    assert act <= 0.001


def test_age_aware_activation_handles_missing_open_time():
    """Position without open_time falls back to base (no crash)."""
    from core.trailing_stop_manager import TrailingStopManager
    mgr = TrailingStopManager()
    pos = MagicMock(spec=[])  # no attributes — no open_time
    act = mgr._adaptive_activation(pos)
    assert act > 0  # returns base; shouldn't crash


# ─── Layer B — Per-side PAIR_OVERRIDES ──────────────────────────


def test_per_side_schema_recognized():
    from config import PAIR_OVERRIDES
    bnb = PAIR_OVERRIDES.get("BNB/USDT:USDT")
    assert isinstance(bnb, dict)
    assert "long" in bnb and "short" in bnb
    assert bnb["short"]["tp_pct"] < bnb["long"]["tp_pct"], \
        "Shorts should have tighter TP per attribution finding"


def test_doge_short_tp_tighter_than_long():
    from config import PAIR_OVERRIDES
    doge = PAIR_OVERRIDES["DOGE/USDT:USDT"]
    assert doge["long"]["tp_pct"] >= doge["short"]["tp_pct"]


def test_flat_schema_pairs_still_supported():
    """ARB/ATOM/ORDI/etc kept the flat {sl_pct, tp_pct} schema."""
    from config import PAIR_OVERRIDES
    arb = PAIR_OVERRIDES["ARB/USDT:USDT"]
    assert "sl_pct" in arb and "tp_pct" in arb
    # Should NOT have long/short keys
    assert "long" not in arb


def test_lookup_path_in_mcp_brain_handles_per_side():
    """Source must dispatch on side for per-side schema."""
    src = open("core/mcp_brain.py", encoding="utf-8").read()
    # The new branch unwraps long/short keys from the override dict
    assert '_ov_raw["long"]' in src or "_ov_raw['long']" in src
    assert '_ov_raw["short"]' in src or "_ov_raw['short']" in src
    # Backward-compat: flat schema still works
    assert '"sl_pct" in _ov_raw' in src


# ─── Layer C — Vol-adaptive SL multiplier ───────────────────────


def test_vol_adaptive_layer_present_in_mcp_brain():
    src = open("core/mcp_brain.py", encoding="utf-8").read()
    assert "Layer C" in src
    assert "low_vol" in src
    assert "high_vol" in src
    assert "_vol_mult" in src


def test_vol_adaptive_preserves_rr_ratio():
    """When SL is scaled by vol_mult, TP must scale too so R:R is preserved."""
    src = open("core/mcp_brain.py", encoding="utf-8").read()
    # The TP rescaling line must exist near vol_mult
    assert "tp_pct = tp_pct * (sl_pct / sl_pct_pre_vol)" in src


# ─── Layer D — Confidence-scaled SL ─────────────────────────────


def test_conf_scaled_layer_present_in_mcp_brain():
    src = open("core/mcp_brain.py", encoding="utf-8").read()
    assert "Layer D" in src
    assert "_conf_mult" in src


def test_conf_scaled_disabled_in_v4():
    """v4 (2026-05-27): confidence-scaled SL DISABLED. MCP score is
    anti-predictive (r=-0.285), so widening SL for score>=85 entries
    maximized loss. _conf_mult is now always 1.0."""
    src = open("core/mcp_brain.py", encoding="utf-8").read()
    assert "_conf_mult = 1.0" in src
    assert "DISABLED (v4)" in src


def test_conf_scaled_preserves_rr_ratio():
    src = open("core/mcp_brain.py", encoding="utf-8").read()
    assert "tp_pct = tp_pct * (sl_pct / sl_pct_pre_conf)" in src


# ─── Layer order invariants ─────────────────────────────────────


def test_layer_ordering_in_source():
    """Order must be: PAIR_OVERRIDES → DistFitSL fallback → Layer C → Layer D
    → SMC zone tightening → STAR extender."""
    src = open("core/mcp_brain.py", encoding="utf-8").read()
    pair_idx = src.index("from config import PAIR_OVERRIDES")
    layer_c_idx = src.index("Layer C: volatility-adaptive")
    layer_d_idx = src.index("Layer D: confidence-scaled")
    smc_idx = src.index("Smart Money SL/TP refinement")
    star_idx = src.index("STAR_SYMBOLS TP extender")
    assert pair_idx < layer_c_idx < layer_d_idx < smc_idx < star_idx


def test_sl_floor_15pct_invariant_preserved():
    """Every layer that touches sl_pct must respect the 1.5% floor."""
    src = open("core/mcp_brain.py", encoding="utf-8").read()
    # Each multiplier application must wrap in max(1.5, ...)
    assert src.count("max(1.5, sl_pct * _vol_mult)") == 1
    assert src.count("max(1.5, sl_pct * _conf_mult)") == 1
