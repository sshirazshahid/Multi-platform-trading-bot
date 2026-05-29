"""
strategies/__init__.py — package-level exports for ACTIVE strategies only.

Active live-path strategies (called from `core/bot_engine._build_strategy_pool`):
  * DCAStrategy          — scheduled accumulator
  * RebalancingStrategy  — scheduled rebalancer (try-import, may not be available)

The bot's ENTRY decisions come from the `claude_portfolio` cycle (Claude AI)
or its `systematic_v3_1` fallback (algorithmic) — both inside `core/mcp_brain.py`.
The `strategies/` package classes are NOT consulted during entry.

Research / backtest-only strategies live in `strategies/legacy/`:
  multi_tf, supertrend, trend_following, grid_trading, scalping,
  funding_rate_arb, mean_reversion.

See `strategies/legacy/__init__.py` for the attribution evidence
behind each retirement.
"""

from .base_strategy import BaseStrategy
from .dca_strategy import DCAStrategy

__all__ = ["BaseStrategy", "DCAStrategy"]

try:
    from .rebalancing import RebalancingStrategy  # noqa: F401
    __all__.append("RebalancingStrategy")
except ImportError:
    pass
