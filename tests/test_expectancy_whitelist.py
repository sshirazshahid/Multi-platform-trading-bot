"""EXPECTANCY_FILTER.whitelist bypass.

Per user directive 2026-05-03: DOGE whitelisted because 30d mean -$0.013
(62% WR, asymmetric R:R) was blocking OPEN signals. Whitelist symbols
bypass the expectancy floor entirely. MODEL_GATE, per-trade SL, and
Spec §12 streak halt still apply.
"""
from __future__ import annotations

from tests.bot_engine_source import bot_engine_source_for_grep

from pathlib import Path


def test_whitelist_emptied_phase33():
    """Phase 33 (2026-05-05) emptied the operator whitelist per UNBLOCK_ALL.
    DOGE no longer special — Phase 27 graduated EV evaluates it on the
    same data terms as everything else. This test was originally
    'test_doge_is_in_whitelist' pinning DOGE bypass."""
    from config import EXPECTANCY_FILTER
    wl = EXPECTANCY_FILTER.get("whitelist") or {}
    assert len(wl) == 0
    assert "DOGE/USDT:USDT" not in wl


def test_whitelist_check_lives_before_floor_comparison():
    """Phase 27 (2026-05-05): the whitelist bypass is now inside
    `_ev_per_symbol_multiplier`. When a symbol is in the whitelist,
    the helper returns (1.0, 'whitelisted') BEFORE calling
    `recent_expectancy`. This test pins that ordering at source level
    so the bypass cannot regress to firing post-query."""
    src = bot_engine_source_for_grep()
    # Find _ev_per_symbol_multiplier definition
    func_start = src.index("def _ev_per_symbol_multiplier")
    func_end = src.index("def _execute_open", func_start)
    func_block = src[func_start:func_end]
    # whitelist bypass must come before recent_expectancy call
    wl_idx = func_block.index("whitelisted")
    re_idx = func_block.index("recent_expectancy")
    assert wl_idx < re_idx, (
        "Whitelist short-circuit must precede the recent_expectancy "
        "call inside _ev_per_symbol_multiplier"
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
