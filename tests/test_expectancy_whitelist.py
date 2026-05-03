"""EXPECTANCY_FILTER.whitelist bypass.

Per user directive 2026-05-03: DOGE whitelisted because 30d mean -$0.013
(62% WR, asymmetric R:R) was blocking OPEN signals. Whitelist symbols
bypass the expectancy floor entirely. MODEL_GATE, per-trade SL, and
Spec §12 streak halt still apply.
"""
from __future__ import annotations

from pathlib import Path


def test_doge_is_in_whitelist():
    """Without this, the user's directive isn't actually applied."""
    from config import EXPECTANCY_FILTER
    wl = EXPECTANCY_FILTER.get("whitelist", set())
    assert "DOGE/USDT:USDT" in wl
    assert "DOGE/USDT" in wl


def test_whitelist_check_lives_before_floor_comparison():
    """The bypass branch in bot_engine must short-circuit BEFORE
    `_gw().recent_expectancy()` is called and BEFORE the floor compare."""
    src = Path("core/bot_engine.py").read_text(encoding="utf-8")
    # Whitelist short-circuit and the floor check must both exist
    assert "WHITELISTED" in src
    assert "bypass floor" in src
    assert "_EF.get(\"whitelist\")" in src or "_EF.get('whitelist')" in src
    # And the bypass must precede recent_expectancy in source order
    bypass_idx = src.index("WHITELISTED")
    floor_idx = src.index("recent mean")
    assert bypass_idx < floor_idx, (
        "Whitelist log must appear before the BLOCKED log in source order — "
        "otherwise the bypass is wired after the comparison"
    )


def test_whitelist_does_not_break_non_whitelisted_symbols():
    """A symbol not on the whitelist should still go through normal flow."""
    from config import EXPECTANCY_FILTER
    wl = EXPECTANCY_FILTER.get("whitelist", set())
    # BTC, ETH, ATOM, ARB are NOT whitelisted (only DOGE)
    assert "BTC/USDT:USDT" not in wl
    assert "ETH/USDT:USDT" not in wl
    assert "ATOM/USDT:USDT" not in wl
    assert "ARB/USDT:USDT" not in wl


def test_whitelist_is_a_set_for_O1_lookup():
    """Membership check is on the entry hot path — must be O(1)."""
    from config import EXPECTANCY_FILTER
    wl = EXPECTANCY_FILTER.get("whitelist", set())
    assert isinstance(wl, (set, frozenset)), \
        "whitelist must be a set/frozenset for O(1) lookup"
