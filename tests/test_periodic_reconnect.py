"""Periodic reconnect retry on stale-halt (Phase 15.2, 2026-05-03).

Bug observed: bot ran 519 consecutive fetch_ticker failures over 8.5h
because the one-shot reconnect at fails==3 didn't retry. A fresh
process was confirmed to work — the stale ccxt session in the running
bot couldn't recover without a full restart.

Fix: reconnect retries every 5th fail while the exchange is in the
halted set. Each retry calls exchange._init_exchange() to rebuild
the ccxt client.
"""
from __future__ import annotations

from tests.bot_engine_source import bot_engine_source_for_grep

from pathlib import Path


def test_try_reconnect_method_exists():
    from core.bot_engine import BotEngine
    assert hasattr(BotEngine, "_try_reconnect")


def test_periodic_retry_branch_in_health_check():
    """Source contains the periodic-retry branch."""
    src = bot_engine_source_for_grep()
    assert "stale-halt retry" in src
    assert "fails % 5 == 0" in src


def test_first_halt_transition_still_calls_reconnect():
    """The original 'first transition to halted' path also routes through
    _try_reconnect (unified) — not duplicated inline."""
    src = bot_engine_source_for_grep()
    # The first-transition block calls _try_reconnect
    first_transition_idx = src.index(
        "EXCHANGE HALTED: {ex_name.upper()} unreachable for"
    )
    # _try_reconnect is called somewhere after that intro
    after = src[first_transition_idx:first_transition_idx + 2000]
    assert "_try_reconnect" in after, \
        "First-halt path must call _try_reconnect"


def test_reconnect_clears_fails_and_halted_set():
    """When reconnect succeeds, clear both the consecutive_fails counter
    and the _exchange_halted set."""
    src = bot_engine_source_for_grep()
    # Find the _try_reconnect body
    method_idx = src.index("def _try_reconnect(self")
    body = src[method_idx:method_idx + 1200]
    assert "_consecutive_api_fails[ex_name] = 0" in body
    assert "_exchange_halted.discard(ex_name)" in body


def test_reconnect_handles_exception():
    """Exception in reconnect attempt does not crash the health-check loop."""
    src = bot_engine_source_for_grep()
    method_idx = src.index("def _try_reconnect(self")
    body = src[method_idx:method_idx + 1200]
    assert "except Exception" in body


def test_disconnected_venue_reconnects_instead_of_empty_ticker():
    """Bitget `_ok()` False returns `{}`; treating that as Empty ticker latched
    exchange_outage:bitget for 80+ fails while auth was the real 40085."""
    from unittest.mock import MagicMock

    from core.engine.monitors import _MonitorsMixin

    class Harness(_MonitorsMixin):
        pass

    client = MagicMock()
    client._ok.return_value = False
    client._connected = False
    client.exchange = None
    client.name = "Bitget"
    client.fetch_ticker.return_value = {}

    h = Harness()
    h.active_exchanges = {"bitget": client}
    h.exchanges = {"bitget": client}
    h._consecutive_api_fails = {}
    h._exchange_halted = set()
    h._api_latency = {}
    h._inactive_reconnect_at = {"bitget": 10**12}
    h.tracker = MagicMock()
    h.tracker.count_open.return_value = 0
    h.notifier = MagicMock()
    h.order_mgr = MagicMock()

    h._check_exchange_health()

    client._init_exchange.assert_called()
    client.fetch_ticker.assert_not_called()
    assert h._consecutive_api_fails.get("bitget", 0) >= 1
