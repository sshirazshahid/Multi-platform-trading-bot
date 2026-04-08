"""
backtest_all.py — Comprehensive strategy backtester.

Pulls historical OHLCV data via ccxt, runs every strategy through a realistic
simulation (ATR-based SL/TP, fees, position sizing), and outputs a comparison
table so you can see which strategies actually have edge.

Usage:
    python backtest_all.py                     # BTC + ETH, 90 days, all strategies
    python backtest_all.py --symbol BTC/USDT   # Single symbol
    python backtest_all.py --days 180          # Longer history
    python backtest_all.py --strategy supertrend  # Single strategy

Metrics reported:
    - Win Rate, Total PnL, Sharpe, Sortino, Max Drawdown
    - Profit Factor, Avg Win/Loss, Expectancy per trade
    - Train/Test split (70/30) with overfit detection
"""

import argparse
import sys
import time
import numpy as np
import pandas as pd
from dataclasses import dataclass, field
from typing import Optional
from rich.console import Console
from rich.table import Table
from rich import box

# ── Indicator helpers (pure pandas, no TA-Lib needed) ────────────────

def ema(s: pd.Series, p: int) -> pd.Series:
    return s.ewm(span=p, adjust=False).mean()

def sma(s: pd.Series, p: int) -> pd.Series:
    return s.rolling(p).mean()

def rsi(s: pd.Series, p: int = 14) -> pd.Series:
    d = s.diff()
    g = d.clip(lower=0).ewm(span=p, adjust=False).mean()
    l = (-d.clip(upper=0)).ewm(span=p, adjust=False).mean()
    return 100 - 100 / (1 + g / l.replace(0, np.nan))

def atr(high, low, close, p: int = 14) -> pd.Series:
    tr = pd.concat([
        high - low,
        (high - close.shift(1)).abs(),
        (low - close.shift(1)).abs(),
    ], axis=1).max(axis=1)
    return tr.ewm(span=p, adjust=False).mean()

def adx(high, low, close, p: int = 14) -> pd.Series:
    tr_val = atr(high, low, close, p)
    up = high.diff()
    down = -low.diff()
    pdm = up.where((up > down) & (up > 0), 0.0)
    mdm = down.where((down > up) & (down > 0), 0.0)
    pdi = 100 * pdm.ewm(span=p, adjust=False).mean() / tr_val.replace(0, np.nan)
    mdi = 100 * mdm.ewm(span=p, adjust=False).mean() / tr_val.replace(0, np.nan)
    dx = 100 * (pdi - mdi).abs() / (pdi + mdi).replace(0, np.nan)
    return dx.ewm(span=p, adjust=False).mean()

def bbands(s: pd.Series, p: int = 20, std: float = 2.0):
    mid = s.rolling(p).mean()
    sigma = s.rolling(p).std()
    return mid - std * sigma, mid, mid + std * sigma

def supertrend(high, low, close, period=10, multiplier=3.0):
    tr = pd.concat([
        high - low,
        (high - close.shift(1)).abs(),
        (low - close.shift(1)).abs(),
    ], axis=1).max(axis=1)
    atr_val = tr.ewm(span=period, adjust=False).mean()
    hl2 = (high + low) / 2
    upper_raw = hl2 + multiplier * atr_val
    lower_raw = hl2 - multiplier * atr_val
    upper = upper_raw.copy()
    lower = lower_raw.copy()
    for i in range(1, len(close)):
        upper.iloc[i] = upper_raw.iloc[i] if (
            upper_raw.iloc[i] < upper.iloc[i-1] or close.iloc[i-1] > upper.iloc[i-1]
        ) else upper.iloc[i-1]
        lower.iloc[i] = lower_raw.iloc[i] if (
            lower_raw.iloc[i] > lower.iloc[i-1] or close.iloc[i-1] < lower.iloc[i-1]
        ) else lower.iloc[i-1]
    direction = pd.Series(index=close.index, dtype=int)
    direction.iloc[0] = 1
    for i in range(1, len(close)):
        prev_dir = direction.iloc[i-1]
        if prev_dir == -1:
            direction.iloc[i] = 1 if close.iloc[i] > upper.iloc[i] else -1
        else:
            direction.iloc[i] = -1 if close.iloc[i] < lower.iloc[i] else 1
    return direction

def macd_hist(s, fast=12, slow=26, sig=9):
    macd_line = ema(s, fast) - ema(s, slow)
    signal_line = ema(macd_line, sig)
    return macd_line - signal_line


# ── Strategy signal generators (standalone, no exchange needed) ──────

def signal_supertrend(df: pd.DataFrame) -> list:
    """Supertrend crossover + RSI + volume filter. Returns list of (index, signal, atr_val)."""
    from config import SUPERTREND as CFG
    signals = []
    df = df.copy()
    df["st_dir"] = supertrend(df["high"], df["low"], df["close"],
                               CFG["atr_period"], CFG["atr_multiplier"])
    df["rsi"] = rsi(df["close"], CFG["rsi_period"])
    df["atr_val"] = atr(df["high"], df["low"], df["close"], CFG["atr_period"])
    df["vol_ma"] = df["volume"].rolling(CFG["volume_ma"]).mean()
    df.dropna(inplace=True)

    for i in range(1, len(df)):
        curr = df.iloc[i]
        prev = df.iloc[i-1]
        atr_pct = curr["atr_val"] / curr["close"]
        if atr_pct < CFG["min_atr_pct"] or atr_pct > CFG["max_atr_pct"]:
            continue
        vol_ok = curr["volume"] >= curr["vol_ma"] * CFG["min_volume_mult"]
        bull_cross = (prev["st_dir"] == -1 and curr["st_dir"] == 1)
        bear_cross = (prev["st_dir"] == 1 and curr["st_dir"] == -1)

        if bull_cross and CFG["rsi_min"] < curr["rsi"] < CFG["rsi_max"] and vol_ok:
            signals.append((df.index[i], "buy", float(curr["atr_val"])))
        elif bear_cross and CFG["rsi_min"] < curr["rsi"] < CFG["rsi_max"] and vol_ok:
            signals.append((df.index[i], "sell", float(curr["atr_val"])))
    return signals


def signal_mean_reversion(df: pd.DataFrame) -> list:
    """Bollinger Band + RSI mean reversion. Returns (index, signal, sl, tp)."""
    from config import MEAN_REVERSION as CFG
    signals = []
    df = df.copy()
    bb_lower, bb_mid, bb_upper = bbands(df["close"], CFG["bb_period"], CFG["bb_std"])
    df["bb_lower"] = bb_lower
    df["bb_mid"] = bb_mid
    df["bb_upper"] = bb_upper
    df["bb_width"] = (bb_upper - bb_lower) / bb_mid
    df["rsi"] = rsi(df["close"], CFG["rsi_period"])
    df["vol_ma"] = df["volume"].rolling(CFG["volume_ma"]).mean()
    if CFG.get("trend_filter"):
        df["ema200"] = ema(df["close"], CFG["trend_ema_period"])
    df.dropna(inplace=True)

    for i in range(len(df)):
        curr = df.iloc[i]
        if curr["bb_width"] < CFG["bb_squeeze_min"]:
            continue
        if curr["volume"] < curr["vol_ma"] * CFG["min_volume_mult"]:
            continue

        close = curr["close"]
        ema200 = curr.get("ema200", close)

        # LONG
        if close <= curr["bb_lower"] * 1.002 and curr["rsi"] <= CFG["rsi_oversold"]:
            trend_ok = close <= ema200 * 1.02 if CFG.get("trend_filter") else True
            if trend_ok:
                sl = min(curr["bb_lower"] * (1 - CFG["sl_bb_mult"] * 0.01), close * 0.98)
                tp = curr["bb_mid"] + (curr["bb_mid"] - curr["bb_lower"]) * 0.3
                rr = (tp - close) / max(close - sl, 1e-8)
                if rr >= 1.5:
                    signals.append((df.index[i], "buy", sl, tp))

        # SHORT
        if close >= curr["bb_upper"] * 0.998 and curr["rsi"] >= CFG["rsi_overbought"]:
            trend_ok = close >= ema200 * 0.98 if CFG.get("trend_filter") else True
            if trend_ok:
                sl = max(curr["bb_upper"] * (1 + CFG["sl_bb_mult"] * 0.01), close * 1.02)
                tp = curr["bb_mid"] - (curr["bb_upper"] - curr["bb_mid"]) * 0.3
                rr = (close - tp) / max(sl - close, 1e-8)
                if rr >= 1.5:
                    signals.append((df.index[i], "sell", sl, tp))
    return signals


def signal_multi_tf(df: pd.DataFrame) -> list:
    """Multi-TF EMA crossover + ADX + RSI. Returns (index, signal, atr_val)."""
    from config import MULTI_TF as CFG
    signals = []
    df = df.copy()
    df["ema_fast"] = ema(df["close"], CFG["entry_fast"])
    df["ema_slow"] = ema(df["close"], CFG["entry_slow"])
    df["rsi"] = rsi(df["close"], CFG["rsi_period"])
    df["atr_val"] = atr(df["high"], df["low"], df["close"], CFG["atr_period"])
    df["adx"] = adx(df["high"], df["low"], df["close"], CFG["adx_period"])
    df.dropna(inplace=True)

    for i in range(1, len(df)):
        curr = df.iloc[i]
        prev = df.iloc[i-1]
        if curr["adx"] < CFG["adx_min"]:
            continue
        bull_cross = prev["ema_fast"] <= prev["ema_slow"] and curr["ema_fast"] > curr["ema_slow"]
        bear_cross = prev["ema_fast"] >= prev["ema_slow"] and curr["ema_fast"] < curr["ema_slow"]

        if bull_cross and curr["rsi"] < CFG["rsi_overbought"]:
            signals.append((df.index[i], "buy", float(curr["atr_val"])))
        elif bear_cross and curr["rsi"] > CFG["rsi_oversold"]:
            signals.append((df.index[i], "sell", float(curr["atr_val"])))
    return signals


def signal_trend_following(df: pd.DataFrame) -> list:
    """EMA stack + MACD + ADX + volume. Returns (index, signal, atr_val)."""
    from config import TREND_FOLLOWING as CFG
    signals = []
    df = df.copy()
    df["ema_fast"] = ema(df["close"], CFG["fast_ema"])
    df["ema_slow"] = ema(df["close"], CFG["slow_ema"])
    df["ema_trend"] = ema(df["close"], CFG["trend_ema"])
    df["rsi"] = rsi(df["close"], CFG["rsi_period"])
    df["macd_hist"] = macd_hist(df["close"], CFG["macd_fast"], CFG["macd_slow"], CFG["macd_signal"])
    df["vol_ma"] = df["volume"].rolling(CFG["volume_ma"]).mean()
    df["adx"] = adx(df["high"], df["low"], df["close"], 14)
    df["atr_val"] = atr(df["high"], df["low"], df["close"], CFG["atr_period"])
    df.dropna(inplace=True)

    for i in range(1, len(df)):
        curr = df.iloc[i]
        prev = df.iloc[i-1]
        vol_ok = curr["volume"] >= curr["vol_ma"] * CFG["min_volume_mult"]
        if curr["adx"] < 22:
            continue

        # Full EMA stack
        if (curr["ema_fast"] > curr["ema_slow"] > curr["ema_trend"]
                and curr["rsi"] < CFG["rsi_overbought"]
                and curr["macd_hist"] > 0 and vol_ok):
            signals.append((df.index[i], "buy", float(curr["atr_val"])))
        elif (curr["ema_fast"] < curr["ema_slow"] < curr["ema_trend"]
                and curr["rsi"] > CFG["rsi_oversold"]
                and curr["macd_hist"] < 0 and vol_ok):
            signals.append((df.index[i], "sell", float(curr["atr_val"])))
        # EMA crossover
        elif prev["ema_fast"] <= prev["ema_slow"] and curr["ema_fast"] > curr["ema_slow"]:
            if curr["close"] > curr["ema_trend"] and curr["adx"] > 25 and vol_ok:
                signals.append((df.index[i], "buy", float(curr["atr_val"])))
        elif prev["ema_fast"] >= prev["ema_slow"] and curr["ema_fast"] < curr["ema_slow"]:
            if curr["close"] < curr["ema_trend"] and curr["adx"] > 25 and vol_ok:
                signals.append((df.index[i], "sell", float(curr["atr_val"])))
    return signals


# ── Backtest engine ──────────────────────────────────────────────────

@dataclass
class Trade:
    entry_time: object
    exit_time: object
    side: str
    entry_price: float
    exit_price: float
    sl: float
    tp: float
    pnl_pct: float
    pnl_usd: float
    reason: str  # "tp", "sl", "timeout"
    leverage: int = 1


@dataclass
class StrategyResult:
    name: str
    symbol: str
    timeframe: str
    total_trades: int
    wins: int
    losses: int
    win_rate: float
    total_pnl: float
    sharpe: float
    sortino: float
    max_drawdown: float
    profit_factor: float
    avg_win: float
    avg_loss: float
    expectancy: float
    best_trade: float
    worst_trade: float
    # Train/test split
    train_wr: float = 0.0
    test_wr: float = 0.0
    train_pnl: float = 0.0
    test_pnl: float = 0.0
    overfit: bool = False
    trades: list = field(default_factory=list)


def simulate_trades(df: pd.DataFrame, signals: list, strategy_name: str,
                    fee_pct: float = 0.001, initial_balance: float = 10000.0,
                    leverage: int = 1, position_pct: float = 0.05,
                    atr_sl_mult: float = 2.5, atr_tp_mult: float = 6.0,
                    max_hold_bars: int = 50) -> list:
    """
    Simulate trades from a list of signals against OHLCV data.
    Uses ATR-based SL/TP when atr_val is provided in signals.
    """
    trades = []
    balance = initial_balance
    in_trade = False
    entry_price = entry_time = side = sl = tp = entry_idx = 0

    # Build a lookup: index -> (signal, *extra)
    signal_map = {}
    for sig in signals:
        idx = sig[0]
        signal_map[idx] = sig[1:]  # (signal, atr_val) or (signal, sl, tp)

    df_indices = list(df.index)

    for i, idx in enumerate(df_indices):
        if i < 50:  # warmup
            continue
        row = df.loc[idx]
        high_val = float(row["high"])
        low_val = float(row["low"])
        close_val = float(row["close"])

        if in_trade:
            bars_held = i - entry_idx

            # Check SL/TP
            hit_sl = hit_tp = False
            if side == "buy":
                hit_sl = low_val <= sl
                hit_tp = high_val >= tp
            else:
                hit_sl = high_val >= sl
                hit_tp = low_val <= tp

            # Timeout: close at market after max_hold_bars
            timeout = bars_held >= max_hold_bars

            if hit_sl or hit_tp or timeout:
                if hit_tp:
                    exit_price = tp
                    reason = "tp"
                elif hit_sl:
                    exit_price = sl
                    reason = "sl"
                else:
                    exit_price = close_val
                    reason = "timeout"

                if side == "buy":
                    pnl_pct = (exit_price - entry_price) / entry_price * leverage
                else:
                    pnl_pct = (entry_price - exit_price) / entry_price * leverage

                # Deduct fees (entry + exit)
                pnl_pct -= 2 * fee_pct * leverage
                pnl_usd = balance * position_pct * pnl_pct
                balance += pnl_usd

                trades.append(Trade(
                    entry_time=entry_time, exit_time=idx,
                    side=side, entry_price=entry_price, exit_price=exit_price,
                    sl=sl, tp=tp, pnl_pct=pnl_pct, pnl_usd=pnl_usd,
                    reason=reason, leverage=leverage,
                ))
                in_trade = False

        if not in_trade and idx in signal_map:
            sig_data = signal_map[idx]
            signal = sig_data[0]

            if signal in ("buy", "sell"):
                entry_price = close_val
                entry_time = idx
                side = signal
                entry_idx = i

                if len(sig_data) == 3 and isinstance(sig_data[1], (int, float)):
                    # Mean reversion: (signal, sl, tp)
                    sl = float(sig_data[1])
                    tp = float(sig_data[2])
                elif len(sig_data) >= 2:
                    # ATR-based: (signal, atr_val)
                    atr_val = float(sig_data[1])
                    # Enforce 2% minimum SL floor (matches risk_manager)
                    sl_dist = max(atr_val * atr_sl_mult, entry_price * 0.02)
                    tp_dist = atr_val * atr_tp_mult
                    if signal == "buy":
                        sl = entry_price - sl_dist
                        tp = entry_price + tp_dist
                    else:
                        sl = entry_price + sl_dist
                        tp = entry_price - tp_dist
                else:
                    # Fallback: fixed 2.5% SL, 6% TP
                    if signal == "buy":
                        sl = entry_price * 0.975
                        tp = entry_price * 1.06
                    else:
                        sl = entry_price * 1.025
                        tp = entry_price * 0.94

                in_trade = True

    return trades


def compute_metrics(trades: list, initial_balance: float = 10000.0) -> dict:
    """Compute comprehensive metrics from trade list."""
    if not trades:
        return {
            "total": 0, "wins": 0, "losses": 0, "win_rate": 0,
            "total_pnl": 0, "sharpe": 0, "sortino": 0, "max_dd": 0,
            "profit_factor": 0, "avg_win": 0, "avg_loss": 0,
            "expectancy": 0, "best": 0, "worst": 0,
        }

    pnls = [t.pnl_pct for t in trades]
    pnl_usds = [t.pnl_usd for t in trades]
    wins = [t for t in trades if t.pnl_pct > 0]
    losses = [t for t in trades if t.pnl_pct <= 0]

    total_pnl = sum(pnl_usds)
    win_rate = len(wins) / len(trades) * 100 if trades else 0

    # Sharpe (annualized, assume 6h per trade avg)
    mean_r = np.mean(pnls)
    std_r = np.std(pnls)
    trades_per_year = 365 * 4  # rough: 4 trades/day
    sharpe = (mean_r / std_r * np.sqrt(trades_per_year)) if std_r > 0 else 0

    # Sortino (only downside deviation)
    downside = [p for p in pnls if p < 0]
    downside_std = np.std(downside) if downside else 0
    sortino = (mean_r / downside_std * np.sqrt(trades_per_year)) if downside_std > 0 else 0

    # Max drawdown
    balance = initial_balance
    peak = balance
    max_dd = 0
    for t in trades:
        balance += t.pnl_usd
        peak = max(peak, balance)
        dd = (peak - balance) / peak * 100
        max_dd = max(max_dd, dd)

    # Profit factor
    gross_profit = sum(t.pnl_usd for t in wins) if wins else 0
    gross_loss = abs(sum(t.pnl_usd for t in losses)) if losses else 0
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else float('inf') if gross_profit > 0 else 0

    avg_win = np.mean([t.pnl_pct * 100 for t in wins]) if wins else 0
    avg_loss = np.mean([t.pnl_pct * 100 for t in losses]) if losses else 0
    expectancy = mean_r * 100  # avg pnl per trade in %

    return {
        "total": len(trades), "wins": len(wins), "losses": len(losses),
        "win_rate": round(win_rate, 1), "total_pnl": round(total_pnl, 2),
        "sharpe": round(sharpe, 2), "sortino": round(sortino, 2),
        "max_dd": round(max_dd, 2), "profit_factor": round(profit_factor, 2),
        "avg_win": round(avg_win, 3), "avg_loss": round(avg_loss, 3),
        "expectancy": round(expectancy, 3),
        "best": round(max(pnls) * 100, 3) if pnls else 0,
        "worst": round(min(pnls) * 100, 3) if pnls else 0,
    }


# ── Data fetching ────────────────────────────────────────────────────

def fetch_ohlcv(symbol: str, timeframe: str = "1h", days: int = 90) -> pd.DataFrame:
    """Fetch historical OHLCV from Binance (no API key needed for public data)."""
    import ccxt
    exchange = ccxt.binance({"enableRateLimit": True})

    all_candles = []
    since = exchange.milliseconds() - days * 24 * 60 * 60 * 1000
    limit = 1000

    print(f"  Fetching {symbol} {timeframe} data ({days} days)...", end=" ", flush=True)
    while True:
        try:
            candles = exchange.fetch_ohlcv(symbol, timeframe, since=since, limit=limit)
        except Exception as e:
            print(f"\n  Error fetching {symbol}: {e}")
            return pd.DataFrame()

        if not candles:
            break
        all_candles.extend(candles)
        since = candles[-1][0] + 1
        if len(candles) < limit:
            break
        time.sleep(0.1)

    print(f"{len(all_candles)} candles")

    if not all_candles:
        return pd.DataFrame()

    df = pd.DataFrame(all_candles, columns=["timestamp", "open", "high", "low", "close", "volume"])
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
    df.set_index("timestamp", inplace=True)
    df = df.astype(float)
    return df


# ── Strategy configs for backtesting ─────────────────────────────────

STRATEGY_CONFIGS = {
    "supertrend": {
        "signal_fn": signal_supertrend,
        "timeframe": "1h",
        "atr_sl_mult": 2.5,
        "atr_tp_mult": 6.0,
        "leverage": 3,
        "max_hold": 48,
        "fee": 0.0005,  # futures taker
    },
    "mean_reversion": {
        "signal_fn": signal_mean_reversion,
        "timeframe": "1h",
        "atr_sl_mult": 2.0,  # not used — MR uses BB-based SL/TP
        "atr_tp_mult": 4.0,
        "leverage": 1,
        "max_hold": 36,
        "fee": 0.001,  # spot taker
    },
    "multi_tf": {
        "signal_fn": signal_multi_tf,
        "timeframe": "15m",
        "atr_sl_mult": 2.5,
        "atr_tp_mult": 6.5,
        "leverage": 3,
        "max_hold": 80,
        "fee": 0.0005,
    },
    "trend_following": {
        "signal_fn": signal_trend_following,
        "timeframe": "15m",
        "atr_sl_mult": 2.5,
        "atr_tp_mult": 6.5,
        "leverage": 3,
        "max_hold": 60,
        "fee": 0.0005,
    },
}


# ── Main ─────────────────────────────────────────────────────────────

def run_backtest(symbol: str, days: int, strategies: list,
                 initial_balance: float = 10000.0) -> list:
    """Run all strategies against a symbol. Returns list of StrategyResult."""
    results = []

    for strat_name in strategies:
        cfg = STRATEGY_CONFIGS[strat_name]
        tf = cfg["timeframe"]

        # Fetch data
        df = fetch_ohlcv(symbol, tf, days)
        if df.empty or len(df) < 100:
            print(f"  {strat_name}: insufficient data ({len(df)} candles)")
            continue

        # Generate signals
        try:
            signals = cfg["signal_fn"](df)
        except Exception as e:
            print(f"  {strat_name}: signal generation error: {e}")
            continue

        if not signals:
            print(f"  {strat_name}: no signals generated")
            results.append(StrategyResult(
                name=strat_name, symbol=symbol, timeframe=tf,
                total_trades=0, wins=0, losses=0, win_rate=0,
                total_pnl=0, sharpe=0, sortino=0, max_drawdown=0,
                profit_factor=0, avg_win=0, avg_loss=0, expectancy=0,
                best_trade=0, worst_trade=0,
            ))
            continue

        # Simulate trades
        trades = simulate_trades(
            df, signals, strat_name,
            fee_pct=cfg["fee"],
            initial_balance=initial_balance,
            leverage=cfg["leverage"],
            atr_sl_mult=cfg["atr_sl_mult"],
            atr_tp_mult=cfg["atr_tp_mult"],
            max_hold_bars=cfg["max_hold"],
        )

        # Overall metrics
        m = compute_metrics(trades, initial_balance)

        # Train/test split (70/30)
        split_idx = int(len(df) * 0.70)
        split_time = df.index[split_idx]
        train_trades = [t for t in trades if t.entry_time < split_time]
        test_trades = [t for t in trades if t.entry_time >= split_time]
        m_train = compute_metrics(train_trades, initial_balance)
        m_test = compute_metrics(test_trades, initial_balance)

        overfit = False
        if m_train["total"] >= 5 and m_test["total"] >= 3:
            wr_drop = m_train["win_rate"] - m_test["win_rate"]
            if wr_drop > 20:
                overfit = True

        results.append(StrategyResult(
            name=strat_name, symbol=symbol, timeframe=tf,
            total_trades=m["total"], wins=m["wins"], losses=m["losses"],
            win_rate=m["win_rate"], total_pnl=m["total_pnl"],
            sharpe=m["sharpe"], sortino=m["sortino"],
            max_drawdown=m["max_dd"], profit_factor=m["profit_factor"],
            avg_win=m["avg_win"], avg_loss=m["avg_loss"],
            expectancy=m["expectancy"],
            best_trade=m["best"], worst_trade=m["worst"],
            train_wr=m_train["win_rate"], test_wr=m_test["win_rate"],
            train_pnl=m_train["total_pnl"], test_pnl=m_test["total_pnl"],
            overfit=overfit, trades=trades,
        ))

        print(f"  {strat_name}: {m['total']} trades, WR={m['win_rate']}%, "
              f"PnL=${m['total_pnl']:+.2f}, Sharpe={m['sharpe']}, "
              f"MaxDD={m['max_dd']:.1f}%")

    return results


def print_results_table(all_results: list):
    """Print a rich comparison table of all strategy results."""
    console = Console()

    table = Table(
        title="Strategy Backtest Comparison",
        box=box.HEAVY_HEAD,
        show_lines=True,
    )
    table.add_column("Strategy", style="cyan bold")
    table.add_column("Symbol", style="white")
    table.add_column("Trades", justify="right")
    table.add_column("Win Rate", justify="right")
    table.add_column("PnL ($)", justify="right")
    table.add_column("Sharpe", justify="right")
    table.add_column("Sortino", justify="right")
    table.add_column("Max DD", justify="right")
    table.add_column("PF", justify="right")
    table.add_column("Expect.", justify="right")
    table.add_column("Train WR", justify="right")
    table.add_column("Test WR", justify="right")
    table.add_column("Overfit?", justify="center")

    for r in sorted(all_results, key=lambda x: x.total_pnl, reverse=True):
        # Color coding
        pnl_style = "green" if r.total_pnl > 0 else "red"
        wr_style = "green" if r.win_rate >= 55 else ("yellow" if r.win_rate >= 45 else "red")
        sharpe_style = "green" if r.sharpe > 1 else ("yellow" if r.sharpe > 0 else "red")
        dd_style = "green" if r.max_drawdown < 10 else ("yellow" if r.max_drawdown < 20 else "red")
        overfit_str = "[red bold]YES[/]" if r.overfit else "[green]NO[/]"

        table.add_row(
            r.name,
            r.symbol,
            str(r.total_trades),
            f"[{wr_style}]{r.win_rate:.1f}%[/]",
            f"[{pnl_style}]${r.total_pnl:+,.2f}[/]",
            f"[{sharpe_style}]{r.sharpe:.2f}[/]",
            f"{r.sortino:.2f}",
            f"[{dd_style}]{r.max_drawdown:.1f}%[/]",
            f"{r.profit_factor:.2f}",
            f"{r.expectancy:+.3f}%",
            f"{r.train_wr:.1f}%",
            f"{r.test_wr:.1f}%",
            overfit_str,
        )

    console.print()
    console.print(table)

    # Summary
    console.print()
    profitable = [r for r in all_results if r.total_pnl > 0]
    losing = [r for r in all_results if r.total_pnl <= 0]
    console.print(f"  [green bold]{len(profitable)} profitable[/] / "
                  f"[red bold]{len(losing)} losing[/] strategies")
    if profitable:
        best = max(profitable, key=lambda x: x.sharpe)
        console.print(f"  Best risk-adjusted: [cyan bold]{best.name}[/] on {best.symbol} "
                      f"(Sharpe={best.sharpe}, PnL=${best.total_pnl:+,.2f})")
    if losing:
        worst = min(losing, key=lambda x: x.total_pnl)
        console.print(f"  Worst performer: [red bold]{worst.name}[/] on {worst.symbol} "
                      f"(PnL=${worst.total_pnl:+,.2f})")

    # Overfit warnings
    overfit_strats = [r for r in all_results if r.overfit]
    if overfit_strats:
        console.print()
        console.print("  [yellow bold]OVERFIT WARNINGS:[/]")
        for r in overfit_strats:
            console.print(f"    {r.name} ({r.symbol}): Train WR={r.train_wr:.1f}% -> "
                          f"Test WR={r.test_wr:.1f}% (drop={r.train_wr - r.test_wr:.0f}pp)")


def main():
    parser = argparse.ArgumentParser(description="Backtest all trading strategies")
    parser.add_argument("--symbol", nargs="+", default=["BTC/USDT", "ETH/USDT", "SOL/USDT"],
                        help="Symbols to test (default: BTC ETH SOL)")
    parser.add_argument("--days", type=int, default=90, help="Days of history (default: 90)")
    parser.add_argument("--strategy", nargs="+", default=list(STRATEGY_CONFIGS.keys()),
                        help="Strategies to test (default: all)")
    parser.add_argument("--balance", type=float, default=10000.0,
                        help="Starting balance (default: $10,000)")
    args = parser.parse_args()

    console = Console()
    console.print()
    console.print("[bold]COMPREHENSIVE STRATEGY BACKTEST[/]")
    console.print(f"  Symbols:    {', '.join(args.symbol)}")
    console.print(f"  History:    {args.days} days")
    console.print(f"  Strategies: {', '.join(args.strategy)}")
    console.print(f"  Balance:    ${args.balance:,.0f}")
    console.print()

    all_results = []
    for symbol in args.symbol:
        console.print(f"[bold cyan]--- {symbol} ---[/]")
        results = run_backtest(symbol, args.days, args.strategy, args.balance)
        all_results.extend(results)
        console.print()

    print_results_table(all_results)


if __name__ == "__main__":
    main()
