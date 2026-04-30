"""Dashboard extras-file merge invariants (2026-04-30).

Background
----------
Direct edits to `data/positions.json` are clobbered within seconds by
`tracker._save()` because the bot writes from in-memory state and never
merges external changes. To make recovered closes (e.g. trades the
bot's runtime path missed) persist for the dashboard, we introduced
`data/positions_extra.json` — an operator-owned file the bot never
touches. Dashboard merges it during `load_positions()`.

The merge can't dedup by `id` alone because the extras file is
typically built from the warehouse, which uses synthetic IDs
(`WAREHOUSE-binance-...`) while positions.json uses exchange order_ids
(`97059916222`). Naive id-only dedup would let the SAME trade through
both feeds and double-count totals.

Tests below exercise the (exchange, symbol, side, close_time //60)
time-bucket dedup logic as a self-contained replica of dashboard.py.
"""
from __future__ import annotations

import pytest


# ─── Self-contained replica of the merge logic ────────────────────────


def merge_extras(positions_closed: list[dict],
                 extras_closed: list[dict]) -> list[dict]:
    """Replica of the merge logic in dashboard.load_positions."""
    seen_ids: set = set()
    all_closed: list = []

    # 1. positions.json (id-only dedup, like the existing dashboard code)
    for pos in positions_closed:
        pid = pos.get("id", "")
        if pid in seen_ids:
            continue
        seen_ids.add(pid)
        all_closed.append(pos)

    # 2. extras with time-bucket dedup
    existing_close_keys: set = set()
    for p in all_closed:
        ct = p.get("close_time") or 0
        if not ct:
            continue
        existing_close_keys.add((
            (p.get("exchange") or "").lower(),
            p.get("symbol", ""),
            p.get("side", ""),
            int(float(ct) // 60),
        ))

    for pos in extras_closed:
        ct = pos.get("close_time") or 0
        key = (
            (pos.get("exchange") or "").lower(),
            pos.get("symbol", ""),
            pos.get("side", ""),
            int(float(ct) // 60) if ct else None,
        )
        adjacent = {key}
        if ct:
            bucket = int(float(ct) // 60)
            adjacent.add((key[0], key[1], key[2], bucket - 1))
            adjacent.add((key[0], key[1], key[2], bucket + 1))
        if any(k in existing_close_keys for k in adjacent):
            continue
        pid = pos.get("id", "")
        if pid in seen_ids:
            continue
        seen_ids.add(pid)
        all_closed.append(pos)
        existing_close_keys.add(key)

    return all_closed


# ─── Tests ────────────────────────────────────────────────────────────


def _trade(*, ex="binance", sym="DOGE/USDT:USDT", side="buy",
           close_time=1777528163.0, pnl=1.0, pid="x"):
    return {
        "id": pid, "exchange": ex, "symbol": sym, "side": side,
        "close_time": close_time, "pnl": pnl, "gross_pnl": pnl,
    }


def test_extras_record_with_no_collision_is_added():
    positions = [_trade(close_time=1777500000.0, pnl=1.0, pid="A")]
    extras = [_trade(close_time=1777528163.0, pnl=5.4238,
                     pid="WAREHOUSE-...")]
    merged = merge_extras(positions, extras)
    assert len(merged) == 2
    pnls = sorted([m["pnl"] for m in merged])
    assert pnls == [1.0, 5.4238]


def test_extras_record_with_id_collision_is_dropped():
    """Even with different (sym, time), an exact-ID match is a duplicate."""
    positions = [_trade(close_time=1777500000.0, pid="DUP")]
    extras = [_trade(close_time=1777600000.0, sym="ETH/USDT:USDT",
                     pnl=99.0, pid="DUP")]
    merged = merge_extras(positions, extras)
    assert len(merged) == 1
    assert merged[0]["pnl"] == 1.0  # original kept; extras dropped


def test_extras_record_with_time_bucket_collision_is_dropped():
    """Same (ex, sym, side) within the SAME 60-second bucket = duplicate.
    This is the case that motivated the fix: warehouse rebuild gives
    every trade a synthetic id, but the (sym, time) coordinates match
    positions.json's record under its real exchange order_id."""
    positions = [_trade(close_time=1777528163.0, pnl=1.0, pid="binance-order-id-12345")]
    extras = [_trade(close_time=1777528163.0, pnl=1.0,
                     pid="WAREHOUSE-binance-DOGE_USDT_USDT-1777528163")]
    merged = merge_extras(positions, extras)
    assert len(merged) == 1, (
        "Same trade in both feeds under different IDs must dedup "
        "by (exchange, symbol, side, close_time bucket).")


def test_extras_record_in_adjacent_bucket_is_dropped():
    """Drift of ~30-60s across a bucket boundary still dedups via the
    ±1 adjacent-bucket check."""
    # positions.json at second-15 of minute 100 (bucket 100)
    pos_ct = 60 * 100 + 15
    # extras at second-50 of minute 99 (bucket 99 = 100 - 1, adjacent)
    ext_ct = 60 *  99 + 50
    positions = [_trade(close_time=pos_ct, pid="real")]
    extras = [_trade(close_time=ext_ct, pnl=2.0, pid="WAREHOUSE-x")]
    merged = merge_extras(positions, extras)
    assert len(merged) == 1
    assert merged[0]["pid" if False else "id"] == "real"


def test_extras_record_in_distant_minute_is_added():
    """5-minute gap between positions and extras for same symbol/side =
    two genuinely-different round-trips of the same pair, both kept."""
    positions = [_trade(close_time=1777500000.0, pid="A")]
    extras = [_trade(close_time=1777500300.0, pnl=2.0, pid="B")]
    merged = merge_extras(positions, extras)
    assert len(merged) == 2


def test_different_side_does_not_dedup():
    """Same symbol + same time but different side = different trade."""
    positions = [_trade(side="buy", pid="A")]
    extras = [_trade(side="sell", pnl=2.0, pid="B")]
    merged = merge_extras(positions, extras)
    assert len(merged) == 2


def test_different_exchange_does_not_dedup():
    positions = [_trade(ex="binance", pid="A")]
    extras = [_trade(ex="bybit", pnl=2.0, pid="B")]
    merged = merge_extras(positions, extras)
    assert len(merged) == 2


def test_extras_with_no_close_time_falls_through_to_id_dedup():
    """If close_time is missing/zero, the time-bucket key is None for
    everyone — fall back to id-only dedup."""
    positions = [_trade(close_time=0, pid="A")]
    extras = [_trade(close_time=0, pnl=2.0, pid="A")]  # same id
    merged = merge_extras(positions, extras)
    assert len(merged) == 1


def test_doge_5_42_recovery_scenario():
    """End-to-end test of the production scenario: positions.json has
    the +$3.5328 DOGE close (a different trade later in the same day),
    extras has the +$5.4238 close. Both must end up in the merged list,
    +$5.4238 must be the top PnL."""
    positions = [
        _trade(close_time=1777544632.0, pnl=3.5328,
               pid="binance-order-id-Y"),  # 14:43:52
        _trade(close_time=1777400000.0, pnl=3.6885, sym="QTUM/USDT:USDT",
               side="sell", pid="binance-order-id-Z"),
    ]
    extras = [
        _trade(close_time=1777528163.0, pnl=5.4238,
               pid="WAREHOUSE-binance-DOGE_USDT_USDT-1777528163"),  # 05:49
    ]
    merged = merge_extras(positions, extras)
    assert len(merged) == 3, "All three trades must be present"
    top = max(merged, key=lambda x: x["pnl"])
    assert top["pnl"] == pytest.approx(5.4238)
    assert top["symbol"] == "DOGE/USDT:USDT"
