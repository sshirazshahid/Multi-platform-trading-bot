"""Regression: partial-TP must book the closed fraction in the paper wallet.

Audit 2026-06-03: partial_close_position() reduced position.size but booked NOTHING —
no wallet.on_close for the taken fraction. Only _finalize_close booked PnL/margin, and
on the now-REDUCED size, so every partial take-profit silently leaked the taken
fraction's reserved margin + realized profit from the paper wallet, biasing the
displayed balance/PnL DOWNWARD. Fix: partial_close_position now calls wallet.on_close
for the taken fraction (paper-only); disjoint from the final close, so additive with no
double-count.
"""
from __future__ import annotations

from pathlib import Path


def test_partial_plus_final_close_returns_full_margin_and_total_pnl(tmp_path, monkeypatch):
    """on_close on the partial fraction + on_close on the remainder must together return
    the FULL reserved margin + TOTAL realized PnL — i.e. no leak."""
    from core.virtual_wallet import VirtualWallet
    # isolate the on-disk file so _save doesn't touch the real data/ dir.
    # (2026-06-11: this test's _save() previously clobbered the PRODUCTION
    # data/virtual_wallet.json with start=1000, which made the bot re-seed the
    # paper wallet to 5000/exchange on every restart — erasing paper losses.)
    monkeypatch.chdir(tmp_path)
    (tmp_path / "data").mkdir(exist_ok=True)
    w = VirtualWallet()
    w._enabled = True
    w._start = 1000.0
    w._balances = {}
    ex, sym = "binance", "BTC/USDT:USDT"

    def fee(sz, px):
        return sz * px * 0.0005  # futures taker

    # open 100 units @ 10, lev 2 -> margin 500 + open fee
    fo = fee(100, 10)
    assert w.on_open(ex, sym, "buy", 100, 10, fo, "futures", 2) is True
    assert abs(w.balance(ex) - (1000 - 500 - fo)) < 1e-6

    # partial close 30 @ 11 (gross +30) and final close 70 @ 12 (gross +140)
    ef1 = fee(30, 11)
    w.on_close(ex, sym, "buy", 30, 11, 10, ef1, (11 - 10) * 30, "futures", 2)
    ef2 = fee(70, 12)
    w.on_close(ex, sym, "buy", 70, 12, 10, ef2, (12 - 10) * 70, "futures", 2)

    # full margin returns net-zero (500 out, 150+350 back); total gross PnL = 170;
    # minus the three fees. WITHOUT the partial on_close the balance would be short by
    # the taken fraction's margin (150) + profit (30) = 180.
    expected = 1000 + 170 - fo - ef1 - ef2
    assert abs(w.balance(ex) - expected) < 1e-6, f"got {w.balance(ex):.6f}, want {expected:.6f}"


def test_partial_close_books_the_paper_wallet():
    """partial_close_position() must call wallet.on_close for the taken fraction."""
    src = Path("core/order_manager.py").read_text(encoding="utf-8")
    i = src.index("def partial_close_position")
    block = src[i:i + 4500]
    assert "self.wallet.on_close(" in block, (
        "partial_close_position must book the taken fraction in the paper wallet "
        "(else partial TPs leak margin+profit from the paper balance)")
    assert "partial_size, price, position.entry_price" in block  # booked at the partial fill


def test_partial_close_records_daily_pnl_for_breaker():
    """partial_close_position() must feed the partial leg's net PnL to
    risk.record_trade_pnl so the daily-loss breaker sees banked partials
    WHEN they bank (2026-06-11; _finalize_close records only the runner's
    pos.pnl, which excludes realized_partial_pnl). is_win=None is mandatory:
    a partial is not a completed trade — Spec §12 streaks, recent-results
    and Kelly history update once, at _finalize_close, on the whole trade."""
    src = Path("core/order_manager.py").read_text(encoding="utf-8")
    i = src.index("def partial_close_position")
    block = src[i:i + 7000]
    assert "self.risk.record_trade_pnl(" in block, (
        "partial leg must reach the daily-loss breaker")
    ri = block.index("self.risk.record_trade_pnl(")
    assert "is_win=None" in block[ri:ri + 400], (
        "partial leg must NOT update streaks/Kelly — is_win must be None")
