"""
strategies/__init__.py — Export all active strategies.

All strategies are exported here so backtest.py and the strategy
selector can import them without conditional logic.
"""

from .base_strategy    import BaseStrategy
from .supertrend       import SupertrendStrategy
from .mean_reversion   import MeanReversionStrategy
from .multi_tf         import MultiTFStrategy
from .trend_following  import TrendFollowingStrategy
from .grid_trading     import GridTradingStrategy
from .scalping         import ScalpingStrategy
from .dca_strategy     import DCAStrategy
from .funding_rate_arb import FundingRateArbStrategy
try:
    from .rebalancing  import RebalancingStrategy
except ImportError:
    pass
