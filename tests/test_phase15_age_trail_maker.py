"""Phase 15 changes (2026-05-03):
  1. AGE_LIMIT cuts: 4h → 1.25h, 2h → 1h, 1.5h → 0.75h
  2. Trailing activation: 2.0% → 1.2%, distance 1.0% → 0.8%
  3. Trailing lock_fraction small/medium tier: 0.55 → 0.65
  4. MAKER_ONLY config + smart_executor postOnly path
"""
from __future__ import annotations

import inspect
from pathlib import Path

# ── 1. AGE cuts ────────────────────────────────────────────────────


def test_age_cuts_applied_to_risk_profile():
    from config import RISK
    # 2026-05-28: relaxed for 15m-1h candle entries
    assert RISK["max_position_age_hours"] == 4.0, \
        f"max_position_age_hours should be 4.0; got {RISK['max_position_age_hours']}"
    assert RISK["max_stale_hours"] == 3.0
    assert RISK["max_loss_age_hours"] == 1.5


def test_age_cuts_preserve_30_60min_sweet_spot():
    """The 30-60min profitable cell sits at 0.5-1.0h — must stay below
    max_position_age_hours so winners are not force-closed prematurely."""
    from config import RISK
    sweet_spot_upper_h = 1.0  # 60 min boundary
    assert RISK["max_position_age_hours"] > sweet_spot_upper_h, \
        "max_position_age_hours must exceed 60min, the sweet-spot upper bound"


# ── 2/3. Trailing tightening ────────────────────────────────────────


def test_trailing_activation_lowered():
    from config import RISK
    assert RISK["trailing_activation"] == 0.012, \
        f"trailing_activation should be 1.2%; got {RISK['trailing_activation']}"
    assert RISK["trailing_distance"] == 0.008


def test_lock_fraction_small_win_tier_at_065():
    """Small/medium-win tier must be 0.65 to lock faster under tighter age cap."""
    from core.trailing_stop_manager import TrailingStopManager
    assert TrailingStopManager._lock_fraction_default(0.01) == 0.65
    assert TrailingStopManager._lock_fraction_default(0.025) == 0.65
    assert TrailingStopManager._lock_fraction_default(0.04) == 0.65


def test_lock_fraction_high_tiers_preserved():
    """Don't change the 5-8% / >8% tiers — those are sample-poor."""
    from core.trailing_stop_manager import TrailingStopManager
    assert TrailingStopManager._lock_fraction_default(0.06) == 0.70
    assert TrailingStopManager._lock_fraction_default(0.10) == 0.80


# ── 4. MAKER_ONLY ────────────────────────────────────────────────


def test_maker_only_config_present_default_on():
    # 2026-05-29: default ON per owner directive for a live maker-fee soak,
    # after the executor<->order_manager skip/partial integration landed
    # (tests/test_maker_only_integration.py). Caveat: measures fill-rate/fees,
    # not profitability (known-negative). Revert with MAKER_ONLY_ENABLED=false.
    from config import MAKER_ONLY
    assert "enabled" in MAKER_ONLY
    assert "max_wait_sec" in MAKER_ONLY
    assert MAKER_ONLY["enabled"] is True, \
        "Default ON 2026-05-29 (owner directive, post-integration); MAKER_ONLY_ENABLED=false reverts"


def test_smart_executor_supports_maker_only():
    """Verify the executor source contains the maker-only branches."""
    from core import smart_executor
    src = inspect.getsource(smart_executor)
    assert "MAKER_ONLY" in src
    assert "postOnly" in src
    assert "skipped_maker_only" in src
    # 2026-06-11: the explicit timeInForce="PostOnly" assignment was
    # REMOVED — that string is Bybit v5 vocabulary; passed raw it breaks
    # Binance USD-M maker orders (ccxt wants GTX there and translates the
    # postOnly boolean itself per venue).
    assert 'params["timeInForce"] = "PostOnly"' not in src


def test_smart_executor_maker_only_branch_lives_before_market_fallback():
    """The maker-only skip must be checked BEFORE the unconditional market
    fallback — otherwise enabling MAKER_ONLY does nothing."""
    src = Path("core/smart_executor.py").read_text(encoding="utf-8")
    skip_idx = src.index("skipped_maker_only")
    fallback_idx = src.index("limit timeout, cancelled → market order")
    # The skip branch (maker_only) must come BEFORE the market-fallback log
    # in the same code block — i.e., the if-maker-only check guards the fallback
    assert skip_idx < fallback_idx, (
        "maker-only skip path must precede the market fallback log"
    )


def test_phase15_full_set_summary():
    """Sanity: every Phase 15 knob is present with the expected value."""
    from config import MAKER_ONLY, RISK
    expected = {
        "max_position_age_hours": 4.0,
        "max_stale_hours": 3.0,
        "max_loss_age_hours": 1.5,
        "trailing_activation": 0.012,
        "trailing_distance": 0.008,
    }
    for k, v in expected.items():
        assert RISK[k] == v, f"RISK[{k}] expected {v}, got {RISK[k]}"
    assert MAKER_ONLY["enabled"] is True  # default ON 2026-05-29 (owner directive, post-integration soak)
