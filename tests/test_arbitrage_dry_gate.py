"""Defense-in-depth: ArbitrageEngine must not place REAL orders when the global
OPERATING_MODE is not CONTROLLED_LIVE, even if it was instantiated with dry_run=False.

On 2026-06-05 an audit sub-agent instantiated ArbitrageEngine(dry_run=False) inside a
PAPER bot and reached the live create_order branch (it avoided real orders only because
its exchanges were mocked — the rollback raised a bare Exception("x")). The live branch
trusted only the constructor's dry_run flag; this pins a global-DRY_RUN cross-check so no
caller or test can place real arb orders unless the whole bot is in CONTROLLED_LIVE.

Production is unaffected: multi_profile_runner constructs the engine with dry_run=self.dry_run
(True in PAPER -> the paper branch; False only in CONTROLLED_LIVE where global DRY_RUN is also
False -> live orders proceed).
"""
from __future__ import annotations

from unittest.mock import MagicMock

import config
from core.arbitrage_engine import ArbitrageEngine, ArbOpportunity


def _opp() -> ArbOpportunity:
    return ArbOpportunity(
        id="ARB-TEST", symbol="BTC/USDT", buy_exchange="binance",
        sell_exchange="bybit", buy_price=100.0, sell_price=101.0,
        gross_spread=0.01, net_spread=0.006, buy_fee_pct=0.001, sell_fee_pct=0.001,
        volume_buy=1e6, volume_sell=1e6, zscore=2.0, confidence=0.8,
    )


def _engine(buy_ex, sell_ex) -> ArbitrageEngine:
    eng = ArbitrageEngine(
        active_exchanges={"binance": buy_ex, "bybit": sell_ex},
        dry_run=False, profile_name="test",  # engine says LIVE...
    )
    eng._get_balance = lambda ex: 1000.0  # bypass real balance parsing
    return eng


def test_paper_global_blocks_live_arb_orders(monkeypatch):
    """Bot is PAPER (global DRY_RUN True) -> even a dry_run=False engine places NO real order."""
    monkeypatch.setattr(config, "DRY_RUN", True)
    buy_ex, sell_ex = MagicMock(), MagicMock()
    eng = _engine(buy_ex, sell_ex)
    assert eng._execute(_opp()) is False
    buy_ex.create_order.assert_not_called()
    sell_ex.create_order.assert_not_called()


def test_controlled_live_global_allows_live_arb_path(monkeypatch):
    """Bot is CONTROLLED_LIVE (global DRY_RUN False) -> the live path is reached
    (here the buy leg is made to fail so we don't depend on the success-path shape)."""
    monkeypatch.setattr(config, "DRY_RUN", False)
    buy_ex, sell_ex = MagicMock(), MagicMock()
    buy_ex.create_order.side_effect = Exception("boom")  # reached, then fails
    eng = _engine(buy_ex, sell_ex)
    eng._execute(_opp())
    buy_ex.create_order.assert_called_once()  # gate allowed the live path through
