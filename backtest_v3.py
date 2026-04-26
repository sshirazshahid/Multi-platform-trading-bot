"""
backtest_v3.py — Backtest the v3 scoring engine on historical data.

Fetches 1h candles, resamples to 4h for HTF indicators, and walks forward
applying the exact same _score_coin logic that runs live.

Usage:
    venv/Scripts/python backtest_v3.py --symbol BTC/USDT --days 60
    venv/Scripts/python backtest_v3.py --symbol ETH/USDT --days 90 --leverage 5
    venv/Scripts/python backtest_v3.py --all --days 60
"""

import argparse
import sys
import numpy as np
import pandas as pd
from dataclasses import dataclass, field
from utils.logger import setup_logger
from loguru import logger
from utils.indicators import ema, rsi, atr, adx, macd, bbands


SLIPPAGE_PCT = 0.0005
FEE_PCT = 0.0007

TOP_SYMBOLS = [
    "BTC/USDT", "ETH/USDT", "SOL/USDT", "BNB/USDT", "AVAX/USDT",
    "ADA/USDT", "XRP/USDT", "LINK/USDT", "DOGE/USDT", "DOT/USDT",
]


@dataclass
class Trade:
    side: str
    entry_price: float
    entry_bar: int
    sl: float
    tp: float
    sl_pct: float
    tp_pct: float
    score: int
    bonus_count: int
    reason: str
    exit_price: float = 0.0
    exit_bar: int = 0
    pnl_pct: float = 0.0
    pnl_usd: float = 0.0
    exit_reason: str = ""


def compute_indicators(df_1h: pd.DataFrame) -> pd.DataFrame:
    """Compute all v3 indicators on 1h data."""
    c = df_1h["close"]
    h = df_1h["high"]
    l = df_1h["low"]
    v = df_1h["volume"]

    df_1h = df_1h.copy()

    # Core indicators
    df_1h["ema20"] = ema(c, 20)
    df_1h["ema50"] = ema(c, 50)
    df_1h["rsi"] = rsi(c, 14)
    adx_s, pdi_s, mdi_s = adx(h, l, c, 14)
    df_1h["adx"] = adx_s
    atr_s = atr(h, l, c, 14)
    df_1h["atr"] = atr_s
    df_1h["atr_pct"] = atr_s / c * 100

    # MACD
    _, _, hist = macd(c, 12, 26, 9)
    df_1h["macd_hist"] = hist
    df_1h["macd_hist_prev"] = hist.shift(1)
    df_1h["macd_hist_rising"] = hist > hist.shift(1)

    # Bollinger Band width
    bb_lo, bb_mid, bb_hi = bbands(c, 20, 2.0)
    df_1h["bb_width"] = (bb_hi - bb_lo) / bb_mid * 100

    # EMA slope
    df_1h["ema20_prev"] = df_1h["ema20"].shift(1)
    df_1h["ema20_slope"] = (df_1h["ema20"] - df_1h["ema20_prev"]) / df_1h["ema20_prev"].abs().clip(lower=1e-9)

    # EMA alignment
    df_1h["ema20_above_50"] = df_1h["ema20"] > df_1h["ema50"]

    # Volume ratio
    vol_ma = v.rolling(20).mean()
    df_1h["vol_ratio"] = v / vol_ma.clip(lower=1e-9)

    # 5-bar swing structure
    hh_list, hl_list, ll_list, lh_list = [], [], [], []
    for i in range(len(df_1h)):
        if i < 5:
            hh_list.append(False)
            hl_list.append(False)
            ll_list.append(False)
            lh_list.append(False)
            continue
        recent_h = h.iloc[i-5:i].values
        recent_l = l.iloc[i-5:i].values
        curr_h = float(h.iloc[i])
        curr_l = float(l.iloc[i])
        hh_list.append(curr_h > recent_h.max())
        hl_list.append(curr_l > recent_l.min())
        ll_list.append(curr_l < recent_l.min())
        lh_list.append(curr_h < recent_h.max())
    df_1h["hh"] = hh_list
    df_1h["hl"] = hl_list
    df_1h["ll"] = ll_list
    df_1h["lh"] = lh_list

    return df_1h


def resample_to_4h(df_1h: pd.DataFrame) -> pd.DataFrame:
    """Resample 1h OHLCV to 4h and compute 4h indicators."""
    df_4h = df_1h.resample("4h").agg({
        "open": "first", "high": "max", "low": "min",
        "close": "last", "volume": "sum"
    }).dropna()

    c = df_4h["close"]
    h = df_4h["high"]
    l = df_4h["low"]

    df_4h["ema20"] = ema(c, 20)
    df_4h["ema50"] = ema(c, 50)
    df_4h["ema20_above_50"] = df_4h["ema20"] > df_4h["ema50"]
    df_4h["ema20_prev"] = df_4h["ema20"].shift(1)
    df_4h["ema20_slope"] = (df_4h["ema20"] - df_4h["ema20_prev"]) / df_4h["ema20_prev"].abs().clip(lower=1e-9)

    adx_s, _, _ = adx(h, l, c, 14)
    df_4h["adx"] = adx_s

    bb_lo, bb_mid, bb_hi = bbands(c, 20, 2.0)
    df_4h["bb_width"] = (bb_hi - bb_lo) / bb_mid * 100

    # EMA gap %
    df_4h["ema_gap_pct"] = (df_4h["ema20"] - df_4h["ema50"]).abs() / df_4h["ema50"].clip(lower=1e-9) * 100

    return df_4h


def score_bar(bar_1h, bar_4h) -> dict:
    """Apply v3.1 scoring logic to a single bar. Returns score dict or None.

    v3.1 relaxation: 4 REQUIRED (was 5), EMA slope moved to bonus,
    ADX >= 20 (was 25), RSI [38-68] longs / [32-62] shorts,
    regime filter ADX < 15 / BB < 1.0%, volume 1.2x, confidence base 0.66.
    """
    result = {"score": 0, "side": None, "layers_ok": 0, "bonus_count": 0,
              "sl_pct": 0, "tp_pct": 0, "confidence": 0, "reason": ""}

    # Regime filter (v3.1: relaxed thresholds)
    adx_4h = bar_4h.get("adx", 0)
    bb_4h = bar_4h.get("bb_width", 99)
    if pd.isna(adx_4h) or adx_4h < 15:
        result["reason"] = "regime_chop"
        return result
    if pd.isna(bb_4h) or bb_4h < 1.0:
        result["reason"] = "regime_squeeze"
        return result

    # EMA gap check
    ema_gap = bar_4h.get("ema_gap_pct", 0)
    if pd.isna(ema_gap) or ema_gap < 0.1:
        result["reason"] = "4h_emas_flat"
        return result

    ema20_above_50_4h = bar_4h.get("ema20_above_50", False)
    if pd.isna(ema20_above_50_4h):
        result["reason"] = "no_data"
        return result

    side = "buy" if ema20_above_50_4h else "sell"

    # 4 REQUIRED conditions (v3.1: removed EMA slope from required)
    req_pass = 0
    req_names = []

    # R1: 4h EMA alignment
    r1 = ema20_above_50_4h if side == "buy" else not ema20_above_50_4h
    if r1:
        req_pass += 1
        req_names.append("4h_ema")

    # R2: 1h EMA alignment
    ema20_above_50_1h = bar_1h.get("ema20_above_50", False)
    if pd.isna(ema20_above_50_1h):
        result["reason"] = "no_1h_data"
        return result
    r2 = ema20_above_50_1h if side == "buy" else not ema20_above_50_1h
    if r2:
        req_pass += 1
        req_names.append("1h_ema")

    # R3: RSI (v3.1: widened bands)
    rsi_1h = bar_1h.get("rsi", 50)
    if pd.isna(rsi_1h):
        rsi_1h = 50
    if side == "buy":
        r3 = 38 <= rsi_1h <= 68
    else:
        r3 = 32 <= rsi_1h <= 62
    if r3:
        req_pass += 1
        req_names.append(f"rsi={rsi_1h:.0f}")

    # R4: ADX >= 20 (v3.1: was 25)
    adx_1h = bar_1h.get("adx", 0)
    if pd.isna(adx_1h):
        adx_1h = 0
    if adx_1h >= 20:
        req_pass += 1
        req_names.append(f"adx={adx_1h:.0f}")

    if req_pass < 4:
        result["reason"] = f"req_fail({req_pass}/4)"
        return result

    # BONUS conditions (v3.1: 6 bonuses, EMA slope added here)
    score = 50
    bonus = 0
    bonus_names = []

    # B1: MACD histogram direction (+12)
    macd_h = bar_1h.get("macd_hist", 0)
    macd_r = bar_1h.get("macd_hist_rising", False)
    if pd.isna(macd_h):
        macd_h = 0
    if side == "buy" and macd_h > 0 and macd_r:
        bonus += 1; score += 12; bonus_names.append("macd")
    elif side == "sell" and macd_h < 0 and not macd_r:
        bonus += 1; score += 12; bonus_names.append("macd")

    # B2: 4h EMA slope (+8) — moved from required in v3.1
    slope_4h = bar_4h.get("ema20_slope", 0)
    if pd.isna(slope_4h):
        slope_4h = 0
    if side == "buy" and slope_4h > 0:
        bonus += 1; score += 8; bonus_names.append("slope_up")
    elif side == "sell" and slope_4h < 0:
        bonus += 1; score += 8; bonus_names.append("slope_dn")

    # B3: (skip 15m — not available in 1h backtest)

    # B4: Volume >= 1.2x (v3.1: was 1.5x) (+8)
    vol_r = bar_1h.get("vol_ratio", 0)
    if not pd.isna(vol_r) and vol_r >= 1.2:
        bonus += 1; score += 8; bonus_names.append(f"vol={vol_r:.1f}x")

    # B5: Market structure (+8)
    if side == "buy":
        if bar_1h.get("hh", False) and bar_1h.get("hl", False):
            bonus += 1; score += 8; bonus_names.append("structure")
    else:
        if bar_1h.get("ll", False) and bar_1h.get("lh", False):
            bonus += 1; score += 8; bonus_names.append("structure")

    # B6: Microstructure (skip — no orderbook/funding in backtest)

    layers_ok = req_pass + bonus

    # SL/TP
    atr_pct = bar_1h.get("atr_pct", 2.0)
    if pd.isna(atr_pct):
        atr_pct = 2.0
    sl_pct = max(1.5, min(3.5, atr_pct * 1.5))
    tp_pct = sl_pct * 2.0

    confidence = min(0.92, 0.66 + bonus * 0.04)

    result.update({
        "score": score,
        "side": side,
        "layers_ok": layers_ok,
        "bonus_count": bonus,
        "sl_pct": sl_pct,
        "tp_pct": tp_pct,
        "confidence": confidence,
        "reason": " | ".join(req_names + bonus_names),
    })
    return result


def run_backtest(df_1h: pd.DataFrame, symbol: str, leverage: int = 5,
                 balance: float = 420.0, risk_pct: float = 1.0,
                 max_loss_usd: float = 4.0) -> list:
    """Walk-forward backtest using v3 scoring. Returns list of Trade objects."""

    # Need datetime index for resampling
    if not isinstance(df_1h.index, pd.DatetimeIndex):
        df_1h.index = pd.to_datetime(df_1h.index)

    df_1h = compute_indicators(df_1h)
    df_4h = resample_to_4h(df_1h)

    trades = []
    in_trade = False
    current_trade = None
    warmup = 60  # bars

    for i in range(warmup, len(df_1h)):
        bar = df_1h.iloc[i]
        price = float(bar["close"])
        high = float(bar["high"])
        low = float(bar["low"])
        ts = df_1h.index[i]

        # Get corresponding 4h bar (most recent 4h bar before this 1h bar)
        mask_4h = df_4h.index <= ts
        if mask_4h.sum() == 0:
            continue
        bar_4h_row = df_4h.loc[mask_4h].iloc[-1]
        bar_4h = bar_4h_row.to_dict()

        bar_1h = bar.to_dict()

        if in_trade:
            t = current_trade
            # Check exits
            age_bars = i - t.entry_bar
            # price_pnl = unleveraged price move %, pnl_pct = leveraged P&L %
            if t.side == "buy":
                price_pnl = (price - t.entry_price) / t.entry_price * 100
                hit_sl = low <= t.sl
                hit_tp = high >= t.tp
            else:
                price_pnl = (t.entry_price - price) / t.entry_price * 100
                hit_sl = high >= t.sl
                hit_tp = low <= t.tp
            pnl_pct_now = price_pnl * leverage

            exit_reason = None

            # Priority 1: SL hit (ATR-based, checked on wick)
            if hit_sl:
                exit_reason = "stop_loss"
            # Priority 2: TP hit
            elif hit_tp:
                exit_reason = "take_profit"
            # Priority 3: Hard max loss — 3% PRICE move (not leveraged)
            elif price_pnl <= -3.0:
                exit_reason = "hard_max_loss"
            # Priority 4: (MACD flip removed — too noisy on 1h for exits.
            #   SL + trend_reversed + stale handle downside adequately.)
            # Priority 5: Trend reversed + meaningfully losing (> -0.5% leveraged)
            if exit_reason is None and pnl_pct_now < -0.5:
                ema_with = bar_1h.get("ema20_above_50", True)
                if pd.isna(ema_with):
                    ema_with = True
                if t.side == "buy" and not ema_with:
                    exit_reason = "trend_reversed"
                elif t.side == "sell" and ema_with:
                    exit_reason = "trend_reversed"
            # Priority 6: Stale (6h = 6 bars on 1h)
            if exit_reason is None and age_bars > 6 and -0.3 <= pnl_pct_now <= 0.3:
                exit_reason = "stale"

            if exit_reason:
                if exit_reason == "take_profit":
                    exit_p = t.tp
                elif exit_reason == "stop_loss":
                    exit_p = t.sl
                else:
                    exit_p = price

                # Calculate actual PnL
                if t.side == "buy":
                    raw_pnl_pct = (exit_p - t.entry_price) / t.entry_price * leverage
                else:
                    raw_pnl_pct = (t.entry_price - exit_p) / t.entry_price * leverage

                # Apply fees + slippage
                net_pnl_pct = raw_pnl_pct - 2 * FEE_PCT - 2 * SLIPPAGE_PCT

                # Position size from risk-based sizing
                size_pct = risk_pct * 100.0 / (t.sl_pct * leverage)
                size_pct = min(5.0, max(1.0, size_pct))
                # Dollar cap
                if balance > 0 and leverage > 0 and t.sl_pct > 0:
                    max_size = max_loss_usd / (balance * leverage * t.sl_pct / 100.0) * 100.0
                    size_pct = min(size_pct, max_size)

                margin = balance * size_pct / 100.0
                pnl_usd = margin * net_pnl_pct

                t.exit_price = exit_p
                t.exit_bar = i
                t.pnl_pct = net_pnl_pct * 100
                t.pnl_usd = pnl_usd
                t.exit_reason = exit_reason
                trades.append(t)
                balance += pnl_usd
                in_trade = False
                current_trade = None

        else:
            # Try to enter
            result = score_bar(bar_1h, bar_4h)
            if result["score"] >= 66 and result["layers_ok"] >= 6:
                side = result["side"]
                sl_pct = result["sl_pct"]
                tp_pct = result["tp_pct"]

                if side == "buy":
                    entry_p = price * (1 + SLIPPAGE_PCT)
                    sl = entry_p * (1 - sl_pct / 100)
                    tp = entry_p * (1 + tp_pct / 100)
                else:
                    entry_p = price * (1 - SLIPPAGE_PCT)
                    sl = entry_p * (1 + sl_pct / 100)
                    tp = entry_p * (1 - tp_pct / 100)

                current_trade = Trade(
                    side=side, entry_price=entry_p, entry_bar=i,
                    sl=sl, tp=tp, sl_pct=sl_pct, tp_pct=tp_pct,
                    score=result["score"], bonus_count=result["bonus_count"],
                    reason=result["reason"],
                )
                in_trade = True

    return trades


def print_results(symbol: str, trades: list, total_bars: int,
                  initial_balance: float, leverage: int):
    """Print formatted backtest results."""
    n = len(trades)
    if n == 0:
        print(f"\n  {symbol}: 0 trades in {total_bars} bars. "
              "v3 filters may be too strict for this pair/period.\n")
        return

    wins = [t for t in trades if t.pnl_usd > 0]
    losses = [t for t in trades if t.pnl_usd <= 0]
    wr = len(wins) / n * 100

    total_pnl = sum(t.pnl_usd for t in trades)
    avg_win = np.mean([t.pnl_usd for t in wins]) if wins else 0
    avg_loss = np.mean([t.pnl_usd for t in losses]) if losses else 0
    best = max(t.pnl_usd for t in trades)
    worst = min(t.pnl_usd for t in trades)

    # Equity curve + drawdown
    bal = initial_balance
    peak = bal
    max_dd = 0
    for t in trades:
        bal += t.pnl_usd
        peak = max(peak, bal)
        dd = (peak - bal) / peak * 100 if peak > 0 else 0
        max_dd = max(max_dd, dd)

    # Profit factor
    gross_wins = sum(t.pnl_usd for t in wins) if wins else 0
    gross_loss = abs(sum(t.pnl_usd for t in losses)) if losses else 0.001
    pf = gross_wins / gross_loss

    # Exit reason breakdown
    reasons = {}
    for t in trades:
        reasons[t.exit_reason] = reasons.get(t.exit_reason, 0) + 1

    # Sharpe
    returns = [t.pnl_pct for t in trades]
    sharpe = (np.mean(returns) / np.std(returns) * (252**0.5)) if np.std(returns) > 0 else 0

    # Train/test split (70/30)
    split = int(n * 0.7)
    train_trades = trades[:split] if split > 0 else trades
    test_trades = trades[split:] if split < n else []

    print()
    print("  " + "=" * 62)
    print(f"  V3 SCORING ENGINE BACKTEST — {symbol} | {leverage}x leverage")
    print("  " + "=" * 62)
    print(f"  {'Metric':<28} {'Value':>12}")
    print("  " + "-" * 42)
    print(f"  {'Total Trades':<28} {n:>12}")
    print(f"  {'Wins / Losses':<28} {f'{len(wins)} / {len(losses)}':>12}")
    print(f"  {'Win Rate':<28} {f'{wr:.1f}%':>12}")
    print(f"  {'Total PnL':<28} {f'${total_pnl:+.2f}':>12}")
    print(f"  {'Final Balance':<28} {f'${initial_balance + total_pnl:.2f}':>12}")
    print(f"  {'Profit Factor':<28} {f'{pf:.2f}':>12}")
    print(f"  {'Sharpe Ratio':<28} {f'{sharpe:.2f}':>12}")
    print(f"  {'Max Drawdown':<28} {f'{max_dd:.1f}%':>12}")
    print(f"  {'Avg Win':<28} {f'${avg_win:+.2f}':>12}")
    print(f"  {'Avg Loss':<28} {f'${avg_loss:+.2f}':>12}")
    print(f"  {'Best Trade':<28} {f'${best:+.2f}':>12}")
    print(f"  {'Worst Trade':<28} {f'${worst:+.2f}':>12}")
    print()
    print(f"  EXIT REASONS:")
    for reason, count in sorted(reasons.items(), key=lambda x: -x[1]):
        w = sum(1 for t in trades if t.exit_reason == reason and t.pnl_usd > 0)
        print(f"    {reason:<20} {count:>4} trades  (WR: {w/count*100:.0f}%)")

    # Train/test comparison
    if test_trades:
        print()
        tw = [t for t in train_trades if t.pnl_usd > 0]
        tl = [t for t in test_trades if t.pnl_usd > 0]
        train_wr = len(tw) / len(train_trades) * 100 if train_trades else 0
        test_wr = len(tl) / len(test_trades) * 100 if test_trades else 0
        train_pnl = sum(t.pnl_usd for t in train_trades)
        test_pnl = sum(t.pnl_usd for t in test_trades)
        print(f"  TRAIN/TEST SPLIT (70/30):")
        print(f"    Train: {len(train_trades)} trades, WR={train_wr:.1f}%, PnL=${train_pnl:+.2f}")
        print(f"    Test:  {len(test_trades)} trades, WR={test_wr:.1f}%, PnL=${test_pnl:+.2f}")
        wr_drop = train_wr - test_wr
        if wr_drop > 15:
            print(f"    WARNING: WR drops {wr_drop:.0f}pp train->test — possible overfit")
        elif wr_drop > 5:
            print(f"    MILD degradation ({wr_drop:.0f}pp) — monitor in live")
        else:
            print(f"    ROBUST: WR delta = {wr_drop:+.0f}pp")

    # Verdict
    print()
    if wr >= 55 and pf >= 1.3 and max_dd < 15:
        print(f"  VERDICT: STRONG — {symbol} v3 engine looks profitable.")
    elif wr >= 50 and pf >= 1.0:
        print(f"  VERDICT: MARGINAL — {symbol} breakeven-ish, needs tuning.")
    elif n < 10:
        print(f"  VERDICT: INSUFFICIENT DATA — only {n} trades, need more history.")
    else:
        print(f"  VERDICT: WEAK — {symbol} v3 underperforms on this period.")
    print("  " + "=" * 62)
    print()


def main():
    setup_logger()
    p = argparse.ArgumentParser(description="V3 Scoring Engine Backtester")
    p.add_argument("--symbol", default="BTC/USDT")
    p.add_argument("--all", action="store_true", help="Run on top 10 symbols")
    p.add_argument("--days", default=60, type=int)
    p.add_argument("--leverage", default=5, type=int)
    p.add_argument("--balance", default=420.0, type=float)
    p.add_argument("--exchange", default="binance",
                   choices=["binance", "bybit", "bitget"])
    args = p.parse_args()

    from exchanges import BinanceClient, BybitClient, BitgetClient
    clients = {
        "binance": BinanceClient,
        "bybit": BybitClient,
        "bitget": BitgetClient,
    }
    exchange = clients[args.exchange]()
    if not getattr(exchange, "_connected", False):
        print(f"  ERROR: {args.exchange} not connected. Check API keys.")
        sys.exit(1)

    symbols = TOP_SYMBOLS if args.all else [args.symbol]
    candles_needed = args.days * 24  # 1h candles

    all_results = {}

    for symbol in symbols:
        print(f"\n  Fetching {candles_needed} x 1h candles for {symbol}...")
        raw = exchange.fetch_ohlcv(symbol, "1h", limit=candles_needed,
                                    market_type="spot")
        if not raw or len(raw) < 100:
            print(f"  {symbol}: insufficient data ({len(raw) if raw else 0} candles)")
            continue

        df = pd.DataFrame(raw, columns=["timestamp", "open", "high", "low",
                                         "close", "volume"])
        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
        df.set_index("timestamp", inplace=True)
        df = df.astype(float)

        print(f"  Loaded {len(df)} candles: {df.index[0]} -> {df.index[-1]}")

        trades = run_backtest(df, symbol, leverage=args.leverage,
                              balance=args.balance)
        print_results(symbol, trades, len(df), args.balance, args.leverage)
        all_results[symbol] = trades

    # Summary if running multiple symbols
    if len(all_results) > 1:
        print("\n  " + "=" * 62)
        print("  MULTI-SYMBOL SUMMARY")
        print("  " + "=" * 62)
        print(f"  {'Symbol':<14} {'Trades':>7} {'WR':>7} {'PnL':>10} {'PF':>7} {'MaxDD':>7}")
        print("  " + "-" * 54)
        total_trades = 0
        total_wins = 0
        total_pnl = 0
        for sym, tds in all_results.items():
            n = len(tds)
            if n == 0:
                print(f"  {sym:<14} {'0':>7} {'N/A':>7} {'$0.00':>10} {'N/A':>7} {'N/A':>7}")
                continue
            w = sum(1 for t in tds if t.pnl_usd > 0)
            pnl = sum(t.pnl_usd for t in tds)
            gw = sum(t.pnl_usd for t in tds if t.pnl_usd > 0)
            gl = abs(sum(t.pnl_usd for t in tds if t.pnl_usd <= 0)) or 0.001
            pf = gw / gl
            # Quick drawdown
            bal = args.balance
            pk = bal
            dd = 0
            for t in tds:
                bal += t.pnl_usd
                pk = max(pk, bal)
                dd = max(dd, (pk - bal) / pk * 100 if pk > 0 else 0)
            print(f"  {sym:<14} {n:>7} {w/n*100:>6.1f}% {f'${pnl:+.2f}':>10} {pf:>6.2f} {dd:>6.1f}%")
            total_trades += n
            total_wins += w
            total_pnl += pnl
        print("  " + "-" * 54)
        overall_wr = total_wins / total_trades * 100 if total_trades > 0 else 0
        print(f"  {'TOTAL':<14} {total_trades:>7} {overall_wr:>6.1f}% {f'${total_pnl:+.2f}':>10}")
        print("  " + "=" * 62)


if __name__ == "__main__":
    main()
