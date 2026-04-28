"""Go-all-in auto-transfer invariants (2026-04-29).

User directive: "Go all in with the available USDT on all exchanges."
The Spec §14 expectancy-gate that previously blocked spot→futures
transfers (because kelly_stats showed all-negative-expectancy from
pre-fix bot bugs) was lifted. These tests pin the new behavior so a
future "let me restore Spec §14" doesn't silently break "go all-in".
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    Path("data").mkdir(exist_ok=True)
    yield


@pytest.fixture
def order_mgr_live(monkeypatch):
    """OrderManager configured for live (dry_run=False) so auto-transfer
    actually runs. We patch the exchange transfer to capture calls
    without touching real ccxt."""
    from core.order_manager import OrderManager
    om = OrderManager.__new__(OrderManager)
    om.dry_run = False
    om.available_balance = MagicMock()
    return om


def test_spot_to_futures_transfer_allowed_with_negative_kelly(order_mgr_live):
    """Pre-fix Spec §14 would have blocked this. Post-fix: allowed."""
    # Simulate: futures balance below required, spot has plenty.
    Path("data/kelly_stats.json").write_text(json.dumps({
        "strat_a": {"wins": 5, "losses": 20, "total_win": 10, "total_loss": 60},
        # All-negative expectancy: WR=0.20, avg_win=2, avg_loss=3 → E=-2.0
    }))

    ex = MagicMock()
    ex.transfer.return_value = True
    order_mgr_live.available_balance.side_effect = lambda e, t: 5.0 if t == "futures" else 100.0

    result = order_mgr_live.auto_transfer_for_trade(ex, "futures", needed=50.0)

    assert result is True, (
        "Spec §14 gate is lifted; spot→futures must succeed even when "
        "kelly_stats shows all-negative expectancy")
    ex.transfer.assert_called_once()
    args = ex.transfer.call_args[0]
    assert args[1] == "spot" and args[2] == "futures"


def test_spot_to_futures_transfer_allowed_with_no_kelly_file(order_mgr_live):
    """No kelly_stats.json at all → must still allow (was previously
    a 'safe default = block')."""
    assert not Path("data/kelly_stats.json").exists()

    ex = MagicMock()
    ex.transfer.return_value = True
    order_mgr_live.available_balance.side_effect = lambda e, t: 5.0 if t == "futures" else 100.0

    result = order_mgr_live.auto_transfer_for_trade(ex, "futures", needed=50.0)
    assert result is True


def test_futures_to_spot_transfer_always_allowed(order_mgr_live):
    """De-risking direction was never blocked; verify it still isn't."""
    ex = MagicMock()
    ex.transfer.return_value = True
    order_mgr_live.available_balance.side_effect = lambda e, t: 5.0 if t == "spot" else 100.0

    result = order_mgr_live.auto_transfer_for_trade(ex, "spot", needed=50.0)
    assert result is True
    args = ex.transfer.call_args[0]
    assert args[1] == "futures" and args[2] == "spot"


def test_no_transfer_when_other_pocket_too_small(order_mgr_live):
    """If both pockets are tiny, no transfer attempted (other_bal < 5)."""
    ex = MagicMock()
    order_mgr_live.available_balance.side_effect = lambda e, t: 2.0

    result = order_mgr_live.auto_transfer_for_trade(ex, "futures", needed=50.0)
    assert result is False
    ex.transfer.assert_not_called()


def test_no_transfer_when_already_sufficient(order_mgr_live):
    """If futures already has enough, no-op success."""
    ex = MagicMock()
    order_mgr_live.available_balance.side_effect = lambda e, t: 100.0

    result = order_mgr_live.auto_transfer_for_trade(ex, "futures", needed=50.0)
    assert result is True
    ex.transfer.assert_not_called()


def test_dry_run_short_circuits_to_true(order_mgr_live):
    """PAPER mode: never attempts a real transfer; just returns True."""
    order_mgr_live.dry_run = True
    ex = MagicMock()
    result = order_mgr_live.auto_transfer_for_trade(ex, "futures", needed=50.0)
    assert result is True
    ex.transfer.assert_not_called()
