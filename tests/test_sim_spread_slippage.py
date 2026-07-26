"""Spread-aware paper slippage (2026-06-28, deep-research execution finding).

A flat slippage constant understates fill cost in thin / high-volatility markets
where the book is wide. paper_fill_price now adds spread_slip_mult * observed
spread on top of the flat slip, so paper fills get more conservative exactly when
the live market is illiquid/stressed. mult=0 reproduces the prior flat behavior.
"""
from core.sim_execution import SimExecutionModel


class _FakeEx:
    def __init__(self, bid, ask, last):
        self._t = {"bid": bid, "ask": ask, "last": last}

    def fetch_ticker(self, symbol, market_type=None):
        return self._t


def _model(mult):
    m = SimExecutionModel()
    m.enabled = True
    m.prefer_book = True
    m.open_slip = 0.0005
    m.spread_slip_mult = mult
    return m


def test_wide_spread_costs_more_than_flat_on_buy():
    ex = _FakeEx(bid=99.0, ask=101.0, last=100.0)  # 2% spread
    flat = _model(0.0).paper_fill_price(ex, "X/USDT", "buy", "futures")
    wide = _model(0.5).paper_fill_price(ex, "X/USDT", "buy", "futures")
    assert wide > flat  # buy fills worse (higher) when the book is wide
    # spread_frac=0.02 -> extra slip = 0.5*0.02 = 0.01 on top of 0.0005
    assert wide == 101.0 * (1.0 + 0.0005 + 0.01)
    assert flat == 101.0 * (1.0 + 0.0005)


def test_wide_spread_costs_more_than_flat_on_sell():
    ex = _FakeEx(bid=99.0, ask=101.0, last=100.0)
    flat = _model(0.0).paper_fill_price(ex, "X/USDT", "sell", "futures")
    wide = _model(0.5).paper_fill_price(ex, "X/USDT", "sell", "futures")
    assert wide < flat  # sell fills worse (lower) when the book is wide
    assert wide == 99.0 * (1.0 - 0.0005 - 0.01)


def test_tight_spread_negligible_extra():
    ex = _FakeEx(bid=99.99, ask=100.01, last=100.0)  # ~2bps spread
    flat = _model(0.0).paper_fill_price(ex, "X/USDT", "buy", "futures")
    wide = _model(0.5).paper_fill_price(ex, "X/USDT", "buy", "futures")
    assert wide > flat
    assert abs(wide - flat) / flat < 0.0002  # extra < 2bps on a 2bps spread


def test_mult_zero_reproduces_flat():
    ex = _FakeEx(bid=99.0, ask=101.0, last=100.0)
    m = _model(0.0)
    assert m.paper_fill_price(ex, "X/USDT", "buy", "futures") == 101.0 * 1.0005


def test_execution_snapshot_vwap_drives_paper_fill_without_double_spread():
    ex = _FakeEx(bid=99.0, ask=101.0, last=100.0)
    snapshot = {
        "allowed": True,
        "vwap": "102.0",
        "mid": "100.0",
    }
    model = _model(0.5)

    fill = model.paper_fill_price(
        ex,
        "X/USDT",
        "buy",
        "futures",
        size=3,
        execution_snapshot=snapshot,
    )

    # L2 VWAP already includes spread and impact; only latency slip is added.
    assert fill == 102.0 * 1.0005
