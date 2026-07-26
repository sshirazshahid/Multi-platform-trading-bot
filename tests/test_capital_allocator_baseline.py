"""capital_allocator baseline tracking (2026-05-24).

Bug: `_check_accumulation` read
    baseline = self._state.get("baselines", {}).get(ex_name, usdt_free)
and no code path ever wrote to `self._state["baselines"]`. Result:
profit = usdt_free - usdt_free = 0 forever, feature permanently inert.

Fix: seed baseline on first observation; reset after a successful
ACCUMULATE transfer. Safety rule: PAPER may write recommendations but must
not call exchange.transfer(); real transfers require explicit live mode.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "data").mkdir()
    yield


def _build_allocator(monkeypatch, exchanges=None, *, live=False):
    """Create a CapitalAllocator with feature enabled regardless of config
    default (so test runs deterministically even if config is toggled)."""
    import config
    monkeypatch.setitem(config.CAPITAL_ALLOCATION, "enabled", True)
    monkeypatch.setitem(config.CAPITAL_ALLOCATION, "recommendation_only", not live)
    from core import capital_allocator as ca
    monkeypatch.setattr(ca, "DRY_RUN", not live)
    return ca.CapitalAllocator(exchanges=exchanges or {})


def _mock_exchange(usdt_free: float):
    ex = MagicMock()
    ex._connected = True
    ex.fetch_balance.return_value = {"USDT": {"free": usdt_free}}
    ex.transfer.return_value = True
    return ex


def test_baseline_seeded_on_first_observation(monkeypatch):
    """First call with no prior baseline: write baseline = current free,
    return no actions (can't detect profit on first observation)."""
    ex = _mock_exchange(usdt_free=100.0)
    alloc = _build_allocator(monkeypatch, exchanges={"binance": ex}, live=True)
    actions = alloc._check_accumulation()
    assert actions == [], (
        "First observation must seed baseline and produce no action"
    )
    assert alloc._state["baselines"]["binance"] == 100.0, (
        "Baseline must be written to state on first observation"
    )


def test_profit_above_threshold_produces_accumulate_action(monkeypatch):
    """After seeding, a free-balance increase above the threshold
    must produce an ACCUMULATE action."""
    ex = _mock_exchange(usdt_free=100.0)
    alloc = _build_allocator(monkeypatch, exchanges={"binance": ex}, live=True)
    # First call seeds at 100.
    alloc._check_accumulation()
    # Simulate $15 of realized profit (above the $10 threshold).
    ex.fetch_balance.return_value = {"USDT": {"free": 115.0}}
    actions = alloc._check_accumulation()
    accumulate = [a for a in actions if a["type"] == "ACCUMULATE"]
    assert accumulate, (
        f"Expected an ACCUMULATE action; got {[a.get('type') for a in actions]}"
    )


def test_no_action_when_profit_below_threshold(monkeypatch):
    """Free-balance increase below the threshold must not trigger."""
    ex = _mock_exchange(usdt_free=100.0)
    alloc = _build_allocator(monkeypatch, exchanges={"binance": ex})
    alloc._check_accumulation()  # seed
    ex.fetch_balance.return_value = {"USDT": {"free": 105.0}}  # +5, below $10
    actions = alloc._check_accumulation()
    assert actions == [], (
        "Profit below accumulation_threshold_usd must produce no action"
    )


def test_baseline_resets_after_successful_transfer(monkeypatch):
    """A successful ACCUMULATE must reset the baseline to the
    post-transfer free balance so the same profit isn't re-swept."""
    monkeypatch.setattr(
        "core.entry_policy.authorize_runtime_entry",
        lambda *args, **kwargs: SimpleNamespace(allowed=True, reason="test_authorized"),
    )
    ex = _mock_exchange(usdt_free=120.0)
    alloc = _build_allocator(monkeypatch, exchanges={"binance": ex}, live=True)
    # Seed the baseline manually so accumulator detects profit on next call.
    alloc._state.setdefault("baselines", {})["binance"] = 100.0
    actions = alloc._check_accumulation()
    accumulate = [a for a in actions if a["type"] == "ACCUMULATE"]
    assert accumulate

    # Execute the action — exchange returns success and post-transfer free
    # of 90 (the transfer drained some balance).
    ex.fetch_balance.return_value = {"USDT": {"free": 90.0}}
    ok = alloc.execute_allocation(accumulate[0])
    assert ok is True
    assert alloc._state["baselines"]["binance"] == 90.0, (
        "Baseline must reset to post-transfer free balance, not stay at 100"
    )


def test_paper_accumulate_recommendation_does_not_transfer(monkeypatch):
    """In PAPER/DRY_RUN, ACCUMULATE writes a recommendation and never moves funds."""
    ex = _mock_exchange(usdt_free=120.0)
    alloc = _build_allocator(monkeypatch, exchanges={"binance": ex}, live=False)
    action = {
        "type": "ACCUMULATE",
        "exchange": "binance",
        "from": "futures",
        "to": "spot",
        "coin": "BTC",
        "amount_usdt": 12.0,
    }
    assert alloc.execute_allocation(action) is False
    ex.transfer.assert_not_called()


def test_real_transfers_default_disabled_to_recommendation_only():
    """Capital allocation stays enabled for learning, but defaults to recommendations.

    NB: do NOT reload config here — other tests cache the module object
    at import time, so popping/re-importing invalidates their cached
    reference. Just read the live values from the already-loaded module.
    """
    import config
    assert config.CAPITAL_ALLOCATION["enabled"] is True
    assert config.CAPITAL_ALLOCATION["recommendation_only"] is True
