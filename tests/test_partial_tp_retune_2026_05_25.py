"""Partial-TP retune pin (2026-05-25) — the keystone realized-R fix.

5-agent forensic swarm traced realized R:R 0.63 (vs configured 1.2) to
PARTIAL_TP booking 60% of every winner at first_take_at_pct (0.42 R) then
breakevening the rest. first_take_size lowered 0.6 → 0.3 so 70% of each
winner rides to full TP. Memory: no-edge-forensics-2026-05-25.
"""
from __future__ import annotations

from dataclasses import dataclass


def test_partial_tp_first_take_size_is_30pct():
    """Pin the retuned value — guards against silent revert to 0.6."""
    import config
    assert config.PARTIAL_TP["first_take_size"] == 0.3, (
        f"first_take_size is {config.PARTIAL_TP['first_take_size']}; the "
        f"2026-05-25 retune set it to 0.3 to stop clipping winners. "
        f"Reverting to 0.6 re-introduces the realized-0.63-R bleed."
    )
    # at_pct preserved so early-win capture in the 30-60min cell remains.
    assert config.PARTIAL_TP["first_take_at_pct"] == 0.35
    assert config.PARTIAL_TP["enabled"] is True
    assert config.PARTIAL_TP["move_sl_to_breakeven"] is True


@dataclass
class _StubPos:
    side: str = "buy"
    entry_price: float = 100.0
    take_profit: float = 101.8   # +1.8% (SCALP TP)
    partial_taken: bool = False


def test_partial_tp_books_smaller_chunk_now():
    """_should_fire_partial_tp must return take_size 0.3 (was 0.6) when
    price reaches the 35%-of-TP partial level."""
    from core.order_manager import _should_fire_partial_tp
    import config

    pos = _StubPos()
    # partial level = entry + (tp-entry)*0.35 = 100 + 1.8*0.35 = 100.63
    fire, take_sz, level = _should_fire_partial_tp(pos, 100.63, config.PARTIAL_TP)
    assert fire is True
    assert take_sz == 0.3, f"expected 0.3 book, got {take_sz}"
    assert abs(level - 100.63) < 1e-6


def test_blended_r_improves_at_data_implied_continuation():
    """Math contract, grounded in the OBSERVED realized R rather than an
    assumed continuation probability.

    The forensics agent measured realized R ≈ 0.63 under the OLD split
    (book=0.6 at 0.42R, rider=0.4 to 1.2R). Back-solve the implied
    continuation probability p from that:
        0.6*0.42 + 0.4*1.2*p = 0.63  →  p ≈ 0.79
    At that data-implied p, lowering the early book to 0.3 lifts blended
    winner-R from ~0.63 toward ~0.79.

    IMPORTANT honesty guard: the fix only helps when p > 0.35 (the
    crossover). The data puts us well above it (p≈0.79), so the change is
    justified — but this test pins BOTH facts so a future regime shift
    that drops continuation below 0.35 would surface here.

    Even at 0.79 R, winners (0.79R) are still smaller than losers (1.0R at
    full SL), so the bot still bleeds — just slower. Break-even needs
    winner-R ≈ 1.18 at the 45.8% WR. This fix is necessary, not sufficient.
    """
    sl_pct, tp_pct, at_pct = 1.5, 1.8, 0.35
    partial_r = (tp_pct * at_pct) / sl_pct   # 0.42
    full_r = tp_pct / sl_pct                  # 1.2

    def blended(book_frac, p):
        return book_frac * partial_r + (1.0 - book_frac) * (full_r * p)

    # Back-solve continuation p from observed 0.63 R at the old split.
    # 0.252 + 0.48p = 0.63  →  p = 0.7875
    p_implied = (0.63 - 0.6 * partial_r) / (0.4 * full_r)
    assert 0.75 < p_implied < 0.82, f"implied continuation {p_implied:.3f}"

    old = blended(0.6, p_implied)
    new = blended(0.3, p_implied)
    assert abs(old - 0.63) < 0.01, "old split should reproduce observed 0.63 R"
    assert new > old, f"new {new:.3f} must beat old {old:.3f} at data p"
    assert new - old > 0.10, "lift at data-implied p should be material (~0.16 R)"

    # Crossover honesty guard: below p=0.35 the fix would HURT.
    crossover_p = (0.6 - 0.3) * partial_r / ((0.7 - 0.4) * full_r)
    assert abs(crossover_p - 0.35) < 0.01
    assert blended(0.3, 0.30) < blended(0.6, 0.30)  # below crossover → worse
    assert p_implied > crossover_p  # but data sits safely above it
