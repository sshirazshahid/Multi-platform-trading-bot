"""Tests for BybitClient.transfer() and CapitalAllocator circuit-breaker.

Root cause fixed (2026-05-27):
  BybitClient had no transfer() method — every ACCUMULATE fell through to
  BaseExchange.transfer() which is a no-op stub returning False. The 88+
  consecutive 'success: false' entries in data/capital_allocator.json were
  all silent stub returns, not exchange errors.

Fix A — BybitClient.transfer() (revised 2026-05-30):
  This account is a Unified Trading Account (UTA 2.0, verified
  unifiedMarginStatus=5): spot & derivatives share ONE margin wallet, so
  there is NO spot<->futures transfer (the legacy spot<->contract endpoint
  is gone and the UI offers none). transfer() therefore treats a
  spot<->futures move as a no-op SUCCESS (funds already available to both),
  returning True without an API call — so the allocator sweep neither errors
  nor churns the breaker. (The original "ccxt transfer contract/spot" fix
  was based on the old separate-wallet model and would just fail on a UTA.)

Fix B — CapitalAllocator circuit-breaker:
  After TRANSFER_FAILURE_LIMIT (5) consecutive failures on one exchange,
  suppress attempts for TRANSFER_BACKOFF_SEC (2h) to avoid log spam and
  futile API hammering.
"""
from __future__ import annotations

import time
from unittest.mock import MagicMock

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "data").mkdir()
    yield


def _build_allocator(monkeypatch, exchanges=None, *, live=True):
    import config
    monkeypatch.setitem(config.CAPITAL_ALLOCATION, "enabled", True)
    monkeypatch.setitem(config.CAPITAL_ALLOCATION, "recommendation_only", False)
    from core import capital_allocator as ca
    monkeypatch.setattr(ca, "DRY_RUN", not live)
    return ca.CapitalAllocator(exchanges=exchanges or {})


def _mock_bybit_exchange(transfer_side_effect=None, transfer_return=None):
    """Build a mock that looks like a connected BybitClient."""
    ex = MagicMock()
    ex._connected = True
    ex.name = "Bybit"
    ex.fetch_balance.return_value = {"USDT": {"free": 120.0}}
    if transfer_side_effect is not None:
        ex.transfer.side_effect = transfer_side_effect
    else:
        ex.transfer.return_value = transfer_return if transfer_return is not None else True
    return ex


# ===========================================================================
# BybitClient.transfer() unit tests
# ===========================================================================

class TestBybitClientTransfer:

    def _make_client(self):
        """Build a BybitClient with a mocked ccxt exchange, bypassing _init_exchange."""
        from exchanges.bybit_client import BybitClient
        client = object.__new__(BybitClient)
        client.api_key = "key"
        client.secret = "secret"
        client.testnet = False
        client._connected = True
        client.exchange = MagicMock()
        client.exchange.transfer.return_value = {"id": "abc", "status": "ok"}
        return client

    # This account is a Unified Trading Account (UTA 2.0, verified 2026-05-30
    # unifiedMarginStatus=5): spot & derivatives share ONE margin wallet, so a
    # spot<->futures move is a NO-OP success — transfer() returns True WITHOUT
    # any ccxt transfer() call (the legacy spot<->contract endpoint is gone).

    def test_transfer_futures_to_spot_is_uta_noop(self):
        client = self._make_client()
        result = client.transfer(10.0, from_account="futures", to_account="spot")
        assert result is True
        client.exchange.transfer.assert_not_called()

    def test_transfer_spot_to_futures_is_uta_noop(self):
        client = self._make_client()
        result = client.transfer(5.0, from_account="spot", to_account="futures")
        assert result is True
        client.exchange.transfer.assert_not_called()

    def test_transfer_swap_alias_is_uta_noop(self):
        client = self._make_client()
        result = client.transfer(3.0, from_account="swap", to_account="spot")
        assert result is True
        client.exchange.transfer.assert_not_called()

    def test_transfer_unsupported_route_returns_false(self):
        # A non-pooled route (e.g. funding<->unified) is not handled here; it
        # must return False without claiming success or calling ccxt.
        client = self._make_client()
        result = client.transfer(10.0, from_account="funding", to_account="spot")
        assert result is False
        client.exchange.transfer.assert_not_called()

    def test_transfer_returns_false_when_not_connected(self):
        client = self._make_client()
        client._connected = False
        result = client.transfer(10.0)
        assert result is False
        client.exchange.transfer.assert_not_called()

    def test_transfer_returns_false_when_exchange_none(self):
        client = self._make_client()
        client.exchange = None
        result = client.transfer(10.0)
        assert result is False


# ===========================================================================
# CapitalAllocator circuit-breaker tests
# ===========================================================================

class TestCapitalAllocatorCircuitBreaker:

    def test_no_suppression_before_limit(self, monkeypatch):
        alloc = _build_allocator(monkeypatch)
        # 4 failures — below the limit of 5
        alloc._state["transfer_breakers"] = {
            "bybit": {"consecutive_failures": 4, "tripped_at": 0}
        }
        assert alloc._transfer_circuit_open("bybit") is False

    def test_circuit_opens_at_limit(self, monkeypatch):
        from core import capital_allocator as ca
        alloc = _build_allocator(monkeypatch)
        alloc._state["transfer_breakers"] = {
            "bybit": {
                "consecutive_failures": ca.TRANSFER_FAILURE_LIMIT,
                "tripped_at": time.time(),  # tripped just now
            }
        }
        assert alloc._transfer_circuit_open("bybit") is True

    def test_circuit_resets_after_backoff(self, monkeypatch):
        from core import capital_allocator as ca
        alloc = _build_allocator(monkeypatch)
        # Tripped well in the past — backoff window expired
        alloc._state["transfer_breakers"] = {
            "bybit": {
                "consecutive_failures": ca.TRANSFER_FAILURE_LIMIT,
                "tripped_at": time.time() - ca.TRANSFER_BACKOFF_SEC - 1,
            }
        }
        assert alloc._transfer_circuit_open("bybit") is False
        # Counter must have been reset
        assert alloc._state["transfer_breakers"]["bybit"]["consecutive_failures"] == 0

    def test_success_resets_failure_count(self, monkeypatch):
        alloc = _build_allocator(monkeypatch)
        alloc._state["transfer_breakers"] = {
            "bybit": {"consecutive_failures": 3, "tripped_at": 0}
        }
        alloc._record_transfer_result("bybit", success=True)
        assert alloc._state["transfer_breakers"]["bybit"]["consecutive_failures"] == 0

    def test_failure_increments_count(self, monkeypatch):
        alloc = _build_allocator(monkeypatch)
        alloc._record_transfer_result("bybit", success=False)
        assert alloc._state["transfer_breakers"]["bybit"]["consecutive_failures"] == 1

    def test_failure_at_limit_records_tripped_at(self, monkeypatch):
        from core import capital_allocator as ca
        alloc = _build_allocator(monkeypatch)
        # Bring it to one-below the limit, then tip it over
        alloc._state["transfer_breakers"] = {
            "bybit": {"consecutive_failures": ca.TRANSFER_FAILURE_LIMIT - 1, "tripped_at": 0}
        }
        before = time.time()
        alloc._record_transfer_result("bybit", success=False)
        info = alloc._state["transfer_breakers"]["bybit"]
        assert info["consecutive_failures"] == ca.TRANSFER_FAILURE_LIMIT
        assert info["tripped_at"] >= before

    def test_execute_skips_when_circuit_open(self, monkeypatch):
        """When circuit is open, execute_allocation must return False without
        calling exchange.transfer()."""
        from core import capital_allocator as ca
        ex = _mock_bybit_exchange(transfer_return=True)
        alloc = _build_allocator(monkeypatch, exchanges={"bybit": ex})
        # Force circuit open
        alloc._state["transfer_breakers"] = {
            "bybit": {
                "consecutive_failures": ca.TRANSFER_FAILURE_LIMIT,
                "tripped_at": time.time(),
            }
        }
        action = {
            "type": "ACCUMULATE", "exchange": "bybit",
            "from": "futures", "to": "spot",
            "coin": "BTC", "amount_usdt": 12.0,
        }
        result = alloc.execute_allocation(action)
        assert result is False
        ex.transfer.assert_not_called()

    def test_execute_accumulate_calls_bybit_transfer(self, monkeypatch):
        """Live happy path: execute_allocation calls exchange.transfer() and returns True."""
        ex = _mock_bybit_exchange(transfer_return=True)
        alloc = _build_allocator(monkeypatch, exchanges={"bybit": ex})
        # Seed a known baseline so profit is detectable
        alloc._state.setdefault("baselines", {})["bybit"] = 100.0
        action = {
            "type": "ACCUMULATE", "exchange": "bybit",
            "from": "futures", "to": "spot",
            "coin": "BTC", "amount_usdt": 12.0,
        }
        result = alloc.execute_allocation(action)
        assert result is True
        ex.transfer.assert_called_once_with(12.0, "futures", "spot")

    def test_paper_execute_accumulate_recommends_only(self, monkeypatch):
        """PAPER/DRY_RUN must not move wallet funds even if recommendation_only=False."""
        ex = _mock_bybit_exchange(transfer_return=True)
        alloc = _build_allocator(monkeypatch, exchanges={"bybit": ex}, live=False)
        action = {
            "type": "ACCUMULATE", "exchange": "bybit",
            "from": "futures", "to": "spot",
            "coin": "BTC", "amount_usdt": 12.0,
        }
        result = alloc.execute_allocation(action)
        assert result is False
        ex.transfer.assert_not_called()

    def test_transfer_failure_recorded_in_breaker(self, monkeypatch):
        """A failed transfer must increment the breaker counter."""
        ex = _mock_bybit_exchange(transfer_return=False)
        alloc = _build_allocator(monkeypatch, exchanges={"bybit": ex})
        alloc._state.setdefault("baselines", {})["bybit"] = 100.0
        action = {
            "type": "ACCUMULATE", "exchange": "bybit",
            "from": "futures", "to": "spot",
            "coin": "BTC", "amount_usdt": 12.0,
        }
        alloc.execute_allocation(action)
        failures = alloc._state["transfer_breakers"]["bybit"]["consecutive_failures"]
        assert failures == 1
