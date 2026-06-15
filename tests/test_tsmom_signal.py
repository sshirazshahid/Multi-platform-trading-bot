"""Tests for core/tsmom_signal.py — the long-only TSMOM capital-preservation signal.

Contract + invariant coverage for the drop-in alternative to mcp_brain.analyze_portfolio
(behind config.SIGNAL_SOURCE=tsmom). The load-bearing invariant is LONG-ONLY: this signal
must NEVER emit a short, because shorting is the anti-pattern the research flagged and the
reason the current bot is edgeless. The other invariants (majors-only universe, daily
momentum gating, exit-on-flip, fresh decision_id, full action-dict shape) are also pinned.
"""
from __future__ import annotations

from core.tsmom_signal import (
    TSMOMSignal,
    is_tsmom_action,
    is_tsmom_position,
    tsmom_entry_shape,
)


class FakeExchange:
    """Minimal exchange stub: returns a synthetic OHLCV series per base symbol.

    Rising closes -> positive momentum, falling -> negative, short series -> skip.
    """

    def __init__(self, closes_by_base: dict):
        self._closes_by_base = closes_by_base

    def fetch_ohlcv(self, symbol: str, timeframe: str = "1d",
                    limit: int = 100, market_type: str = "spot") -> list:
        base = symbol.split("/")[0].split(":")[0].upper()
        closes = self._closes_by_base.get(base)
        if closes is None:
            return []
        rows = []
        ts = 1_600_000_000_000
        for i, c in enumerate(closes):
            rows.append([ts + i * 86_400_000, c, c * 1.01, c * 0.99, c, 1000.0])
        return rows[-limit:]


def _rising(n=80, start=100.0, step=1.0):
    return [start + step * i for i in range(n)]


def _falling(n=80, start=200.0, step=1.0):
    return [start - step * i for i in range(n)]


ENV = {
    "exchange_balances": {"binance": {"spot": 500.0, "futures": 500.0}},
    "risk_envelope": {"total_balance": 1000.0, "max_new_positions": 5, "daily_loss_pct": 0.0},
    "recent_trades": [],
}


def _signal(closes_by_base, universe=None):
    ex = FakeExchange(closes_by_base)
    return TSMOMSignal(exchanges={"binance": ex},
                       universe=universe or {"BTC", "ETH", "SOL", "BNB", "XRP"},
                       lookback_days=28)


# ── INVARIANT 1: long-only, never shorts ─────────────────────────────────────

def test_negative_momentum_flat_emits_no_short():
    sig = _signal({"BTC": _falling()})
    actions = sig.analyze_portfolio(coins=["BTC"], open_positions=[], **ENV)
    # Down-trending + no position => stay in cash. No OPEN at all, certainly no sell.
    assert all(a["type"] != "OPEN" for a in actions)
    assert not any(a.get("side") == "sell" and a["type"] == "OPEN" for a in actions)


def test_no_open_action_is_ever_a_short_across_mixed_universe():
    sig = _signal({"BTC": _rising(), "ETH": _falling(), "SOL": _rising()})
    actions = sig.analyze_portfolio(coins=["BTC", "ETH", "SOL"], open_positions=[], **ENV)
    opens = [a for a in actions if a["type"] == "OPEN"]
    assert opens, "rising majors should produce long entries"
    assert all(a["side"] == "buy" for a in opens), "TSMOM must be long-only"


# ── INVARIANT 2: positive momentum opens a long ──────────────────────────────

def test_positive_momentum_opens_long():
    sig = _signal({"ETH": _rising()})
    actions = sig.analyze_portfolio(coins=["ETH"], open_positions=[], **ENV)
    opens = [a for a in actions if a["type"] == "OPEN"]
    assert len(opens) == 1
    a = opens[0]
    assert a["side"] == "buy"
    assert a["leverage"] == 1            # unleveraged — capital preservation
    assert a["source"] == "tsmom"
    assert a["symbol"].startswith("ETH/")


# ── INVARIANT 3: universe restricted to majors ───────────────────────────────

def test_non_major_is_filtered_out():
    sig = _signal({"BTC": _rising(), "DOGE": _rising(), "SHIB": _rising()})
    actions = sig.analyze_portfolio(coins=["BTC", "DOGE", "SHIB"], open_positions=[], **ENV)
    bases = {a["symbol"].split("/")[0] for a in actions}
    assert "BTC" in bases
    assert "DOGE" not in bases and "SHIB" not in bases


# ── INVARIANT 4: exit-on-flip closes an existing long ────────────────────────

def test_negative_momentum_closes_existing_long():
    sig = _signal({"BTC": _falling()})
    pos = [{"id": "pos-1", "symbol": "BTC/USDT:USDT", "side": "buy",
            "exchange": "binance", "market_type": "futures"}]
    actions = sig.analyze_portfolio(coins=["BTC"], open_positions=pos, **ENV)
    closes = [a for a in actions if a["type"] == "CLOSE"]
    assert len(closes) == 1
    assert closes[0]["position_id"] == "pos-1"


def test_positive_momentum_with_existing_long_holds():
    sig = _signal({"BTC": _rising()})
    pos = [{"id": "pos-1", "symbol": "BTC/USDT:USDT", "side": "buy",
            "exchange": "binance", "market_type": "futures"}]
    actions = sig.analyze_portfolio(coins=["BTC"], open_positions=pos, **ENV)
    # Already long + still trending up => no churn.
    assert actions == []


# ── INVARIANT 5: provenance — fresh, unique decision_id ──────────────────────

def test_decision_ids_unique_and_nonempty():
    sig = _signal({"BTC": _rising(), "ETH": _rising(), "SOL": _rising()})
    actions = sig.analyze_portfolio(coins=["BTC", "ETH", "SOL"], open_positions=[], **ENV)
    ids = [a["decision_id"] for a in actions]
    assert all(ids) and len(ids) == len(set(ids))


# ── INVARIANT 6: full action-dict contract (drop-in compatibility) ───────────

REQUIRED_KEYS = {"type", "symbol", "exchange", "market_type", "side", "leverage",
                 "size_pct", "sl_pct", "tp_pct", "confidence", "reason",
                 "position_id", "decision_id", "source"}


def test_open_action_has_full_contract():
    sig = _signal({"SOL": _rising()})
    actions = sig.analyze_portfolio(coins=["SOL"], open_positions=[], **ENV)
    a = next(x for x in actions if x["type"] == "OPEN")
    assert REQUIRED_KEYS.issubset(a.keys())


def test_close_action_has_full_contract():
    sig = _signal({"BTC": _falling()})
    pos = [{"id": "p", "symbol": "BTC/USDT:USDT", "side": "buy",
            "exchange": "binance", "market_type": "futures"}]
    a = next(x for x in sig.analyze_portfolio(coins=["BTC"], open_positions=pos, **ENV)
             if x["type"] == "CLOSE")
    assert REQUIRED_KEYS.issubset(a.keys())


# ── INVARIANT 7: robustness — insufficient data is skipped, not crashed ──────

def test_insufficient_data_skipped():
    sig = _signal({"BTC": _rising(n=10)})   # < lookback+warmup
    actions = sig.analyze_portfolio(coins=["BTC"], open_positions=[], **ENV)
    assert actions == []


def test_unknown_symbol_no_data_skipped():
    sig = _signal({})   # exchange returns [] for everything
    actions = sig.analyze_portfolio(coins=["BTC", "ETH"], open_positions=[], **ENV)
    assert actions == []


# ── Phase 2b helpers: identity + entry shape ─────────────────────────────────

def test_is_tsmom_action():
    assert is_tsmom_action({"source": "tsmom"})
    assert is_tsmom_action({"source": "TSMOM"})
    assert not is_tsmom_action({"source": "claude"})
    assert not is_tsmom_action({"source": "algo"})
    assert not is_tsmom_action({})
    assert not is_tsmom_action(None)


class _FakePos:
    def __init__(self, strategy):
        self.strategy = strategy


def test_is_tsmom_position():
    assert is_tsmom_position(_FakePos("tsmom"))
    assert is_tsmom_position(_FakePos("TSMOM"))
    assert not is_tsmom_position(_FakePos("claude_portfolio"))
    assert not is_tsmom_position(_FakePos("systematic_v3_1"))
    assert not is_tsmom_position(_FakePos(None))
    assert not is_tsmom_position(object())   # no strategy attr at all


def test_tsmom_entry_shape_honours_signal_sl():
    assert tsmom_entry_shape({"sl_pct": 8.0}) == (8.0, 0.0, 1, True)


def test_tsmom_entry_shape_missing_sl_uses_default():
    assert tsmom_entry_shape({}) == (8.0, 0.0, 1, True)


def test_tsmom_entry_shape_custom_sl():
    sl, tp, lev, bypass = tsmom_entry_shape({"sl_pct": 12.0})
    assert sl == 12.0 and tp == 0.0 and lev == 1 and bypass is True


def test_tsmom_entry_shape_never_has_take_profit_or_leverage():
    # Cardinal contract: no TP (exit is the momentum flip) and unleveraged.
    for action in ({"sl_pct": 8.0}, {"sl_pct": 5.0}, {}):
        _sl, tp, lev, _b = tsmom_entry_shape(action)
        assert tp == 0.0
        assert lev == 1


# ── observability: watch_summary explains the quiet (downtrend) state ─────────

def test_watch_summary_empty_before_run():
    sig = _signal({"BTC": _rising()})
    assert sig.watch_summary() == "no majors evaluated yet"


def test_watch_summary_reports_downtrend_holds():
    sig = _signal({"BTC": _falling(), "ETH": _falling()})
    sig.analyze_portfolio(coins=["BTC", "ETH"], open_positions=[], **ENV)
    s = sig.watch_summary()
    assert "0/2 majors in 28d uptrend" in s
    assert "[flat]" in s


def test_watch_summary_reports_uptrend_open():
    sig = _signal({"BTC": _rising()})
    sig.analyze_portfolio(coins=["BTC"], open_positions=[], **ENV)
    s = sig.watch_summary()
    assert "1/1 majors in 28d uptrend" in s
    assert "[OPEN]" in s


def test_last_status_records_momentum_and_state():
    sig = _signal({"BTC": _falling()})
    sig.analyze_portfolio(coins=["BTC"], open_positions=[], **ENV)
    assert sig.last_status and sig.last_status[0]["base"] == "BTC"
    assert sig.last_status[0]["mom_pct"] < 0      # falling -> negative 28d momentum
    assert sig.last_status[0]["state"] == "flat"   # negative + no position = stay in cash
