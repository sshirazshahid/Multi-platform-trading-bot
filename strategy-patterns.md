# Strategy Patterns

A library of strategy templates across complexity tiers, and an honest discussion of the hardest case: codifying SMC/ICT patterns.

## Tier 1: Simple rule-based (mechanical, unambiguous)

These strategies are cleanly mechanical — there's no judgment call in when they fire. This makes them ideal for building confidence in the backtest engine. Also worth noting: most of these simple strategies don't work in their basic form — they've been tested exhaustively by every retail trader and no longer provide consistent edge. That's fine for learning purposes; backtest them to see *how* they fail, not to expect them to succeed.

### EMA crossover (trend-following)
Enter long when a faster EMA crosses above a slower EMA; exit on the reverse cross.

**Typical parameters:** (20, 50), (9, 21), (50, 200).

**Strength:** cleanly mechanical, no ambiguity.

**Why it typically fails:** EMAs lag. By the time the signal fires, the move is partly done. Works in strongly trending regimes, chops badly in ranges. Rule of thumb: basic EMA crossovers on 4H/1D crypto have been break-even to slightly negative across 2020-2025 data.

**Code:** see `assets/example_strategies/ema_cross.py`

### RSI mean reversion
Enter long when RSI dips below 30, exit when RSI returns above 50 (or opposite). Variants add a trend filter to only fire with the trend.

**Typical parameters:** RSI period 14, thresholds 30/70.

**Strength:** counter-trend strategies have diversification value. Simple to implement.

**Why it typically fails without a filter:** crypto has strong trends and RSI can stay oversold for extended periods in a downtrend. A pure RSI mean-reversion system will get stopped out in trends.

**Code:** see `assets/example_strategies/rsi_mean_reversion.py`

### Donchian breakout
Enter long when price breaks above the N-period high; exit when price breaks below the N-period low.

**Typical parameters:** 20 or 55 period high (famous Turtle Traders values).

**Strength:** doesn't predict, just reacts to price. Captures big trends when they happen.

**Why it typically fails:** many false breakouts. Works phenomenally when one trade becomes a huge runner; brutal win rate (often < 40%).

### Bollinger Band mean reversion
Enter long when price touches the lower band, exit at the middle band (or opposite).

**Typical parameters:** 20-period SMA ± 2 standard deviations.

**Similar issues to RSI:** works in ranges, dies in trends.

---

## Tier 2: Multi-indicator / multi-timeframe

Combining a signal with a filter, or using a higher timeframe to contextualize a lower timeframe entry. This is where most genuinely workable retail strategies live.

### Trend-filtered mean reversion
RSI mean reversion, but only take longs when the higher timeframe trend is up (e.g., 1D EMA 50 > EMA 200) and only take shorts in downtrends.

**Why this often works better than pure mean reversion:** avoids fighting strong trends.

### Breakout with retest confirmation
Wait for price to break a level, pull back to retest it, then enter on confirmation. Reduces false breakout problem.

**How to codify:** price closes above level → price pulls back within X% of level → price prints bullish candle (close > open) → enter long.

**Code:** see `assets/example_strategies/breakout_retest.py`. This is the strategy closest to what discretionary traders might actually take by eye.

### Volatility regime filter
Only trade when volatility is in a specific regime. E.g., enter trend-following trades only when ATR is rising; enter mean-reversion trades only when ATR is falling.

**Implementation:** compute 14-period ATR, require ATR > its 50-period SMA (or variants).

### Volume confirmation
Require volume on the signal bar to exceed a rolling average. Helps filter low-conviction moves.

---

## Tier 3: SMC/ICT-style patterns — the codification problem

This tier needs an explicit discussion of why it's hard.

### The problem

When a discretionary trader says "bullish order block," they're invoking a visual pattern with dozens of implicit criteria they apply in real-time using judgment:
- Which candle counts as the "last opposite-colored candle"?
- Does the displacement need to leave an FVG? (ICT says yes, some SMC traders say no)
- How strong does displacement need to be? (Some use "breaks prior swing high"; some use ATR multiples; some use a visual "impulse")
- How fresh does the block need to be? Can it be used after one touch?
- What counts as the block's entry zone — the whole candle range, 50% back into it, the open-close body?

Every SMC trader has their own answers to these questions, and most answers are visual rather than precise. When you codify the pattern into Python, **you're picking specific numeric answers** to all of these. The backtest tells you how that *specific codification* performs. It does not tell you how the pattern "SMC traders talk about" performs, because there is no such pattern — there are thousands of slight variants.

### What this means practically

1. **Your SMC backtest is really a "backtest of my version of SMC."** Results don't transfer to other SMC traders' versions.
2. **The variant you pick matters a lot.** A strategy that uses "OB = last red candle before 0.5%+ move up in next 3 candles" will have very different performance from one that uses "OB = last red candle before FVG-creating displacement."
3. **Visual discretion may add value that the algo can't capture.** Traders often filter setups by context (HTF structure, news, vibe) in ways that are hard to codify. Or this discretion might be illusory alpha. The backtest can't distinguish.
4. **It can still be worth doing.** Even an imperfect codification can answer useful questions: "does this general class of setup produce positive expectancy?", "does adding a trend filter help?", "how does stop placement affect outcomes?"

### A sensible approach to SMC backtesting

1. Pick one specific codification, document it clearly (e.g., "OB = last red candle of a pullback preceding a move of ≥1.5 ATR in the next 5 candles, where that move closes above the prior swing high"). 
2. Visually verify on 20-50 historical charts that your code identifies setups that match what you'd flag by eye. If there's significant divergence, your codification doesn't capture what you actually trade.
3. Backtest the codification.
4. Vary specific parameters (the ATR threshold, the lookback for "swing high") and see if performance is robust or sensitive. Sensitive = overfit/fragile.
5. Accept that the result is about this codification, not about "SMC in general."

### Order block detection — a starting codification

Here's one concrete, testable definition:

```python
def detect_bullish_order_blocks(df, displacement_mult=1.5, atr_period=14, confirm_window=5):
    """
    Bullish order block = last red candle that:
    1. Is followed within `confirm_window` bars by a move of >= `displacement_mult * ATR` to the upside
    2. That move closes above the prior swing high (check last 10 bars before the red candle)
    
    Returns a Series of booleans marking the bar where each OB was confirmed.
    """
    import numpy as np
    atr = (df['high'] - df['low']).rolling(atr_period).mean()
    prior_swing_high = df['high'].rolling(10).max().shift(1)
    is_red = df['close'] < df['open']
    
    signals = pd.Series(False, index=df.index)
    for i in range(len(df) - confirm_window):
        if not is_red.iloc[i]:
            continue
        forward = df.iloc[i+1:i+1+confirm_window]
        displacement = forward['high'].max() - df['close'].iloc[i]
        broke_prior_swing = forward['close'].max() > prior_swing_high.iloc[i]
        if displacement >= displacement_mult * atr.iloc[i] and broke_prior_swing:
            # OB confirmed at bar i+confirm_window (when displacement completes)
            signals.iloc[i + confirm_window] = True
    return signals
```

Note how much is baked in: 10-bar lookback for swing high, 1.5 ATR for displacement, 5-bar confirmation window. Different choices give different strategies. Also note the `i + confirm_window` — this prevents look-ahead (you only confirm the OB after the displacement has fully happened, not while it's happening).

### FVG detection — a starting codification

```python
def detect_bullish_fvgs(df):
    """
    Bullish FVG = 3-candle pattern where candle[i-1].high < candle[i+1].low.
    The gap exists between candle[i-1].high and candle[i+1].low.
    FVG is "valid" once candle i+1 closes.
    Returns a DataFrame with columns: confirmed_at (bar index), gap_low, gap_high, mid.
    """
    highs = df['high']
    lows = df['low']
    fvg_mask = highs.shift(1) < lows.shift(-1)
    # Confirmed at bar i+1 (i.e., 1 bar after center candle)
    # Mark in next bar's row to avoid look-ahead
    confirmed = fvg_mask.shift(1).fillna(False)
    gap_low = highs.shift(2)  # candle i-1's high
    gap_high = lows           # candle i+1's low
    return pd.DataFrame({
        'confirmed': confirmed,
        'gap_low': gap_low,
        'gap_high': gap_high,
        'gap_mid': (gap_low + gap_high) / 2,
    })
```

### Liquidity sweep detection — a starting codification

```python
def detect_bullish_liquidity_sweep(df, lookback=20, reclaim_bars=3):
    """
    Bullish sweep = price makes a new `lookback`-bar low (wick below), then reclaims
    that prior low (close > prior low) within `reclaim_bars` bars.
    """
    prior_low = df['low'].rolling(lookback).min().shift(1)
    swept = df['low'] < prior_low
    # Reclaim: close > prior_low within next `reclaim_bars` bars
    reclaim_mask = pd.Series(False, index=df.index)
    for i in range(len(df) - reclaim_bars):
        if not swept.iloc[i]:
            continue
        forward = df.iloc[i+1:i+1+reclaim_bars]
        if (forward['close'] > prior_low.iloc[i]).any():
            first_reclaim = forward[forward['close'] > prior_low.iloc[i]].index[0]
            reclaim_mask.loc[first_reclaim] = True
    return reclaim_mask
```

---

## Combining patterns into a strategy

A typical SMC strategy isn't just "detect OB, buy OB." It's a combination:

```
Setup:
1. HTF trend is up (1D EMA 50 > EMA 200)
2. LTF sweeps liquidity (sweep detected)
3. LTF price pulls back into a bullish FVG or OB
4. Confirmation: bullish CHoCH or displacement on LTF after entering the zone

Entry: confirmation bar close
Stop: below the sweep low
Target: prior swing high or 2R
```

When backtesting this, test each component's contribution separately:
- Baseline: random entries at the same frequency (control)
- + HTF filter
- + Sweep requirement
- + Zone (FVG/OB) requirement
- + Confirmation requirement

Each additional filter should add expectancy per trade (while reducing trade count). If a filter doesn't add per-trade expectancy, it's just reducing sample size without improving edge.

---

## The meta-lesson

Strategy complexity is not correlated with strategy profitability. Some of the best-performing strategies in retail are extraordinarily simple (momentum, volatility targeting). Some of the most elaborate SMC frameworks underperform buy-and-hold when backtested honestly. Complexity is often a proxy for "this feels like it should work" rather than "this does work." Backtest accordingly.
