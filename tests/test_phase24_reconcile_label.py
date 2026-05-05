"""Phase 24 — RECONCILE vs MANUAL re-import labelling (2026-05-05).

User flagged the close+reimport pattern: bot closes 0.01 ETH, sync sees
0.01 leftover on the exchange and imports it as MANUAL with a WARNING
log. Investigation showed this is a tracker-vs-exchange size drift, not
a hedge-mode close failure — the close orders DO reduce the position;
the orphan is the leftover that wasn't tracked.

Fix scope (Option A): cosmetic relabelling. When sync imports a
position that matches a close booked in the last 5 min on the same
(exchange, symbol, side) within ±0.5% size and ±1% entry, tag it as
RECONCILE not MANUAL — INFO log instead of WARNING, dashboard filter
excludes it from bot-trade stats. Functional behavior unchanged: SL/TP
still gets placed by _protect_imported_positions.

The deeper fix (size-aware sync that prevents the orphan from existing)
is Option B and deferred until we have warehouse data showing whether
this matters beyond the cosmetic noise.
"""
from __future__ import annotations

from pathlib import Path

import pytest

# ─── Tracker has _recent_closes and helpers ───────────────────────────


def test_position_tracker_has_recent_closes_attr(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    Path("data").mkdir(exist_ok=True)
    from core.position_tracker import PositionTracker
    t = PositionTracker()
    assert hasattr(t, "_recent_closes")
    assert isinstance(t._recent_closes, list)
    assert t._recent_closes == []


def test_record_recent_close_appends_dict(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    Path("data").mkdir(exist_ok=True)
    from core.position_tracker import Position, PositionTracker
    t = PositionTracker()
    p = Position(
        id="t1", exchange="bybit", symbol="ETH/USDT:USDT", side="buy",
        market_type="futures", strategy="claude_portfolio",
        entry_price=2368.0, size=0.01, stop_loss=2300, take_profit=2480,
    )
    t._record_recent_close(p)
    assert len(t._recent_closes) == 1
    r = t._recent_closes[0]
    assert r["exchange"] == "bybit"
    assert r["symbol"] == "ETH/USDT:USDT"
    assert r["side"] == "buy"
    assert r["size"] == pytest.approx(0.01)
    assert r["entry_price"] == pytest.approx(2368.0)
    assert r["close_time"] > 0


def test_record_recent_close_prunes_old_entries(tmp_path, monkeypatch):
    """Entries older than 5 min are dropped on each record call."""
    monkeypatch.chdir(tmp_path)
    Path("data").mkdir(exist_ok=True)
    import time as _t

    from core.position_tracker import Position, PositionTracker
    t = PositionTracker()
    # Inject an old entry directly
    t._recent_closes.append({
        "exchange": "bybit", "symbol": "OLD/USDT:USDT", "side": "buy",
        "size": 0.01, "entry_price": 100.0,
        "close_time": _t.time() - 600,  # 10 min ago
    })
    p = Position(
        id="t1", exchange="bybit", symbol="ETH/USDT:USDT", side="buy",
        market_type="futures", strategy="claude_portfolio",
        entry_price=2368.0, size=0.01, stop_loss=2300, take_profit=2480,
    )
    t._record_recent_close(p)
    # Old one pruned, new one kept
    syms = [r["symbol"] for r in t._recent_closes]
    assert "OLD/USDT:USDT" not in syms
    assert "ETH/USDT:USDT" in syms


# ─── Match logic ──────────────────────────────────────────────────────


def _make_tracker_with_close(tmp_path, monkeypatch, *,
                             exchange="bybit", symbol="ETH/USDT:USDT",
                             side="buy", size=0.01, entry=2368.43):
    monkeypatch.chdir(tmp_path)
    Path("data").mkdir(exist_ok=True)
    from core.position_tracker import Position, PositionTracker
    t = PositionTracker()
    p = Position(
        id="t1", exchange=exchange, symbol=symbol, side=side,
        market_type="futures", strategy="claude_portfolio",
        entry_price=entry, size=size, stop_loss=0, take_profit=0,
    )
    t._record_recent_close(p)
    return t


def test_match_recent_close_returns_match_on_exact(tmp_path, monkeypatch):
    t = _make_tracker_with_close(tmp_path, monkeypatch)
    m = t._match_recent_close("bybit", "ETH/USDT:USDT", "buy", 0.01, 2368.43)
    assert m is not None
    assert m["symbol"] == "ETH/USDT:USDT"


def test_match_recent_close_size_tolerance(tmp_path, monkeypatch):
    """Size within 0.5% should still match — accounts for Bybit avg-entry drift."""
    t = _make_tracker_with_close(tmp_path, monkeypatch, size=0.01000)
    # 0.5% larger
    m = t._match_recent_close("bybit", "ETH/USDT:USDT", "buy", 0.01005, 2368.43)
    assert m is not None
    # >0.5% larger should miss
    m2 = t._match_recent_close("bybit", "ETH/USDT:USDT", "buy", 0.01100, 2368.43)
    assert m2 is None


def test_match_recent_close_entry_tolerance(tmp_path, monkeypatch):
    """Entry within 1% should still match — Bybit may report avg vs fill price."""
    t = _make_tracker_with_close(tmp_path, monkeypatch, entry=2368.43)
    m = t._match_recent_close("bybit", "ETH/USDT:USDT", "buy", 0.01, 2367.85)
    assert m is not None  # ~0.025% diff, well within 1%
    m2 = t._match_recent_close("bybit", "ETH/USDT:USDT", "buy", 0.01, 2400.00)
    assert m2 is None  # >1% diff


def test_match_recent_close_different_side_no_match(tmp_path, monkeypatch):
    t = _make_tracker_with_close(tmp_path, monkeypatch, side="buy")
    m = t._match_recent_close("bybit", "ETH/USDT:USDT", "sell", 0.01, 2368.43)
    assert m is None


def test_match_recent_close_different_exchange_no_match(tmp_path, monkeypatch):
    t = _make_tracker_with_close(tmp_path, monkeypatch, exchange="bybit")
    m = t._match_recent_close("binance", "ETH/USDT:USDT", "buy", 0.01, 2368.43)
    assert m is None


def test_match_recent_close_empty_returns_none(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    Path("data").mkdir(exist_ok=True)
    from core.position_tracker import PositionTracker
    t = PositionTracker()
    m = t._match_recent_close("bybit", "ETH/USDT:USDT", "buy", 0.01, 2368.43)
    assert m is None


# ─── close() wires the recorder ───────────────────────────────────────


def test_close_appends_to_recent_closes(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    Path("data").mkdir(exist_ok=True)
    from core.position_tracker import Position, PositionTracker
    t = PositionTracker()
    p = Position(
        id="t-eth", exchange="bybit", symbol="ETH/USDT:USDT", side="buy",
        market_type="futures", strategy="claude_portfolio",
        entry_price=2368.43, size=0.01, stop_loss=2300, take_profit=2480,
    )
    t.add(p)
    assert len(t._recent_closes) == 0
    t.close("t-eth", 2360.0, "AGE_LOSS")
    assert len(t._recent_closes) == 1
    assert t._recent_closes[0]["symbol"] == "ETH/USDT:USDT"


# ─── Source-level invariants on sync_with_exchanges ───────────────────


def test_sync_uses_match_recent_close():
    src = Path("core/position_tracker.py").read_text(encoding="utf-8")
    sync_idx = src.index("def sync_with_exchanges")
    next_def = src.index("\n    def ", sync_idx + 10)
    sync_block = src[sync_idx:next_def]
    assert "_match_recent_close" in sync_block, \
        "sync must call _match_recent_close to detect reconcile imports"


def test_sync_emits_RECONCILE_prefix_when_match():
    src = Path("core/position_tracker.py").read_text(encoding="utf-8")
    assert 'pid = f"RECONCILE-' in src
    assert 'pid = f"MANUAL-' in src


def test_sync_uses_info_for_reconcile_warning_for_manual():
    """RECONCILE = info-level, MANUAL = warning-level."""
    src = Path("core/position_tracker.py").read_text(encoding="utf-8")
    sync_idx = src.index("def sync_with_exchanges")
    next_def = src.index("\n    def ", sync_idx + 10)
    sync_block = src[sync_idx:next_def]
    assert "RECONCILED leftover position" in sync_block
    assert "IMPORTED manual position" in sync_block
    # The two log levels must both be present in the same path
    assert "logger.warning(_msg)" in sync_block
    assert "logger.info(_msg)" in sync_block


def test_sync_assigns_strategy_reconcile_when_match():
    src = Path("core/position_tracker.py").read_text(encoding="utf-8")
    assert 'strat = "reconcile"' in src
    assert 'strat = "manual"' in src


# ─── Dashboard filter excludes RECONCILE rows ─────────────────────────


def test_dashboard_filter_excludes_reconcile_id_prefix():
    from dashboard import _is_bot_trade
    row = {
        "id": "RECONCILE-bybit-ETH/USDT:USDT-buy-1234567890",
        "strategy": "reconcile", "close_reason": "trailing_stop",
    }
    assert _is_bot_trade(row) is False


def test_dashboard_filter_excludes_reconcile_strategy():
    from dashboard import _is_bot_trade
    row = {
        "id": "some-other-id",
        "strategy": "reconcile", "close_reason": "trailing_stop",
    }
    assert _is_bot_trade(row) is False


def test_dashboard_filter_keeps_normal_bot_trade():
    """Sanity — claude_portfolio rows still count."""
    from dashboard import _is_bot_trade
    row = {
        "id": "regular-bot-id-123",
        "strategy": "claude_portfolio", "close_reason": "trailing_stop",
    }
    assert _is_bot_trade(row) is True


def test_dashboard_filter_keeps_manual_filter_intact():
    """Phase 21 invariant — MANUAL- prefix still filtered."""
    from dashboard import _is_bot_trade
    row = {
        "id": "MANUAL-binance-X-buy-1",
        "strategy": "claude_portfolio", "close_reason": "trailing_stop",
    }
    assert _is_bot_trade(row) is False
