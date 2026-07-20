"""F1 (2026-07-20 deep audit) — lev-1 futures must use the FUTURES wallet branch.

Verified money-math bug: VirtualWallet.on_close gated the futures branch with
``market_type == "futures" and leverage > 1``, so a leverage-1 futures position
fell through to the SPOT formula ``proceeds = size * exit_price - exit_fee``.
For a SHORT that sign-flips the PnL (warehouse trades row 2588: AXS
DRY-359d8a43 booked net -0.1237 while the wallet delta was $0.00).

Fix under test: the futures branch applies for ALL futures regardless of
leverage (``proceeds = margin + gross_pnl - exit_fee``), and the OPEN side
drops the same ``leverage > 1`` gate class (identical arithmetic at lev 1,
but the gate class is removed and a lev<=0 input can no longer divide by zero).
"""
from __future__ import annotations

from core.virtual_wallet import VirtualWallet

START = 1000.0


def _wallet(tmp_path, monkeypatch):
    # isolate the on-disk file so _save doesn't touch the real data/ dir
    monkeypatch.chdir(tmp_path)
    (tmp_path / "data").mkdir(exist_ok=True)
    w = VirtualWallet()
    w._enabled = True
    w._start = START
    w._balances = {}
    return w


def test_lev1_futures_short_close_wallet_delta_equals_booked_net(tmp_path, monkeypatch):
    """Winning lev-1 futures short: wallet delta == gross - entry_fee - exit_fee."""
    w = _wallet(tmp_path, monkeypatch)
    entry_fee, exit_fee = 0.05, 0.045
    assert w.on_open("bybit", "AXS/USDT:USDT", "sell", 10.0, 10.0, entry_fee,
                     "futures", 1)
    # margin at lev 1 == full notional (100.0) + entry fee
    assert abs((START - w.balance("bybit")) - (100.0 + entry_fee)) < 1e-9

    # price drops 10.0 -> 9.0: short gross pnl = +10.0
    gross_pnl = (10.0 - 9.0) * 10.0
    w.on_close("bybit", "AXS/USDT:USDT", "sell", 10.0, 9.0, 10.0,
               exit_fee, gross_pnl, "futures", 1)
    booked_net = gross_pnl - entry_fee - exit_fee
    delta = w.balance("bybit") - START
    assert abs(delta - booked_net) < 1e-9, (
        f"wallet delta {delta:+.4f} != booked net {booked_net:+.4f} "
        "(lev-1 short fell into the spot formula?)")


def test_lev1_futures_losing_short_is_not_sign_flipped(tmp_path, monkeypatch):
    """Losing lev-1 futures short must DEBIT the wallet (the old spot formula
    credited size*exit_price, flipping the sign)."""
    w = _wallet(tmp_path, monkeypatch)
    entry_fee, exit_fee = 0.05, 0.055
    assert w.on_open("bybit", "AXS/USDT:USDT", "sell", 10.0, 10.0, entry_fee,
                     "futures", 1)
    # price rises 10.0 -> 11.0: short gross pnl = -10.0
    gross_pnl = (10.0 - 11.0) * 10.0
    w.on_close("bybit", "AXS/USDT:USDT", "sell", 10.0, 11.0, 10.0,
               exit_fee, gross_pnl, "futures", 1)
    delta = w.balance("bybit") - START
    booked_net = gross_pnl - entry_fee - exit_fee
    assert delta < 0, f"losing short RAISED the wallet by {delta:+.4f} (sign flip)"
    assert abs(delta - booked_net) < 1e-9


def test_leveraged_futures_short_behavior_unchanged(tmp_path, monkeypatch):
    """Regression guard: lev-2 short (the path that already worked) is untouched."""
    w = _wallet(tmp_path, monkeypatch)
    entry_fee, exit_fee = 0.05, 0.045
    assert w.on_open("bybit", "AXS/USDT:USDT", "sell", 10.0, 10.0, entry_fee,
                     "futures", 2)
    # margin = notional/2 = 50.0
    assert abs((START - w.balance("bybit")) - (50.0 + entry_fee)) < 1e-9
    gross_pnl = (10.0 - 9.0) * 10.0
    w.on_close("bybit", "AXS/USDT:USDT", "sell", 10.0, 9.0, 10.0,
               exit_fee, gross_pnl, "futures", 2)
    delta = w.balance("bybit") - START
    assert abs(delta - (gross_pnl - entry_fee - exit_fee)) < 1e-9


def test_spot_close_formula_unchanged(tmp_path, monkeypatch):
    """Spot stays on the sell-proceeds formula."""
    w = _wallet(tmp_path, monkeypatch)
    assert w.on_open("binance", "AXS/USDT", "buy", 10.0, 10.0, 0.1, "spot", 1)
    assert abs((START - w.balance("binance")) - 100.1) < 1e-9
    w.on_close("binance", "AXS/USDT", "buy", 10.0, 11.0, 10.0, 0.11, 10.0, "spot", 1)
    # spot proceeds = 10*11 - 0.11
    assert abs((w.balance("binance") - START) - (110.0 - 0.11 - 100.1)) < 1e-9


def test_open_side_lev_zero_futures_does_not_divide_by_zero(tmp_path, monkeypatch):
    """The de-gated open branch must clamp leverage <= 0 to 1, not crash."""
    w = _wallet(tmp_path, monkeypatch)
    assert w.on_open("bybit", "AXS/USDT:USDT", "sell", 10.0, 10.0, 0.05,
                     "futures", 0)
    assert abs((START - w.balance("bybit")) - 100.05) < 1e-9
