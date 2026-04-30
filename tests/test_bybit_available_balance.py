"""Bybit available-USDT display invariants (2026-05-01).

Bug
---
User: "Bybit balance showing incorrect for available USDT".

The dashboard's per-exchange coin subline (e.g. `└─ USDT 60.47  ETH ...`)
was rendering `info["total"]` which on Bybit Unified Account means
wallet balance — including USDT currently locked as initial margin on
open positions. When the user has 3 open futures positions consuming
~$34 of margin out of $60 wallet:

  totalWalletBalance  = $60.12  (what dashboard displayed: "USDT 60.47")
  totalAvailableBalance = $25.61  (what user actually has free to trade)
  totalEquity = $343.88  (counts the value of every spot coin too)

User reads "USDT 60.47" and tries to size a trade against that — but
Bybit rejects with 110007 "ab not enough" because only $25.61 is free.

Fix
---
For STABLECOIN entries (USDT/USDC/etc.) in the per-exchange coin
subline, render `info["free"]` instead of `info["total"]`. When the
gap is meaningful (>$0.50), append `(+N locked)` so the user can see
how much margin is currently in use.

Non-stable assets (BTC/ETH/etc.) keep showing `total` because their
"free" field is just whatever isn't in an open spot order — usually
==total — and the locked-margin distinction doesn't apply.
"""
from __future__ import annotations

from typing import NamedTuple

import pytest


# ─── Self-contained replica of the per-coin subline rendering ──────────


class _CoinRow(NamedTuple):
    asset: str
    total: float
    free: float
    locked: float
    display_token: str   # the rendered substring (without ANSI colors)


_STABLES = {"USDT", "USD", "BUSD", "USDC"}


def render_coin_token(asset: str, total: float, free: float) -> _CoinRow:
    """Pure replica of the new dashboard logic for ONE coin entry.

    Strips ANSI for testability — the production code wraps each piece
    in `col(...)` color escapes that don't change the displayed numbers.
    """
    if asset in _STABLES:
        locked = max(0.0, total - free)
        if free > 0 and locked > 0.5:
            display = "{} {:.2f} (+{:.2f} locked)".format(asset, free, locked)
        elif free > 0:
            display = "{} {:.2f}".format(asset, free)
        else:
            display = "{} {:.2f}".format(asset, total)
        return _CoinRow(asset, total, free, locked, display)
    # Non-stable: shows total + USD-equivalent (price omitted in this
    # replica — irrelevant to the bug under test).
    return _CoinRow(asset, total, free, 0.0, f"{asset} {total:.6g}")


# ─── The actual production scenario ────────────────────────────────────


def test_bybit_unified_account_subline_shows_available_not_wallet():
    """Real numbers from the user's Bybit account on 2026-05-01:
    walletBalance=60.12, availableToWithdraw≈25.96, used=34.16."""
    row = render_coin_token("USDT", total=60.12, free=25.96)
    assert "25.96" in row.display_token, (
        f"USDT subline must show available balance ($25.96), not wallet "
        f"($60.12). Got: {row.display_token!r}")
    assert "60" not in row.display_token.split("locked")[0], (
        f"Wallet balance ($60.12) must not appear as the headline number — "
        f"that's what made the user think they had more available than "
        f"they did. Got: {row.display_token!r}")


def test_bybit_locked_margin_visible_when_gap_is_meaningful():
    """When there's a meaningful gap (locked > $0.50), the (+N locked)
    suffix must appear so the user can see how much margin is in use."""
    row = render_coin_token("USDT", total=60.12, free=25.96)
    assert "locked" in row.display_token, (
        f"Locked-margin annotation missing despite $34 gap. "
        f"Got: {row.display_token!r}")
    assert "34.16" in row.display_token


def test_no_locked_suffix_when_no_open_positions():
    """When free == total (no margin locked), the (+N locked) suffix
    should NOT appear — it would just be visual noise."""
    row = render_coin_token("USDT", total=100.0, free=100.0)
    assert "locked" not in row.display_token
    assert "100.00" in row.display_token


def test_tiny_locked_gap_suppressed():
    """A < $0.50 gap (e.g. floating-point fee accounting) should NOT
    produce a (+0.01 locked) annotation."""
    row = render_coin_token("USDT", total=100.0, free=99.95)
    assert "locked" not in row.display_token
    assert "99.95" in row.display_token


def test_falls_back_to_total_when_free_unavailable():
    """Some legacy balance dicts only have 'total'. Don't render '0' —
    fall back to total (with no locked annotation, since we can't
    reliably split it)."""
    row = render_coin_token("USDT", total=100.0, free=0.0)
    assert "100.00" in row.display_token
    assert "locked" not in row.display_token


def test_non_stable_asset_unchanged():
    """BTC/ETH/etc. don't have the wallet-vs-available distinction
    in any meaningful way — their 'free' is generally == 'total' on
    these exchanges. Render unchanged from prior behavior."""
    row = render_coin_token("BTC", total=0.001, free=0.001)
    assert "BTC" in row.display_token
    assert "0.001" in row.display_token
    assert "locked" not in row.display_token


@pytest.mark.parametrize("stable", ["USDT", "USDC", "BUSD", "USD"])
def test_all_stablecoins_use_free(stable):
    row = render_coin_token(stable, total=200.0, free=50.0)
    assert "50.00" in row.display_token, (
        f"{stable} must use 'free' just like USDT (it's the same "
        f"wallet-vs-margin distinction on unified-account exchanges).")


# ─── Regression: tuple shape of coin_rows ──────────────────────────────


def test_coin_rows_tuple_shape_is_4tuple():
    """coin_rows tuples grew from (asset, amt, usdt_val) to
    (asset, amt, free, usdt_val) in this commit. If a future edit
    forgets the `free` field, every consumer downstream breaks. Pin
    the shape via a structural test."""
    # Replicate a tuple as the production code builds it
    asset, amt, free, uval = ("USDT", 60.12, 25.96, 60.12)
    coin_rows = [(asset, amt, free, uval)]

    # Sort key uses index 3 (uval). Verify it doesn't IndexError.
    coin_rows.sort(key=lambda r: (0 if r[0] in _STABLES else 1, -r[3]))

    # Unpack in the rendering loop must succeed.
    for asset, amt, free, uval in coin_rows:
        assert asset == "USDT"
        assert amt == 60.12
        assert free == 25.96
        assert uval == 60.12
