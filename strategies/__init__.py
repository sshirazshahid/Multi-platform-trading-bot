"""
strategies/__init__.py — Export all active strategies.

All strategies are exported here so backtest.py and the strategy
selector can import them without conditional logic.
"""

from .base_strategy    import BaseStrategy
from .supertrend       import SupertrendStrategy
from .mean_reversion   import MeanReversionStrategy
from .multi_tf         import MultiTFStrategy
# TrendFollowing: 0% WR over 8 live trades — NOT exported in live path.
# Import remains available to backtest.py for replay/research only.
from .trend_following  import TrendFollowingStrategy  # noqa: F401  — backtest only
from .grid_trading     import GridTradingStrategy
from .scalping         import ScalpingStrategy
from .dca_strategy     import DCAStrategy
from .funding_rate_arb import FundingRateArbStrategy
try:
    from .rebalancing  import RebalancingStrategy
except ImportError:
    pass
