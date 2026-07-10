"""PAPER↔live execution-parity invariants for SimExecutionModel.

`core/sim_execution.py` exists because paper trading drifted *optimistic* from
live (filled ~5bps better, ignored intrabar SL wicks, skipped funding), turning
profitable paper into live losses. These tests lock in the SAFETY invariant that
paper is NEVER optimistic vs live, so a PAPER→live transition cannot silently
improve fills. Pure unit tests with fake exchange objects — no network.
"""

from __future__ import annotations

from core.sim_execution import SimExecutionModel


class _FakeTickerExchange:
    def __init__(self, bid, ask, last):
        self._t = {"bid": bid, "ask": ask, "last": last}

    def fetch_ticker(self, symbol, market_type=None):
        return self._t


class _FakeCandleExchange:
    def __init__(self, high, low):
        # two candles [ts, open, high, low, close, vol]; the last is used
        self._c = [[0, 100, high, low, 100, 0], [1, 100, high, low, 100, 0]]

    def fetch_ohlcv(self, symbol, timeframe, limit=2, market_type=None):
        return self._c


def _model():
    """Deterministic model independent of config SLIPPAGE values."""
    m = SimExecutionModel()
    m.enabled = True
    m.prefer_book = True
    m.wick_enable = True
    m.funding_on = True
    m.open_slip = 0.0005
    m.close_slip = 0.0005
    m.sl_slip = 0.0010
    return m


# ── fill-price conservatism: paper is never better than mid ──


def test_buy_fill_never_below_last():
    """A paper BUY must fill at/above mid (crosses to ask + slippage)."""
    ex = _FakeTickerExchange(bid=99.0, ask=101.0, last=100.0)
    fill = _model().paper_fill_price(ex, "X/USDT:USDT", "buy", "futures", phase="open", size=1.0)
    assert fill >= 100.0


def test_sell_fill_never_above_last():
    """A paper SELL must fill at/below mid (crosses to bid - slippage)."""
    ex = _FakeTickerExchange(bid=99.0, ask=101.0, last=100.0)
    fill = _model().paper_fill_price(ex, "X/USDT:USDT", "sell", "futures", phase="open", size=1.0)
    assert fill <= 100.0


def test_stop_phase_slippage_is_wider_than_open():
    """Stop fills are pessimistically worse than open fills (wider slippage)."""
    ex = _FakeTickerExchange(bid=99.0, ask=101.0, last=100.0)
    m = _model()
    open_fill = m.paper_fill_price(ex, "X", "buy", "futures", phase="open", size=1.0)
    stop_fill = m.paper_fill_price(ex, "X", "buy", "futures", phase="stop", size=1.0)
    assert stop_fill >= open_fill


# ── wick SL/TP: pessimistic — SL fires before TP when both wick same candle ──


def test_both_wicked_returns_stop_loss_first_long():
    ex = _FakeCandleExchange(high=110.0, low=90.0)  # wicks both SL(95) and TP(105)
    res, px = _model().check_wick_trigger(ex, "X", "futures", "buy", sl=95.0, tp=105.0)
    assert res == "stop_loss" and px == 95.0


def test_only_tp_wicked_returns_take_profit_long():
    ex = _FakeCandleExchange(high=110.0, low=99.0)  # low above SL, high crosses TP
    res, px = _model().check_wick_trigger(ex, "X", "futures", "buy", sl=95.0, tp=105.0)
    assert res == "take_profit" and px == 105.0


def test_both_wicked_returns_stop_loss_first_short():
    ex = _FakeCandleExchange(high=110.0, low=90.0)  # short: SL=105 above, TP=95 below
    res, px = _model().check_wick_trigger(ex, "X", "futures", "sell", sl=105.0, tp=95.0)
    assert res == "stop_loss" and px == 105.0


# ── wick trigger must ignore bars that pre-date the position (trade 2539) ──


class _FakeTimedCandleExchange:
    def __init__(self, candles):
        self._c = candles

    def fetch_ohlcv(self, symbol, timeframe, limit=3, market_type=None):
        return self._c


def test_wick_ignores_candle_that_opened_before_entry():
    """Trade-2539 regression: a closed 1m bar whose range predates the entry
    must not fire TP — it produced a 'take_profit' fill BELOW entry."""
    t0 = 1_000_000.0
    ex = _FakeTimedCandleExchange([[t0 * 1000, 100, 110.0, 99.0, 100, 0]])
    res, px = _model().check_wick_trigger(
        ex, "X", "futures", "buy", sl=95.0, tp=105.0, now=t0 + 120, entry_ts=t0 + 90
    )  # entry AFTER that bar closed
    assert (res, px) == (None, None)


def test_wick_still_fires_on_post_entry_candle():
    t0 = 1_000_000.0
    ex = _FakeTimedCandleExchange([[(t0 + 60) * 1000, 100, 110.0, 99.0, 100, 0]])
    res, px = _model().check_wick_trigger(
        ex, "X", "futures", "buy", sl=95.0, tp=105.0, now=t0 + 180, entry_ts=t0 + 30
    )  # bar opened after entry
    assert res == "take_profit" and px == 105.0


# ── funding direction: long pays positive rate, short receives ──


def test_funding_long_pays_positive_rate():
    pos = type("P", (), {"size": 10.0, "side": "buy", "market_type": "futures"})()
    assert _model().funding_payment(pos, mark_price=100.0, funding_rate=0.0001) > 0


def test_funding_short_receives_positive_rate():
    pos = type("P", (), {"size": 10.0, "side": "sell", "market_type": "futures"})()
    assert _model().funding_payment(pos, mark_price=100.0, funding_rate=0.0001) < 0
