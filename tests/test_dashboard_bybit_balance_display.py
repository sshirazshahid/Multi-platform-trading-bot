"""Pin the 2026-05-02 fix: dashboard shows wallet equity, not free margin.

Bug we're fixing
================
Pre-fix, dashboard.extract_usdt for Bybit used the same priority as
core/bot_engine._extract_usdt: totalAvailableBalance first ($50 free).
Result: dashboard displayed ~$50 for Bybit when actual wallet was $207.
Every position open dropped the displayed balance by the margin used,
making the user think their account had crashed.

Fix
===
For the DASHBOARD (display), prefer totalWalletBalance ($207, USDT
free + locked margin). bot_engine still uses totalAvailableBalance for
SIZING (conservative — won't over-deploy). Dashboard is for display
only; sizing path is unchanged.

This separation mirrors the core/bot_engine fix in commit d4aeead:
  _extract_usdt        → free margin (sizing — bot_engine)
  _extract_usdt_equity → wallet equity (drawdown / display — bot_engine)
  dashboard.extract_usdt → wallet equity (display only)
"""
from __future__ import annotations

import pytest


def _bybit_response(free_usdt: float, used_usdt: float):
    return {
        "info": {
            "result": {
                "list": [{
                    "totalEquity": str(free_usdt + used_usdt + 138.5),  # +collateral
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


def test_dashboard_bybit_returns_wallet_balance_not_free():
    """Dashboard must show wallet equity (free + locked), not just free."""
    import dashboard
    bal = _bybit_response(free_usdt=50.0, used_usdt=157.0)
    result = dashboard.extract_usdt(bal, "bybit")
    # Should return $207 (wallet), NOT $50 (free)
    assert result == pytest.approx(207.0, abs=0.01), \
        f"Dashboard should show wallet equity $207, not free $50. Got {result}"


def test_dashboard_bybit_invariant_under_margin_lock():
    """When position opens, free drops but wallet is unchanged. Display
    must NOT swing — that was the original symptom."""
    import dashboard

    # Before position opens: $200 free, no margin used
    bal_before = _bybit_response(free_usdt=200.0, used_usdt=0.0)
    display_before = dashboard.extract_usdt(bal_before, "bybit")

    # After position opens (locks $150 margin): $50 free, $150 used
    bal_after = _bybit_response(free_usdt=50.0, used_usdt=150.0)
    display_after = dashboard.extract_usdt(bal_after, "bybit")

    # Wallet balance unchanged → display unchanged ✓
    assert display_before == pytest.approx(display_after, abs=0.01), \
        f"Display swung from {display_before} to {display_after} on margin lock"


def test_dashboard_binance_unchanged():
    """Binance/non-unified path unchanged — already returned correct numbers."""
    import dashboard
    bal = {
        "USDT": {"free": 70.0, "used": 145.0, "total": 215.0},
        "free": {"USDT": 70.0},
        "total": {"USDT": 215.0},
    }
    result = dashboard.extract_usdt(bal, "binance")
    # ccxt path: prefers 'total' which is 215
    assert result == pytest.approx(215.0, abs=0.01)


def test_dashboard_bybit_falls_back_to_equity_when_wallet_missing():
    """Edge case: if totalWalletBalance is missing, fall back through the
    chain (margin → available → per-coin → totalEquity)."""
    import dashboard
    # Stripped-down response with only totalEquity present
    bal = {
        "info": {
            "result": {
                "list": [{
                    "totalEquity": "150.00",
                    # totalWalletBalance, totalMarginBalance, totalAvailableBalance absent
                }]
            }
        },
    }
    result = dashboard.extract_usdt(bal, "bybit")
    assert result == pytest.approx(150.0, abs=0.01)
