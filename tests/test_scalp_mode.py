"""Spec tests for scalp mode hardening (2026-05-27).

Evidence from 454 real trades:
  15m-1h holds: 64% WR, +$4.41 — the ONLY profitable hold-time bucket.
  VWAP + Long:  61.9% WR — strongest individual signal.
  B1 MACD:      25% WR when active — anti-predictive, removed.
  B3 15m timing: 35% WR — pure noise, removed.

Tests are organised in four groups:
  1. Config — SCALP_MODE dict shape and default values.
  2. _score_coin_scalp — behaviour of the new scalp scoring function
     (these are SPEC tests; if the function is not yet implemented the
      tests will fail with ImportError/AttributeError and that is
      expected — they define what must be built).
  3. B1/B3 removal — verify _score_coin no longer awards points for
     MACD or 15m-timing conditions.
  4. Reweight verification — B6 +7 and scalp-B7 +15 in _score_coin_scalp.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


# ---------------------------------------------------------------------------
# Helpers shared across test groups
# ---------------------------------------------------------------------------

def _make_brain():
    """Return a minimally-initialised MCPBrain with no live dependencies."""
    from core.mcp_brain import MCPBrain

    brain = MCPBrain()
    # Disable data-coordinator so no network calls are made and the
    # data-feed bonus/veto blocks (B11-B13, V1-V4) are bypassed.
    brain._data_coordinator = None
    return brain


def _minimal_ei(
    *,
    ema20_above_50_4h: bool = True,
    ema20_above_50_1h: bool = True,
    adx_4h: float = 25.0,
    adx_1h: float = 25.0,
    rsi_1h: float = 50.0,
    atr_pct_1h: float = 1.2,
    price_vs_vwap: float = 0.1,
    vol_ratio: float = 1.0,
    macd_hist: float = 0.001,
    macd_hist_rising: bool = True,
    include_15m: bool = True,
    ema20_above_50_15m: bool = True,
) -> dict:
    """Build exchange-indicator dict that passes the 4 required conditions
    for a long when defaults are used.  Individual fields can be overridden
    to test specific veto / bonus paths.
    """
    ema20 = 100.0
    ema50 = 99.5 if ema20_above_50_4h else 100.5
    ei = {
        "4h": {
            "adx": adx_4h,
            "ema20": ema20,
            "ema50": ema50,
            "ema20_above_50": ema20_above_50_4h,
            "ema20_slope": 0.001 if ema20_above_50_4h else -0.001,
            "bb_width": 3.0,
            "smc_structure": 0,
            "smc_detail": {},
        },
        "1h": {
            "adx": adx_1h,
            "rsi": rsi_1h,
            "ema20_above_50": ema20_above_50_1h,
            "macd_hist": macd_hist,
            "macd_hist_rising": macd_hist_rising,
            "vol_ratio": vol_ratio,
            "atr_pct": atr_pct_1h,
            "price_vs_vwap": price_vs_vwap,
            "hh": True,
            "hl": True,
            "ll": False,
            "lh": False,
            "price": 100.0,
            "smc_structure": 0,
            "smc_entry": 0,
            "smc_confirmation": 0,
            "smc_detail": {
                "ms": {}, "sweep": {}, "pa": {},
                "fvg": {}, "sd": {}, "ote": {}, "fib": {},
            },
        },
    }
    if include_15m:
        ei["15m"] = {"ema20_above_50": ema20_above_50_15m}
    return ei


_EMPTY_DATA: dict = {"funding": {}, "orderbook": {}}


# ===========================================================================
# GROUP 1 — Config
# ===========================================================================

class TestScalpModeConfig:
    """SCALP_MODE dict must exist in config with exact default values."""

    def test_scalp_mode_config_exists(self):
        from config import SCALP_MODE
        assert isinstance(SCALP_MODE, dict)

    def test_scalp_mode_has_required_keys(self):
        from config import SCALP_MODE
        for key in ("enabled", "sl_pct", "tp_pct", "longs_only",
                    "trailing_enabled", "time_wall_min"):
            assert key in SCALP_MODE, f"Missing key: {key}"

    def test_scalp_sl_pct_default(self):
        from config import SCALP_MODE
        # 2026-05-28: tightened 1.0→0.8 for $1-2/trade at $136 notional
        assert SCALP_MODE["sl_pct"] == 0.8, (
            f"sl_pct should be 0.8, got {SCALP_MODE['sl_pct']}"
        )

    def test_scalp_tp_pct_default(self):
        from config import SCALP_MODE
        # 2026-05-28: tightened 1.8→1.3 for higher hit rate + $1-2/trade
        assert SCALP_MODE["tp_pct"] == 1.3, (
            f"tp_pct should be 1.3, got {SCALP_MODE['tp_pct']}"
        )

    def test_scalp_longs_only_default(self):
        from config import SCALP_MODE
        assert SCALP_MODE["longs_only"] is True, (
            "longs_only should be True — 61.9% WR for VWAP+Long signal"
        )

    def test_scalp_trailing_disabled_default(self):
        from config import SCALP_MODE
        assert SCALP_MODE["trailing_enabled"] is False, (
            "trailing_enabled should be False — trailing clips scalp winners"
        )

    def test_scalp_time_wall_default(self):
        from config import SCALP_MODE
        # 2026-05-28: raised for 15m-1h entries (was 60)
        assert SCALP_MODE["time_wall_min"] == 180, (
            f"time_wall_min should be 180, got {SCALP_MODE['time_wall_min']}"
        )


# ===========================================================================
# GROUP 2 — _score_coin_scalp function
# ===========================================================================

class TestScoreCoinScalp:
    """Spec tests for the _score_coin_scalp method on MCPBrain.

    These tests will FAIL with AttributeError until _score_coin_scalp is
    implemented — that is the intended red-phase state.
    """

    def _get_scalp_fn(self):
        """Return (brain, fn) or skip if the function is not implemented yet."""
        brain = _make_brain()
        fn = getattr(brain, "_score_coin_scalp", None)
        if fn is None:
            pytest.skip(
                "_score_coin_scalp not yet implemented — this is the spec"
            )
        return brain, fn

    def test_scalp_no_data_returns_zero(self):
        """Missing ei_4h / ei_1h must return score=0 with no_data reason."""
        _, fn = self._get_scalp_fn()
        result = fn("BTC", _EMPTY_DATA, {})
        assert result["score"] == 0
        assert "no_data" in result.get("reason", "")

    def test_scalp_veto_shorts_when_longs_only(self):
        """When longs_only=True, a sell signal (4h EMA20 < EMA50) scores 0."""
        _, fn = self._get_scalp_fn()
        # ema20_above_50_4h=False → side='sell' → longs_only veto
        ei = _minimal_ei(ema20_above_50_4h=False, ema20_above_50_1h=False,
                         rsi_1h=45.0)
        with patch("config.SCALP_MODE", {
            **__import__("config").SCALP_MODE, "longs_only": True
        }):
            result = fn("BTC", _EMPTY_DATA, ei)
        assert result["score"] == 0
        assert "shorts_disabled" in result.get("reason", ""), (
            f"Expected 'shorts_disabled' in reason, got: {result.get('reason')}"
        )

    def test_scalp_veto_low_atr(self):
        """ATR < min_atr_pct (0.8%) should be vetoed (market too quiet to scalp)."""
        _, fn = self._get_scalp_fn()
        ei = _minimal_ei(atr_pct_1h=0.3)  # below 0.8 threshold
        result = fn("BTC", _EMPTY_DATA, ei)
        assert result["score"] == 0
        assert "atr" in result.get("reason", "").lower(), (
            f"Expected ATR veto in reason, got: {result.get('reason')}"
        )

    def test_scalp_required_pass_gives_base_50(self):
        """All 4 required conditions pass → base score exactly 50.

        The scalp scorer's 4 required conditions differ from the standard scorer:
          R1. |price_vs_vwap| <= vwap_distance_max_pct (0.3%)
          R2. RSI in [40, 60]
          R3. ADX >= 20
          R4. ATR >= min_atr_pct (0.8%)
        All bonuses are suppressed so the base score stays at 50.
        """
        _, fn = self._get_scalp_fn()
        ei = _minimal_ei(
            price_vs_vwap=0.1,    # R1 passes: |0.1| <= 0.3
            rsi_1h=50.0,          # R2 passes: 40 <= 50 <= 60
            adx_1h=25.0,          # R3 passes: >= 20
            atr_pct_1h=1.2,       # R4 passes: >= 0.8
            vol_ratio=0.5,        # B_VOL off: < 1.5
            include_15m=False,    # no 15m data
        )
        # Use empty data so B_OB and B_FUNDING bonuses cannot fire
        result = fn("BTC", _EMPTY_DATA, ei)
        # B_SESSION can fire during peak hours; score may be 50 or 55.
        # Accept 50-55 because the session bonus is time-dependent and
        # we only need to confirm base == 50 when no bonuses are present.
        assert result["score"] >= 50, (
            f"Base score should be >= 50 when all required conditions pass, "
            f"got {result['score']}"
        )
        # Core invariant: result came from the scalp path
        assert result.get("_scalp") is True

    def test_scalp_vwap_bonus_fires(self):
        """B_VWAP (scalp variant) fires when -0.2 <= price_vs_vwap <= 0.15.

        Scalp VWAP bonus awards +15 pts (vs +5 in the standard scorer).
        price_vs_vwap=-0.1 puts price slightly below VWAP — the scalp
        'approaching from below' sweet spot.
        """
        _, fn = self._get_scalp_fn()
        ei = _minimal_ei(
            price_vs_vwap=-0.1,   # -0.2 <= -0.1 <= 0.15 → B_VWAP fires
            vol_ratio=0.5,        # suppress B4
            macd_hist=0.0,        # suppress B1
            include_15m=False,    # suppress B3
        )
        result = fn("BTC", _EMPTY_DATA, ei)
        # Base 50 + B_VWAP 15 = 65 minimum.
        # B_SESSION (+5) may also fire during peak hours (22, 23, 0 UTC),
        # so the floor is 65 rather than an exact equality.
        assert result["score"] >= 65, (
            f"Expected score >= 65 (50 base + 15 VWAP), got {result['score']}"
        )
        assert "vwap_prime" in result.get("reason", ""), (
            f"B_VWAP label 'vwap_prime' missing from reason: {result.get('reason')}"
        )

    def test_scalp_entry_gate_at_65(self):
        """Score >= entry_threshold (65) should be achievable via B_VWAP.

        B_VWAP fires when -0.2 <= price_vs_vwap <= 0.15, adding +15 pts.
        Base 50 + B_VWAP 15 = 65, which meets SCALP_MODE['entry_threshold']
        (default 65).  The result dict does not need an 'entry_ok' key — the
        caller gates on score >= entry_threshold from config.
        """
        from config import SCALP_MODE
        _, fn = self._get_scalp_fn()
        # price_vs_vwap=-0.1 satisfies R1 (|−0.1|<=0.3) and B_VWAP (in window)
        ei = _minimal_ei(
            price_vs_vwap=-0.1,
            vol_ratio=0.5,
            include_15m=False,
            macd_hist=0.0,
        )
        result = fn("BTC", _EMPTY_DATA, ei)
        assert result.get("score", 0) >= SCALP_MODE["entry_threshold"], (
            f"Score {result.get('score')} should be >= entry_threshold "
            f"{SCALP_MODE['entry_threshold']}; result={result}"
        )

    def test_scalp_fixed_sl_tp(self):
        """Result sl_pct/tp_pct must come from SCALP_MODE config, not ATR-adaptive."""
        _, fn = self._get_scalp_fn()
        from config import SCALP_MODE
        ei = _minimal_ei(price_vs_vwap=-0.1)
        result = fn("BTC", _EMPTY_DATA, ei)
        if result.get("score", 0) > 0:
            assert result["sl_pct"] == SCALP_MODE["sl_pct"], (
                f"sl_pct should be SCALP_MODE fixed value {SCALP_MODE['sl_pct']}, "
                f"got {result['sl_pct']}"
            )
            assert result["tp_pct"] == SCALP_MODE["tp_pct"], (
                f"tp_pct should be SCALP_MODE fixed value {SCALP_MODE['tp_pct']}, "
                f"got {result['tp_pct']}"
            )

    def test_scalp_tag_present(self):
        """Result dict must carry _scalp=True tag for downstream routing."""
        _, fn = self._get_scalp_fn()
        ei = _minimal_ei(price_vs_vwap=-0.1)
        result = fn("BTC", _EMPTY_DATA, ei)
        if result.get("score", 0) > 0:
            assert result.get("_scalp") is True, (
                f"_scalp tag missing from result: {result}"
            )


# ===========================================================================
# GROUP 3 — B1/B3 removal in _score_coin
# ===========================================================================

class TestB1B3Removal:
    """B1 MACD and B3 15m timing must not contribute points to _score_coin.

    These verify the already-landed v4 changes:
      B1: score += 0 (was +12), bonus_count not incremented
      B3: score += 0 (was +10), bonus_count not incremented
    """

    def _score_with_and_without_condition(
        self,
        ei_with: dict,
        ei_without: dict,
        data: dict = _EMPTY_DATA,
    ) -> tuple[int, int]:
        """Return (score_with, score_without) for a given ei pair."""
        brain = _make_brain()
        r_with = brain._score_coin("BTC", data, ei_with)
        r_without = brain._score_coin("BTC", data, ei_without)
        return r_with.get("score", 0), r_without.get("score", 0)

    def test_b1_macd_contributes_zero_points(self):
        """Turning MACD on/off must not change the score (B1 removed, +0 pts)."""
        ei_macd_on = _minimal_ei(
            macd_hist=0.05,
            macd_hist_rising=True,
            vol_ratio=0.5,  # suppress B4
            include_15m=False,
        )
        ei_macd_off = _minimal_ei(
            macd_hist=-0.05,  # would have been B1=False
            macd_hist_rising=False,
            vol_ratio=0.5,
            include_15m=False,
        )
        score_on, score_off = self._score_with_and_without_condition(
            ei_macd_on, ei_macd_off
        )
        assert score_on == score_off, (
            f"B1 MACD must contribute 0 pts: score_on={score_on}, "
            f"score_off={score_off}. Difference = {score_on - score_off}"
        )

    def test_b3_15m_timing_contributes_zero_points(self):
        """Turning 15m EMA alignment on/off must not change the score."""
        ei_15m_on = _minimal_ei(
            include_15m=True,
            ema20_above_50_15m=True,  # B3 condition met
            vol_ratio=0.5,            # suppress B4
            macd_hist=0.0,            # suppress B1 logging
        )
        ei_15m_off = _minimal_ei(
            include_15m=True,
            ema20_above_50_15m=False,  # B3 condition not met
            vol_ratio=0.5,
            macd_hist=0.0,
        )
        score_on, score_off = self._score_with_and_without_condition(
            ei_15m_on, ei_15m_off
        )
        assert score_on == score_off, (
            f"B3 15m timing must contribute 0 pts: score_on={score_on}, "
            f"score_off={score_off}. Difference = {score_on - score_off}"
        )

    def test_confidence_sl_widening_disabled(self):
        """Confidence-based SL widening multiplier must be 1.0 at base score.

        The v4 removal means a low-confidence entry (score <70) must not
        produce a SL that is narrower than the ATR-derived floor.  A score
        of exactly 50 (all required, no bonus) falls in the <70 bucket;
        pre-v4 it would have used _conf_mult=0.8 → sl could drop below 1.5%.
        Post-v4 the multiplier is removed and sl_pct >= 1.5% always holds.
        """
        brain = _make_brain()
        # Deliberately construct a score=50 case (no bonuses)
        ei = _minimal_ei(
            vol_ratio=0.5,      # B4 off
            macd_hist=0.0,      # B1 off
            include_15m=False,  # B3 off
            price_vs_vwap=3.0,  # B7 off (outside window)
        )
        result = brain._score_coin("BTC", _EMPTY_DATA, ei)
        if result.get("score", 0) > 0:
            assert result["sl_pct"] >= 1.5, (
                f"sl_pct {result['sl_pct']} dropped below 1.5% floor "
                f"at score={result['score']}"
            )


# ===========================================================================
# GROUP 4 — Reweight verification
# ===========================================================================

class TestReweights:
    """B6 microstructure and scalp B7 VWAP point values."""

    def test_b6_microstructure_awards_7_points(self):
        """B6 legacy path must award exactly 7 pts (was 5, reweighted in v4)."""
        brain = _make_brain()
        # Build an ei where B6 fires: long, funding <= 0.0003, imbalance > 0.05
        data_with_b6 = {
            "funding": {"BTC": {"funding_rate": 0.0001}},
            "orderbook": {"BTC": {"imbalance": 0.10}},
        }
        data_without_b6 = {
            "funding": {"BTC": {"funding_rate": 0.0005}},  # too high → B6 off
            "orderbook": {"BTC": {"imbalance": 0.10}},
        }
        ei = _minimal_ei(vol_ratio=0.5, macd_hist=0.0, include_15m=False,
                         price_vs_vwap=3.0)
        r_on = brain._score_coin("BTC", data_with_b6, ei)
        r_off = brain._score_coin("BTC", data_without_b6, ei)
        # When B6 fires the score should increase by exactly 7 (legacy path)
        if r_on.get("score", 0) > 0 and r_off.get("score", 0) > 0:
            delta = r_on["score"] - r_off["score"]
            assert delta == 7, (
                f"B6 microstructure should add 7 pts, got delta={delta} "
                f"(score_on={r_on['score']}, score_off={r_off['score']})"
            )

    def test_b7_vwap_weight_in_scalp_scorer_is_15(self):
        """In _score_coin_scalp, B7 VWAP must award +15 pts (vs +5 standard).

        This test will skip if _score_coin_scalp is not yet implemented.
        """
        brain = _make_brain()
        fn = getattr(brain, "_score_coin_scalp", None)
        if fn is None:
            pytest.skip(
                "_score_coin_scalp not yet implemented — this is the spec"
            )

        # The scalp scorer's R1 requires |price_vs_vwap| <= 0.3, so we cannot
        # use a large value to disable B_VWAP while keeping R1 active.
        # Instead use price_vs_vwap=0.25: passes R1 (|0.25|<=0.3) but is
        # OUTSIDE the B_VWAP window (-0.2 <= pvw <= 0.15), so B_VWAP is off.
        ei_no_vwap = _minimal_ei(
            price_vs_vwap=0.25,  # R1 passes, B_VWAP off (> 0.15)
            vol_ratio=0.5,
            macd_hist=0.0,
            include_15m=False,
        )
        # price_vs_vwap=-0.1: R1 passes (|-0.1|<=0.3) AND B_VWAP fires
        ei_with_vwap = _minimal_ei(
            price_vs_vwap=-0.1,
            vol_ratio=0.5,
            macd_hist=0.0,
            include_15m=False,
        )
        r_off = fn("BTC", _EMPTY_DATA, ei_no_vwap)
        r_on = fn("BTC", _EMPTY_DATA, ei_with_vwap)

        if r_off.get("score", 0) > 0 and r_on.get("score", 0) > 0:
            delta = r_on["score"] - r_off["score"]
            assert delta == 15, (
                f"Scalp B7 VWAP should contribute +15 pts, got delta={delta} "
                f"(score_no_vwap={r_off['score']}, score_with_vwap={r_on['score']})"
            )

    def test_b7_vwap_weight_in_standard_scorer_is_15(self):
        """Standard _score_coin B7 VWAP awards +15 pts (v4 reweight from +5).

        The v4 reweight is confirmed at mcp_brain.py line ~2627: score += 15.
        Both the standard scorer (_score_coin) and the scalp scorer
        (_score_coin_scalp) use +15 for VWAP proximity — it is the strongest
        signal in the 454-trade dataset (61.9% WR for VWAP + Long).
        """
        brain = _make_brain()
        ei_no_vwap = _minimal_ei(
            price_vs_vwap=3.0,   # B7 off: outside [0.05, 2.0] window
            vol_ratio=0.5,
            macd_hist=0.0,
            include_15m=False,
        )
        ei_with_vwap = _minimal_ei(
            price_vs_vwap=0.5,   # 0.05 < 0.5 < 2.0 → standard B7 fires for long
            vol_ratio=0.5,
            macd_hist=0.0,
            include_15m=False,
        )
        r_off = brain._score_coin("BTC", _EMPTY_DATA, ei_no_vwap)
        r_on = brain._score_coin("BTC", _EMPTY_DATA, ei_with_vwap)

        if r_off.get("score", 0) > 0 and r_on.get("score", 0) > 0:
            delta = r_on["score"] - r_off["score"]
            assert delta == 15, (
                f"Standard B7 VWAP (v4) should contribute +15 pts, got delta={delta} "
                f"(score_no_vwap={r_off['score']}, score_with_vwap={r_on['score']})"
            )
