# Strategies

All strategies extend `BaseStrategy` and must implement `generate_signal(df)` and `run(exchange, symbol)`.

---

## SupertrendStrategy

**File:** `strategies/supertrend_strategy.py`  
**Markets:** Spot (LONG only) + Futures (LONG + SHORT)

Uses ATR-based Supertrend indicator combined with RSI and volume filter. Fires on Supertrend direction changes confirmed by:
- RSI between 35 and 65 (not overextended)
- Volume above 80% of 20-period MA

**Parameters** (`config.py → SUPERTREND`):
| Parameter | Default | Description |
|---|---|---|
| `atr_period` | 10 | ATR lookback |
| `atr_multiplier` | 3.0 | Band width multiplier |
| `atr_sl_mult` | 1.8 | SL = ATR × this |
| `atr_tp_mult` | 4.5 | TP = ATR × this |

---

## MultiTFStrategy

**File:** `strategies/multi_tf_strategy.py`  
**Markets:** Futures (LONG + SHORT)

Three-timeframe confirmation: HTF (4h) sets bias, MTF (1h) confirms structure, LTF (15m) times entry. Entry only when all three agree. Uses EMA structure (9/21 fast, 200 slow) and ADX ≥ 22.

**Parameters** (`config.py → MULTI_TF`):
| Parameter | Default | Description |
|---|---|---|
| `adx_min` | 22 | Minimum ADX for entry |
| `atr_sl_mult` | 1.8 | ATR stop multiplier |
| `atr_tp_mult` | 4.5 | ATR target multiplier |

---

## MeanReversionStrategy

**File:** `strategies/mean_reversion.py`  
**Markets:** Spot (LONG) + Futures SHORT on overbought

Bollinger Band + RSI mean reversion. Buys when price touches lower band with RSI < 30. Shorts when price touches upper band with RSI > 70 (futures only). Exits at BB midline.

**Parameters** (`config.py → MEAN_REVERSION`):
| Parameter | Default | Description |
|---|---|---|
| `bb_std` | 2.0 | Band standard deviations |
| `rsi_oversold` | 30 | Long entry RSI threshold |
| `rsi_overbought` | 70 | Short entry RSI threshold |

---

## TrendFollowingStrategy

**File:** `strategies/trend_following.py`  
**Markets:** Spot (LONG) + Futures (LONG + SHORT)

Fast EMA/MACD crossover on 15m with 50 EMA trend filter. Enters on MACD signal cross confirmed by EMA stack alignment. Uses volume spike filter (1.2× MA required).

---

## GridTradingStrategy

**File:** `strategies/grid_trading.py`  
**Markets:** Spot (LONG only)

Places a grid of buy orders below current price when market is ranging (ADX < 20, BB squeeze). Takes profit at fixed grid intervals. Pauses on high-volatility conditions (ATR spike).

---

## ScalpingStrategy

**File:** `strategies/scalping.py`  
**Markets:** Spot

1-minute timeframe, very tight stops. Uses EMA 5/13 crossovers + RSI 7 + BB squeeze. Only trades during high-liquidity conditions (volume spike ≥ 1.5×).

---

## DCAStrategy

**File:** `strategies/dca_strategy.py`  
**Markets:** Spot (LONG only)

Dollar-cost averaging on dips. Buys on 5% price dips from recent high, multiplies size on further dips. Long-term accumulation, not suited for bearish markets.

---

## Adding a Custom Strategy

```python
# strategies/my_strategy.py
from strategies.base_strategy import BaseStrategy
import pandas as pd

class MyStrategy(BaseStrategy):

    def __init__(self, order_manager, risk_manager, market_type="spot"):
        super().__init__(
            order_manager, risk_manager,
            name="MyStrategy",
            market_type=market_type
        )

    def generate_signal(self, df: pd.DataFrame):
        # Your indicator logic here
        # Return "buy", "sell", or None
        close = df["close"]
        fast  = close.ewm(span=9).mean()
        slow  = close.ewm(span=21).mean()
        if fast.iloc[-1] > slow.iloc[-1] and fast.iloc[-2] <= slow.iloc[-2]:
            return "buy"
        if fast.iloc[-1] < slow.iloc[-1] and fast.iloc[-2] >= slow.iloc[-2]:
            return "sell"
        return None

    def run(self, exchange, symbol: str):
        df = self.get_dataframe(exchange, symbol, "1h", 100)
        if df is None:
            return
        signal = self.generate_signal(df)
        if not signal:
            return
        price  = float(df["close"].iloc[-1])
        self.log_signal(symbol, signal, price)
        # Use order_manager to place the trade
        balance = self.get_usdt_balance(exchange)
        # ... sizing and order logic
```

Then register in `core/bot_engine.py`:
```python
self.pool["my_strategy"] = MyStrategy(
    order_manager=self.order_mgr,
    risk_manager=self.risk,
    market_type="spot"
)
```
