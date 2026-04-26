"""
utils/smart_money.py -- Smart Money / ICT / Price Action indicator functions.

Pure pandas computations for institutional trading concepts:
  - Fibonacci Retracement
  - Fair Value Gaps (FVG)
  - Supply/Demand Zones
  - Liquidity Sweeps
  - Order Blocks (SMC)
  - Market Structure (BOS / ChoCH)
  - Volume Profile
  - ICT Optimal Trade Entry (OTE)
  - Price Action Patterns

Shared by mcp_brain.py and strategy modules.
No strategy logic, no exchange I/O -- just math on price series.
"""

import numpy as np
import pandas as pd


# ---- internal helpers ----

def _safe_float(val) -> float:
    """Convert to float, replacing NaN/inf with 0.0."""
    try:
        f = float(val)
        if np.isnan(f) or np.isinf(f):
            return 0.0
        return f
    except (TypeError, ValueError):
        return 0.0


def _atr_series(high: pd.Series, low: pd.Series, close: pd.Series,
                period: int = 14) -> pd.Series:
    """Local ATR to avoid circular import from indicators.py."""
    tr = pd.concat([
        high - low,
        (high - close.shift(1)).abs(),
        (low - close.shift(1)).abs(),
    ], axis=1).max(axis=1)
    return tr.ewm(com=period - 1, adjust=False).mean()


def _swing_highs_lows(high: pd.Series, low: pd.Series,
                      n: int = 5) -> tuple[list, list]:
    """
    Find n-bar swing highs and swing lows.

    A swing high at bar i means high[i] is the max of high[i-n : i+n+1].
    A swing low at bar i means low[i] is the min of low[i-n : i+n+1].

    Returns:
        swing_highs: list of (index_position, price)
        swing_lows:  list of (index_position, price)
    """
    length = len(high)
    swing_highs: list[tuple[int, float]] = []
    swing_lows: list[tuple[int, float]] = []
    for i in range(n, length - n):
        window_h = high.iloc[i - n: i + n + 1]
        if float(high.iloc[i]) == float(window_h.max()):
            swing_highs.append((i, float(high.iloc[i])))
        window_l = low.iloc[i - n: i + n + 1]
        if float(low.iloc[i]) == float(window_l.min()):
            swing_lows.append((i, float(low.iloc[i])))
    return swing_highs, swing_lows


def _clamp(value: float, lo: float, hi: float) -> float:
    """Clamp value to [lo, hi]."""
    return max(lo, min(hi, value))


# ════════════════════════════════════════════════════════════════
# 1. FIBONACCI RETRACEMENT
# ════════════════════════════════════════════════════════════════

def fibonacci_retracement(high: pd.Series, low: pd.Series, close: pd.Series,
                          lookback: int = 50) -> dict:
    """
    Find the most recent significant swing high/low over *lookback* bars
    and compute Fibonacci retracement levels.

    Returns:
        swing_high, swing_low: the anchor points
        levels: dict of {name: price} for 0%, 23.6%, 38.2%, 50%, 61.8%, 78.6%, 100%
        nearest_level: name of closest level to current price
        nearest_dist_pct: distance to nearest level as % of price
        at_fib_zone: True if price is within 0.3% of any key level
        fib_bias: 'bullish' / 'bearish' / 'neutral'
        trend: 'up' if swing_low came before swing_high, else 'down'
    """
    defaults = {
        "swing_high": 0.0, "swing_low": 0.0,
        "levels": {}, "nearest_level": "none",
        "nearest_dist_pct": 99.0, "at_fib_zone": False,
        "fib_bias": "neutral", "trend": "ranging",
    }
    n = len(close)
    if n < 3:
        return defaults

    lb = min(lookback, n)
    h_slice = high.iloc[-lb:]
    l_slice = low.iloc[-lb:]
    c_slice = close.iloc[-lb:]

    # 2026-04-16 (post-audit): anchor the fib grid on confirmed pivot highs/lows,
    # not raw argmax/argmin. A single news-spike wick was defining the grid
    # and corrupting fib_bias / at_fib_zone downstream in B9 scoring.
    pivot_highs, pivot_lows = _swing_highs_lows(h_slice, l_slice, n=5)
    if pivot_highs:
        swing_high_idx, s_high = pivot_highs[-1]
    else:
        swing_high_idx = int(h_slice.values.argmax())
        s_high = float(h_slice.iloc[swing_high_idx])
    if pivot_lows:
        swing_low_idx, s_low = pivot_lows[-1]
    else:
        swing_low_idx = int(l_slice.values.argmin())
        s_low = float(l_slice.iloc[swing_low_idx])
    price = float(close.iloc[-1])

    rng = s_high - s_low
    if rng < 1e-9:
        defaults["swing_high"] = s_high
        defaults["swing_low"] = s_low
        return defaults

    # Trend: which came first chronologically in the lookback window
    trend = "up" if swing_low_idx < swing_high_idx else "down"

    # Fib ratios
    fib_ratios = {
        "0%": 0.0, "23.6%": 0.236, "38.2%": 0.382,
        "50%": 0.5, "61.8%": 0.618, "78.6%": 0.786, "100%": 1.0,
    }

    # Levels from retracement direction
    # Uptrend: 0% = swing_low, 100% = swing_high; retracement goes from high back down
    # Downtrend: 0% = swing_high, 100% = swing_low; retracement goes from low back up
    levels = {}
    if trend == "up":
        for name, ratio in fib_ratios.items():
            levels[name] = s_high - ratio * rng
    else:
        for name, ratio in fib_ratios.items():
            levels[name] = s_low + ratio * rng

    # Nearest level
    nearest_name = "0%"
    nearest_dist = float("inf")
    for name, lvl in levels.items():
        dist = abs(price - lvl) / max(price, 1e-9) * 100
        if dist < nearest_dist:
            nearest_dist = dist
            nearest_name = name

    # At fib zone: within 0.3% of any key level (38.2, 50, 61.8, 78.6)
    key_levels = ["38.2%", "50%", "61.8%", "78.6%"]
    at_fib_zone = False
    for kl in key_levels:
        if kl in levels:
            dist_pct = abs(price - levels[kl]) / max(price, 1e-9) * 100
            if dist_pct <= 0.3:
                at_fib_zone = True
                break

    # Fib bias: based on position relative to 50% level
    fib_50 = levels.get("50%", (s_high + s_low) / 2)
    tolerance = rng * 0.02  # 2% of range for neutral zone
    if price > fib_50 + tolerance:
        fib_bias = "bullish" if trend == "up" else "bearish"
    elif price < fib_50 - tolerance:
        fib_bias = "bearish" if trend == "up" else "bullish"
    else:
        fib_bias = "neutral"

    return {
        "swing_high": s_high,
        "swing_low": s_low,
        "levels": levels,
        "nearest_level": nearest_name,
        "nearest_dist_pct": round(nearest_dist, 4),
        "at_fib_zone": at_fib_zone,
        "fib_bias": fib_bias,
        "trend": trend,
    }


# ════════════════════════════════════════════════════════════════
# 2. FAIR VALUE GAP (FVG)
# ════════════════════════════════════════════════════════════════

def fair_value_gaps(open_s: pd.Series, high: pd.Series, low: pd.Series,
                    close: pd.Series, lookback: int = 30) -> dict:
    """
    Detect Fair Value Gaps -- 3-candle imbalances where the middle candle's
    range creates a gap between candle 1 and candle 3.

    Bullish FVG: candle[i-2].high < candle[i].low  (gap up, unfilled demand)
    Bearish FVG: candle[i-2].low > candle[i].high  (gap down, unfilled supply)

    Only UNFILLED gaps are returned (subsequent price has not traded through).

    Returns:
        bullish_fvgs, bearish_fvgs: lists of (top, bottom, bar_index)
        nearest_bullish_fvg, nearest_bearish_fvg: (top, bottom) or None
        price_in_bullish_fvg, price_in_bearish_fvg: bool
        fvg_count_bullish, fvg_count_bearish: int
    """
    defaults = {
        "bullish_fvgs": [], "bearish_fvgs": [],
        "nearest_bullish_fvg": None, "nearest_bearish_fvg": None,
        "price_in_bullish_fvg": False, "price_in_bearish_fvg": False,
        "fvg_count_bullish": 0, "fvg_count_bearish": 0,
    }
    n = len(close)
    if n < 4:
        return defaults

    lb = min(lookback, n - 2)
    start = max(2, n - lb)
    price = float(close.iloc[-1])

    bullish_fvgs: list[tuple[float, float, int]] = []
    bearish_fvgs: list[tuple[float, float, int]] = []

    for i in range(start, n):
        h_prev2 = float(high.iloc[i - 2])
        l_curr = float(low.iloc[i])
        l_prev2 = float(low.iloc[i - 2])
        h_curr = float(high.iloc[i])

        # Bullish FVG: gap between candle[i-2] high and candle[i] low
        if l_curr > h_prev2:
            gap_top = l_curr
            gap_bottom = h_prev2
            # Check if filled by any subsequent candle
            filled = False
            for j in range(i + 1, n):
                if float(low.iloc[j]) <= gap_bottom:
                    filled = True
                    break
            if not filled:
                bullish_fvgs.append((gap_top, gap_bottom, i))

        # Bearish FVG: gap between candle[i-2] low and candle[i] high
        if h_curr < l_prev2:
            gap_top = l_prev2
            gap_bottom = h_curr
            # Check if filled by any subsequent candle
            filled = False
            for j in range(i + 1, n):
                if float(high.iloc[j]) >= gap_top:
                    filled = True
                    break
            if not filled:
                bearish_fvgs.append((gap_top, gap_bottom, i))

    # Nearest unfilled bullish FVG below price
    nearest_bull = None
    bull_min_dist = float("inf")
    in_bull = False
    for top, bot, _ in bullish_fvgs:
        if bot <= price <= top:
            in_bull = True
            nearest_bull = (top, bot)
            break
        if top <= price:
            dist = price - top
            if dist < bull_min_dist:
                bull_min_dist = dist
                nearest_bull = (top, bot)

    # Nearest unfilled bearish FVG above price
    nearest_bear = None
    bear_min_dist = float("inf")
    in_bear = False
    for top, bot, _ in bearish_fvgs:
        if bot <= price <= top:
            in_bear = True
            nearest_bear = (top, bot)
            break
        if bot >= price:
            dist = bot - price
            if dist < bear_min_dist:
                bear_min_dist = dist
                nearest_bear = (top, bot)

    return {
        "bullish_fvgs": bullish_fvgs,
        "bearish_fvgs": bearish_fvgs,
        "nearest_bullish_fvg": nearest_bull,
        "nearest_bearish_fvg": nearest_bear,
        "price_in_bullish_fvg": in_bull,
        "price_in_bearish_fvg": in_bear,
        "fvg_count_bullish": len(bullish_fvgs),
        "fvg_count_bearish": len(bearish_fvgs),
    }


# ════════════════════════════════════════════════════════════════
# 3. SUPPLY / DEMAND ZONES
# ════════════════════════════════════════════════════════════════

def supply_demand_zones(open_s: pd.Series, high: pd.Series, low: pd.Series,
                        close: pd.Series, volume: pd.Series,
                        lookback: int = 50) -> dict:
    """
    Identify institutional supply and demand zones.

    Demand zone: consolidation base before an impulsive UP move (> 2x ATR).
    Supply zone: consolidation base before an impulsive DOWN move (> 2x ATR).

    Zones are invalidated if price closes through them.

    Returns:
        demand_zones, supply_zones: lists of (zone_high, zone_low)
        price_in_demand, price_in_supply: bool (within 0.3% counts)
        nearest_demand_dist_pct, nearest_supply_dist_pct: float
    """
    defaults = {
        "demand_zones": [], "supply_zones": [],
        "price_in_demand": False, "price_in_supply": False,
        "nearest_demand_dist_pct": 99.0, "nearest_supply_dist_pct": 99.0,
    }
    n = len(close)
    if n < 10:
        return defaults

    lb = min(lookback, n - 3)
    start = max(0, n - lb)
    atr_s = _atr_series(high, low, close, period=14)
    price = float(close.iloc[-1])

    demand_zones: list[tuple[float, float]] = []
    supply_zones: list[tuple[float, float]] = []

    # Scan for base candles followed by impulsive moves
    impulse_bars = 3
    for i in range(start, n - impulse_bars):
        atr_val = float(atr_s.iloc[i])
        if atr_val < 1e-9:
            continue

        base_high = float(high.iloc[i])
        base_low = float(low.iloc[i])

        # Measure move over next impulse_bars candles
        future_high = float(high.iloc[i + 1: i + 1 + impulse_bars].max())
        future_low = float(low.iloc[i + 1: i + 1 + impulse_bars].min())
        up_move = future_high - base_high
        down_move = base_low - future_low

        # Demand zone: strong up-move after this candle
        if up_move > 2.0 * atr_val:
            # Check if zone is still valid (not broken by a close below base_low)
            valid = True
            for j in range(i + 1 + impulse_bars, n):
                if float(close.iloc[j]) < base_low:
                    valid = False
                    break
            if valid:
                demand_zones.append((base_high, base_low))

        # Supply zone: strong down-move after this candle
        if down_move > 2.0 * atr_val:
            valid = True
            for j in range(i + 1 + impulse_bars, n):
                if float(close.iloc[j]) > base_high:
                    valid = False
                    break
            if valid:
                supply_zones.append((base_high, base_low))

    # Deduplicate overlapping zones: keep unique zones only
    demand_zones = _deduplicate_zones(demand_zones)
    supply_zones = _deduplicate_zones(supply_zones)

    # Price in / near zone
    in_demand = False
    nearest_demand_dist = 99.0
    proximity = 0.003  # 0.3%
    for zh, zl in demand_zones:
        if zl * (1 - proximity) <= price <= zh * (1 + proximity):
            in_demand = True
            nearest_demand_dist = 0.0
            break
        if price > zh:
            dist = (price - zh) / max(price, 1e-9) * 100
        else:
            dist = (zl - price) / max(price, 1e-9) * 100
        nearest_demand_dist = min(nearest_demand_dist, max(dist, 0.0))

    in_supply = False
    nearest_supply_dist = 99.0
    for zh, zl in supply_zones:
        if zl * (1 - proximity) <= price <= zh * (1 + proximity):
            in_supply = True
            nearest_supply_dist = 0.0
            break
        if price < zl:
            dist = (zl - price) / max(price, 1e-9) * 100
        else:
            dist = (price - zh) / max(price, 1e-9) * 100
        nearest_supply_dist = min(nearest_supply_dist, max(dist, 0.0))

    return {
        "demand_zones": demand_zones,
        "supply_zones": supply_zones,
        "price_in_demand": in_demand,
        "price_in_supply": in_supply,
        "nearest_demand_dist_pct": round(nearest_demand_dist, 4),
        "nearest_supply_dist_pct": round(nearest_supply_dist, 4),
    }


def _deduplicate_zones(zones: list[tuple[float, float]],
                       overlap_pct: float = 0.5) -> list[tuple[float, float]]:
    """Merge zones that overlap by more than overlap_pct of the smaller zone."""
    if not zones:
        return zones
    # Sort by zone_low ascending
    sorted_z = sorted(zones, key=lambda z: z[1])
    merged: list[tuple[float, float]] = [sorted_z[0]]
    for zh, zl in sorted_z[1:]:
        prev_h, prev_l = merged[-1]
        overlap = max(0, min(prev_h, zh) - max(prev_l, zl))
        smaller_range = min(prev_h - prev_l, zh - zl)
        if smaller_range > 1e-9 and overlap / smaller_range > overlap_pct:
            # Merge: expand the previous zone
            merged[-1] = (max(prev_h, zh), min(prev_l, zl))
        else:
            merged.append((zh, zl))
    return merged


# ════════════════════════════════════════════════════════════════
# 4. LIQUIDITY SWEEP
# ════════════════════════════════════════════════════════════════

def liquidity_sweep(high: pd.Series, low: pd.Series, close: pd.Series,
                    lookback: int = 20) -> dict:
    """
    Detect liquidity sweeps -- price breaks a key level (where stops cluster)
    then reverses, trapping breakout traders.

    Bullish sweep: price breaks BELOW recent swing low, then closes back above.
    Bearish sweep: price breaks ABOVE recent swing high, then closes back below.

    Uses 5-bar swing detection for key levels.

    Returns:
        bullish_sweep, bearish_sweep: bool (last 3 bars)
        sweep_level: the price level that was swept
        sweep_depth_pct: how far past the level price went before reversing
        bars_since_sweep: how many bars ago (0 = current bar)
    """
    defaults = {
        "bullish_sweep": False, "bearish_sweep": False,
        "sweep_level": 0.0, "sweep_depth_pct": 0.0,
        "bars_since_sweep": -1,
    }
    n = len(close)
    if n < 12:  # need enough for swing detection + check window
        return defaults

    lb = min(lookback, n - 5)
    swing_highs, swing_lows = _swing_highs_lows(high, low, n=5)

    # Filter swings within lookback (but not the very last few bars so we can
    # detect breaks against them)
    recent_sh = [p for idx, p in swing_highs if idx >= n - lb and idx < n - 2]
    recent_sl = [p for idx, p in swing_lows if idx >= n - lb and idx < n - 2]

    if not recent_sh and not recent_sl:
        return defaults

    # Check last 3 bars for sweep patterns
    for bars_ago in range(3):
        bar_idx = n - 1 - bars_ago
        if bar_idx < 1:
            continue

        bar_low = float(low.iloc[bar_idx])
        bar_high = float(high.iloc[bar_idx])
        bar_close = float(close.iloc[bar_idx])

        # Bullish sweep: wick below a swing low, close back above
        for sl_price in recent_sl:
            if bar_low < sl_price and bar_close > sl_price:
                depth = (sl_price - bar_low) / max(sl_price, 1e-9) * 100
                return {
                    "bullish_sweep": True, "bearish_sweep": False,
                    "sweep_level": sl_price,
                    "sweep_depth_pct": round(depth, 4),
                    "bars_since_sweep": bars_ago,
                }

        # Bearish sweep: wick above a swing high, close back below
        for sh_price in recent_sh:
            if bar_high > sh_price and bar_close < sh_price:
                depth = (bar_high - sh_price) / max(sh_price, 1e-9) * 100
                return {
                    "bullish_sweep": False, "bearish_sweep": True,
                    "sweep_level": sh_price,
                    "sweep_depth_pct": round(depth, 4),
                    "bars_since_sweep": bars_ago,
                }

    return defaults


# ════════════════════════════════════════════════════════════════
# 5. ORDER BLOCKS (SMC)
# ════════════════════════════════════════════════════════════════

def order_blocks(open_s: pd.Series, high: pd.Series, low: pd.Series,
                 close: pd.Series, volume: pd.Series,
                 lookback: int = 30) -> dict:
    """
    SMC Order Blocks -- the last opposing candle before an impulsive move.

    Bullish OB: last bearish candle before a strong up-move.
    Bearish OB: last bullish candle before a strong down-move.

    'Strong move' = 3+ consecutive candles in one direction OR single candle > 2x ATR.
    An OB is invalidated if price passes completely through it.

    Returns:
        bullish_obs, bearish_obs: list of (ob_high, ob_low, bar_index)
        price_in_bullish_ob, price_in_bearish_ob: bool
        nearest_bullish_ob, nearest_bearish_ob: (high, low) or None
    """
    defaults = {
        "bullish_obs": [], "bearish_obs": [],
        "price_in_bullish_ob": False, "price_in_bearish_ob": False,
        "nearest_bullish_ob": None, "nearest_bearish_ob": None,
    }
    n = len(close)
    if n < 6:
        return defaults

    lb = min(lookback, n - 4)
    start = max(0, n - lb)
    atr_s = _atr_series(high, low, close, period=14)
    price = float(close.iloc[-1])

    bullish_obs: list[tuple[float, float, int]] = []
    bearish_obs: list[tuple[float, float, int]] = []

    for i in range(start, n - 3):
        o_i = float(open_s.iloc[i])
        c_i = float(close.iloc[i])
        h_i = float(high.iloc[i])
        l_i = float(low.iloc[i])
        atr_val = float(atr_s.iloc[i])
        if atr_val < 1e-9:
            continue

        is_bearish_candle = c_i < o_i
        is_bullish_candle = c_i > o_i

        # Check for strong move after candle i
        # Method 1: 3 consecutive candles in one direction
        consecutive_up = 0
        consecutive_down = 0
        for j in range(i + 1, min(i + 4, n)):
            if float(close.iloc[j]) > float(open_s.iloc[j]):
                consecutive_up += 1
            elif float(close.iloc[j]) < float(open_s.iloc[j]):
                consecutive_down += 1

        # Method 2: single candle > 2x ATR
        big_up = False
        big_down = False
        if i + 1 < n:
            next_move = float(close.iloc[i + 1]) - float(open_s.iloc[i + 1])
            if next_move > 2 * atr_val:
                big_up = True
            elif next_move < -2 * atr_val:
                big_down = True

        strong_up = consecutive_up >= 3 or big_up
        strong_down = consecutive_down >= 3 or big_down

        # Bullish OB: bearish candle before strong up-move
        if is_bearish_candle and strong_up:
            # Validate: not broken (price never closed below OB low after formation)
            valid = True
            for j in range(i + 1, n):
                if float(close.iloc[j]) < l_i:
                    valid = False
                    break
            if valid:
                bullish_obs.append((h_i, l_i, i))

        # Bearish OB: bullish candle before strong down-move
        if is_bullish_candle and strong_down:
            valid = True
            for j in range(i + 1, n):
                if float(close.iloc[j]) > h_i:
                    valid = False
                    break
            if valid:
                bearish_obs.append((h_i, l_i, i))

    # Check price position relative to OBs
    in_bull_ob = False
    nearest_bull = None
    bull_dist = float("inf")
    for ob_h, ob_l, _ in bullish_obs:
        if ob_l <= price <= ob_h:
            in_bull_ob = True
            nearest_bull = (ob_h, ob_l)
            break
        dist = abs(price - (ob_h + ob_l) / 2)
        if dist < bull_dist:
            bull_dist = dist
            nearest_bull = (ob_h, ob_l)

    in_bear_ob = False
    nearest_bear = None
    bear_dist = float("inf")
    for ob_h, ob_l, _ in bearish_obs:
        if ob_l <= price <= ob_h:
            in_bear_ob = True
            nearest_bear = (ob_h, ob_l)
            break
        dist = abs(price - (ob_h + ob_l) / 2)
        if dist < bear_dist:
            bear_dist = dist
            nearest_bear = (ob_h, ob_l)

    return {
        "bullish_obs": bullish_obs,
        "bearish_obs": bearish_obs,
        "price_in_bullish_ob": in_bull_ob,
        "price_in_bearish_ob": in_bear_ob,
        "nearest_bullish_ob": nearest_bull,
        "nearest_bearish_ob": nearest_bear,
    }


# ════════════════════════════════════════════════════════════════
# 6. MARKET STRUCTURE (BOS / ChoCH) -- SMC/ICT
# ════════════════════════════════════════════════════════════════

def market_structure(high: pd.Series, low: pd.Series, close: pd.Series,
                     lookback: int = 30) -> dict:
    """
    SMC/ICT Market Structure -- Break of Structure (BOS) and
    Change of Character (ChoCH).

    BOS (trend continuation): price breaks the most recent swing point in
    the direction of the existing trend.
    ChoCH (trend reversal): price breaks a swing point against the trend,
    signalling a character change.

    Returns:
        trend: 'bullish', 'bearish', or 'ranging'
        bullish_bos, bearish_bos: bool (last 3 bars)
        bullish_choch, bearish_choch: bool (last 5 bars)
        last_swing_high, last_swing_low: float
        structure_quality: 0-100
    """
    defaults = {
        "trend": "ranging",
        "bullish_bos": False, "bearish_bos": False,
        "bullish_choch": False, "bearish_choch": False,
        "last_swing_high": 0.0, "last_swing_low": 0.0,
        "structure_quality": 0,
    }
    n = len(close)
    if n < 15:
        return defaults

    lb = min(lookback, n)
    h_slice = high.iloc[-lb:]
    l_slice = low.iloc[-lb:]

    swing_highs, swing_lows = _swing_highs_lows(h_slice, l_slice, n=5)

    if len(swing_highs) < 2 or len(swing_lows) < 2:
        # Not enough structure
        last_sh = swing_highs[-1][1] if swing_highs else float(h_slice.max())
        last_sl = swing_lows[-1][1] if swing_lows else float(l_slice.min())
        defaults["last_swing_high"] = last_sh
        defaults["last_swing_low"] = last_sl
        return defaults

    last_sh_idx, last_sh = swing_highs[-1]
    prev_sh_idx, prev_sh = swing_highs[-2]
    last_sl_idx, last_sl = swing_lows[-1]
    prev_sl_idx, prev_sl = swing_lows[-2]

    # Determine trend from swing sequence
    hh = last_sh > prev_sh  # higher high
    hl = last_sl > prev_sl  # higher low
    lh = last_sh < prev_sh  # lower high
    ll = last_sl < prev_sl  # lower low

    if hh and hl:
        trend = "bullish"
    elif lh and ll:
        trend = "bearish"
    else:
        trend = "ranging"

    # Structure quality: how clean is the swing sequence?
    quality = 50  # base
    if hh and hl:
        quality += 25  # clean bullish
    elif lh and ll:
        quality += 25  # clean bearish
    # Bonus for sequential ordering (highs and lows alternate properly)
    if len(swing_highs) >= 2 and len(swing_lows) >= 2:
        # Check alternation
        all_swings = (
            [(idx, "H", p) for idx, p in swing_highs[-3:]]
            + [(idx, "L", p) for idx, p in swing_lows[-3:]]
        )
        all_swings.sort(key=lambda x: x[0])
        alternating = True
        for k in range(1, len(all_swings)):
            if all_swings[k][1] == all_swings[k - 1][1]:
                alternating = False
                break
        if alternating:
            quality += 25

    quality = _clamp(quality, 0, 100)

    # BOS detection (last 3 bars)
    price = float(close.iloc[-1])
    bullish_bos = False
    bearish_bos = False
    bos_window = 3
    for bars_ago in range(bos_window):
        idx = len(h_slice) - 1 - bars_ago
        if idx < 0:
            continue
        bar_high = float(h_slice.iloc[idx])
        bar_low = float(l_slice.iloc[idx])
        bar_close = float(close.iloc[-lb + idx]) if (-lb + idx) < n else price

        # Bullish BOS: break above last swing high in bullish trend
        if trend in ("bullish", "ranging") and bar_high > last_sh:
            bullish_bos = True
        # Bearish BOS: break below last swing low in bearish trend
        if trend in ("bearish", "ranging") and bar_low < last_sl:
            bearish_bos = True

    # ChoCH detection (last 5 bars)
    bullish_choch = False
    bearish_choch = False
    choch_window = 5
    for bars_ago in range(choch_window):
        idx = len(h_slice) - 1 - bars_ago
        if idx < 0:
            continue
        bar_high = float(h_slice.iloc[idx])
        bar_low = float(l_slice.iloc[idx])

        # Bullish ChoCH: in bearish trend, break above last lower high
        if trend == "bearish" and bar_high > last_sh:
            bullish_choch = True
        # Bearish ChoCH: in bullish trend, break below last higher low
        if trend == "bullish" and bar_low < last_sl:
            bearish_choch = True

    return {
        "trend": trend,
        "bullish_bos": bullish_bos,
        "bearish_bos": bearish_bos,
        "bullish_choch": bullish_choch,
        "bearish_choch": bearish_choch,
        "last_swing_high": last_sh,
        "last_swing_low": last_sl,
        "structure_quality": int(quality),
    }


# ════════════════════════════════════════════════════════════════
# 7. VOLUME PROFILE
# ════════════════════════════════════════════════════════════════

def volume_profile(close: pd.Series, volume: pd.Series, high: pd.Series,
                   low: pd.Series, num_bins: int = 20,
                   lookback: int = 50) -> dict:
    """
    Volume Profile -- distribution of traded volume across price levels.

    POC (Point of Control): price level with highest traded volume.
    Value Area: price range containing 70% of total volume.
    HVN/LVN: high/low volume nodes.

    Returns:
        poc, vah, val: float price levels
        price_vs_poc: 'above', 'below', or 'at'
        price_in_value_area: bool
        at_hvn, at_lvn: bool
        poc_dist_pct: float
    """
    defaults = {
        "poc": 0.0, "vah": 0.0, "val": 0.0,
        "price_vs_poc": "unknown", "price_in_value_area": False,
        "at_hvn": False, "at_lvn": False, "poc_dist_pct": 99.0,
    }
    n = len(close)
    if n < 5:
        return defaults

    lb = min(lookback, n)
    h_s = high.iloc[-lb:]
    l_s = low.iloc[-lb:]
    c_s = close.iloc[-lb:]
    v_s = volume.iloc[-lb:]

    price_min = _safe_float(l_s.min())
    price_max = _safe_float(h_s.max())
    price_range = price_max - price_min
    if price_range < 1e-9 or np.isnan(price_range):
        defaults["poc"] = float(c_s.iloc[-1])
        defaults["vah"] = defaults["poc"]
        defaults["val"] = defaults["poc"]
        return defaults

    # Create bins
    num_bins = max(num_bins, 2)
    bin_size = price_range / num_bins
    bin_volumes = np.zeros(num_bins)
    bin_mids = np.array([
        price_min + (i + 0.5) * bin_size for i in range(num_bins)
    ])

    # Distribute each candle's volume across bins its range covers
    for k in range(len(c_s)):
        candle_low = float(l_s.iloc[k])
        candle_high = float(h_s.iloc[k])
        candle_vol = float(v_s.iloc[k])
        if candle_vol <= 0 or np.isnan(candle_vol):
            continue
        if np.isnan(candle_low) or np.isnan(candle_high):
            continue

        low_bin = int((candle_low - price_min) / max(bin_size, 1e-9))
        high_bin = int((candle_high - price_min) / max(bin_size, 1e-9))
        low_bin = _clamp(low_bin, 0, num_bins - 1)
        high_bin = _clamp(high_bin, 0, num_bins - 1)

        n_bins_covered = high_bin - low_bin + 1
        vol_per_bin = candle_vol / max(n_bins_covered, 1)
        for b in range(int(low_bin), int(high_bin) + 1):
            bin_volumes[b] += vol_per_bin

    # POC
    poc_idx = int(np.argmax(bin_volumes))
    poc = float(bin_mids[poc_idx])

    # Value Area: expand from POC until 70% of volume is captured
    total_vol = float(bin_volumes.sum())
    if total_vol < 1e-9:
        defaults["poc"] = poc
        defaults["vah"] = poc
        defaults["val"] = poc
        return defaults

    va_target = total_vol * 0.70
    va_vol = float(bin_volumes[poc_idx])
    va_low_idx = poc_idx
    va_high_idx = poc_idx

    while va_vol < va_target:
        expand_up = (
            float(bin_volumes[va_high_idx + 1])
            if va_high_idx + 1 < num_bins else 0.0
        )
        expand_down = (
            float(bin_volumes[va_low_idx - 1])
            if va_low_idx - 1 >= 0 else 0.0
        )

        if expand_up == 0 and expand_down == 0:
            break

        if expand_up >= expand_down:
            va_high_idx += 1
            va_vol += expand_up
        else:
            va_low_idx -= 1
            va_vol += expand_down

    vah = float(bin_mids[va_high_idx] + bin_size / 2)
    val = float(bin_mids[va_low_idx] - bin_size / 2)

    # HVN / LVN detection
    avg_vol = total_vol / max(num_bins, 1)
    price_now = float(close.iloc[-1])
    cur_bin = int((price_now - price_min) / max(bin_size, 1e-9))
    cur_bin = int(_clamp(cur_bin, 0, num_bins - 1))
    cur_bin_vol = float(bin_volumes[cur_bin])

    at_hvn = cur_bin_vol > avg_vol * 1.5
    at_lvn = cur_bin_vol < avg_vol * 0.5

    # Price vs POC
    poc_dist = abs(price_now - poc) / max(price_now, 1e-9) * 100
    if poc_dist <= 0.2:
        price_vs_poc = "at"
    elif price_now > poc:
        price_vs_poc = "above"
    else:
        price_vs_poc = "below"

    return {
        "poc": round(poc, 8),
        "vah": round(vah, 8),
        "val": round(val, 8),
        "price_vs_poc": price_vs_poc,
        "price_in_value_area": val <= price_now <= vah,
        "at_hvn": at_hvn,
        "at_lvn": at_lvn,
        "poc_dist_pct": round(poc_dist, 4),
    }


# ════════════════════════════════════════════════════════════════
# 8. ICT OPTIMAL TRADE ENTRY (OTE)
# ════════════════════════════════════════════════════════════════

def ict_optimal_trade_entry(high: pd.Series, low: pd.Series, close: pd.Series,
                            lookback: int = 30) -> dict:
    """
    ICT Optimal Trade Entry -- the 'sweet spot' between the 61.8% and 78.6%
    Fibonacci retracement of the most recent impulse move, optionally
    confluent with a Fair Value Gap.

    Returns:
        in_ote_zone: bool
        ote_bias: 'bullish', 'bearish', or 'none'
        ote_high, ote_low: float (zone boundaries)
        impulse_strength: float (% of price)
        fvg_confluence: bool
        discount_zone, premium_zone: bool
    """
    defaults = {
        "in_ote_zone": False, "ote_bias": "none",
        "ote_high": 0.0, "ote_low": 0.0,
        "impulse_strength": 0.0, "fvg_confluence": False,
        "discount_zone": False, "premium_zone": False,
    }
    n = len(close)
    if n < 10:
        return defaults

    lb = min(lookback, n)
    atr_s = _atr_series(high, low, close, period=14)
    price = float(close.iloc[-1])

    # 2026-04-16 (post-audit): pick the MOST RECENT qualifying impulse,
    # not the largest over the lookback. Scanning backwards from the
    # current bar, accept the first impulse of magnitude >= 1.5*ATR.
    # Previously the function picked the biggest move in the lookback,
    # meaning a 25-bar-old mega-candle dominated. Price might be sitting
    # nowhere near that stale zone but in_ote_zone could still flip True.
    best_impulse_start = -1
    best_impulse_end = -1
    best_impulse_dir = 0  # +1 up, -1 down
    best_impulse_size = 0.0

    start = max(0, n - lb)
    found = False
    for i in range(n - 3, start - 1, -1):
        if found:
            break
        atr_val = float(atr_s.iloc[i])
        if atr_val < 1e-9:
            continue

        cum_up = 0.0
        cum_down = 0.0
        for j in range(i + 1, min(i + 9, n)):
            move = float(close.iloc[j]) - float(close.iloc[j - 1])
            if move > 0:
                cum_up += move
            else:
                cum_down += abs(move)

            net = cum_up - cum_down
            if net > 1.5 * atr_val:
                best_impulse_size = net
                best_impulse_start = i
                best_impulse_end = j
                best_impulse_dir = 1
                found = True
                break

            net_down = cum_down - cum_up
            if net_down > 1.5 * atr_val:
                best_impulse_size = net_down
                best_impulse_start = i
                best_impulse_end = j
                best_impulse_dir = -1
                found = True
                break

    if best_impulse_start < 0:
        return defaults

    # Impulse anchors
    if best_impulse_dir == 1:
        imp_low = float(low.iloc[best_impulse_start])
        imp_high = float(high.iloc[best_impulse_end])
    else:
        imp_high = float(high.iloc[best_impulse_start])
        imp_low = float(low.iloc[best_impulse_end])

    imp_range = imp_high - imp_low
    if imp_range < 1e-9:
        return defaults

    impulse_strength = imp_range / max(price, 1e-9) * 100

    # OTE zone: 61.8% to 78.6% retracement
    if best_impulse_dir == 1:
        # Bullish impulse: OTE is a pullback zone below
        ote_high = imp_high - 0.618 * imp_range
        ote_low = imp_high - 0.786 * imp_range
        ote_bias = "bullish"
    else:
        # Bearish impulse: OTE is a pullback zone above
        ote_low = imp_low + 0.618 * imp_range
        ote_high = imp_low + 0.786 * imp_range
        ote_bias = "bearish"

    in_ote = ote_low <= price <= ote_high

    # 50% fib for discount/premium
    fib_50 = imp_low + 0.5 * imp_range
    discount_zone = price < fib_50 and best_impulse_dir == 1
    premium_zone = price > fib_50 and best_impulse_dir == -1

    # FVG confluence: check if any FVG overlaps the OTE zone
    fvg_confluence = False
    if n >= 4:
        # Build a simple dummy open series from close for the FVG call
        open_approx = close.shift(1).fillna(close)
        fvg_res = fair_value_gaps(open_approx, high, low, close,
                                  lookback=min(lookback, n - 2))
        all_fvgs = fvg_res["bullish_fvgs"] + fvg_res["bearish_fvgs"]
        for ftop, fbot, _ in all_fvgs:
            # Check overlap between FVG [fbot, ftop] and OTE [ote_low, ote_high]
            if ftop >= ote_low and fbot <= ote_high:
                fvg_confluence = True
                break

    return {
        "in_ote_zone": in_ote,
        "ote_bias": ote_bias,
        "ote_high": round(ote_high, 8),
        "ote_low": round(ote_low, 8),
        "impulse_strength": round(impulse_strength, 4),
        "fvg_confluence": fvg_confluence,
        "discount_zone": discount_zone,
        "premium_zone": premium_zone,
    }


# ════════════════════════════════════════════════════════════════
# 9. PRICE ACTION PATTERNS
# ════════════════════════════════════════════════════════════════

def price_action_patterns(open_s: pd.Series, high: pd.Series,
                          low: pd.Series, close: pd.Series) -> dict:
    """
    Detect classic candlestick patterns on the last few bars.

    Single-candle: Hammer, Shooting Star, Doji, Marubozu.
    Two-candle: Bullish/Bearish Engulfing, Tweezer Top/Bottom.
    Three-candle: Morning Star, Evening Star.

    Returns:
        bullish_patterns, bearish_patterns: list of str
        pattern_score: int (+1..+3 bullish, -1..-3 bearish)
        strongest_pattern: str
        has_reversal_pattern: bool
    """
    defaults = {
        "bullish_patterns": [], "bearish_patterns": [],
        "pattern_score": 0, "strongest_pattern": "none",
        "has_reversal_pattern": False,
    }
    n = len(close)
    if n < 1:
        return defaults

    bullish: list[str] = []
    bearish: list[str] = []

    # --- Single-candle patterns (last bar) ---
    o = float(open_s.iloc[-1])
    h = float(high.iloc[-1])
    l = float(low.iloc[-1])
    c = float(close.iloc[-1])

    body = abs(c - o)
    total_range = h - l
    if total_range < 1e-9:
        total_range = 1e-9

    upper_wick = h - max(o, c)
    lower_wick = min(o, c) - l

    # Doji: body < 10% of range
    if body < 0.10 * total_range:
        # Neutral, but note it
        if n >= 2:
            prev_c = float(close.iloc[-2])
            if c > prev_c:
                bullish.append("doji")
            else:
                bearish.append("doji")

    # Hammer / Hanging Man: lower wick >= 2x body, small upper wick
    if lower_wick >= 2 * max(body, 1e-9) and upper_wick < body * 0.5:
        if c > o:
            bullish.append("hammer")
        else:
            bearish.append("hanging_man")

    # Shooting Star / Inverted Hammer: upper wick >= 2x body
    if upper_wick >= 2 * max(body, 1e-9) and lower_wick < body * 0.5:
        if c < o:
            bearish.append("shooting_star")
        else:
            bullish.append("inverted_hammer")

    # Marubozu: body > 90% of range
    if body > 0.90 * total_range:
        if c > o:
            bullish.append("marubozu")
        else:
            bearish.append("marubozu")

    # --- Two-candle patterns (last 2 bars) ---
    if n >= 2:
        o1 = float(open_s.iloc[-2])
        h1 = float(high.iloc[-2])
        l1 = float(low.iloc[-2])
        c1 = float(close.iloc[-2])
        body1 = abs(c1 - o1)

        # Bullish Engulfing: bearish candle then bullish candle engulfing
        if c1 < o1 and c > o:
            if o <= c1 and c >= o1:
                bullish.append("bullish_engulfing")

        # Bearish Engulfing: bullish candle then bearish candle engulfing
        if c1 > o1 and c < o:
            if o >= c1 and c <= o1:
                bearish.append("bearish_engulfing")

        # Tweezer Bottom: adjacent candles with nearly equal lows
        low_diff = abs(l - l1) / max(total_range, 1e-9)
        if low_diff < 0.05 and c > o and c1 < o1:
            bullish.append("tweezer_bottom")

        # Tweezer Top: adjacent candles with nearly equal highs
        high_diff = abs(h - h1) / max(total_range, 1e-9)
        if high_diff < 0.05 and c < o and c1 > o1:
            bearish.append("tweezer_top")

    # --- Three-candle patterns (last 3 bars) ---
    if n >= 3:
        o2 = float(open_s.iloc[-3])
        h2 = float(high.iloc[-3])
        l2 = float(low.iloc[-3])
        c2 = float(close.iloc[-3])
        body2 = abs(c2 - o2)

        o1 = float(open_s.iloc[-2])
        c1 = float(close.iloc[-2])
        body1 = abs(c1 - o1)
        range1 = float(high.iloc[-2]) - float(low.iloc[-2])

        # Small middle body (star)
        is_star = body1 < 0.3 * max(range1, 1e-9)

        # Morning Star: bearish, small star, bullish
        if c2 < o2 and is_star and c > o and body > 0.5 * body2:
            bullish.append("morning_star")

        # Evening Star: bullish, small star, bearish
        if c2 > o2 and is_star and c < o and body > 0.5 * body2:
            bearish.append("evening_star")

    # Score and rank
    pattern_weights = {
        "morning_star": 3, "evening_star": 3,
        "bullish_engulfing": 2, "bearish_engulfing": 2,
        "hammer": 1, "shooting_star": 1,
        "tweezer_bottom": 2, "tweezer_top": 2,
        "inverted_hammer": 1, "hanging_man": 1,
        "marubozu": 1, "doji": 1,
    }

    score = 0
    strongest = "none"
    best_weight = 0

    for p in bullish:
        w = pattern_weights.get(p, 1)
        score += w
        if w > best_weight:
            best_weight = w
            strongest = p

    for p in bearish:
        w = pattern_weights.get(p, 1)
        score -= w
        if w > best_weight:
            best_weight = w
            strongest = p

    score = _clamp(score, -3, 3)

    reversal_patterns = {
        "morning_star", "evening_star", "bullish_engulfing",
        "bearish_engulfing", "hammer", "shooting_star",
        "tweezer_bottom", "tweezer_top",
    }
    has_reversal = bool(
        set(bullish) & reversal_patterns or set(bearish) & reversal_patterns
    )

    return {
        "bullish_patterns": bullish,
        "bearish_patterns": bearish,
        "pattern_score": int(score),
        "strongest_pattern": strongest,
        "has_reversal_pattern": has_reversal,
    }


# ════════════════════════════════════════════════════════════════
# COMPOSITE: Aggregate smart money signals for scoring engine
# ════════════════════════════════════════════════════════════════

def compute_smart_money_signals(open_s: pd.Series, high: pd.Series,
                                low: pd.Series, close: pd.Series,
                                volume: pd.Series) -> dict:
    """
    Master function: compute ALL smart money indicators in one pass.
    Called by mcp_brain._fetch_exchange_indicators() for each TF.

    Groups results into three composite signals for the scoring engine:

    1. smc_structure (0-100): Order blocks + market structure (BOS/ChoCH).
    2. smc_entry (0-100): FVG + Fibonacci + ICT OTE + Supply/Demand.
    3. smc_confirmation (0-100): Liquidity sweep + Volume Profile + Price Action.

    Returns dict with all individual indicator results PLUS the three
    composite scores for easy consumption by _score_coin().
    """
    # Compute individual indicators with safe fallbacks
    fib = fibonacci_retracement(high, low, close)
    fvg = fair_value_gaps(open_s, high, low, close)
    sd = supply_demand_zones(open_s, high, low, close, volume)
    sweep = liquidity_sweep(high, low, close)
    ob = order_blocks(open_s, high, low, close, volume)
    ms = market_structure(high, low, close)
    vp = volume_profile(close, volume, high, low)
    ote = ict_optimal_trade_entry(high, low, close)
    pa = price_action_patterns(open_s, high, low, close)

    # ---- smc_structure (0-100) ----
    # 40pts: valid order block near price
    # 30pts: BOS aligned with trend
    # 30pts: ChoCH (when confirming a new direction)
    struct_score = 0.0

    if ob["price_in_bullish_ob"] or ob["price_in_bearish_ob"]:
        struct_score += 40.0
    elif ob["nearest_bullish_ob"] is not None or ob["nearest_bearish_ob"] is not None:
        # Partial credit if an OB exists nearby
        struct_score += 15.0

    if ms["bullish_bos"] or ms["bearish_bos"]:
        struct_score += 30.0

    if ms["bullish_choch"] or ms["bearish_choch"]:
        struct_score += 30.0

    # Bonus for structure quality
    struct_score = struct_score * (0.5 + 0.5 * ms["structure_quality"] / 100.0)
    struct_score = _clamp(struct_score, 0, 100)

    # ---- smc_entry (0-100) ----
    # 25pts: fib confluence (at fib zone)
    # 25pts: FVG at price
    # 25pts: supply/demand zone at price
    # 25pts: ICT OTE zone
    entry_score = 0.0

    if fib["at_fib_zone"]:
        entry_score += 25.0
    elif fib["nearest_dist_pct"] < 1.0:
        entry_score += 10.0  # close to a fib level

    if fvg["price_in_bullish_fvg"] or fvg["price_in_bearish_fvg"]:
        entry_score += 25.0
    elif fvg["fvg_count_bullish"] + fvg["fvg_count_bearish"] > 0:
        entry_score += 8.0  # FVGs exist nearby

    if sd["price_in_demand"] or sd["price_in_supply"]:
        entry_score += 25.0
    elif min(sd["nearest_demand_dist_pct"], sd["nearest_supply_dist_pct"]) < 1.0:
        entry_score += 10.0

    if ote["in_ote_zone"]:
        entry_score += 25.0
        if ote["fvg_confluence"]:
            entry_score += 5.0  # extra for FVG+OTE overlap (can push above base 100)

    entry_score = _clamp(entry_score, 0, 100)

    # ---- smc_confirmation (0-100) ----
    # 40pts: fresh liquidity sweep
    # 30pts: price at volume POC or HVN
    # 30pts: price action pattern
    conf_score = 0.0

    if sweep["bullish_sweep"] or sweep["bearish_sweep"]:
        # Scale by recency: 0 bars ago = 40, 1 = 30, 2 = 20
        recency_factor = max(0, 40 - sweep["bars_since_sweep"] * 10)
        conf_score += float(recency_factor)

    if vp["price_vs_poc"] == "at" or vp["at_hvn"]:
        conf_score += 30.0
    elif vp["price_in_value_area"]:
        conf_score += 15.0
    elif vp["at_lvn"]:
        conf_score += 10.0  # LVN = potential breakout, some value

    pa_abs = abs(pa["pattern_score"])
    if pa_abs >= 3:
        conf_score += 30.0
    elif pa_abs == 2:
        conf_score += 20.0
    elif pa_abs == 1:
        conf_score += 10.0

    conf_score = _clamp(conf_score, 0, 100)

    return {
        # Individual results
        "fibonacci": fib,
        "fvg": fvg,
        "supply_demand": sd,
        "liquidity_sweep": sweep,
        "order_blocks": ob,
        "market_structure": ms,
        "volume_profile": vp,
        "ote": ote,
        "price_action": pa,
        # Composite scores for scoring engine
        "smc_structure": round(struct_score, 1),
        "smc_entry": round(entry_score, 1),
        "smc_confirmation": round(conf_score, 1),
    }
