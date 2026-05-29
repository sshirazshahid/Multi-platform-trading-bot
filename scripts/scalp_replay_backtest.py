"""Decisive scalp-edge backtest (the gate for shipping a scalper).

Answers ONE question the prior 443-alpha search left specific to this design:
  Does a mechanical 15-60m scalp entry, under a fixed 0.8% SL / 1.3% TP bracket
  with REALISTIC fees + slippage, clear its breakeven WR OUT-OF-SAMPLE?

Breakeven math (TP=1.3%, SL=0.8%, round-trip cost c as frac of notional):
  WR* = (0.008 + c) / (0.008 + 0.013)
  c=0.0012 (fees only)       -> WR* = 43.8%
  c=0.0022 (fees+slippage)   -> WR* = 48.6%
A no-edge entry yields WR ~= SL/(SL+TP) = 38.1% BY CONSTRUCTION (driftless),
so only WR meaningfully ABOVE ~48% out-of-sample indicates exploitable edge.

Two tests:
  T1  replay the bot's ACTUAL historical entries under the clean bracket
      (isolates entry edge from the broken exit machinery).
  T2  generate a FRESH mechanical scalp signal over history + bracket it,
      walk-forward (tests the proposed strategy's signal directly).

Look-ahead discipline:
  - signal uses closes up to & including bar t
  - entry fills at bar t+1 OPEN (+ open slippage)
  - SL/TP scanned from bar t+1 onward; if a bar wicks BOTH, count SL (pessimistic)
  - max-hold cap -> mark-out at that bar's close
No network. Reads data/ohlcv_cache/*.parquet only.
"""

from __future__ import annotations

import glob
import os
import sqlite3
from collections import defaultdict

import numpy as np
import pandas as pd

CACHE = "data/ohlcv_cache"

# costs (config.py: futures taker 0.0006 bybit/bitget; slippage 5/5/10 bps)
FEE_RT = 0.0006 * 2  # round-trip taker
SLIP_OPEN = 0.0005
SLIP_CLOSE = 0.0005
SLIP_STOP = 0.0010

SL_PCT = 0.008
TP_PCT = 0.013
MAX_HOLD_BARS_15M = 16  # 16 * 15m = 4h cap (covers 15-60m sweet spot + tail)
MAX_HOLD_BARS_1H = 4  # 4h cap on 1h bars


def load(sym_dash: str, tf: str) -> pd.DataFrame | None:
    p = os.path.join(CACHE, f"{sym_dash}_{tf}.parquet")
    if not os.path.exists(p):
        return None
    df = pd.read_parquet(p).copy()
    df = df.sort_values("ts").reset_index(drop=True)
    return df


def ema(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(span=n, adjust=False).mean()


def rsi(s: pd.Series, n: int = 14) -> pd.Series:
    d = s.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    rs = up / dn.replace(0, np.nan)
    return (100 - 100 / (1 + rs)).fillna(50)


# cost profiles: (entry_fee, entry_slip, tp_fee, tp_slip, sl_fee, sl_slip)
COST_TAKER = (0.0006, SLIP_OPEN, 0.0006, SLIP_CLOSE, 0.0006, SLIP_STOP)
# MAKER-RESCUE: limit entry (maker, no cross-spread), limit TP (maker), but SL
# must cross the book => taker + stop slippage. Most favorable REALISTIC scalp
# cost structure. Ignores the (real) cost that maker entries miss fast moves.
COST_MAKER = (0.0002, 0.0, 0.0002, 0.0, 0.0006, SLIP_STOP)


def bracket_outcome(
    df: pd.DataFrame,
    entry_idx: int,
    side: str,
    max_hold: int,
    tiebreak: str = "sl",
    start_offset: int = 0,
    cost=COST_TAKER,
):
    """Fill at entry_idx OPEN; scan for SL/TP.

    tiebreak: 'sl' (pessimistic, SL-first on double-wick) or 'tp' (optimistic).
    start_offset: 0 = check entry bar's range (realistic); 1 = skip entry bar.
    cost: (entry_fee, entry_slip, tp_fee, tp_slip, sl_fee, sl_slip).
    Returns (net_return_frac, outcome_str, bars_held).
    """
    e_fee, e_slip, tp_fee, tp_slip, sl_fee, sl_slip = cost
    o = df["open"].iloc[entry_idx]
    if side == "buy":
        fill = o * (1 + e_slip)
        sl = fill * (1 - SL_PCT)
        tp = fill * (1 + TP_PCT)
    else:
        fill = o * (1 - e_slip)
        sl = fill * (1 + SL_PCT)
        tp = fill * (1 - TP_PCT)

    end = min(entry_idx + max_hold, len(df) - 1)
    for j in range(entry_idx + start_offset, end + 1):
        hi, lo = df["high"].iloc[j], df["low"].iloc[j]
        hit_sl = (lo <= sl) if side == "buy" else (hi >= sl)
        hit_tp = (hi >= tp) if side == "buy" else (lo <= tp)
        if hit_sl and hit_tp:
            first = tiebreak
        elif hit_sl:
            first = "sl"
        elif hit_tp:
            first = "tp"
        else:
            continue
        if first == "sl":
            exit_px = sl * (1 - sl_slip) if side == "buy" else sl * (1 + sl_slip)
            return _ret(side, fill, exit_px) - e_fee - sl_fee, "stop_loss", j - entry_idx
        exit_px = tp * (1 - tp_slip) if side == "buy" else tp * (1 + tp_slip)
        return _ret(side, fill, exit_px) - e_fee - tp_fee, "take_profit", j - entry_idx
    # mark-out at cap (taker exit)
    c = df["close"].iloc[end]
    exit_px = c * (1 - tp_slip) if side == "buy" else c * (1 + tp_slip)
    return _ret(side, fill, exit_px) - e_fee - tp_fee, "markout", end - entry_idx


def _ret(side, entry, exit_):
    return (exit_ / entry - 1) if side == "buy" else (entry / exit_ - 1)


def summarize(returns, label):
    if not returns:
        print(f"  {label:34s}: (none)")
        return None
    r = np.array(returns)
    wr = (r > 0).mean()
    ev = r.mean()
    aw = r[r > 0].mean() if (r > 0).any() else 0
    al = r[r <= 0].mean() if (r <= 0).any() else 0
    print(
        f"  {label:34s}: n={len(r):4d}  WR={wr * 100:5.1f}%  "
        f"EV={ev * 100:+.3f}%  aw={aw * 100:+.3f}% al={al * 100:+.3f}%  "
        f"sum={r.sum() * 100:+.1f}%"
    )
    return {"n": len(r), "wr": wr, "ev": ev}


SYMS = [os.path.basename(f)[: -len("_15m.parquet")] for f in glob.glob(f"{CACHE}/*_15m.parquet")]


def wh_symbol_to_dash(s: str) -> str:
    # 'XRP/USDT:USDT' -> 'XRP-USDT'
    base = s.split("/")[0]
    return f"{base}-USDT"


def test_t1_replay():
    """Replay bot's actual entries under clean bracket, on 15m candles."""
    print("\n" + "=" * 72)
    print("T1 — REPLAY BOT'S ACTUAL ENTRIES under clean 0.8/1.3 bracket (15m)")
    print("=" * 72)
    con = sqlite3.connect("data/warehouse.sqlite")
    con.row_factory = sqlite3.Row
    rows = con.execute(
        "SELECT ts_entry, symbol, side, entry_px FROM trades "
        "WHERE status='CLOSED' AND entry_px IS NOT NULL AND ts_entry IS NOT NULL "
        "ORDER BY ts_entry"
    ).fetchall()
    cache = {}
    rets, covered, side_rets = [], 0, defaultdict(list)
    for r in rows:
        dash = wh_symbol_to_dash(r["symbol"])
        if dash not in cache:
            cache[dash] = load(dash, "15m")
        df = cache[dash]
        if df is None:
            continue
        # first bar strictly AFTER entry timestamp -> enter at its open
        after = df.index[df["ts"] > r["ts_entry"]]
        if len(after) == 0:
            continue
        ei = int(after[0])
        if ei >= len(df) - 1:
            continue
        covered += 1
        ret, _, _ = bracket_outcome(df, ei, r["side"], MAX_HOLD_BARS_15M)
        rets.append(ret)
        side_rets[r["side"]].append(ret)
    print(f"  bot trades: {len(rows)}  |  covered by 15m cache: {covered}")
    summarize(rets, "ALL (clean bracket)")
    summarize(side_rets.get("buy", []), "LONGS only")
    summarize(side_rets.get("sell", []), "SHORTS only")
    print("  breakeven WR (fees+slip) = 48.6% ; (fees only) = 43.8%")


def scalp_signals(df15: pd.DataFrame, df1h: pd.DataFrame | None, df4h: pd.DataFrame | None):
    """Mechanical scalp entries on 15m: EMA9/21 fresh cross + RSI sweet spot
    + optional 1h alignment + 4h trend filter (mirrors core/agents/scalp_agent
    adapted to available timeframes). Returns list of (idx, side)."""
    c = df15["close"]
    e9 = ema(c, 9)
    e21 = ema(c, 21)
    r = rsi(c, 14)
    sigs = []

    # higher-TF context via merge-asof on ts (no look-ahead: use last closed HT bar)
    def ht_trend(df_ht, fast, slow):
        if df_ht is None or len(df_ht) < slow + 2:
            return None
        ef, es = ema(df_ht["close"], fast), ema(df_ht["close"], slow)
        return pd.DataFrame({"ts": df_ht["ts"], "up": (ef >= es).astype(int)})

    t1 = ht_trend(df1h, 9, 21)
    t4 = ht_trend(df4h, 20, 50)

    def aligned(ts, tdf):
        if tdf is None:
            return None
        prior = tdf[tdf["ts"] <= ts]
        if len(prior) == 0:
            return None
        return bool(prior["up"].iloc[-1])

    for i in range(22, len(df15) - 1):
        cu = e9.iloc[i - 1] <= e21.iloc[i - 1] and e9.iloc[i] > e21.iloc[i]
        cd = e9.iloc[i - 1] >= e21.iloc[i - 1] and e9.iloc[i] < e21.iloc[i]
        if not (cu or cd):
            continue
        ts = df15["ts"].iloc[i]
        up1 = aligned(ts, t1)
        up4 = aligned(ts, t4)
        rv = r.iloc[i]
        if cu and 40 <= rv <= 70 and (up1 is not False) and (up4 is not False):
            sigs.append((i + 1, "buy"))  # enter next bar open
        elif cd and 30 <= rv <= 60 and (up1 is not True) and (up4 is not True):
            sigs.append((i + 1, "sell"))
    return sigs


def atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    h, l, c = df["high"], df["low"], df["close"]
    pc = c.shift(1)
    tr = pd.concat([h - l, (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / n, adjust=False).mean()


def meanrev_signals(df15: pd.DataFrame, df1h=None, df4h=None):
    """Mean-reversion scalp: fade stretched moves back to SMA20.
    Long: close < SMA20 - 1.0*ATR AND RSI<35 AND bounce tick (c>prev c).
    Short: mirror. (The classic intraday-reversion scalp — distinct signal
    class from the momentum EMA-cross / 443-alpha set.)"""
    c = df15["close"]
    sma = c.rolling(20).mean()
    a = atr(df15, 14)
    r = rsi(c, 14)
    sigs = []
    for i in range(25, len(df15) - 1):
        if pd.isna(sma.iloc[i]) or pd.isna(a.iloc[i]) or a.iloc[i] <= 0:
            continue
        dev = c.iloc[i] - sma.iloc[i]
        bounce_up = c.iloc[i] > c.iloc[i - 1]
        bounce_dn = c.iloc[i] < c.iloc[i - 1]
        if dev < -1.0 * a.iloc[i] and r.iloc[i] < 35 and bounce_up:
            sigs.append((i + 1, "buy"))
        elif dev > 1.0 * a.iloc[i] and r.iloc[i] > 65 and bounce_dn:
            sigs.append((i + 1, "sell"))
    return sigs


def run_strategy(label, sig_fn, tiebreak="sl", start_offset=0, cost=COST_TAKER):
    all_rets = []
    for sym in SYMS:
        df15 = load(sym, "15m")
        if df15 is None or len(df15) < 60:
            continue
        df1h, df4h = load(sym, "1h"), load(sym, "4h")
        for ei, side in sig_fn(df15, df1h, df4h):
            if ei >= len(df15):
                continue
            ret, _, _ = bracket_outcome(
                df15,
                ei,
                side,
                MAX_HOLD_BARS_15M,
                tiebreak=tiebreak,
                start_offset=start_offset,
                cost=cost,
            )
            all_rets.append((df15["ts"].iloc[ei], side, ret))
    all_rets.sort()
    print(
        f"\n  [{label}]  signals={len(all_rets)}  (tiebreak={tiebreak}, start_offset={start_offset})"
    )
    summarize([r for _, _, r in all_rets], "ALL")
    summarize([r for _, s, r in all_rets if s == "buy"], "LONGS")
    summarize([r for _, s, r in all_rets if s == "sell"], "SHORTS")
    if len(all_rets) >= 60:
        ts_sorted = [t for t, _, _ in all_rets]
        q1, q2 = np.quantile(ts_sorted, [1 / 3, 2 / 3])
        folds = {"fold1": [], "fold2": [], "fold3": []}
        for t, s, r in all_rets:
            k = "fold1" if t <= q1 else ("fold2" if t <= q2 else "fold3")
            folds[k].append(r)
        for k, v in folds.items():
            summarize(v, f"  OOS {k}")
    return all_rets


def test_t2_fresh():
    print("\n" + "=" * 72)
    print("T2 — FRESH SCALP SIGNALS, bracketed 0.8/1.3, 15m, walk-forward")
    print("  breakeven WR: 48.6% (fees+slip) / 43.8% (fees) ; no-edge geom = 38.1%")
    print("=" * 72)
    print("\n--- MOMENTUM (EMA9/21 cross + RSI + MTF align) ---")
    run_strategy("momentum / pessimistic / check-entry-bar", scalp_signals, "sl", 0)
    run_strategy("momentum / OPTIMISTIC bound (tp-first, skip entry bar)", scalp_signals, "tp", 1)
    print("\n--- MEAN-REVERSION (fade stretch to SMA20, RSI extreme + bounce) ---")
    run_strategy("meanrev / pessimistic / check-entry-bar", meanrev_signals, "sl", 0)
    run_strategy("meanrev / OPTIMISTIC bound (tp-first, skip entry bar)", meanrev_signals, "tp", 1)
    print("\n" + "=" * 72)
    print("MAKER-RESCUE: maker entry + maker TP + taker SL (breakeven WR -> ~40%)")
    print("  (charitable: assumes maker entries always fill — they do NOT in fast moves)")
    print("=" * 72)
    run_strategy("momentum / MAKER cost / optimistic", scalp_signals, "tp", 1, COST_MAKER)
    run_strategy("meanrev / MAKER cost / optimistic", meanrev_signals, "tp", 1, COST_MAKER)


if __name__ == "__main__":
    print(f"symbols in 15m cache: {len(SYMS)}")
    test_t1_replay()
    test_t2_fresh()
