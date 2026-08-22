"""Does ANY variant perform consistently, year after year, for decades?

The owner's criterion, verbatim: strategies "that has given consecutively
performed well through multiple years/months". That is a PERSISTENCE test, and
it is a different -- and much harder -- question than the ranked sweep already
run (research/sweep_rsi_mr_rank.py). A single-period Sharpe can be luck; being
positive in 25 of 30 separate calendar years cannot be, unless noise does it
just as often.

Which is exactly what this measures. The decision statistic is NOT "the best
survivor's numbers". It is:

    how many combos clear the persistence bar on REAL data
    vs. how many clear it on SIGN-RANDOMISED NOISE, same grid, same pipeline

If those counts are close, year-consistency is something the search produces by
chance and the criterion adds nothing. That comparison needs no interpretation.

PRE-COMMITTED BEFORE ANY COMPUTATION (so this cannot become a sweep over K):
  * panels    : the 4 deep tradfi daily series only -- EURUSD to 1971, GOLD to
                1975, CRUDE to 1983, SPY to 1993. Crypto is excluded: 3.2 years
                cannot answer a multi-decade persistence question.
  * qualifying year : the combo took >= MIN_TRADES_PER_YEAR trades in it
  * eligible combo  : >= MIN_YEARS qualifying years
  * THE BAR         : positive net return in >= 70% of qualifying years
  * verdict         : if real survivors <= 1.5x noise survivors, persistence is
                      passed by chance.
The full 50->90% curve is reported for transparency, but the VERDICT is decided
at 70% and that threshold does not move after the numbers are seen.

THE TRAP THIS MUST NOT FALL INTO: a long-biased, high-exposure combo on SPY or
GOLD will look beautifully year-consistent because the asset rose for 30 years.
That is beta, not edge. Measured 2026-08-22 on SPY 1d: cross-era survival at
114.5% exposure and null percentile 0. So every survivor is reported with its
exposure, and against the SAME per-year test applied to buy-and-hold.

Read-only; never imported by the bot.

Run: venv/Scripts/python.exe research/persistence_test.py [--null N]
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

from research.sweep_rsi_mr_rank import (  # noqa: E402
    ATR_LEN,
    COST_FRAC,
    ENTRY_THR,
    EXIT_THR,
    MAX_HOLD,
    MIN_TRADES,
    RSI_LENS,
    SIDES,
    STOP_ATR,
    W,
    _indicators,
)

PANELS = ["SPY", "GOLD", "CRUDE", "EURUSD"]  # deep daily only
TIMEFRAME = "1d"
MIN_TRADES_PER_YEAR = 10
MIN_YEARS = 15
BAR = 0.70  # pre-committed
CURVE = (0.50, 0.60, 0.70, 0.80, 0.90)
NOISE_MULTIPLE = 1.5  # real must exceed noise by this to mean anything
SEC_PER_YEAR = 365.25 * 86400.0


def year_index(ts: np.ndarray) -> tuple[np.ndarray, int]:
    """Map epoch SECONDS -> dense 0-based calendar-year index."""
    yrs = (ts / SEC_PER_YEAR + 1970.0).astype(np.int64)
    lo = int(yrs.min())
    return (yrs - lo).astype(np.int64), int(yrs.max() - lo + 1)


def persistence_for_panel(panel: dict, cost: float) -> dict:
    """Per-year positive-rate for every combo in the grid, for one panel.

    Execution semantics are identical to sweep_rsi_mr_rank: entry at the NEXT
    bar's open, stop from the SIGNAL bar's ATR, a gap THROUGH the stop fills at
    the open, and short P&L is 1 - exit/entry (the corrected form).
    """
    o, h, low_, c = panel["open"], panel["high"], panel["low"], panel["close"]
    ts = panel["ts"]
    n = len(c)
    yidx_all, n_years = year_index(ts)

    pos_frac: list[float] = []
    n_qual: list[int] = []
    expo: list[float] = []
    ntr: list[int] = []
    meta: list[tuple] = []

    for rsi_len in RSI_LENS:
        rsi, atr = _indicators(panel, rsi_len)
        atr_frac = np.divide(atr, c, out=np.full_like(c, np.nan), where=c > 0)
        warmup = 5 * max(rsi_len, ATR_LEN)
        base_ok = np.isfinite(rsi) & np.isfinite(atr_frac) & (atr_frac > 0)
        base_ok[:warmup] = False
        base_ok[n - 2 :] = False
        for entry in ENTRY_THR:
            for side in SIDES:
                is_long = side == "long"
                thr = entry if is_long else 100.0 - entry
                sig = np.flatnonzero(base_ok & ((rsi < thr) if is_long else (rsi > thr)))
                if sig.size < MIN_TRADES:
                    continue
                fwd = sig[:, None] + 1 + np.arange(W)[None, :]
                live = fwd <= (n - 1)
                fwd = np.clip(fwd, 0, n - 1)
                oW, hW, lW, cW, rW = o[fwd], h[fwd], low_[fwd], c[fwd], rsi[fwd]
                last = live.sum(axis=1) - 1
                entry_px = o[sig + 1]
                risk = atr_frac[sig]
                yi = yidx_all[sig + 1]
                BIG = W + 10

                def _first(mask, _live=live, _big=BIG):
                    # Bind both loop variables as defaults: a closure that reads
                    # them from the enclosing scope would silently pick up the
                    # LAST iteration's values if this were ever deferred (B023).
                    mask = mask & _live
                    return np.where(mask.any(axis=1), mask.argmax(axis=1), _big)

                stop_pos = np.empty((len(STOP_ATR), sig.size), dtype=np.int64)
                stop_px = np.empty((len(STOP_ATR), sig.size), dtype=float)
                for si, mult in enumerate(STOP_ATR):
                    if not np.isfinite(mult):
                        stop_pos[si] = BIG
                        stop_px[si] = np.nan
                        continue
                    sp = (
                        entry_px * (1.0 - mult * risk)
                        if is_long
                        else entry_px * (1.0 + mult * risk)
                    )
                    stop_px[si] = sp
                    stop_pos[si] = _first(lW <= sp[:, None] if is_long else hW >= sp[:, None])
                exit_pos = np.empty((len(EXIT_THR), sig.size), dtype=np.int64)
                for xi, x in enumerate(EXIT_THR):
                    lvl = x if is_long else 100.0 - x
                    m = (rW >= lvl) if is_long else (rW <= lvl)
                    exit_pos[xi] = _first(m & np.isfinite(rW))

                hold_pos = np.minimum(np.asarray(MAX_HOLD) - 1, W - 1)
                rows = np.arange(sig.size)
                cnt_year = np.bincount(yi, minlength=n_years)
                qual_mask = cnt_year >= MIN_TRADES_PER_YEAR
                nq = int(qual_mask.sum())
                if nq < MIN_YEARS:
                    continue

                for xi in range(len(EXIT_THR)):
                    for si in range(len(STOP_ATR)):
                        for hi, hp in enumerate(hold_pos):
                            pos = np.minimum(np.minimum(exit_pos[xi], stop_pos[si]), hp)
                            pos = np.minimum(pos, last)
                            fired = (stop_pos[si] <= pos) & (stop_pos[si] < BIG)
                            px = cW[rows, pos]
                            if np.isfinite(STOP_ATR[si]):
                                sp = stop_px[si]
                                op = oW[rows, pos]
                                gap = np.minimum(op, sp) if is_long else np.maximum(op, sp)
                                px = np.where(fired, gap, px)
                            gross = (
                                (px / entry_px - 1.0) if is_long else (1.0 - px / entry_px)
                            )
                            r = gross - cost
                            per_year = np.bincount(yi, weights=r, minlength=n_years)
                            pos_years = int((per_year[qual_mask] > 0).sum())
                            pos_frac.append(pos_years / nq)
                            n_qual.append(nq)
                            expo.append(float((pos + 1).sum()) / n * 100.0)
                            ntr.append(int(r.size))
                            meta.append((rsi_len, entry, side, EXIT_THR[xi], si, MAX_HOLD[hi]))
    return {
        "pos_frac": np.asarray(pos_frac),
        "n_qual": np.asarray(n_qual),
        "expo": np.asarray(expo),
        "n": np.asarray(ntr),
        "meta": meta,
    }


def buy_and_hold_persistence(panel: dict) -> tuple[int, int]:
    """The same per-year test applied to simply holding the asset."""
    ts, c = panel["ts"], panel["close"]
    yidx, n_years = year_index(ts)
    r = np.zeros(len(c))
    r[1:] = np.diff(np.log(c))
    per_year = np.bincount(yidx, weights=r, minlength=n_years)
    cnt = np.bincount(yidx, minlength=n_years)
    q = cnt >= MIN_TRADES_PER_YEAR
    return int((per_year[q] > 0).sum()), int(q.sum())


def survivors_at(res: dict, bar: float) -> int:
    if res["pos_frac"].size == 0:
        return 0
    return int(((res["pos_frac"] >= bar) & (res["n_qual"] >= MIN_YEARS)).sum())


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--null", type=int, default=10, help="surrogate universes")
    args = ap.parse_args()

    from research._ohlcv_cache import load_panel
    from research.screen_rsi_mr_powered import surrogate

    print("PERSISTENCE TEST -- does anything work year after year, for decades?")
    print(f"bar (pre-committed): positive net in >= {BAR:.0%} of qualifying years")
    print(f"qualifying year: >= {MIN_TRADES_PER_YEAR} trades; eligible: >= {MIN_YEARS} years")
    print(f"verdict rule: real survivors must exceed {NOISE_MULTIPLE}x noise survivors\n")

    loaded = {}
    for nm in PANELS:
        d = load_panel(nm, TIMEFRAME)
        loaded[nm] = {
            "ts": d["ts"].to_numpy(dtype=np.int64),
            "open": d["open"].to_numpy(dtype=float),
            "high": d["high"].to_numpy(dtype=float),
            "low": d["low"].to_numpy(dtype=float),
            "close": d["close"].to_numpy(dtype=float),
        }
        bh_pos, bh_q = buy_and_hold_persistence(loaded[nm])
        span = (loaded[nm]["ts"][-1] - loaded[nm]["ts"][0]) / SEC_PER_YEAR
        print(
            f"  {nm:7s} {len(d):6d} bars  {span:5.1f}y   "
            f"buy-and-hold positive in {bh_pos}/{bh_q} years ({bh_pos/max(bh_q,1):.0%})"
        )

    print("\n--- REAL DATA ---")
    real_tot = {b: 0 for b in CURVE}
    grand = 0
    best = []
    for nm in PANELS:
        t = time.time()
        res = persistence_for_panel(loaded[nm], COST_FRAC[nm])
        grand += len(res["pos_frac"])
        line = []
        for b in CURVE:
            s = survivors_at(res, b)
            real_tot[b] += s
            line.append(f"{b:.0%}:{s}")
        print(
            f"  {nm:7s} {len(res['pos_frac']):7,} eligible  "
            + "  ".join(line)
            + f"   [{time.time()-t:.0f}s]"
        )
        if res["pos_frac"].size:
            m = (res["pos_frac"] >= BAR) & (res["n_qual"] >= MIN_YEARS)
            for i in np.flatnonzero(m):
                best.append(
                    (res["pos_frac"][i], nm, res["meta"][i], res["expo"][i],
                     res["n"][i], res["n_qual"][i])
                )

    print(f"\n  TOTAL eligible combos: {grand:,}")
    print("  survivors by bar: " + "  ".join(f"{b:.0%}={real_tot[b]}" for b in CURVE))

    if args.null > 0:
        print(f"\n--- NOISE ({args.null} sign-randomised surrogates, identical pipeline) ---")
        rng = np.random.default_rng(2026)
        null_tot = {b: [] for b in CURVE}
        for k in range(args.null):
            tot = {b: 0 for b in CURVE}
            for nm in PANELS:
                sur = surrogate(loaded[nm], rng)
                sur["ts"] = loaded[nm]["ts"]
                res = persistence_for_panel(sur, COST_FRAC[nm])
                for b in CURVE:
                    tot[b] += survivors_at(res, b)
            for b in CURVE:
                null_tot[b].append(tot[b])
            print(
                f"    surrogate {k+1}/{args.null}: "
                + "  ".join(f"{b:.0%}={tot[b]}" for b in CURVE)
            )
        print("\n  noise survivors (mean / max) vs real:")
        for b in CURVE:
            a = np.asarray(null_tot[b])
            print(f"    {b:.0%}  mean={a.mean():8.1f}  max={a.max():6d}   real={real_tot[b]}")
        nm_ = float(np.asarray(null_tot[BAR]).mean())
        print()
        print(f"  VERDICT at the pre-committed {BAR:.0%} bar:")
        print(
            f"    real={real_tot[BAR]}   noise mean={nm_:.1f}   "
            f"ratio={real_tot[BAR]/max(nm_,1e-9):.2f}x"
        )
        print(
            "    -> PERSISTENCE IS PASSED BY CHANCE"
            if real_tot[BAR] <= NOISE_MULTIPLE * nm_
            else "    -> real exceeds noise; inspect survivors below"
        )

    if best:
        best.sort(key=lambda x: -x[0])
        print(f"\n--- top survivors at the {BAR:.0%} bar (exposure exposes the beta trap) ---")
        print(
            f"  {'panel':8s}{'rsi':>4}{'ent':>4}{'side':>6}{'exit':>5}{'stop':>6}"
            f"{'hold':>5}{'yrs+':>8}{'expo%':>8}{'n':>7}"
        )
        for pf, nm, mt, ex, ntr, nq in best[:20]:
            rl, en, sd, xt, si, hh = mt
            st = "none" if not np.isfinite(STOP_ATR[si]) else f"{STOP_ATR[si]:.1f}"
            print(
                f"  {nm:8s}{rl:>4}{en:>4}{sd:>6}{xt:>5}{st:>6}{hh:>5}"
                f"{pf:>6.0%}/{nq:<3d}{ex:>8.1f}{ntr:>7}"
            )
    else:
        print(f"\n  NO combo cleared the {BAR:.0%} bar on real data.")


if __name__ == "__main__":
    main()
