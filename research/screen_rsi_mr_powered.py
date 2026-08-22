"""Screen 90 — RSI mean-reversion, powered pooled screen (8 cells, 6 markets).

Implements _workspace/strategy_pipeline/90_prereg_rsi_mr_powered.md EXACTLY.
Hashed BEFORE outcomes (sha256 0caf2630...7627); the hash is verified before a
single statistic is computed.

CEILING, FIXED IN ADVANCE: the family is REFUTED (refuted-families-ledger:24)
and the reopen bar is NOT met, so this screen CANNOT produce a GO. Its only
permitted output is a MEASUREMENT -- "no edge above X bps", never "no edge".

WHY IT EXISTS: four prior sweeps ran ~1.2M backtests, found zero survivors, and
were not evidence of anything. The fourth measured why -- EURUSD 4h MDE 4.92
bps/bar = oracle Sharpe 4.79 against a >=2.5 fail bar. The instrument could not
see. This pulls both resolvability levers at once: 8 cells instead of 43,680,
and the deepest data the repo can reach (EURUSD to 1971, GOLD to 1975, CRUDE to
1983, SPY to 1993, plus the 28.3k-bar crypto cache).

Read-only; never imported by the bot.

Run: venv/Scripts/python.exe research/screen_rsi_mr_powered.py
"""

from __future__ import annotations

import hashlib
import json
import pathlib
import sys

import numpy as np

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

PREREG = ROOT / "_workspace/strategy_pipeline/91_prereg_rsi_mr_powered_rev2.md"
PREREG_SHA = "5916c04b7bcc235850be51ff88fc729f48e8337008c30b77fbfacb71ba325da7"
OUT_JSON = ROOT / "_workspace/strategy_pipeline/91_screen_rsi_mr_powered.json"
# rev2 (prereg 91) amends ONE thing: the Stage-0 SIZE estimator. Prereg 90's
# t-test-against-zero measured 0.0000 false positives out of 1,600 because the
# surrogate null is centred at t = -2.467 (cost, plus the drift that §6
# deliberately preserves). The screen printed VOID and skipped evaluate(), so
# NO OUTCOME WAS READ and the amendment is legitimate. §4/§6/§7 are unchanged.

# ---------------------------------------------------------------- frozen spec
PANEL_NAMES = ["BTC", "ETH", "SPY", "GOLD", "CRUDE", "EURUSD"]
TIMEFRAMES = ["4h", "1d"]
RSI_LENS = [2, 14]
SIDES = ["long", "short"]
# prereg §3 -- per asset class, at MEDIAN historical price. Round-trip fraction.
COST_FRAC = {
    "BTC": 0.0022,
    "ETH": 0.0022,
    "SPY": 0.0005,
    "GOLD": 0.0004,
    "CRUDE": 0.0005,
    "EURUSD": 0.0002,
}
ENTRY_THR = {2: (10.0, 90.0), 14: (30.0, 70.0)}  # (oversold, overbought)
EXIT_MID = 50.0
STOP_ATR = 3.0
MAX_HOLD = 10
ATR_LEN = 14
SEG_LEN = 20  # prereg §6 -- sign-randomisation segment length
N_TRIALS = 8  # the full cell grid; DSR multiplicity
N_SURROGATE = 200
SEED = 90
SPLIT = 0.70  # OOS = trailing 30% of trades
# Stage-0 bars, pre-committed (prereg §5).
SIZE_BAND = (0.02, 0.08)
POWER_INTERPRETABLE = 1.5
POWER_FAIL = 2.5
CONTROL_MIN_DETECT = 0.60  # prereg 91 §2 positive control; calc predicts ~0.80
# z_{1-a'/2} + z_{0.80} with a' = 0.05/8 (Bonferroni over the 8 cells).
MDE_Z = 2.7344 + 0.8416
SEC_PER_YEAR = 365.25 * 86400.0


def verify_prereg() -> None:
    """Abort before ANY computation if the frozen document moved by one byte.

    Hashes with CRLF normalised to LF: this repo checks out on Windows, so a
    raw-byte hash would break on a fresh clone for a reason that has nothing to
    do with the content being edited. Today both hashes are identical (the file
    is LF on disk); normalising costs no strictness -- any real content change
    still fails.
    """
    actual = hashlib.sha256(PREREG.read_bytes().replace(b"\r\n", b"\n")).hexdigest()
    if actual != PREREG_SHA:
        raise SystemExit(
            f"PREREG HASH MISMATCH\n  expected {PREREG_SHA}\n  actual   {actual}\n"
            "  The frozen hypothesis changed. This run is void."
        )


# ----------------------------------------------------------------- indicators
def _rsi_atr(panel: dict, length: int) -> tuple[np.ndarray, np.ndarray]:
    """Wilder RSI + ATR from the AUDITED canonical implementations.

    Three other ``rsi()`` copies live in this repo and disagree on the
    zero-loss branch (NaN / 50.0 / 100.0). Only ``utils.indicators`` is used
    here, and the parity test pins it against an independent recursion.
    """
    import pandas as pd

    from utils.indicators import atr as _atr
    from utils.indicators import rsi as _rsi

    close = pd.Series(panel["close"])
    r = _rsi(close, length).to_numpy(dtype=float)
    a = _atr(pd.Series(panel["high"]), pd.Series(panel["low"]), close, ATR_LEN).to_numpy(
        dtype=float
    )
    return r, a


# ------------------------------------------------------------------ backtest
def run_cell(panel: dict, rsi_len: int, side: str, cost: float) -> dict:
    """Sequential, non-overlapping trades for one (panel, rsi_len, side).

    NON-OVERLAPPING IS DELIBERATE and is an implementation invariant, not a
    swept parameter: RSI(2) fires on long runs of consecutive bars, and taking
    every signal would count the SAME excursion a dozen times, inflating n and
    destroying the independence the pooled t-test assumes.

    Execution, per prereg §4:
      entry  -> the NEXT bar's open after the signal bar closes.
      stop   -> 3.0 x ATR(14) measured at the SIGNAL bar (known at decision
                time; the entry bar's ATR would be look-ahead).
      fills  -> stop exits fill at min(open, stop) for longs / max(open, stop)
                for shorts, so a gap THROUGH the stop fills at the open and
                never at the unreachable stop price.
      RSI/time exits fill at that bar's close.
      When a bar triggers both the stop and the RSI exit, the STOP wins.
    """
    o, h, low_, c = panel["open"], panel["high"], panel["low"], panel["close"]
    ts = panel["ts"]
    rsi, atr = _rsi_atr(panel, rsi_len)
    atr_frac = np.divide(atr, c, out=np.full_like(c, np.nan), where=c > 0)
    lo_thr, hi_thr = ENTRY_THR[rsi_len]
    is_long = side == "long"
    n = len(c)

    # Warm-up. pandas ewm(adjust=False) SEEDS with the first observation rather
    # than converging from zero, so the opening bars carry an RSI that differs
    # from the converged recursion by up to ~45 points (measured). Those values
    # are indicator artefacts, not signals. Discarding 5 time constants is
    # strictly conservative -- it removes candidate trades, never adds them.
    warmup = 5 * max(rsi_len, ATR_LEN)
    valid = np.isfinite(rsi) & np.isfinite(atr_frac) & (atr_frac > 0)
    valid[:warmup] = False
    trigger = (rsi < lo_thr) if is_long else (rsi > hi_thr)
    signals = np.flatnonzero(valid & trigger)

    out_ts, out_gross, out_cost_u, out_risk, out_bars = [], [], [], [], []
    blocked_until = -1
    for i in signals:
        if i <= blocked_until or i + 1 >= n:
            continue
        entry = o[i + 1]
        if not np.isfinite(entry) or entry <= 0:
            continue
        risk = STOP_ATR * atr_frac[i]  # fraction of price
        stop = entry * (1.0 - risk) if is_long else entry * (1.0 + risk)
        last = min(i + MAX_HOLD, n - 1)
        exit_px, j = c[last], last
        for k in range(i + 1, last + 1):
            if is_long and low_[k] <= stop:
                exit_px, j = min(o[k], stop), k
                break
            if (not is_long) and h[k] >= stop:
                exit_px, j = max(o[k], stop), k
                break
            if np.isfinite(rsi[k]) and (
                (is_long and rsi[k] >= EXIT_MID) or ((not is_long) and rsi[k] <= EXIT_MID)
            ):
                exit_px, j = c[k], k
                break
        if not np.isfinite(exit_px) or exit_px <= 0:
            continue
        # Short P&L is (entry - exit)/ENTRY, i.e. 1 - exit/entry. The tempting
        # `entry/exit - 1` divides the same P&L by the EXIT price: it understates
        # losses and overstates gains, and by Jensen it is biased POSITIVE for
        # shorts even on a martingale. Both errors flatter the short side.
        gross = (exit_px / entry - 1.0) if is_long else (1.0 - exit_px / entry)
        out_ts.append(ts[i + 1])
        out_gross.append(gross)
        out_cost_u.append(cost)
        out_risk.append(atr_frac[i])
        out_bars.append(j - i)
        blocked_until = j

    return {
        "ts": np.asarray(out_ts, dtype=np.int64),
        "gross": np.asarray(out_gross, dtype=float),
        "cost": np.asarray(out_cost_u, dtype=float),
        "risk": np.asarray(out_risk, dtype=float),
        "bars_held": np.asarray(out_bars, dtype=float),
        "nan_rsi_frac": float(np.mean(~np.isfinite(rsi))),
    }


def pool(per_panel: list[dict]) -> dict:
    """Merge panel trade sets into one chronological series in RISK UNITS.

    Crude's daily sigma is 2.47% against EURUSD's 0.60%. Pooling raw returns
    would let crude write the answer. R = net_return / atr_frac_at_entry makes
    the shared 3-ATR stop a common -3R floor across every market.
    """
    keys = ("ts", "gross", "cost", "risk", "bars_held")
    if not any(p["ts"].size for p in per_panel):
        return {k: np.array([], dtype=np.int64 if k == "ts" else float) for k in keys}
    merged = {k: np.concatenate([p[k] for p in per_panel]) for k in keys}
    order = np.argsort(merged["ts"], kind="stable")
    return {k: v[order] for k, v in merged.items()}


def _net_R(p: dict, cost_mult: float = 1.0) -> np.ndarray:
    if p["ts"].size == 0:
        return np.array([], dtype=float)
    return (p["gross"] - cost_mult * p["cost"]) / p["risk"]


# ------------------------------------------------------------- null surrogate
def surrogate(panel: dict, rng: np.random.Generator) -> dict:
    """Per-segment sign randomisation with EXACT re-centering (prereg §6).

    The design that passed the positive control (80% detection vs 7.5% on
    edge-free twins, FPR 0.0475). Runs 1-3 used a block bootstrap that retained
    67.2% of a planted signal and inflated the benchmark 56% -- its null
    contained the alternative.

    Decompose each bar into an inter-bar gap and three intrabar log offsets
    from the open, flip whole segments, and REFLECT the intrabar geometry when
    a segment flips (high and low swap about the open). Without the reflection
    a flipped down-bar keeps a down-bar's excursion and every stop test is
    biased. Re-centering is applied to the gap term so the surrogate's mean
    bar-to-bar log return equals the original's exactly.
    """
    o, h, low_, c = panel["open"], panel["high"], panel["low"], panel["close"]
    n = len(c)
    if n < 3:
        return panel
    up = np.log(h / o)  # >= 0
    dn = np.log(low_ / o)  # <= 0
    body = np.log(c / o)
    gap = np.empty(n)
    gap[0] = 0.0
    gap[1:] = np.log(o[1:] / c[:-1])

    n_seg = int(np.ceil(n / SEG_LEN))
    flip = rng.integers(0, 2, size=n_seg).astype(bool).repeat(SEG_LEN)[:n]
    s = np.where(flip, -1.0, 1.0)

    gap_s, body_s = s * gap, s * body
    up_s = np.where(flip, -dn, up)
    dn_s = np.where(flip, -up, dn)

    # Exact re-centering. c[t] = c[t-1]*exp(gap[t] + body[t]), so the bar-to-bar
    # log return is exactly gap+body -- there is no lagged-body term. Shifting
    # the gap by the mean difference makes the surrogate's mean log return equal
    # the original's to floating-point exactness (asserted below).
    r_orig = np.log(c[1:] / c[:-1])
    r_new = gap_s[1:] + body_s[1:]
    gap_s[1:] += float(r_orig.mean() - r_new.mean())

    # Cumulate in log space: log c[t] = log c[0] + sum_{k<=t}(gap+body).
    logc = np.log(o[0]) + np.cumsum(gap_s + body_s)
    close_s = np.exp(logc)
    open_s = np.empty(n)
    open_s[0] = o[0]
    open_s[1:] = close_s[:-1] * np.exp(gap_s[1:])
    # A silently-miscalibrated null is what wasted four prior runs. Assert it.
    drift = abs(float(np.diff(np.log(close_s)).mean() - r_orig.mean()))
    if drift > 1e-12:
        raise AssertionError(f"surrogate drift {drift:.3e} -- null is not re-centred")
    return {
        "ts": panel["ts"],
        "open": open_s,
        "high": open_s * np.exp(up_s),
        "low": open_s * np.exp(dn_s),
        "close": close_s,
    }


# --------------------------------------------------------------- statistics
def _metrics(R: np.ndarray) -> dict:
    if R.size == 0:
        return {"n": 0, "mean_R": 0.0, "wr": 0.0, "pf": 0.0, "sharpe": 0.0, "maxdd_R": 0.0}
    eq = np.cumsum(R)
    dd = float(np.max(np.maximum.accumulate(eq) - eq))
    wins, losses = R[R > 0].sum(), -R[R < 0].sum()
    sd = float(R.std(ddof=1)) if R.size > 1 else 0.0
    return {
        "n": int(R.size),
        "mean_R": float(R.mean()),
        "wr": float((R > 0).mean()),
        "pf": float(wins / losses) if losses > 0 else float("inf"),
        "sharpe": float(R.mean() / sd) if sd > 0 else 0.0,
        "maxdd_R": dd,
    }


def _t_pvalue(R: np.ndarray) -> float:
    """One-sided t-test p-value for mean(R) > 0."""
    from scipy import stats

    if R.size < 3 or R.std(ddof=1) == 0:
        return 1.0
    t = R.mean() / (R.std(ddof=1) / np.sqrt(R.size))
    return float(1.0 - stats.t.cdf(t, df=R.size - 1))


def _dsr_prob(arr: np.ndarray, n_trials: int) -> float:
    from core.promotion_gate import _kurt, _skew
    from core.stat_tests import deflated_sharpe, sharpe

    if arr.size < 4:
        return float("nan")
    return float(
        deflated_sharpe(
            sr_observed=sharpe(arr),
            n_trials=int(n_trials),
            n_obs=int(arr.size),
            skew=float(_skew(arr)),
            kurt=float(_kurt(arr)),
            sr_var=1.0 / max(2, arr.size),
        )
    )


def _cscv_matrix(cell_series: dict, n_buckets: int = 64) -> np.ndarray:
    """(T, 8) CSCV matrix over the FULL grid -- winners AND abandoned cells.

    ``core.stat_tests.trial_pnl_matrix`` documents the invariant this honours:
    PBO only measures selection overfitting when its columns are every
    candidate that competed in selection. Its own signature takes classifier
    trials (``{row: p_win}``), which a parameter-cell sweep has no analogue
    for, so the matrix is built directly -- same contract, same full grid.
    Cells are bucketed on a COMMON calendar clock; a cell with no trade in a
    bucket contributes 0.0 there, which is its actual PnL for that period.
    """
    names = sorted(cell_series)
    present = [cell_series[k]["ts"] for k in names if cell_series[k]["ts"].size]
    if not present:
        return np.zeros((n_buckets, len(names)))
    all_ts = np.concatenate(present)
    edges = np.linspace(all_ts.min(), all_ts.max() + 1, n_buckets + 1)
    mat = np.zeros((n_buckets, len(names)))
    for col, k in enumerate(names):
        p = cell_series[k]
        if p["ts"].size == 0:
            continue
        idx = np.clip(np.searchsorted(edges, p["ts"], side="right") - 1, 0, n_buckets - 1)
        np.add.at(mat[:, col], idx, _net_R(p))
    return mat


# -------------------------------------------------------------------- driver
def _load_all() -> dict:
    from research._ohlcv_cache import load_panel

    panels = {}
    for name in PANEL_NAMES:
        for tf in TIMEFRAMES:
            df = load_panel(name, tf)
            panels[(name, tf)] = {
                "ts": df["ts"].to_numpy(dtype=np.int64),
                "open": df["open"].to_numpy(dtype=float),
                "high": df["high"].to_numpy(dtype=float),
                "low": df["low"].to_numpy(dtype=float),
                "close": df["close"].to_numpy(dtype=float),
            }
    return panels


def _run_grid(panels: dict) -> dict:
    """All 8 cells over all 6 panels -> {cell_key: pooled series}."""
    out = {}
    for rsi_len in RSI_LENS:
        for side in SIDES:
            for tf in TIMEFRAMES:
                per = [
                    run_cell(panels[(nm, tf)], rsi_len, side, COST_FRAC[nm]) for nm in PANEL_NAMES
                ]
                pooled = pool(per)
                pooled["nan_rsi_frac"] = float(np.mean([p["nan_rsi_frac"] for p in per]))
                out[f"rsi{rsi_len}_{side}_{tf}"] = pooled
    return out


def stage0(panels: dict, rng: np.random.Generator) -> tuple[dict, list[dict]]:
    """SIZE + POWER from SURROGATES ONLY -- computed and read BEFORE outcomes.

    Deliberately blind: every quantity here (false-positive rate, sigma_R,
    trade count, trades/year) comes from edge-free surrogate universes, so
    nothing about the real result can leak into the decision to read it.
    """
    reps = []
    for rep in range(N_SURROGATE):
        sur = {k: surrogate(v, rng) for k, v in panels.items()}
        reps.append(_run_grid(sur))
        if (rep + 1) % 25 == 0:
            print(f"    surrogate {rep + 1}/{N_SURROGATE}")

    # ---- POWER: dispersion and sample size, from edge-free universes only ----
    sig, ns, spans = [], [], []
    for grid in reps:
        for p in grid.values():
            R = _net_R(p)
            if R.size > 2:
                sig.append(float(R.std(ddof=1)))
                ns.append(int(R.size))
                spans.append(float(p["ts"].max() - p["ts"].min()) / SEC_PER_YEAR)
    sigma_R = float(np.median(sig)) if sig else float("nan")
    n_med = float(np.median(ns)) if ns else 0.0
    years = float(np.median(spans)) if spans else 1.0
    mde_R = MDE_Z * sigma_R / np.sqrt(n_med) if n_med > 0 else float("inf")
    per_year = n_med / years if years > 0 else 0.0
    oracle_sharpe = (mde_R / sigma_R) * np.sqrt(per_year) if sigma_R > 0 else float("inf")

    # ---- SIZE (prereg 91 §1): the SURROGATE-REFERENCED percentile test ----
    # This is the statistic §7 actually decides on. Estimated leave-one-out:
    # each rep is scored against the other K-1. It returns ~0.05 BY
    # CONSTRUCTION -- a plumbing assertion, not evidence. The evidence is the
    # positive control below.
    #
    # ---- POSITIVE CONTROL (prereg 91 §2): the check that carries weight ----
    # Plant exactly MDE_R risk units of edge into each rep and re-test. If the
    # detection rate is below 0.60, the MDE is optimistic and the POWER pass
    # was not real. This is what prior rounds got wrong.
    fired = planted_hits = tot = 0
    for key in sorted(reps[0]):
        means = np.array(
            [float(_net_R(g[key]).mean()) if _net_R(g[key]).size > 2 else np.nan for g in reps]
        )
        good = np.flatnonzero(np.isfinite(means))
        for i in good:
            ref = np.delete(means[good], np.searchsorted(good, i))
            if ref.size < 20:
                continue
            thr = float(np.percentile(ref, 95))
            tot += 1
            fired += int(means[i] > thr)
            planted_hits += int(means[i] + mde_R > thr)
    size_fpr = fired / tot if tot else float("nan")
    power_detect = planted_hits / tot if tot else float("nan")

    verdict = "PASS"
    if not (SIZE_BAND[0] <= size_fpr <= SIZE_BAND[1]):
        verdict = "VOID"
    elif power_detect < CONTROL_MIN_DETECT:
        verdict = "VOID"
    elif oracle_sharpe >= POWER_FAIL:
        verdict = "UNDERPOWERED"
    elif oracle_sharpe > POWER_INTERPRETABLE:
        verdict = "BORDERLINE"
    return {
        "size_fpr": size_fpr,
        "size_band": list(SIZE_BAND),
        "size_tests": tot,
        "positive_control_detect": power_detect,
        "positive_control_bar": CONTROL_MIN_DETECT,
        "sigma_R": sigma_R,
        "median_n_trades": n_med,
        "median_span_years": years,
        "trades_per_year": per_year,
        "mde_R": mde_R,
        "oracle_sharpe": oracle_sharpe,
        "bar_interpretable": POWER_INTERPRETABLE,
        "bar_fail": POWER_FAIL,
        # Honest multiplicity note: the 95th-percentile bar is PER CELL. Across
        # 8 cells the family-wise false-positive rate is 1 - 0.95^8 = 0.337.
        # DSR (n_trials=8) and PBO in §7 are the multiplicity controls layered
        # on top of it; null_pctile alone is not one.
        "familywise_fp_at_95": float(1.0 - 0.95 ** (len(RSI_LENS) * len(SIDES) * len(TIMEFRAMES))),
        "verdict": verdict,
    }, reps


def evaluate(real: dict, surrogate_reps: list[dict], stage: dict) -> dict:
    from core.decision.monte_carlo import monte_carlo_trade_sequence
    from core.promotion_gate import MAX_PBO, MIN_DSR, MIN_OOS_WR
    from core.stat_tests import pbo as _pbo

    try:
        pbo_val = float(_pbo(_cscv_matrix(real), n_partitions=8))
    except Exception as exc:  # noqa: BLE001 -- report, never silently zero
        pbo_val = float("nan")
        print(f"  PBO unavailable: {type(exc).__name__}: {exc}")

    cells = []
    for key in sorted(real):
        p = real[key]
        R = _net_R(p)
        m = _metrics(R)
        null_means = [
            float(_net_R(g[key]).mean()) for g in surrogate_reps if _net_R(g[key]).size > 2
        ]
        pct = (
            float((np.asarray(null_means) < m["mean_R"]).mean() * 100.0)
            if null_means
            else float("nan")
        )
        oos = R[int(SPLIT * R.size) :]
        gross_R = p["gross"] / p["risk"] if R.size else np.array([])
        cost_R = p["cost"] / p["risk"] if R.size else np.array([])
        be_mult = (
            float(gross_R.mean() / cost_R.mean())
            if R.size and cost_R.mean() > 0
            else float("nan")
        )
        mc = monte_carlo_trade_sequence(R, seed=SEED) if R.size >= 3 else None
        rsi_tag, side, tf = key.split("_")
        cd = {
            "cell": key,
            "rsi_len": int(rsi_tag[3:]),
            "side": side,
            "timeframe": tf,
            **m,
            "mean_bps": float((p["gross"] - p["cost"]).mean() * 1e4) if R.size else 0.0,
            "mean_R_2x_cost": float(_net_R(p, 2.0).mean()) if R.size else 0.0,
            "breakeven_cost_mult": be_mult,
            "avg_bars_held": float(p["bars_held"].mean()) if R.size else 0.0,
            "oos_wr": float((oos > 0).mean()) if oos.size else 0.0,
            "null_pctile": pct,
            "p_value": _t_pvalue(R),
            "dsr": _dsr_prob(R, N_TRIALS),
            "mc_p_positive": float(mc.p_total_positive) if mc else 0.0,
            "mc_maxdd_p95": float(mc.max_drawdown_p95) if mc else 1.0,
            "nan_rsi_frac": float(p.get("nan_rsi_frac", 0.0)),
        }
        cd["clears"] = bool(
            cd["mean_R"] > 0
            and cd["null_pctile"] >= 95.0
            and cd["oos_wr"] >= MIN_OOS_WR
            and np.isfinite(cd["dsr"])
            and cd["dsr"] >= MIN_DSR
            and (not np.isfinite(pbo_val) or pbo_val <= MAX_PBO)
            and cd["mc_p_positive"] >= 0.95
            and cd["mc_maxdd_p95"] <= 0.25
        )
        cells.append(cd)

    # MDE in bps: convert the risk-unit MDE with the observed median R->bps
    # scale (median atr_frac across cells), so the verdict carries a number a
    # human can compare against a spread.
    scales = [c["mean_bps"] / c["mean_R"] for c in cells if c["mean_R"]]
    mde_bps = stage["mde_R"] * abs(float(np.median(scales))) if scales else float("nan")
    any_clear = any(c["clears"] for c in cells)
    return {
        "cells": sorted(cells, key=lambda d: -d["mean_R"]),
        "pbo": pbo_val,
        "verdict": "MEASURED_POSITIVE" if any_clear else "NO_EDGE_ABOVE_X_BPS",
        "mde_bps": mde_bps,
        # Ledger STOP: refuted family, reopen bar unmet. No GO is reachable.
        "promotion": "NONE",
        "live_trade_authorized": False,
    }


def main() -> None:
    verify_prereg()
    print(f"prereg verified sha256={PREREG_SHA[:16]}...")
    panels = _load_all()
    for (nm, tf), p in panels.items():
        print(f"  loaded {nm:7s} {tf:3s} {len(p['close']):7d} bars")

    rng = np.random.default_rng(SEED)
    print(f"\nSTAGE 0 ({N_SURROGATE} surrogate universes, outcome-blind) ...")
    stage, reps = stage0(panels, rng)
    print(json.dumps(stage, indent=2, default=float))

    payload = {
        "prereg_sha256": PREREG_SHA,
        "stage0": stage,
        "n_surrogate": N_SURROGATE,
        "cost_frac": COST_FRAC,
        "family": "RSI mean-reversion (REFUTED, ledger:24)",
        "live_trade_authorized": False,
        "promotion": "NONE",
    }
    if stage["verdict"] in ("VOID", "UNDERPOWERED"):
        if stage["verdict"] == "UNDERPOWERED":
            reason = "stage0 power: oracle Sharpe >= 2.5"
        elif not (SIZE_BAND[0] <= stage["size_fpr"] <= SIZE_BAND[1]):
            reason = f"stage0 SIZE {stage['size_fpr']:.4f} outside {list(SIZE_BAND)}"
        else:
            reason = (
                f"stage0 POSITIVE CONTROL {stage['positive_control_detect']:.3f} "
                f"< {CONTROL_MIN_DETECT} -- the MDE is optimistic"
            )
        payload["verdict"] = stage["verdict"]
        payload["reason"] = reason
        print(f"\n{stage['verdict']}: outcome NOT read, per prereg §5.")
    else:
        print(f"\nStage 0 {stage['verdict']} -- reading outcomes.\n")
        payload.update(evaluate(_run_grid(panels), reps, stage))

    OUT_JSON.write_text(json.dumps(payload, indent=1, default=float), encoding="utf-8")
    print(json.dumps({k: v for k, v in payload.items() if k != "stage0"}, indent=1, default=float))
    print(f"\nwrote {OUT_JSON}")


if __name__ == "__main__":
    main()
