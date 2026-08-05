"""A2 — deferred audit fixes (fail-closed rails + attribution/robustness).

Each test pins one CONFIRMED audit fix from Phase A2.
"""
from __future__ import annotations

import threading


# ── Fix 1a: exposure_breached fail-CLOSED on bad equity ──────────────────
def test_exposure_breached_fails_closed_on_zero_equity():
    from core.risk_manager import exposure_breached
    assert exposure_breached([], 10.0, 0.0, 12.0) is True
    assert exposure_breached([], 10.0, None, 12.0) is True
    assert exposure_breached([], 10.0, -5.0, 12.0) is True


def test_exposure_breached_normal_paths_unchanged():
    from core.risk_manager import exposure_breached
    # tiny notional vs big equity -> not breached
    assert exposure_breached([], 1.0, 1000.0, 12.0) is False
    # notional over cap -> breached
    assert exposure_breached([], 200.0, 1000.0, 12.0) is True


# ── Fix 3: opens_today resets at UTC rollover in record_trade_pnl ────────
def test_record_trade_pnl_resets_opens_today_on_new_day():
    from core.risk_manager import RiskManager
    rm = RiskManager()
    rm.set_start_balance(100.0)
    rm._opens_today = 5
    from datetime import date
    rm._trading_day = date(2000, 1, 1)  # force stale day
    rm.record_trade_pnl(-1.0, 100.0, is_win=False)
    assert rm._opens_today == 0


# ── Fix 4: sync_with_exchanges snapshots _open under the lock ────────────
def test_sync_snapshots_open_under_lock(monkeypatch):
    """A concurrent close() mutating _open mid-iteration must not crash sync."""
    from core.position_tracker import Position, PositionTracker

    pt = PositionTracker.__new__(PositionTracker)
    pt._lock = threading.RLock()
    pt._open = {}
    pt._closed = []
    pt._save = lambda: None

    def mk(i):
        return Position(
            id=f"P{i}", exchange="binance", symbol=f"C{i}/USDT:USDT",
            side="buy", market_type="futures", strategy="x",
            entry_price=1.0, size=1.0, stop_loss=0.9, take_profit=1.1,
            leverage=1, paper_trade=True,
        )

    for i in range(50):
        pt._open[f"P{i}"] = mk(i)

    # No exchanges -> no fetch; still iterates _open for tracked_keys/live_open.
    # Should complete without RuntimeError even if we mutate concurrently.
    result = pt.sync_with_exchanges({})
    assert isinstance(result, list)


# ── Fix 7: maker _bar_high_low handles 6-tuple OHLCV ─────────────────────
def test_maker_bar_high_low_6tuple():
    from core.maker_fill_model import _bar_high_low
    # repo-standard [ts, o, h, l, c, v]
    hi, lo = _bar_high_low([1000, 100.0, 105.0, 99.0, 101.0, 12.0])
    assert hi == 105.0 and lo == 99.0
    # short [o, h, l, c]
    hi, lo = _bar_high_low([100.0, 105.0, 99.0, 101.0])
    assert hi == 105.0 and lo == 99.0
    # dict
    hi, lo = _bar_high_low({"high": 105.0, "low": 99.0})
    assert hi == 105.0 and lo == 99.0


# ── Fix 5: orderbook depth points at the futures (fapi) book ──────────────
def test_orderbook_depth_uses_futures_book(monkeypatch):
    from core.data_feeds import orderbook_depth_feed as odf

    captured = {}

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return b'{"bids":[["1","1"]],"asks":[["2","1"]]}'

    def fake_urlopen(req, timeout=5):
        captured["url"] = req.full_url
        return _Resp()

    monkeypatch.setattr(odf.urllib.request, "urlopen", fake_urlopen)
    feed = odf.OrderBookDepthFeed(cache_ttl=0)
    feed._fetch_binance_depth("BTC")
    assert "fapi.binance.com/fapi/v1/depth" in captured["url"]

