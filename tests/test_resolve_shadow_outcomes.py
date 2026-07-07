"""Tests for scripts/resolve_shadow_outcomes.py (shadow-resolver runner wiring).

The KEYSTONE resolver (core.shadow_resolver.resolve_pending) was shipped with
unit tests but never wired into any runtime path — shadow_decisions accumulated
2,105 PENDING rows with shadow_outcomes forever empty. The runner script closes
that gap; these tests cover its pure candle-fetch/caching logic.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load_module():
    spec = importlib.util.spec_from_file_location(
        "resolve_shadow_outcomes", ROOT / "scripts" / "resolve_shadow_outcomes.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


HOUR_MS = 3_600_000


def _bar(ts_ms, o=100.0, h=101.0, lo=99.0, c=100.5, v=1.0):
    return [ts_ms, o, h, lo, c, v]


def _row(ts_sec, venue="binance", symbol="BTC/USDT:USDT", tf="1h", horizon=5):
    return {
        "ts": ts_sec,
        "venue": venue,
        "symbol": symbol,
        "timeframe": tf,
        "horizon_bars": horizon,
    }


def test_tf_seconds_parses_common_frames():
    mod = _load_module()
    assert mod._tf_seconds("1h") == 3600
    assert mod._tf_seconds("15m") == 900
    assert mod._tf_seconds("1d") == 86400


def test_slices_strictly_after_entry_and_drops_forming_bar():
    mod = _load_module()
    entry_sec = 1_000_000_000
    entry_ms = entry_sec * 1000
    now_ms = entry_ms + 2 * HOUR_MS + 30 * 60_000  # 2.5h after entry
    # entry bar (contains entry -> excluded), +1h closed bar (kept),
    # +2h bar still forming at now (dropped)
    candles = [_bar(entry_ms), _bar(entry_ms + HOUR_MS), _bar(entry_ms + 2 * HOUR_MS)]
    calls = []

    def fake_fetch(venue, symbol, tf, since_ms):
        calls.append((venue, symbol, tf, since_ms))
        return candles

    fetch_candles = mod.build_fetch_candles(fake_fetch, now_ms=now_ms)
    out = fetch_candles(_row(entry_sec))
    assert [c[0] for c in out] == [entry_ms + HOUR_MS]
    assert len(calls) == 1


def test_group_fetch_cached_across_rows():
    mod = _load_module()
    entry_sec = 1_000_000_000
    entry_ms = entry_sec * 1000
    now_ms = entry_ms + 10 * HOUR_MS
    candles = [_bar(entry_ms + i * HOUR_MS) for i in range(8)]
    calls = []

    def fake_fetch(venue, symbol, tf, since_ms):
        calls.append(since_ms)
        return candles

    fetch_candles = mod.build_fetch_candles(fake_fetch, now_ms=now_ms)
    fetch_candles(_row(entry_sec))
    fetch_candles(_row(entry_sec + 3600))  # same (venue, symbol, tf) group, later entry
    assert len(calls) == 1, "same-group rows must reuse the cached fetch"


def test_widens_refetch_for_earlier_entry():
    mod = _load_module()
    late_sec = 1_000_000_000
    early_sec = late_sec - 7200
    now_ms = (late_sec + 10 * 3600) * 1000

    def fake_fetch(venue, symbol, tf, since_ms):
        base = since_ms - (since_ms % HOUR_MS)
        return [_bar(base + i * HOUR_MS) for i in range(12)]

    calls = []

    def counting_fetch(venue, symbol, tf, since_ms):
        calls.append(since_ms)
        return fake_fetch(venue, symbol, tf, since_ms)

    fetch_candles = mod.build_fetch_candles(counting_fetch, now_ms=now_ms)
    fetch_candles(_row(late_sec))
    out = fetch_candles(_row(early_sec))  # earlier entry -> cache must widen
    assert len(calls) == 2
    assert out, "earlier row must get candles after the widened refetch"
    assert all(c[0] > early_sec * 1000 for c in out)


def test_budget_caps_new_group_fetches():
    mod = _load_module()
    entry_sec = 1_000_000_000
    now_ms = (entry_sec + 10 * 3600) * 1000
    candles = [_bar((entry_sec + 3600) * 1000)]

    def fake_fetch(venue, symbol, tf, since_ms):
        return candles

    fetch_candles = mod.build_fetch_candles(
        fake_fetch, now_ms=now_ms, max_group_fetches=1
    )
    assert fetch_candles(_row(entry_sec, symbol="BTC/USDT:USDT"))
    assert fetch_candles(_row(entry_sec, symbol="ETH/USDT:USDT")) == []


def test_horizon_caps_returned_bars():
    mod = _load_module()
    entry_sec = 1_000_000_000
    entry_ms = entry_sec * 1000
    now_ms = entry_ms + 50 * HOUR_MS
    candles = [_bar(entry_ms + i * HOUR_MS) for i in range(40)]

    def fake_fetch(venue, symbol, tf, since_ms):
        return candles

    fetch_candles = mod.build_fetch_candles(fake_fetch, now_ms=now_ms)
    out = fetch_candles(_row(entry_sec, horizon=3))
    assert len(out) <= 5  # horizon + 2 buffer


def test_malformed_row_returns_empty():
    mod = _load_module()

    def fake_fetch(venue, symbol, tf, since_ms):  # pragma: no cover - not reached
        raise AssertionError("must not fetch for malformed rows")

    fetch_candles = mod.build_fetch_candles(fake_fetch, now_ms=1)
    assert fetch_candles({"symbol": "BTC/USDT:USDT"}) == []
    assert fetch_candles({"ts": None, "venue": "binance", "symbol": "X", "timeframe": "1h"}) == []


def test_zero_horizon_rows_use_default_horizon_cap():
    """horizon_bars=0 legacy rows must not stream unbounded candles — the fetch
    cap uses the resolver's DEFAULT_HORIZON_BARS so fetch and resolve agree."""
    mod = _load_module()
    from core.shadow_resolver import DEFAULT_HORIZON_BARS

    entry_sec = 1_000_000_000
    entry_ms = entry_sec * 1000
    candles = [_bar(entry_ms + i * HOUR_MS) for i in range(1, 200)]
    now_ms = entry_ms + 200 * HOUR_MS
    fc = mod.build_fetch_candles(lambda *a: candles, now_ms=now_ms)
    out = fc(_row(entry_sec, horizon=0))
    assert len(out) == DEFAULT_HORIZON_BARS + 2
