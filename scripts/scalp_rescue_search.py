"""RESCUE-SEARCH: try hardest to find a profitable mechanical scalp config.

Adversarial steelman of the NO_EDGE finding. Imports building blocks from
scalp_replay_backtest.py and adds:
  - BLENDED maker/taker cost model (maker entry+TP on wins; taker SL+slip on
    losses — because a stop exit MUST cross the spread). Breakeven ~43.1%.
  - per-cohort (side) per-OOS-fold EV (edge => EV>0 in ALL folds).
  - 1h/3yr port (real OOS depth vs 15m's single ~45d regime slice).
  - longs-only-in-bull / shorts-only-in-bear regime conditioning.
  - RSI threshold sweep + meanrev deviation sweep.
  - momentum+meanrev ensemble.

A variant only "rescues" if it clears its cost-adjusted breakeven EV>0
OUT-OF-SAMPLE across ALL folds, on a multi-regime window. No network.
"""

from __future__ import annotations

import numpy as np
import scalp_replay_backtest as B

# ---- blended maker/taker cost model -------------------------------------
# Bybit futures: maker 0.0001, taker 0.0006. Best-case maker entry+TP.
# SL leg is ALWAYS taker (you cross spread to stop out) + stop slippage.
MK_FEE = 0.0001  # maker per side (bybit futures, best case)
TK_FEE = 0.0006  # taker per side
SLIP_STOP = 0.0010  # stop-out slippage


def bracket_blended(
    df, entry_idx, side, max_hold, tiebreak="sl", start_offset=0, mode="taker", arrs=None
):
    """Like B.bracket_outcome but with selectable cost model.

    mode='taker' : current taker model (5bps open/close, 10bps stop, 0.0006/side)
    mode='maker' : maker entry (no open slip) + maker TP exit (no close slip);
                   SL exit is TAKER + stop slip (realistic — stops cross spread).
    arrs: optional (open,high,low,close) numpy tuple to avoid per-bar .iloc.
    Returns (net_return_frac, outcome_str, bars_held).
    """
    if arrs is not None:
        oarr, harr, larr, carr = arrs
    else:
        oarr = df["open"].to_numpy()
        harr = df["high"].to_numpy()
        larr = df["low"].to_numpy()
        carr = df["close"].to_numpy()
    o = oarr[entry_idx]
    if mode == "maker":
        # maker entry: assume resting limit fills at open (CHARITABLE — see notes)
        fill = o
    else:
        fill = o * (1 + B.SLIP_OPEN) if side == "buy" else o * (1 - B.SLIP_OPEN)

    if side == "buy":
        sl = fill * (1 - B.SL_PCT)
        tp = fill * (1 + B.TP_PCT)
    else:
        sl = fill * (1 + B.SL_PCT)
        tp = fill * (1 - B.TP_PCT)

    end = min(entry_idx + max_hold, len(oarr) - 1)
    for j in range(entry_idx + start_offset, end + 1):
        hi, lo = harr[j], larr[j]
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
            exit_px = sl * (1 - SLIP_STOP) if side == "buy" else sl * (1 + SLIP_STOP)
            if mode == "maker":
                cost = MK_FEE + TK_FEE  # maker entry + taker stop
            else:
                cost = TK_FEE * 2
            return B._ret(side, fill, exit_px) - cost, "stop_loss", j - entry_idx
        # TP
        if mode == "maker":
            exit_px = tp  # maker limit fills exactly at tp
            cost = MK_FEE + MK_FEE  # maker entry + maker TP
        else:
            exit_px = tp * (1 - B.SLIP_CLOSE) if side == "buy" else tp * (1 + B.SLIP_CLOSE)
            cost = TK_FEE * 2
        return B._ret(side, fill, exit_px) - cost, "take_profit", j - entry_idx
    # mark-out at cap (treat as taker close)
    c = carr[end]
    exit_px = c * (1 - B.SLIP_CLOSE) if side == "buy" else c * (1 + B.SLIP_CLOSE)
    cost = (MK_FEE + TK_FEE) if mode == "maker" else TK_FEE * 2
    return B._ret(side, fill, exit_px) - cost, "markout", end - entry_idx


def breakeven_wr(mode):
    SL, TP = B.SL_PCT, B.TP_PCT
    if mode == "maker":
        cwin, closs = MK_FEE * 2, MK_FEE + TK_FEE + SLIP_STOP
    else:
        cwin = TK_FEE * 2 + B.SLIP_OPEN + B.SLIP_CLOSE
        closs = TK_FEE * 2 + B.SLIP_OPEN + SLIP_STOP
    return (SL + closs) / ((TP - cwin) + (SL + closs))


def fold_split(rows):
    """rows = list of (ts, side, ret). Returns dict fold-> list[(side,ret)]."""
    if len(rows) < 30:
        return None
    ts = [t for t, _, _ in rows]
    q1, q2 = np.quantile(ts, [1 / 3, 2 / 3])
    folds = {"f1": [], "f2": [], "f3": []}
    for t, s, r in rows:
        k = "f1" if t <= q1 else ("f2" if t <= q2 else "f3")
        folds[k].append((s, r))
    return folds


def report_cohort_folds(label, rows, mode, be):
    """Print ALL/LONGS/SHORTS EV per fold; flag edge = EV>0 in all 3 folds."""
    print(f"\n  [{label}]  n={len(rows)}  mode={mode}  breakeven_WR={be * 100:.1f}%")
    folds = fold_split(rows)
    for cohort, sel in [
        ("ALL", lambda s: True),
        ("LONGS", lambda s: s == "buy"),
        ("SHORTS", lambda s: s == "sell"),
    ]:
        cr = [r for _, s, r in rows if sel(s)]
        if not cr:
            continue
        arr = np.array(cr)
        wr, ev = (arr > 0).mean(), arr.mean()
        line = f"    {cohort:7s} all: n={len(arr):4d} WR={wr * 100:4.1f}% EV={ev * 100:+.3f}%"
        if folds:
            fevs = []
            for fk, fv in folds.items():
                fc = [r for s, r in fv if sel(s)]
                if fc:
                    fa = np.array(fc)
                    fevs.append((fk, len(fa), (fa > 0).mean(), fa.mean()))
            fstr = "  ".join(
                f"{fk}:WR{w * 100:.0f}/EV{e * 100:+.2f}%(n{n})" for fk, n, w, e in fevs
            )
            all_pos = len(fevs) == 3 and all(e > 0 for _, _, _, e in fevs)
            flag = "  <== EDGE (EV>0 all folds)" if all_pos else ""
            line += "\n             folds " + fstr + flag
        print(line)


# ---- signal generators on arbitrary TF ----------------------------------
def momentum_sigs(
    df,
    rsi_lo_long=40,
    rsi_hi_long=70,
    rsi_lo_short=30,
    rsi_hi_short=60,
    regime=None,
    regime_ema=(50, 200),
):
    """EMA9/21 cross + RSI gate on the SAME tf. regime: None / 'bull' / 'bear'
    using EMA(fast)>EMA(slow) on same series (longs only in bull, shorts in bear).
    Vectorized. Signal at bar i -> enter next bar (i+1) open."""
    c = df["close"].to_numpy()
    e9 = B.ema(df["close"], 9).to_numpy()
    e21 = B.ema(df["close"], 21).to_numpy()
    r = B.rsi(df["close"], 14).to_numpy()
    bull = (
        B.ema(df["close"], regime_ema[0]).to_numpy() >= B.ema(df["close"], regime_ema[1]).to_numpy()
    )
    n = len(c)
    prev_le = np.concatenate([[False], e9[:-1] <= e21[:-1]])
    prev_ge = np.concatenate([[False], e9[:-1] >= e21[:-1]])
    cu = prev_le & (e9 > e21)
    cd = prev_ge & (e9 < e21)
    start = max(22, regime_ema[1])
    idx = np.arange(n)
    valid = (idx >= start) & (idx < n - 1)
    long_mask = valid & cu & (r >= rsi_lo_long) & (r <= rsi_hi_long)
    short_mask = valid & cd & (r >= rsi_lo_short) & (r <= rsi_hi_short)
    if regime == "bull":
        long_mask &= bull
    if regime == "bear":
        short_mask &= ~bull
    sigs = [(i + 1, "buy") for i in idx[long_mask]]
    sigs += [(i + 1, "sell") for i in idx[short_mask]]
    return sigs


def meanrev_sigs(df, dev_mult=1.0, rsi_long=35, rsi_short=65, regime=None, regime_ema=(50, 200)):
    """Vectorized mean-reversion fade. Signal at bar i -> enter i+1 open."""
    c = df["close"].to_numpy()
    sma = df["close"].rolling(20).mean().to_numpy()
    a = B.atr(df, 14).to_numpy()
    r = B.rsi(df["close"], 14).to_numpy()
    bull = (
        B.ema(df["close"], regime_ema[0]).to_numpy() >= B.ema(df["close"], regime_ema[1]).to_numpy()
    )
    n = len(c)
    dev = c - sma
    prev_c = np.concatenate([[np.nan], c[:-1]])
    bounce_up = c > prev_c
    bounce_dn = c < prev_c
    start = max(25, regime_ema[1])
    idx = np.arange(n)
    good = (idx >= start) & (idx < n - 1) & ~np.isnan(sma) & ~np.isnan(a) & (a > 0)
    long_mask = good & (dev < -dev_mult * a) & (r < rsi_long) & bounce_up
    short_mask = good & (dev > dev_mult * a) & (r > rsi_short) & bounce_dn
    if regime == "bear_only":
        short_mask &= ~bull
    sigs = [(i + 1, "buy") for i in idx[long_mask]]
    sigs += [(i + 1, "sell") for i in idx[short_mask]]
    return sigs


def run(tf, sig_fn, label, mode="maker", tiebreak="sl", start_offset=0, max_hold=None, syms=None):
    if max_hold is None:
        max_hold = B.MAX_HOLD_BARS_1H if tf == "1h" else B.MAX_HOLD_BARS_15M
    if syms is None:
        syms = B.SYMS
    be = breakeven_wr(mode)
    rows = []
    for sym in syms:
        df = B.load(sym, tf)
        if df is None or len(df) < 250:
            continue
        arrs = (
            df["open"].to_numpy(),
            df["high"].to_numpy(),
            df["low"].to_numpy(),
            df["close"].to_numpy(),
        )
        tsarr = df["ts"].to_numpy()
        for ei, side in sig_fn(df):
            if ei >= len(df):
                continue
            ret, _, _ = bracket_blended(
                df,
                ei,
                side,
                max_hold,
                tiebreak=tiebreak,
                start_offset=start_offset,
                mode=mode,
                arrs=arrs,
            )
            rows.append((tsarr[ei], side, ret))
    rows.sort()
    report_cohort_folds(label, rows, mode, be)
    return rows


if __name__ == "__main__":
    print("=" * 78)
    print("RESCUE-SEARCH — try hardest to find a profitable scalp")
    print(f"  taker breakeven WR  = {breakeven_wr('taker') * 100:.1f}%")
    print(
        f"  maker breakeven WR  = {breakeven_wr('maker') * 100:.1f}%  "
        "(maker entry+TP on wins; TAKER stop on losses)"
    )
    print("=" * 78)

    print("\n### 15m / ~45d (single regime slice) ###")
    run("15m", lambda d: meanrev_sigs(d), "meanrev 15m / MAKER / pess", "maker", "sl", 0)
    run("15m", lambda d: meanrev_sigs(d), "meanrev 15m / MAKER / OPTIMISTIC", "maker", "tp", 1)
    run("15m", lambda d: momentum_sigs(d), "momentum 15m / MAKER / pess", "maker", "sl", 0)

    print("\n### 1h / 3yr (MULTI-REGIME — real OOS depth) ###")
    run("1h", lambda d: momentum_sigs(d), "momentum 1h / MAKER / pess", "maker", "sl", 0)
    run("1h", lambda d: meanrev_sigs(d), "meanrev 1h / MAKER / pess", "maker", "sl", 0)
    run("1h", lambda d: meanrev_sigs(d), "meanrev 1h / MAKER / OPTIMISTIC", "maker", "tp", 1)
    run("1h", lambda d: momentum_sigs(d), "momentum 1h / TAKER / pess", "taker", "sl", 0)

    print("\n### regime-conditioned (longs-only-bull / shorts-only-bear) ###")
    run(
        "1h",
        lambda d: momentum_sigs(d, regime="bull"),
        "momentum 1h / longs-in-bull / MAKER",
        "maker",
        "sl",
        0,
    )
    run(
        "1h",
        lambda d: meanrev_sigs(d, regime="bear_only"),
        "meanrev 1h / shorts-in-bear / MAKER",
        "maker",
        "sl",
        0,
    )

    print("\n### meanrev shorts deeper stretch (dev 1.5/2.0, RSI 70/75) ###")
    run(
        "1h",
        lambda d: meanrev_sigs(d, dev_mult=1.5, rsi_short=70),
        "meanrev 1h dev1.5 RSI70 / MAKER",
        "maker",
        "sl",
        0,
    )
    run(
        "15m",
        lambda d: meanrev_sigs(d, dev_mult=1.5, rsi_short=70),
        "meanrev 15m dev1.5 RSI70 / MAKER",
        "maker",
        "sl",
        0,
    )
    run(
        "1h",
        lambda d: meanrev_sigs(d, dev_mult=2.0, rsi_short=75),
        "meanrev 1h dev2.0 RSI75 / MAKER",
        "maker",
        "sl",
        0,
    )
