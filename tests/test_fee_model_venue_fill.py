"""Venue+fill-aware fee model (2026-06-11, maker-first groundwork).

_fee_rate gains optional exchange/fill params: LIVE maker fills were
booked at Binance taker rate, and Bybit/Bitget taker (6bps) was
undercharged as Binance's 5bps. Defaults keep legacy calls identical.
Also pins the smart_executor TIF fix (raw timeInForce="PostOnly" was
Bybit-only vocabulary — broke Binance USD-M maker orders).
"""
from __future__ import annotations

from pathlib import Path

from core.position_tracker import _fee_rate


def test_legacy_calls_unchanged():
    from config import FEE
    assert _fee_rate("futures") == FEE.get("futures_taker", 0.0005)
    assert _fee_rate("spot") == FEE.get("spot_taker", 0.001)


def test_venue_specific_taker():
    from config import FEE
    key = "bybit_futures_taker"
    if key in FEE:  # config carries per-venue keys
        assert _fee_rate("futures", "Bybit", "taker") == FEE[key]
        assert _fee_rate("futures", "bybit") == FEE[key]


def test_maker_rates_resolve():
    from config import FEE
    assert _fee_rate("futures", "bybit", "maker") == FEE["bybit_futures_maker"]
    # no venue -> generic maker fallback
    assert _fee_rate("futures", None, "maker") == FEE["futures_maker"]


def test_unknown_venue_falls_back_to_generic():
    from config import FEE
    assert _fee_rate("futures", "kraken", "taker") == FEE.get("futures_taker", 0.0005)


def test_order_manager_books_fill_aware_entry_fee():
    src = Path("core/order_manager.py").read_text(encoding="utf-8")
    i = src.index("Recalculate entry_fee based on actual fill price")
    block = src[i:i + 600]
    assert "pos.market_type, pos.exchange" in block
    assert '"maker" if _fill_type in ("maker", "maker_partial")' in block


def test_smart_executor_no_raw_postonly_tif():
    src = Path("core/smart_executor.py").read_text(encoding="utf-8")
    assert 'params["postOnly"] = True' in src
    assert 'params["timeInForce"] = "PostOnly"' not in src
