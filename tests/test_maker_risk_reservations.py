"""Pending PAPER maker intents consume portfolio risk before and at fill."""
from __future__ import annotations

import inspect
from types import SimpleNamespace

import pytest

from core.bot_engine import BotEngine
from core.risk_manager import (
    aggregate_open_risk_breached,
    exposure_breached,
)
from tests.test_maker_first_paper import SYM, FakeExchange, _om, _open, _ticker


@pytest.fixture
def mf_on(monkeypatch):
    import config

    monkeypatch.setattr(
        config,
        "MAKER_FIRST_PAPER",
        {"enabled": True, "timeout_sec": 45, "max_chase": 1},
        raising=False,
    )


def _other_reservation(intent: dict) -> tuple[str, dict]:
    key = "Binance:ETH/USDT:USDT"
    other = dict(intent)
    other.update(
        {
            "exchange": "Binance",
            "symbol": "ETH/USDT:USDT",
            "limit_px": 100.0,
            "signal_px": 100.0,
            "size": 1.0,
            "sl_pct": 0.02,
        }
    )
    return key, other


def test_execute_open_gross_and_aggregate_gates_include_maker_reservations():
    source = inspect.getsource(BotEngine._execute_open)
    gross = source.index("_risk_positions =")
    reservation = source.index("pending_maker_reservations()", gross)
    exposure = source.index("_exp_breached(_risk_positions", reservation)
    aggregate = source.index("aggregate_open_risk_breached(", exposure)
    aggregate_positions = source.index("_risk_positions,", aggregate)

    assert gross < reservation < exposure < aggregate < aggregate_positions


def test_bot_engine_composes_current_paper_equity_for_fill_recheck():
    source = inspect.getsource(BotEngine.__init__)

    assert "portfolio_equity_provider" in source
    assert "_deployable_total(self._balances)" in source


def test_reservation_view_can_exclude_only_the_resolving_intent(
    mf_on, tmp_path
):
    om = _om(tmp_path)
    ex = FakeExchange(ticker=_ticker())
    _open(om, ex)
    own_key = f"{ex.name}:{SYM}"
    other_key, other = _other_reservation(om._pending_maker[own_key])
    om._pending_maker[other_key] = other

    reservations = om.pending_maker_reservations(exclude_key=own_key)

    assert [row.reservation_key for row in reservations] == [other_key]


def test_fill_recheck_excludes_self_and_includes_other_reservations(
    mf_on, tmp_path, monkeypatch
):
    import core.risk_manager as risk_module

    om = _om(tmp_path)
    ex = FakeExchange(ticker=_ticker())
    _open(om, ex)
    own_key = f"{ex.name}:{SYM}"
    other_key, other = _other_reservation(om._pending_maker[own_key])
    om._pending_maker[other_key] = other
    calls = []

    def gross_spy(positions, new_notional, equity, max_pct):
        calls.append(("gross", list(positions), new_notional, equity))
        return False

    def aggregate_spy(positions, *args):
        calls.append(("aggregate", list(positions), args))
        return False

    monkeypatch.setattr(risk_module, "exposure_breached", gross_spy)
    monkeypatch.setattr(
        risk_module, "aggregate_open_risk_breached", aggregate_spy
    )
    ex.ticker = {"last": 99.85, "bid": 99.8, "ask": 99.9}

    om._resolve_pending_maker_entries(ex)

    assert om.tracker.add.called
    assert [p.reservation_key for p in calls[0][1]] == [other_key]
    assert [p.reservation_key for p in calls[1][1]] == [other_key]
    assert calls[0][2] == pytest.approx(99.9)
    assert calls[0][3] == pytest.approx(10_000.0)


def test_fill_recheck_fails_closed_on_malformed_reservation(
    mf_on, tmp_path
):
    om = _om(tmp_path)
    ex = FakeExchange(ticker=_ticker())
    _open(om, ex)
    own_key = f"{ex.name}:{SYM}"
    other_key, other = _other_reservation(om._pending_maker[own_key])
    other["size"] = "not-a-number"
    om._pending_maker[other_key] = other
    ex.ticker = {"last": 99.85, "bid": 99.8, "ask": 99.9}

    om._resolve_pending_maker_entries(ex)

    assert not om.tracker.add.called
    assert om.last_open_reject == "maker_fill_portfolio_exposure_cap"


def test_fill_recheck_fails_closed_on_nonfinite_equity(mf_on, tmp_path):
    om = _om(tmp_path)
    om.portfolio_equity_provider = lambda: float("nan")
    ex = FakeExchange(ticker=_ticker())
    _open(om, ex)
    ex.ticker = {"last": 99.85, "bid": 99.8, "ask": 99.9}

    om._resolve_pending_maker_entries(ex)

    assert not om.tracker.add.called
    assert om.last_open_reject == "maker_fill_portfolio_exposure_cap"


def test_fill_recheck_enforces_aggregate_risk_with_other_reservation(
    mf_on, tmp_path, monkeypatch
):
    import config

    monkeypatch.setattr(config, "MAX_PORTFOLIO_EXPOSURE_PCT", 100.0)
    monkeypatch.setattr(config, "MAX_AGGREGATE_OPEN_RISK_PCT", 0.003)
    monkeypatch.setattr(config, "STRESSED_EXIT_COST_FRAC", 0.0)
    om = _om(tmp_path)
    om.portfolio_equity_provider = lambda: 1_000.0
    ex = FakeExchange(ticker=_ticker())
    _open(om, ex)
    own_key = f"{ex.name}:{SYM}"
    other_key, other = _other_reservation(om._pending_maker[own_key])
    om._pending_maker[other_key] = other
    ex.ticker = {"last": 99.85, "bid": 99.8, "ask": 99.9}

    om._resolve_pending_maker_entries(ex)

    assert not om.tracker.add.called
    assert om.last_open_reject == "maker_fill_aggregate_open_risk_cap"


def test_risk_helpers_fail_closed_on_malformed_reservation_and_equity():
    malformed = SimpleNamespace(
        size=0.0,
        entry_price=0.0,
        stop_loss=0.0,
        market_type="futures",
        is_pending_maker_reservation=True,
    )

    assert exposure_breached([malformed], 0.0, 1_000.0, 12.0) is True
    assert exposure_breached([], 10.0, float("nan"), 12.0) is True
    assert aggregate_open_risk_breached(
        [], 10.0, 0.02, float("nan"), 0.01
    ) is True
