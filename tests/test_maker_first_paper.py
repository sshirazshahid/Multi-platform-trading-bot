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

from tests.bot_engine_source import bot_engine_source_for_grep
from tests.config_source import config_source_for_grep
from tests.order_manager_source import order_manager_impl_source

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


@pytest.fixture
def maker_only_on(monkeypatch):
    """Live governs entries via MAKER_ONLY (skip on timeout, 120s window).
    Paper must MIRROR that: skip on timeout, no taker fallback (2026-08-19)."""
    import config
    monkeypatch.setattr(
        config, "MAKER_ONLY", {"enabled": True, "max_wait_sec": 120},
        raising=False)


@pytest.fixture
def maker_only_off(monkeypatch):
    """Legacy path: no live maker-only, so paper keeps the 45s taker fallback."""
    import config
    monkeypatch.setattr(
        config, "MAKER_ONLY", {"enabled": False, "max_wait_sec": 120},
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
    om.portfolio_equity_provider = lambda: 10_000.0
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

def test_timeout_falls_back_to_taker_at_current_price(mf_on, maker_only_off, tmp_path):
    # Legacy path (no live maker-only): 45s timeout -> taker fallback.
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


def test_runaway_market_abandons_chase(mf_on, maker_only_off, tmp_path):
    # Runaway guard protects the TAKER-FALLBACK path (legacy). Under
    # maker-only, timeout skips anyway, so this is a maker_only_off scenario.
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


# ── align paper with live: MAKER_ONLY -> skip on timeout, no taker fallback ──
# 2026-08-19. Live governs entries via MAKER_ONLY (core/smart_executor.py:
# 120s wait, then SKIP with no market fallback). Paper's 45s -> taker fallback
# meant ~50% of paper entries (measured 171/344) were trades a live maker-only
# run would NEVER have taken — paper P&L drawn from a different population than
# live. Paper now MIRRORS live when MAKER_ONLY is enabled.

def test_maker_only_skips_on_timeout_no_taker_fallback(mf_on, maker_only_on, tmp_path):
    om = _om(tmp_path)
    ex = FakeExchange(ticker=_ticker())
    _open(om, ex)
    key = f"{ex.name}:{SYM}"
    om._pending_maker[key]["created_ts"] = time.time() - 121  # past the 120s window
    om._resolve_pending_maker_entries(ex)  # never traded through, not runaway
    assert not om.tracker.add.called, (
        "maker-only timeout must SKIP like live, not open a taker fallback")
    assert om._pending_maker == {}
    assert om._maker_counters["taker_fallback"] == 0
    assert om._maker_counters.get("maker_only_skip") == 1
    assert om.last_open_reject == "maker_only_skip"


def test_maker_only_uses_the_120s_live_window_not_45s(mf_on, maker_only_on, tmp_path):
    """At 60s a maker-only intent is STILL RESTING (live waits 120s), where
    the legacy 45s path would already have resolved."""
    om = _om(tmp_path)
    ex = FakeExchange(ticker=_ticker())
    _open(om, ex)
    key = f"{ex.name}:{SYM}"
    om._pending_maker[key]["created_ts"] = time.time() - 60  # past 45, under 120
    om._resolve_pending_maker_entries(ex)
    assert key in om._pending_maker, "must still rest inside the 120s live window"
    assert not om.tracker.add.called


def test_maker_only_off_preserves_legacy_taker_fallback(mf_on, maker_only_off, tmp_path):
    """Backward-compat: with no live maker-only, 45s timeout still taker-fills."""
    om = _om(tmp_path)
    ex = FakeExchange(ticker=_ticker())
    _open(om, ex)
    key = f"{ex.name}:{SYM}"
    om._pending_maker[key]["created_ts"] = time.time() - 46
    om._resolve_pending_maker_entries(ex)
    assert _added_pos(om).entry_price >= 100.1  # taker fill happened
    assert om._maker_counters["taker_fallback"] == 1


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
    src = config_source_for_grep()
    assert "MAKER_FIRST_PAPER_ENABLED" in src
    assert "MAKER_FIRST_PAPER_TIMEOUT_SEC" in src


def test_measurement_log_present():
    src = order_manager_impl_source()
    assert "filled as MAKER" in src
    assert "chase abandoned" in src


# ── 2026-07-11: ccxt binanceusdm tickers carry bid/ask=None ──────────────────
# The silent no-book fall-through fired on EVERY entry — the feature was a
# no-op on day one (zero MakerFirst log lines, no state file, 3 taker fills).
# When the ticker lacks bid/ask, the intercept must pull the order-book top.
def test_intercept_uses_book_top_when_ticker_lacks_bid_ask(mf_on, tmp_path):
    om = _om(tmp_path)
    ex = FakeExchange(ticker={"last": 100.0, "bid": None, "ask": None})
    ex.fetch_order_book = lambda symbol, limit=5, market_type=None: {
        "bids": [[99.9, 5.0]], "asks": [[100.1, 5.0]]}
    pos = _open(om, ex, side="buy")
    assert pos is None, "intent must register (pending), not fill as taker"
    assert om.last_open_reject == "maker_first_pending"
    key = f"{ex.name}:{SYM}"
    assert key in om._pending_maker
    assert om._pending_maker[key]["limit_px"] == 99.9  # book-top bid


def test_no_book_at_all_falls_through_to_taker(mf_on, tmp_path):
    om = _om(tmp_path)
    ex = FakeExchange(ticker={"last": 100.0, "bid": None, "ask": None})
    # no fetch_order_book on the double -> AttributeError inside the guarded
    # fetch -> honest taker fall-through (now LOUD, never silent).
    pos = _open(om, ex, side="buy")
    assert pos is not None, "no honest maker price -> normal taker entry"
    assert f"{ex.name}:{SYM}" not in om._pending_maker


# ── 2026-07-11: zero-open starvation deadlock ────────────────────────────────
# _check_all_sl_tp early-returned when count_open()==0 — but pending maker
# intents ARE entries without positions, so on an empty book the resolver
# never ran and intents hung forever past their 45s timeout (INJ/ARB lost).
def test_monitor_runs_with_zero_positions_when_intents_pending():
    from pathlib import Path

    src = bot_engine_source_for_grep()
    i = src.index("def _check_all_sl_tp")
    block = src[i : i + 900]
    assert "_pending_maker" in block, (
        "the zero-open early-return must be bypassed while maker intents "
        "are pending, or the resolver starves and entries are lost"
    )


# ── 2026-07-11: watchdog runtime net for the starvation class ────────────────
def test_watchdog_alerts_on_stale_maker_intent(tmp_path, monkeypatch):
    """A pending intent older than STALE_MAKER_INTENT_SEC must raise the
    stale_maker_intents WARN edge-alert (the runtime net that turns any
    future resolver-starvation bug into an email instead of lost entries)."""
    import core.health_watchdog as hw

    monkeypatch.chdir(tmp_path)
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "pending_maker_entries.json").write_text(json.dumps({
        "pending": {"Binance:INJ/USDT:USDT": {
            "created_ts": time.time() - 3600}},
        "counters": {"maker": 0, "taker_fallback": 0, "abandoned": 0},
        "fills": [],
    }), encoding="utf-8")
    wd = hw.HealthWatchdog(bot_engine=MagicMock(), notifier=MagicMock())
    alerts = []
    wd._edge_alert = lambda key, is_bad, level, msg, ctx=None, **kw: alerts.append(
        (key, is_bad))
    wd._check_stale_maker_intents()
    assert ("stale_maker_intents", True) in alerts


def test_watchdog_quiet_on_fresh_or_empty_pending(tmp_path, monkeypatch):
    import core.health_watchdog as hw

    monkeypatch.chdir(tmp_path)
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "pending_maker_entries.json").write_text(json.dumps({
        "pending": {"Binance:ARB/USDT:USDT": {"created_ts": time.time() - 30}},
        "counters": {}, "fills": [],
    }), encoding="utf-8")
    wd = hw.HealthWatchdog(bot_engine=MagicMock(), notifier=MagicMock())
    alerts = []
    wd._edge_alert = lambda key, is_bad, level, msg, ctx=None, **kw: alerts.append(
        (key, is_bad))
    wd._check_stale_maker_intents()
    assert ("stale_maker_intents", False) in alerts  # re-arm path, not bad
