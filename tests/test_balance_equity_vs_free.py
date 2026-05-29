"""Pin the 2026-05-02 split: drawdown uses wallet equity, sizing uses free.

Bug we're fixing
================
Pre-2026-05-02, `_log_balances` used `_extract_usdt` (which returns FREE
margin) for both sizing AND drawdown tracking. On Bybit specifically,
free margin = $50 while wallet balance = $207 (the rest locked as
position margin). When a position opened, free dropped by margin used
($50 → $20), and the bot interpreted that as a $30 balance drop —
firing the flake guard 53 times in one day from margin-state noise.

Fix
===
Add `_extract_usdt_equity` that returns wallet balance (free + locked).
Use it for `risk.update_current_balance` (drawdown tracker). Keep
`_extract_usdt` for sizing decisions (`_balances` cache, risk envelope).

These tests pin both behaviors.
"""
from __future__ import annotations

import pytest


def _bybit_unified_response(free_usdt: float, used_usdt: float):
    """Mock a ccxt.fetch_balance() response from Bybit unified account."""
    return {
        "info": {
            "result": {
                "list": [{
                    "totalEquity": str(free_usdt + used_usdt + 137.5),  # +unrealized
                    "totalWalletBalance": str(free_usdt + used_usdt),
                    "totalMarginBalance": str(free_usdt + used_usdt),
                    "totalAvailableBalance": str(free_usdt),
                    "coin": [{
                        "coin": "USDT",
                        "walletBalance": str(free_usdt + used_usdt),
                        "availableToWithdraw": str(free_usdt),
                        "availableBalance": str(free_usdt),
                    }]
                }]
            }
        },
        "USDT": {"free": free_usdt, "used": used_usdt, "total": free_usdt + used_usdt},
        "free": {"USDT": free_usdt},
        "used": {"USDT": used_usdt},
        "total": {"USDT": free_usdt + used_usdt},
    }


def _binance_response(free_usdt: float, used_usdt: float):
    """Mock a ccxt.fetch_balance() response from Binance/Bitget standard format."""
    return {
        "USDT": {"free": free_usdt, "used": used_usdt, "total": free_usdt + used_usdt},
        "free": {"USDT": free_usdt},
        "used": {"USDT": used_usdt},
        "total": {"USDT": free_usdt + used_usdt},
    }


# ─── Bybit unified account ────────────────────────────────────────────


def test_extract_usdt_returns_FREE_for_bybit_sizing():
    """For sizing (existing behavior), Bybit must return totalAvailableBalance.

    Pin: do not regress to using totalEquity for sizing — that caused
    the 2026-04-12 'ab not enough' rejection problem."""
    from core.bot_engine import BotEngine
    bal = _bybit_unified_response(free_usdt=50.0, used_usdt=157.0)
    result = BotEngine._extract_usdt(BotEngine, bal, "bybit")  # type: ignore[arg-type]
    # Should return $50 (free), not $207 (wallet) or $344 (equity)
    assert result == pytest.approx(50.0, abs=0.01)


def test_extract_usdt_equity_returns_WALLET_for_bybit_drawdown():
    """For drawdown (new behavior), Bybit must return totalWalletBalance
    so that opening/closing positions doesn't move the drawdown number."""
    from core.bot_engine import BotEngine
    bal = _bybit_unified_response(free_usdt=50.0, used_usdt=157.0)
    result = BotEngine._extract_usdt_equity(BotEngine, bal, "bybit")  # type: ignore[arg-type]
    # Should return $207 (free + used = wallet balance)
    assert result == pytest.approx(207.0, abs=0.01)


def test_bybit_drawdown_invariant_under_margin_reallocation():
    """When position opens, free drops by margin amount but wallet stays
    the same. Equity-based drawdown tracker must NOT see this as a P&L move."""
    from core.bot_engine import BotEngine

    # Before position opens: $200 free, $0 used
    bal_before = _bybit_unified_response(free_usdt=200.0, used_usdt=0.0)
    eq_before = BotEngine._extract_usdt_equity(BotEngine, bal_before, "bybit")  # type: ignore[arg-type]
    free_before = BotEngine._extract_usdt(BotEngine, bal_before, "bybit")  # type: ignore[arg-type]

    # After position opens (locks $150 margin): $50 free, $150 used
    bal_after = _bybit_unified_response(free_usdt=50.0, used_usdt=150.0)
    eq_after = BotEngine._extract_usdt_equity(BotEngine, bal_after, "bybit")  # type: ignore[arg-type]
    free_after = BotEngine._extract_usdt(BotEngine, bal_after, "bybit")  # type: ignore[arg-type]

    # Free margin DROPPED $150 (margin locked) — sizing-conservative ✓
    assert free_before - free_after == pytest.approx(150.0, abs=0.01)

    # Equity unchanged (wallet balance didn't move) — drawdown stable ✓
    assert eq_before == pytest.approx(eq_after, abs=0.01)


# ─── Standard ccxt (Binance, Bitget) ──────────────────────────────────


def test_extract_usdt_returns_FREE_for_binance_sizing():
    from core.bot_engine import BotEngine
    bal = _binance_response(free_usdt=70.0, used_usdt=145.0)
    result = BotEngine._extract_usdt(BotEngine, bal, "binance")  # type: ignore[arg-type]
    assert result == pytest.approx(70.0, abs=0.01)


def test_extract_usdt_equity_returns_TOTAL_for_binance_drawdown():
    from core.bot_engine import BotEngine
    bal = _binance_response(free_usdt=70.0, used_usdt=145.0)
    result = BotEngine._extract_usdt_equity(BotEngine, bal, "binance")  # type: ignore[arg-type]
    # total = free + used = 215
    assert result == pytest.approx(215.0, abs=0.01)


def test_extract_usdt_equity_returns_TOTAL_for_bitget_drawdown():
    from core.bot_engine import BotEngine
    bal = _binance_response(free_usdt=10.0, used_usdt=80.0)
    result = BotEngine._extract_usdt_equity(BotEngine, bal, "bitget")  # type: ignore[arg-type]
    assert result == pytest.approx(90.0, abs=0.01)


# ─── Edge cases ────────────────────────────────────────────────────────


def test_extract_usdt_equity_handles_empty_bal():
    from core.bot_engine import BotEngine
    assert BotEngine._extract_usdt_equity(BotEngine, {}, "bybit") == 0.0  # type: ignore[arg-type]
    assert BotEngine._extract_usdt_equity(BotEngine, None, "binance") == 0.0  # type: ignore[arg-type]


def test_extract_usdt_equity_falls_back_to_free_when_total_missing():
    """Degraded path: if `total` is missing from bal, fall back to free.
    Better than returning 0 (which would propagate as a drawdown spike)."""
    from core.bot_engine import BotEngine
    bal = {"USDT": {"free": 100.0}}  # no total key
    result = BotEngine._extract_usdt_equity(BotEngine, bal, "binance")  # type: ignore[arg-type]
    assert result == pytest.approx(100.0, abs=0.01)
