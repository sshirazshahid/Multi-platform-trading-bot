"""MAKER-FIRST PAPER ENTRIES (2026-07-10).

Fees are 25.4% of the last-500-trade loss; on the no-edge directional lane
cost engineering is the only honest expectancy lever. When
MAKER_FIRST_PAPER["enabled"] is on, PAPER futures entries on the
mcp/algorithmic lane place a VIRTUAL post-only limit at the touch (bid for
buys / ask for sells) instead of an immediate taker fill.

HONEST-FILL RULE under test: the resting limit fills as maker ONLY when the
market trades strictly THROUGH the price (ticker last, or a post-intent
closed 1m bar extreme, strictly beyond the limit). Touch-at-price does NOT
fill. Timeout -> taker fallback at the CURRENT price; runaway market ->
abandoned (reject_reason maker_chase_abandoned). SL/TP are recomputed off
the ACTUAL fill price with the original percentages so the ACCURACY band
geometry is preserved. Maker fills book the venue maker fee. Pending intents
from a dead process are cancelled at boot, never ghost-opened.

Flag OFF (default) must be byte-identical to today's immediate taker fill.

Harness mirrors tests/test_accuracy_band_time_exit.py (mock collaborators,
scripted-ticker fake exchange; tests/conftest.py isolates the warehouse).
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from core.order_manager import OrderManager

SYM = "SOL/USDT:USDT"


class FakeExchange:
    """Minimal scripted-ticker exchange double."""

    def __init__(self, name="Bybit", ticker=None):
        self.name = name
        self.ticker = dict(ticker or {})
        self.candles = []

    def fetch_ticker(self, symbol, market_type=None):
        return dict(self.ticker)

    def fetch_ohlcv(self, symbol, timeframe, limit=3, market_type=None):
        return list(self.candles)

    def get_min_order_size(self, symbol):
        return 0.0

    def round_quantity(self, symbol, size, market_type=None):
        return size

    def round_price(self, symbol, price, market_type=None):
        return price


@pytest.fixture
def mf_on(monkeypatch):
    import config
    monkeypatch.setattr(
        config, "MAKER_FIRST_PAPER",
        {"enabled": True, "timeout_sec": 45, "max_chase": 1},
        raising=False)


@pytest.fixture
def mf_off(monkeypatch):
    import config
    monkeypatch.setattr(
        config, "MAKER_FIRST_PAPER",
        {"enabled": False, "timeout_sec": 45, "max_chase": 1},
        raising=False)


def _om(tmp_path):
    om = OrderManager(tracker=MagicMock(), risk=MagicMock(),
                      notifier=MagicMock())
    om.dry_run = True
    om.mcp_brain = None
    om.blacklist = MagicMock()
    om.blacklist.is_blacklisted.return_value = False
    om.risk = MagicMock()
    om.risk.can_trade.return_value = True
    om.kelly = MagicMock()
    om.kelly.should_block_trade.return_value = (False, "")
    om.wallet = MagicMock()
    om.wallet.on_open.return_value = True
    om.compliance = MagicMock()
    om.notifier = MagicMock()
    om.tracker = MagicMock()
    om.tracker.count_open.return_value = 0
    om.tracker.get_open.return_value = []
    om.accrue_paper_funding = MagicMock()
    om._check_price_band = lambda *a, **k: True
    om._pending_maker_path = tmp_path / "pending_maker_entries.json"
    return om


def _ticker():
    return {"last": 100.0, "bid": 99.9, "ask": 100.1}


def _open(om, ex, side="buy", market_type="futures", symbol=SYM,
          strategy="claude_portfolio", sl=None, tp=None):
    if sl is None:
        sl = 98.0 if side == "buy" else 102.0
    if tp is None:
        tp = 101.0 if side == "buy" else 99.0
    return om.open_position(
        ex, symbol, side, market_type, strategy,
        size=1.0, sl=sl, tp=tp, leverage=2, price=100.0)


def _added_pos(om):
    assert om.tracker.add.called, "no position was registered"
    return om.tracker.add.call_args.args[0]


# ── flag OFF: byte-identical immediate taker fill ────────────────────────────

def test_flag_off_fills_immediately_as_today(mf_off, tmp_path):
    om = _om(tmp_path)
    ex = FakeExchange(ticker=_ticker())
    pos = _open(om, ex)
    assert pos is not None, "flag off must open the position immediately"
    # Taker realism: a buy crosses the book — pays >= ask.
    assert pos.entry_price >= 100.1
    assert om._pending_maker == {}
    assert not om._pending_maker_path.exists(), \
        "flag off must never touch the pending-state file"


def test_config_defaults_off():
    import os

    import config
    cfg = config.MAKER_FIRST_PAPER
    if os.getenv("MAKER_FIRST_PAPER_ENABLED") is None:
        assert cfg["enabled"] is False
    if os.getenv("MAKER_FIRST_PAPER_TIMEOUT_SEC") is None:
        assert cfg["timeout_sec"] == 45
    assert "max_chase" in cfg


# ── enabled: virtual post-only limit, position NOT opened yet ────────────────

def test_enabled_registers_pending_no_position(mf_on, tmp_path):
    om = _om(tmp_path)
    ex = FakeExchange(ticker=_ticker())
    pos = _open(om, ex)
    assert pos is None
    assert om.last_open_reject == "maker_first_pending"
    assert not om.tracker.add.called
    assert not om.wallet.on_open.called
    key = f"{ex.name}:{SYM}"
    assert key in om._pending_maker
    intent = om._pending_maker[key]
    assert intent["limit_px"] == pytest.approx(99.9)  # best bid for a buy
    # Restart safety: the intent is persisted.
    state = json.loads(om._pending_maker_path.read_text(encoding="utf-8"))
    assert key in state["pending"]


# ── honest fill resolution ───────────────────────────────────────────────────

def test_touch_at_price_does_not_fill(mf_on, tmp_path):
    om = _om(tmp_path)
    ex = FakeExchange(ticker=_ticker())
    _open(om, ex)
    ex.ticker = {"last": 99.9, "bid": 99.85, "ask": 99.95}  # touch, no through
    om._resolve_pending_maker_entries(ex)
    assert not om.tracker.add.called, \
        "touch-at-price must NOT fill — only a strict trade-through does"
    assert f"{ex.name}:{SYM}" in om._pending_maker


def test_strict_trade_through_fills_as_maker(mf_on, tmp_path):
    om = _om(tmp_path)
    ex = FakeExchange(ticker=_ticker())
    _open(om, ex)
    ex.ticker = {"last": 99.85, "bid": 99.8, "ask": 99.9}  # sold through 99.9
    om._resolve_pending_maker_entries(ex)
    pos = _added_pos(om)
    # Fill AT the resting limit exactly — no slippage; that IS the maker price.
    assert pos.entry_price == pytest.approx(99.9)
    # Maker fee booked (Bybit futures maker 0.0001), NOT taker (0.0006).
    assert pos.entry_fee == pytest.approx(1.0 * 99.9 * 0.0001)
    # SL/TP recomputed off the ACTUAL fill with the original pcts
    # (sl 2% / tp 1% of signal px 100 -> band geometry ratio preserved).
    assert pos.stop_loss == pytest.approx(99.9 * 0.98)
    assert pos.take_profit == pytest.approx(99.9 * 1.01)
    assert om._pending_maker == {}
    assert om._maker_counters["maker"] == 1


def test_wick_trade_through_fills(mf_on, tmp_path):
    om = _om(tmp_path)
    ex = FakeExchange(ticker=_ticker())
    _open(om, ex)
    key = f"{ex.name}:{SYM}"
    om._pending_maker[key]["created_ts"] = time.time() - 120
    ex.ticker = {"last": 99.95, "bid": 99.9, "ask": 100.0}  # last never below
    # Closed 1m bar OPENED AFTER the intent whose low printed through 99.9.
    ex.candles = [[(time.time() - 90) * 1000.0, 99.95, 99.97, 99.8, 99.9, 1.0]]
    om._resolve_pending_maker_entries(ex)
    pos = _added_pos(om)
    assert pos.entry_price == pytest.approx(99.9)


def test_pre_intent_bar_does_not_fill(mf_on, tmp_path):
    om = _om(tmp_path)
    ex = FakeExchange(ticker=_ticker())
    _open(om, ex)
    ex.ticker = {"last": 99.95, "bid": 99.9, "ask": 100.0}
    # Bar opened BEFORE the intent — its wick predates the resting limit.
    ex.candles = [[(time.time() - 90) * 1000.0, 99.95, 99.97, 99.8, 99.9, 1.0]]
    om._resolve_pending_maker_entries(ex)
    assert not om.tracker.add.called


def test_sell_side_mirror(mf_on, tmp_path):
    om = _om(tmp_path)
    ex = FakeExchange(ticker=_ticker())
    _open(om, ex, side="sell")
    key = f"{ex.name}:{SYM}"
    assert om._pending_maker[key]["limit_px"] == pytest.approx(100.1)  # ask
    ex.ticker = {"last": 100.15, "bid": 100.1, "ask": 100.2}  # bought through
    om._resolve_pending_maker_entries(ex)
    pos = _added_pos(om)
    assert pos.entry_price == pytest.approx(100.1)
    assert pos.stop_loss == pytest.approx(100.1 * 1.02)
    assert pos.take_profit == pytest.approx(100.1 * 0.99)


# ── timeout / runaway ────────────────────────────────────────────────────────

def test_timeout_falls_back_to_taker_at_current_price(mf_on, tmp_path):
    om = _om(tmp_path)
    ex = FakeExchange(ticker=_ticker())
    _open(om, ex)
    key = f"{ex.name}:{SYM}"
    om._pending_maker[key]["created_ts"] = time.time() - 46
    om._resolve_pending_maker_entries(ex)  # price never traded through
    pos = _added_pos(om)
    # Current taker fill: crosses the book + slippage, >= ask.
    assert pos.entry_price >= 100.1
    # Taker fee (generic futures taker), not maker.
    assert pos.entry_fee == pytest.approx(1.0 * pos.entry_price * 0.0005)
    # SL/TP recomputed off the ACTUAL fallback fill — geometry preserved:
    # tp-dist / sl-dist stays 1% / 2% = 0.5.
    e = pos.entry_price
    assert pos.stop_loss == pytest.approx(e * 0.98)
    assert pos.take_profit == pytest.approx(e * 1.01)
    assert (pos.take_profit - e) / (e - pos.stop_loss) == pytest.approx(0.5)
    assert om._pending_maker == {}
    assert om._maker_counters["taker_fallback"] == 1


def test_runaway_market_abandons_chase(mf_on, tmp_path):
    om = _om(tmp_path)
    ex = FakeExchange(ticker=_ticker())
    _open(om, ex)
    key = f"{ex.name}:{SYM}"
    om._pending_maker[key]["created_ts"] = time.time() - 46
    # Price ran > 0.3% beyond the original signal price (100 -> ask 100.5).
    ex.ticker = {"last": 100.45, "bid": 100.4, "ask": 100.5}
    om._resolve_pending_maker_entries(ex)
    assert not om.tracker.add.called, "a moved market must NOT be chased"
    assert om._pending_maker == {}
    assert om._maker_counters["abandoned"] == 1
    assert om.last_open_reject == "maker_chase_abandoned"


# ── restart cleanliness ──────────────────────────────────────────────────────

def test_restart_cancels_stale_pending_no_ghost(mf_on, tmp_path):
    path = tmp_path / "pending_maker_entries.json"
    stale = {
        "pending": {f"Bybit:{SYM}": {
            "exchange": "Bybit", "symbol": SYM, "side": "buy",
            "market_type": "futures", "strategy": "claude_portfolio",
            "size": 1.0, "leverage": 2, "limit_px": 99.9, "signal_px": 100.0,
            "sl_pct": 0.02, "tp_pct": 0.01, "created_ts": time.time() - 5,
        }},
        "counters": {"maker": 3, "taker_fallback": 1, "abandoned": 0},
    }
    path.write_text(json.dumps(stale), encoding="utf-8")
    om = _om(tmp_path)  # fresh process
    ex = FakeExchange(ticker={"last": 99.5, "bid": 99.4, "ask": 99.6})
    om._resolve_pending_maker_entries(ex)  # would trade through if ghosted
    assert not om.tracker.add.called, \
        "a dead process's pending intent must NEVER ghost-open a position"
    assert om._pending_maker == {}
    state = json.loads(path.read_text(encoding="utf-8"))
    assert state["pending"] == {}
    assert state["counters"]["maker"] == 3  # counters survive the restart


# ── monitor-cadence wiring ───────────────────────────────────────────────────

def test_check_sl_tp_resolves_pending(mf_on, tmp_path):
    om = _om(tmp_path)
    ex = FakeExchange(ticker=_ticker())
    _open(om, ex)
    ex.ticker = {"last": 99.85, "bid": 99.8, "ask": 99.9}
    om.check_sl_tp(ex, "futures")
    assert om.tracker.add.called, \
        "check_sl_tp (the monitor tick) must resolve pending maker entries"


# ── scope: PAPER futures mcp/algorithmic lane only ───────────────────────────

def test_tsmom_lane_not_intercepted(mf_on, tmp_path):
    om = _om(tmp_path)
    ex = FakeExchange(ticker=_ticker())
    pos = _open(om, ex, strategy="tsmom_longonly")
    assert pos is not None
    assert om._pending_maker == {}


def test_spot_not_intercepted(mf_on, tmp_path):
    om = _om(tmp_path)
    ex = FakeExchange(ticker=_ticker())
    pos = _open(om, ex, market_type="spot", symbol="SOL/USDT")
    assert pos is not None
    assert om._pending_maker == {}


def test_duplicate_intent_rejected_not_stacked(mf_on, tmp_path):
    om = _om(tmp_path)
    ex = FakeExchange(ticker=_ticker())
    _open(om, ex)
    pos = _open(om, ex)  # same symbol pitched again while pending
    assert pos is None
    assert om.last_open_reject == "maker_first_pending"
    assert len(om._pending_maker) == 1


# ── source pins ──────────────────────────────────────────────────────────────

def test_config_exposes_env_knobs():
    src = Path("config.py").read_text(encoding="utf-8")
    assert "MAKER_FIRST_PAPER_ENABLED" in src
    assert "MAKER_FIRST_PAPER_TIMEOUT_SEC" in src


def test_measurement_log_present():
    src = Path("core/order_manager.py").read_text(encoding="utf-8")
    assert "filled as MAKER" in src
    assert "chase abandoned" in src
