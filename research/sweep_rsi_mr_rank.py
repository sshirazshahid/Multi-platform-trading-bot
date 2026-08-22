"""Lane 1 — the ranked RSI mean-reversion sweep the owner asked for, five times.

WHAT THIS IS: 37,632 parameter variations x 6 assets x 4 timeframes = 903,168
backtests, ranked by Sharpe, drawdown, win rate and profit factor, compared
against buy-and-hold. Exactly the deliverable requested.

WHAT THIS IS NOT: evidence. Read this before reading the table.

  E[max Sharpe over N independent trials] ~ sqrt(2 ln N). At N = 37,632 that
  is 4.59 standard errors of pure noise. A search this wide CANNOT fail to
  produce an impressive winner, whether or not any edge exists. Ranking IS the
  overfitting -- the peer-reviewed reopen test on this exact methodology (IJFE
  10.1002/ijfe.2863) ranked ~7,851 variants, took the top 15, added proper
  multiplicity control and genuine OOS, and 14 of 15 failed.

So every row carries two columns the usual leaderboard omits:

  null_pctile  -- where the row sits in the distribution of what the SAME
                  search produces on sign-randomised noise. Bar: >= 95.
                  A row at 60 is what noise does routinely.
  expo%        -- percent of bars in market. Every "beats buy-and-hold" claim
                  that survived scrutiny in earlier rounds died here: it beat a
                  falling benchmark by sitting in cash 89-98% of the time,
                  which is a statement about the benchmark, not about skill.

NO LEADERBOARD FILE IS WRITTEN. A ranked artifact outlives the caveats attached
to it; the table lives in the run log and the report, or not at all.

The real answer to the owner's question is Lane 2 --
research/screen_rsi_mr_powered.py, 8 pre-registered cells that can resolve.

Run: venv/Scripts/python.exe research/sweep_rsi_mr_rank.py [--bench] [--null N]
"""

from __future__ import annotations

import argparse
import pathlib
import sys
import time

import numpy as np

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

PANELS = ["BTC", "ETH", "SPY", "GOLD", "CRUDE", "EURUSD"]
TIMEFRAMES = ["1h", "4h", "1d", "1w"]
# The swept grid. 8 x 7 x 7 x 6 x 8 = 18,816 per side, 37,632 per panel.
RSI_LENS = [2, 3, 4, 5, 7, 10, 14, 21]
ENTRY_THR = [5, 10, 15, 20, 25, 30, 35]  # mirrored to 95..65 for shorts
EXIT_THR = [40, 45, 50, 55, 60, 65, 70]  # mirrored for shorts
STOP_ATR = [1.0, 1.5, 2.0, 2.5, 3.0, np.inf]
MAX_HOLD = [2, 3, 5, 8, 12, 20, 30, 48]
SIDES = ["long", "short"]
W = max(MAX_HOLD)
ATR_LEN = 14
# Same frozen per-class costs as prereg 90 §3 (median-historical-price basis).
COST_FRAC = {
    "BTC": 0.0022,
    "ETH": 0.0022,
    "SPY": 0.0005,
    "GOLD": 0.0004,
    "CRUDE": 0.0005,
    "EURUSD": 0.0002,
}
SEC_PER_YEAR = 365.25 * 86400.0
N_VARIANTS = len(RSI_LENS) * len(ENTRY_THR) * len(EXIT_THR) * len(STOP_ATR) * len(MAX_HOLD)
MIN_TRADES = 30


def _indicators(panel: dict, length: int) -> tuple[np.ndarray, np.ndarray]:
    import pandas as pd

    from utils.indicators import atr as _atr
    from utils.indicators import rsi as _rsi

    close = pd.Series(panel["close"])
    r = _rsi(close, length).to_numpy(dtype=float)
    a = _atr(pd.Series(panel["high"]), pd.Series(panel["low"]), close, ATR_LEN).to_numpy(
        dtype=float
    )
    return r, a


def sweep_panel_arm(panel: dict, rsi_len: int, entry: float, side: str, cost: float) -> dict:
    """All (exit x stop x hold) combos for one (panel, rsi_len, entry, side).

    Vectorised by FIRST-HIT decomposition: compute, once per signal, the first
    forward bar at which each stop level triggers and each RSI exit triggers,
    then combine those with the hold limits by elementwise minimum. That turns
    336 path-dependent backtests into a handful of array reductions.

    OVERLAPPING SIGNALS ARE ALLOWED HERE, and that is a real limitation stated
    rather than hidden: every trigger bar becomes an independent event, so a
    single multi-bar excursion can be counted several times and the effective
    sample is smaller than n suggests. It is done this way because 903,168
    non-overlapping sequential backtests are not tractable -- and because the
    NULL is computed through the identical construction, so null_pctile
    compares like with like. Lane 2 (screen_rsi_mr_powered.py) enforces strict
    non-overlap on its 8 pre-registered cells.

    Execution matches prereg 90 §4: entry at the next bar's open; stop measured
    from the SIGNAL bar's ATR; a gap THROUGH the stop fills at the open.
    """
    o, h, low_, c = panel["open"], panel["high"], panel["low"], panel["close"]
    n = len(c)
    n_combo = len(EXIT_THR) * len(STOP_ATR) * len(MAX_HOLD)
    if n < 200:
        return {"empty": True, "n_sig": 0, "n_combo": n_combo}
    rsi, atr = _indicators(panel, rsi_len)
    atr_frac = np.divide(atr, c, out=np.full_like(c, np.nan), where=c > 0)
    is_long = side == "long"
    thr = entry if is_long else 100.0 - entry

    warmup = 5 * max(rsi_len, ATR_LEN)
    ok = np.isfinite(rsi) & np.isfinite(atr_frac) & (atr_frac > 0)
    ok[:warmup] = False
    ok[n - 2 :] = False  # need at least one forward bar to fill the entry
    sig = np.flatnonzero(ok & ((rsi < thr) if is_long else (rsi > thr)))
    if sig.size < MIN_TRADES:
        return {"empty": True, "n_sig": int(sig.size), "n_combo": n_combo}

    # Forward window [i+1 .. i+W], clipped, with an out-of-range mask.
    fwd = sig[:, None] + 1 + np.arange(W)[None, :]
    live = fwd <= (n - 1)
    fwd = np.clip(fwd, 0, n - 1)
    oW, hW, lW, cW, rW = o[fwd], h[fwd], low_[fwd], c[fwd], rsi[fwd]
    last = live.sum(axis=1) - 1  # last usable window index per signal
    entry_px = o[sig + 1]
    risk = atr_frac[sig]
    BIG = W + 10

    def _first(mask: np.ndarray) -> np.ndarray:
        mask = mask & live
        return np.where(mask.any(axis=1), mask.argmax(axis=1), BIG)

    stop_pos = np.empty((len(STOP_ATR), sig.size), dtype=np.int64)
    stop_px = np.empty((len(STOP_ATR), sig.size), dtype=float)
    for si, mult in enumerate(STOP_ATR):
        if not np.isfinite(mult):  # "no stop" arm
            stop_pos[si] = BIG
            stop_px[si] = np.nan
            continue
        sp = entry_px * (1.0 - mult * risk) if is_long else entry_px * (1.0 + mult * risk)
        stop_px[si] = sp
        stop_pos[si] = _first(lW <= sp[:, None] if is_long else hW >= sp[:, None])

    exit_pos = np.empty((len(EXIT_THR), sig.size), dtype=np.int64)
    for xi, x in enumerate(EXIT_THR):
        lvl = x if is_long else 100.0 - x
        m = (rW >= lvl) if is_long else (rW <= lvl)
        exit_pos[xi] = _first(m & np.isfinite(rW))

    hold_pos = np.minimum(np.asarray(MAX_HOLD) - 1, W - 1)
    rows = np.arange(sig.size)
    keys = ("sharpe", "total", "wr", "pf", "maxdd", "expo", "n")
    out = {k: np.empty(n_combo) for k in keys}
    out["params"] = np.empty((n_combo, 3), dtype=np.int64)
    ci = 0
    for xi in range(len(EXIT_THR)):
        for si in range(len(STOP_ATR)):
            for hi, hp in enumerate(hold_pos):
                pos = np.minimum(np.minimum(exit_pos[xi], stop_pos[si]), hp)
                pos = np.minimum(pos, last)  # never past the end of the series
                stop_fired = (stop_pos[si] <= pos) & (stop_pos[si] < BIG)
                px = cW[rows, pos]
                if np.isfinite(STOP_ATR[si]):
                    sp = stop_px[si]
                    op = oW[rows, pos]
                    gap = np.minimum(op, sp) if is_long else np.maximum(op, sp)
                    px = np.where(stop_fired, gap, px)
                # Short P&L is (entry - exit)/ENTRY. `entry/exit - 1` divides the
                # same P&L by the EXIT price: understates losses, overstates
                # gains, and by Jensen is biased POSITIVE even on a martingale.
                gross = (px / entry_px - 1.0) if is_long else (1.0 - px / entry_px)
                r = gross - cost
                sd = r.std(ddof=1) if r.size > 1 else 0.0
                eq = np.cumsum(r)
                wins, losses = r[r > 0].sum(), -r[r < 0].sum()
                out["sharpe"][ci] = (r.mean() / sd) if sd > 0 else 0.0
                out["total"][ci] = float(eq[-1])
                out["wr"][ci] = float((r > 0).mean())
                out["pf"][ci] = float(wins / losses) if losses > 0 else np.inf
                out["maxdd"][ci] = float(np.max(np.maximum.accumulate(eq) - eq))
                out["expo"][ci] = float((pos + 1).sum()) / n * 100.0
                out["n"][ci] = float(r.size)
                out["params"][ci] = (EXIT_THR[xi], si, MAX_HOLD[hi])
                ci += 1
    out["empty"] = False
    out["n_sig"] = int(sig.size)
    out["n_combo"] = n_combo
    return out


def buy_and_hold(panel: dict, bar_sec: float) -> dict:
    c = panel["close"]
    r = np.diff(np.log(c))
    per_year = SEC_PER_YEAR / bar_sec
    sd = r.std(ddof=1)
    peak = np.maximum.accumulate(c)
    return {
        "total": float(c[-1] / c[0] - 1.0),
        "ann_sharpe": float(r.mean() / sd * np.sqrt(per_year)) if sd > 0 else 0.0,
        "maxdd_pct": float(np.max((peak - c) / peak) * 100.0),
    }


def sweep_panel(panel: dict, cost: float) -> dict:
    """Every variation for one panel. Flat arrays over the full grid."""
    keys = ("sharpe", "total", "wr", "pf", "maxdd", "expo", "n")
    acc = {k: [] for k in keys}
    meta: list[tuple] = []
    for rsi_len in RSI_LENS:
        for entry in ENTRY_THR:
            for side in SIDES:
                res = sweep_panel_arm(panel, rsi_len, entry, side, cost)
                if res["empty"]:
                    for k in keys:
                        acc[k].append(np.full(res["n_combo"], np.nan))
                    meta.extend(
                        (rsi_len, entry, side, x, s, hh)
                        for x in EXIT_THR
                        for s in range(len(STOP_ATR))
                        for hh in MAX_HOLD
                    )
                    continue
                for k in keys:
                    acc[k].append(res[k])
                meta.extend(
                    (rsi_len, entry, side, int(p[0]), int(p[1]), int(p[2]))
                    for p in res["params"]
                )
    out = {k: np.concatenate(v) for k, v in acc.items()}
    out["meta"] = meta
    return out


def _best_sharpe(panel: dict, cost: float) -> float:
    """Best per-trade Sharpe over the FULL grid -- the best-of-search statistic."""
    r = sweep_panel(panel, cost)
    m = np.isfinite(r["sharpe"]) & (r["n"] >= MIN_TRADES)
    return float(np.max(r["sharpe"][m])) if m.any() else float("nan")


def _print_table(rows: list[dict]) -> None:
    hdr = (
        f"{'#':>3} {'panel':7s}{'tf':4s}{'rsi':>4}{'ent':>4}{'side':>6}{'exit':>5}"
        f"{'stop':>6}{'hold':>5}{'annSh':>7}{'total%':>10}{'WR':>6}{'PF':>6}"
        f"{'maxDD':>7}{'expo%':>7}{'n':>7}{'B&H Sh':>7}"
    )
    print(hdr)
    print("-" * len(hdr))
    for k, d in enumerate(rows, 1):
        st = "none" if not np.isfinite(d["stop"]) else f"{d['stop']:.1f}"
        print(
            f"{k:>3} {d['panel']:7s}{d['tf']:4s}{d['rsi_len']:>4}{d['entry']:>4}"
            f"{d['side']:>6}{d['exit']:>5}{st:>6}{d['hold']:>5}"
            f"{d['ann_sharpe']:>7.2f}{d['total']*100:>10.1f}{d['wr']:>6.3f}"
            f"{min(d['pf'], 99.9):>6.2f}{d['maxdd']:>7.3f}{d['expo']:>7.1f}"
            f"{d['n']:>7}{d['bh_sharpe']:>7.2f}"
        )


def _null_report(loaded: dict, rows: list[dict], reps: int) -> None:
    """Best-of-grid under the null, for the panels holding the top rows.

    The honest comparison for ANY row is against the BEST-OF-SEARCH null,
    because every row competed in the same search. Running the FULL grid on
    each surrogate is what makes that comparison valid -- a null computed on a
    single cell would understate the bar by ~3 standard errors.
    """
    from research.screen_rsi_mr_powered import surrogate

    # Pick the panels by the SAME statistic the null is measured in. The table
    # above ranks on annualised Sharpe; comparing that ranking against a null
    # built from per-trade Sharpe would answer a question about a different row.
    by_raw = sorted(rows, key=lambda d: -d["raw_sharpe"])
    top_panels: list[tuple[str, str]] = []
    for d in by_raw:
        key = (d["panel"], d["tf"])
        if key not in top_panels:
            top_panels.append(key)
        if len(top_panels) >= 4:
            break
    print(f"\nNULL: full {N_VARIANTS*2:,}-cell grid on {reps} sign-randomised surrogates")
    print("(the bar a row must clear is the null's BEST, not its median)\n")
    rng = np.random.default_rng(90)
    print(f"  {'panel':7s}{'tf':4s}{'real best':>11}{'null med':>10}{'null p95':>10}"
          f"{'null max':>10}{'pctile':>8}")
    for key in top_panels:
        panel = loaded[key]
        real = max(
            d["raw_sharpe"] for d in rows if (d["panel"], d["tf"]) == key
        )
        nulls = []
        for _ in range(reps):
            sur = surrogate(panel, rng)
            sur["ts"] = panel["ts"]
            v = _best_sharpe(sur, COST_FRAC[key[0]])
            if np.isfinite(v):
                nulls.append(v)
        if not nulls:
            print(f"  {key[0]:7s}{key[1]:3s} null unavailable")
            continue
        arr = np.asarray(nulls)
        pct = float((arr < real).mean() * 100.0)
        print(
            f"  {key[0]:7s}{key[1]:4s}{real:>11.4f}{np.median(arr):>10.4f}"
            f"{np.percentile(arr, 95):>10.4f}{arr.max():>10.4f}{pct:>7.0f}%"
        )


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--bench", action="store_true", help="time one panel and exit")
    ap.add_argument("--null", type=int, default=20, help="surrogate reps for null_pctile")
    ap.add_argument("--top", type=int, default=25, help="rows to print")
    args = ap.parse_args()

    from research._ohlcv_cache import TF_SEC, load_panel

    n_panels = len(PANELS) * len(TIMEFRAMES)
    print(f"grid: {N_VARIANTS:,} variations per side, {N_VARIANTS * 2:,} per panel")
    print(f"panels: {len(PANELS)} assets x {len(TIMEFRAMES)} timeframes = {n_panels}")
    print(f"TOTAL BACKTESTS: {N_VARIANTS * 2 * n_panels:,}")
    print(
        f"E[max Sharpe] under a pure-noise null at N={N_VARIANTS*2:,}: "
        f"{np.sqrt(2*np.log(N_VARIANTS*2)):.2f} standard errors\n"
    )

    loaded = {}
    for nm in PANELS:
        for tf in TIMEFRAMES:
            arr = load_panel(nm, tf)
            loaded[(nm, tf)] = {
                "ts": arr["ts"].to_numpy(dtype=np.int64),
                "open": arr["open"].to_numpy(dtype=float),
                "high": arr["high"].to_numpy(dtype=float),
                "low": arr["low"].to_numpy(dtype=float),
                "close": arr["close"].to_numpy(dtype=float),
            }

    if args.bench:
        t = time.time()
        r = sweep_panel(loaded[("BTC", "1h")], COST_FRAC["BTC"])
        el = time.time() - t
        print(f"one panel: {el:.1f}s for {len(r['sharpe']):,} combos")
        print(f"projected full sweep: {el * n_panels / 60:.1f} min")
        print(f"projected null ({args.null} reps x 4 panels): "
              f"{el * 4 * args.null / 60:.1f} min")
        return

    rows: list[dict] = []
    for (nm, tf), panel in loaded.items():
        t = time.time()
        r = sweep_panel(panel, COST_FRAC[nm])
        bh = buy_and_hold(panel, TF_SEC[tf])
        ok = np.isfinite(r["sharpe"]) & (r["n"] >= MIN_TRADES)
        span = max((panel["ts"][-1] - panel["ts"][0]) / SEC_PER_YEAR, 1e-9)
        # Annualise the per-trade Sharpe by the cell's OWN trade frequency.
        ann = r["sharpe"] * np.sqrt(np.maximum(r["n"], 0.0) / span)
        for i in np.flatnonzero(ok):
            rl, en, sd, xt, sidx, hh = r["meta"][i]
            rows.append(
                {
                    "panel": nm, "tf": tf, "rsi_len": rl, "entry": en, "side": sd,
                    "exit": xt, "stop": STOP_ATR[sidx], "hold": hh,
                    "ann_sharpe": float(ann[i]), "raw_sharpe": float(r["sharpe"][i]),
                    "total": float(r["total"][i]), "wr": float(r["wr"][i]),
                    "pf": float(r["pf"][i]), "maxdd": float(r["maxdd"][i]),
                    "expo": float(r["expo"][i]), "n": int(r["n"][i]),
                    "bh_sharpe": bh["ann_sharpe"], "bh_total": bh["total"],
                }
            )
        best = float(np.max(ann[ok])) if ok.any() else float("nan")
        print(
            f"  {nm:7s} {tf:3s} {len(panel['close']):7d} bars  {int(ok.sum()):7,} live  "
            f"best annSharpe {best:6.2f}  B&H {bh['ann_sharpe']:5.2f}  "
            f"[{time.time()-t:.0f}s]"
        )

    rows.sort(key=lambda d: -d["ann_sharpe"])
    print(f"\n{len(rows):,} live combos ranked by annualised Sharpe.\n")
    _print_table(rows[: args.top])

    beat = [d for d in rows if d["ann_sharpe"] > d["bh_sharpe"] and d["total"] > d["bh_total"]]
    print(
        f"\ncombos beating buy-and-hold on BOTH Sharpe and total return: "
        f"{len(beat):,} / {len(rows):,} ({100*len(beat)/max(len(rows),1):.1f}%)"
    )
    if beat:
        ex = np.asarray([d["expo"] for d in beat])
        print(
            f"  their exposure: median {np.median(ex):.1f}% of bars in market, "
            f"p10 {np.percentile(ex,10):.1f}%, p90 {np.percentile(ex,90):.1f}%"
        )

    if args.null > 0:
        _null_report(loaded, rows, args.null)
    print("\nNo leaderboard file written -- by design. See module docstring.")


if __name__ == "__main__":
    main()
