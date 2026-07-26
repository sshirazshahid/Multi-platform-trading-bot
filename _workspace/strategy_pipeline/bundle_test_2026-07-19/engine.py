#!/usr/bin/env python3
"""Backtest engine for the futures_strategies_bundle test.

Two engines:
  E1 flip_backtest  - faithful re-implementation of the bundle's accounting
                      (position flips, close-to-close returns, turnover costs,
                      flat daily funding drag) for H1 testing.
  E2 bracket_backtest - event/bracket engine for the Else-branch: maker limit
                      entries, ATR TP/SL brackets, time stop, conservative
                      same-bar rule, real signed funding cash flows.
All returns are fractions of notional at 1x leverage; reported as %.
"""
import numpy as np
import pandas as pd

DATA = "/root/work/pipeline/data"

FEES = {  # VIP0/regular, USDT-M perp (playbook table, checked 2026-07-18 vs exchange docs)
    "binance": {"taker": 0.00050, "maker": 0.00020},
    "bybit":   {"taker": 0.00055, "maker": 0.00020},
    "bitget":  {"taker": 0.00060, "maker": 0.00020},
}
SLIP = 0.0003          # market/stop orders only
FUND_DAY = 0.0003      # bundle's flat drag (E1 only)

# ---------------- data ----------------
def load(exchange, sym, tf):
    """Price source per venue model: binance & bybit models use Binance klines
    (bybit = fee/funding model swap, validated vs 2024 Bybit archive);
    bitget uses native Bitget candles."""
    src = "bitget" if exchange == "bitget" else "binance"
    df = pd.read_parquet(f"{DATA}/{src}_{sym}_{tf}.parquet")
    df = df.sort_values("open_time").drop_duplicates("open_time").reset_index(drop=True)
    df["ts"] = pd.to_datetime(df["ts"], utc=True)
    return df[["ts", "open", "high", "low", "close", "volume"]]

def load_funding(exchange, sym):
    src = "bitget" if exchange == "bitget" else "binance"
    try:
        f = pd.read_parquet(f"{DATA}/{src}_funding_{sym}.parquet")
        f = f.sort_values("ts").reset_index(drop=True)
        return f[["ts", "rate"]]
    except Exception:
        return None

# ---------------- indicators ----------------
def sma(s, n): return s.rolling(n).mean()
def ema(s, n): return s.ewm(span=n, adjust=False).mean()

def rsi(s, n=14):
    d = s.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    rs = up / dn.replace(0, np.nan)
    return (100 - 100 / (1 + rs)).fillna(50)

def atr(df, n=14):
    tr = pd.concat([df.high - df.low,
                    (df.high - df.close.shift()).abs(),
                    (df.low - df.close.shift()).abs()], axis=1).max(axis=1)
    return tr.rolling(n).mean()

def adx(df, n=14):
    up = df.high.diff()
    dn = -df.low.diff()
    plus_dm = np.where((up > dn) & (up > 0), up, 0.0)
    minus_dm = np.where((dn > up) & (dn > 0), dn, 0.0)
    tr = pd.concat([df.high - df.low,
                    (df.high - df.close.shift()).abs(),
                    (df.low - df.close.shift()).abs()], axis=1).max(axis=1)
    atr_w = tr.ewm(alpha=1 / n, adjust=False).mean()
    pdi = 100 * pd.Series(plus_dm, index=df.index).ewm(alpha=1 / n, adjust=False).mean() / atr_w
    mdi = 100 * pd.Series(minus_dm, index=df.index).ewm(alpha=1 / n, adjust=False).mean() / atr_w
    dx = ((pdi - mdi).abs() / (pdi + mdi).replace(0, np.nan)) * 100
    return dx.ewm(alpha=1 / n, adjust=False).mean().fillna(0)

def zscore(s, n=20):
    m = s.rolling(n).mean()
    sd = s.rolling(n).std()
    return (s - m) / sd.replace(0, np.nan)

# ---------------- bundle strategies (ported faithfully) ----------------
def s_trend(df, f=20, s=50):
    fa, sl = sma(df.close, f), sma(df.close, s)
    p = np.where(fa > sl, 1, np.where(fa < sl, -1, 0)).astype(float)
    p[:s] = 0
    return pd.Series(p, index=df.index)

def s_meanrev(df, n=14, lo=30, hi=70):
    r = rsi(df.close, n).values
    p = np.zeros(len(df)); c = 0
    for i in range(len(df)):
        if i < n:
            continue
        if c == 0:
            if r[i] < lo: c = 1
            elif r[i] > hi: c = -1
        elif c == 1 and r[i] >= 50: c = 0
        elif c == -1 and r[i] <= 50: c = 0
        p[i] = c
    return pd.Series(p, index=df.index)

def s_breakout(df, n=20):
    hi = df.high.rolling(n).max().shift(1)
    lo = df.low.rolling(n).min().shift(1)
    p = np.zeros(len(df)); c = 0
    hiv, lov, cl = hi.values, lo.values, df.close.values
    for i in range(len(df)):
        if i < n:
            continue
        if cl[i] > hiv[i]: c = 1
        elif cl[i] < lov[i]: c = -1
        p[i] = c
    return pd.Series(p, index=df.index)

BUNDLE_STRATS = {"Trend(SMA20/50)": s_trend,
                 "MeanRev(RSI14-30/70)": s_meanrev,
                 "Breakout(Donch20)": s_breakout}

# ---------------- E1: bundle-faithful flip engine ----------------
def flip_backtest(df, pos, taker, slip=SLIP, funding_day=FUND_DAY, bars_per_day=24):
    ret = df.close.pct_change().fillna(0)
    held = pos.shift(1).fillna(0)
    turn = held.diff().abs().fillna(held.abs())
    fund_bar = funding_day / bars_per_day
    net = held * ret - turn * (taker + slip) - held.abs() * fund_bar
    eq = (1 + net).cumprod()
    dd = eq / eq.cummax() - 1
    tr = []; c = 0.0; ei = None
    hv, nv = held.values, net.values
    for i in range(len(hv)):
        if hv[i] != c:
            if c != 0 and ei is not None:
                tr.append(nv[ei:i].sum())
            ei = i if hv[i] != 0 else None
            c = hv[i]
    if c != 0 and ei is not None:
        tr.append(nv[ei:].sum())
    tr = np.array(tr) if tr else np.array([0.0])
    w, l = tr[tr > 0], tr[tr < 0]
    return {"Return%": (eq.iloc[-1] - 1) * 100,
            "MaxDD%": dd.min() * 100,
            "Trades": len(tr),
            "Win%": len(w) / len(tr) * 100 if len(tr) else 0,
            "PF": w.sum() / abs(l.sum()) if l.sum() != 0 else float("inf")}

# ---------------- E2: bracket engine (vectorized) ----------------
def _resolve_exits(sig, side, close, high, low, a, tp_mult, sl_mult, tstop, n):
    """Vectorized: for each signal index i (entry limit=close[i], fill bar i+1),
    return fill mask, exit index, exit price, reason code (0=SL,1=TP,2=TIME).
    Conservative: SL wins any bar where both touched."""
    if len(sig) == 0:
        return (np.zeros(0, bool),) + (np.zeros(0, int), np.zeros(0), np.zeros(0, int))
    L = close[sig]
    tp = L + side * tp_mult * a[sig]
    sl = L - side * sl_mult * a[sig]
    fb = sig + 1
    filled = (low[fb] <= L) if side == 1 else (high[fb] >= L)
    T = tstop + 1  # bars fb..fb+tstop inclusive
    # pad arrays so windows never run off the end (padded bars can't trigger)
    pad_hi = np.concatenate([high, np.full(T, -np.inf)])
    pad_lo = np.concatenate([low, np.full(T, np.inf)])
    from numpy.lib.stride_tricks import sliding_window_view as swv
    Whi = swv(pad_hi, T)[fb]          # (n_sig, T)
    Wlo = swv(pad_lo, T)[fb]
    if side == 1:
        sl_hit = Wlo <= sl[:, None]
        tp_hit = Whi >= tp[:, None]
    else:
        sl_hit = Whi >= sl[:, None]
        tp_hit = Wlo <= tp[:, None]
    j_sl = np.where(sl_hit.any(1), sl_hit.argmax(1), T)
    j_tp = np.where(tp_hit.any(1), tp_hit.argmax(1), T)
    reason = np.where(j_sl <= j_tp, 0, 1)              # SL priority on same bar
    j_exit = np.minimum(j_sl, j_tp)
    timed = j_exit >= T
    exit_i = np.minimum(fb + np.where(timed, tstop, j_exit), n - 1)
    reason = np.where(timed, 2, reason)
    exit_px = np.where(reason == 0, sl, np.where(reason == 1, tp, close[exit_i]))
    return filled, exit_i, exit_px, reason

def bracket_backtest(df, entries_long, entries_short, cfg, fees, funding=None):
    """Signal at bar close t -> limit entry at close[t], fill only if bar t+1 touches.
    ATR TP/SL brackets + time stop. One position at a time per symbol.
    Returns trade DataFrame."""
    n = len(df)
    close = df.close.values; high = df.high.values; low = df.low.values
    ts = df.ts.values
    a = atr(df, cfg.get("atr_n", 14)).values
    valid = np.isfinite(a) & (a > 0)
    maker, taker = fees["maker"], fees["taker"]
    fund_ts, fund_cum = None, None
    if funding is not None and len(funding):
        fund_ts = funding.ts.values
        fund_cum = np.concatenate([[0.0], np.cumsum(funding.rate.values)])

    packs = []
    for side, mask in ((1, entries_long), (-1, entries_short)):
        sig = np.where(mask & valid & (np.arange(n) + 1 < n))[0]
        f, ei, ep, rs = _resolve_exits(sig, side, close, high, low, a,
                                       cfg["tp_mult"], cfg["sl_mult"], cfg["tstop"], n)
        for k in range(len(sig)):
            if f[k]:
                packs.append((sig[k], side, ei[k], ep[k], rs[k]))
    packs.sort(key=lambda x: x[0])

    trades, in_pos_until = [], -1
    for i, side, exit_i, exit_px, rs in packs:
        if i <= in_pos_until:
            continue
        L = close[i]; fb = i + 1
        gross = side * (exit_px - L) / L
        reason = ("SL", "TP", "TIME")[rs]
        fee = maker + (maker if reason == "TP" else taker + SLIP)
        fnd = 0.0
        if fund_cum is not None:
            j1 = np.searchsorted(fund_ts, ts[fb], side="right")
            j2 = np.searchsorted(fund_ts, ts[exit_i], side="right")
            fnd = side * (fund_cum[j2] - fund_cum[j1])   # long pays positive funding
        net = gross - fee - fnd
        trades.append((ts[fb], ts[exit_i], side, L, exit_px, reason, gross, fee, fnd, net))
        in_pos_until = exit_i
    return pd.DataFrame(trades, columns=["entry_ts", "exit_ts", "side", "entry", "exit",
                                         "reason", "gross", "fee", "funding", "net"])

def summarize(trades, alloc=0.10):
    if len(trades) == 0:
        return {"Trades": 0, "Win%": np.nan, "PF": np.nan, "NetSum%": 0.0,
                "AvgBps": np.nan, "Eq%": 0.0, "MaxDD%": 0.0}
    t = trades.sort_values("exit_ts")
    net = t.net.values
    w, l = net[net > 0], net[net < 0]
    eq = np.cumprod(1 + net * alloc)
    peak = np.maximum.accumulate(eq)
    dd = (eq / peak - 1).min() * 100 if len(eq) else 0.0
    return {"Trades": int(len(net)),
            "Win%": 100 * (net > 0).mean(),
            "PF": w.sum() / abs(l.sum()) if l.sum() != 0 else float("inf"),
            "NetSum%": net.sum() * 100,
            "AvgBps": net.mean() * 1e4,
            "Eq%": (eq[-1] - 1) * 100,
            "MaxDD%": dd}

def bootstrap(trades, iters=2000, seed=7):
    if len(trades) < 5:
        return {"P(net>0)": np.nan, "WR_lo": np.nan, "WR_hi": np.nan}
    rng = np.random.default_rng(seed)
    net = trades.net.values
    n = len(net)
    sums, wrs = np.empty(iters), np.empty(iters)
    for k in range(iters):
        s = net[rng.integers(0, n, n)]
        sums[k] = s.sum()
        wrs[k] = (s > 0).mean()
    return {"P(net>0)": float((sums > 0).mean()),
            "WR_lo": float(np.percentile(wrs, 5) * 100),
            "WR_hi": float(np.percentile(wrs, 95) * 100)}
