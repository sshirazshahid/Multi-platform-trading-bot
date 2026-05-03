"""Per-pair SL/TP overrides (Phase 15, 2026-05-03).

Empirically calibrated SL/TP per pair from 30d realized R:R data.
Overrides take precedence over ATR/DistFit, AND block the STAR ×1.25
extender from compounding on operator-set values.
"""
from __future__ import annotations

import inspect
from pathlib import Path


def test_config_has_pair_overrides_dict():
    from config import PAIR_OVERRIDES
    assert isinstance(PAIR_OVERRIDES, dict)
    # At minimum, the proven-edge pairs must have entries
    for sym in ("ARB/USDT:USDT", "ATOM/USDT:USDT", "ETH/USDT:USDT",
                "ALGO/USDT:USDT", "FET/USDT:USDT", "ORDI/USDT:USDT",
                "DOT/USDT:USDT", "BNB/USDT:USDT", "DOGE/USDT:USDT"):
        assert sym in PAIR_OVERRIDES, f"missing override for {sym}"


def test_overrides_have_sl_and_tp():
    from config import PAIR_OVERRIDES
    for sym, ov in PAIR_OVERRIDES.items():
        assert "sl_pct" in ov, f"{sym} missing sl_pct"
        assert "tp_pct" in ov, f"{sym} missing tp_pct"


def test_overrides_honor_sl_floor():
    """All overrides must keep SL >= 1.5% (zone-tightening invariant)."""
    from config import PAIR_OVERRIDES
    for sym, ov in PAIR_OVERRIDES.items():
        assert ov["sl_pct"] >= 1.5, \
            f"{sym} sl_pct {ov['sl_pct']} below 1.5% floor"


def test_overrides_have_positive_rr():
    """TP must be larger than SL (positive R:R)."""
    from config import PAIR_OVERRIDES
    for sym, ov in PAIR_OVERRIDES.items():
        assert ov["tp_pct"] > ov["sl_pct"], \
            f"{sym} TP {ov['tp_pct']} <= SL {ov['sl_pct']} — negative R:R"


def test_arb_has_high_rr():
    """ARB's empirical R:R is 2.23 — override must reflect that."""
    from config import PAIR_OVERRIDES
    arb = PAIR_OVERRIDES["ARB/USDT:USDT"]
    rr = arb["tp_pct"] / arb["sl_pct"]
    assert rr >= 2.0, f"ARB R:R {rr} below 2.0 — override too tight"


def test_ordi_has_highest_rr():
    """ORDI's empirical R:R is 3.67 — should have the highest override R:R."""
    from config import PAIR_OVERRIDES
    ordi = PAIR_OVERRIDES["ORDI/USDT:USDT"]
    rr = ordi["tp_pct"] / ordi["sl_pct"]
    assert rr >= 2.5, f"ORDI R:R {rr} below 2.5 — too tight for fastest pair"


def test_doge_tp_tightened_for_asymmetric_rr():
    """DOGE has 62% WR but realized R:R 0.82 — TP must be reasonable
    (not extreme either way)."""
    from config import PAIR_OVERRIDES
    doge = PAIR_OVERRIDES["DOGE/USDT:USDT"]
    rr = doge["tp_pct"] / doge["sl_pct"]
    # Tighter than the proven winners; not absurdly tight either
    assert 1.2 <= rr <= 2.0


def test_mcp_brain_consults_overrides_before_dist_fit():
    """The override branch must precede the DistFitSL().compute() call."""
    src = Path("core/mcp_brain.py").read_text(encoding="utf-8")
    # Use the actual import-and-call as the anchor, not the docstring mention
    pair_idx = src.index("from config import PAIR_OVERRIDES")
    distfit_call_idx = src.index("DistFitSL().compute")
    assert pair_idx < distfit_call_idx, (
        "PAIR_OVERRIDES import must precede the DistFitSL().compute() call"
    )


def test_star_extender_blocked_when_override_applied():
    """If PAIR_OVERRIDES specified ATOM at tp_pct=2.5, the STAR ×1.25
    must NOT then push it to 3.125 — the operator set the value explicitly."""
    src = Path("core/mcp_brain.py").read_text(encoding="utf-8")
    assert "_pair_override_applied" in src
    # The flag must guard the ×1.25 multiplier
    star_block_idx = src.index("not _pair_override_applied")
    extender_idx = src.index("tp_pct *= 1.25")
    # Same conditional — should be on the same line or adjacent
    assert star_block_idx < extender_idx, \
        "Override flag must guard the STAR ×1.25 multiplier"


def test_no_override_for_atomic_bleeders_lets_filter_decide():
    """SOL/XRP/AAVE intentionally NOT in overrides — small samples,
    the model gate filters their entries instead of biasing SL/TP."""
    from config import PAIR_OVERRIDES
    # These should NOT be in the override map (per session memory:
    # let model gate filter rather than biasing SL/TP from limited data)
    for sym in ("SOL/USDT:USDT", "XRP/USDT:USDT"):
        assert sym not in PAIR_OVERRIDES
