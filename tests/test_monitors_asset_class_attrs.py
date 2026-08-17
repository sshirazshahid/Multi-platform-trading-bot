"""Regression: the D5 engine split dropped _STABLECOINS/_COMMODITY_BASES.

core/engine/monitors.py references self._STABLECOINS and self._COMMODITY_BASES
(exchange scan asset classification); the original class attributes lived on
BotEngine in the pre-split core/bot_engine.py and were not carried over,
so the MCP position monitor failed every tick with AttributeError
('BotEngine' object has no attribute '_COMMODITY_BASES') — observed 2026-08-14.
"""

from core.engine.monitors import _MonitorsMixin


def test_monitors_mixin_defines_stablecoins():
    assert "USDT" in _MonitorsMixin._STABLECOINS
    assert "USDC" in _MonitorsMixin._STABLECOINS


def test_monitors_mixin_defines_commodity_bases():
    assert "XAU" in _MonitorsMixin._COMMODITY_BASES
    assert "CL" in _MonitorsMixin._COMMODITY_BASES
