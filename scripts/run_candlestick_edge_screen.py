"""Candlestick-MORPHOLOGY edge screen — beta-adjusted, multi-timeframe, FUTURES.

Falsifies a SPECIFIC current-data hypothesis: "these coins are going up — is there a
candlestick pattern, on some sweet-spot timeframe, that is profitably predictive?"

This is the genuinely-untested sliver. The existing `strategy_pattern_search.py` already
tested momentum / mean-reversion / breakout on 1d/4h (→ 0 survivors). It does NOT test
candlestick *morphology* (engulfing / hammer / shooting-star / pin / doji / inside /
marubozu / three-soldiers), and it does NOT test sub-hour timeframes. This does both.

WHY BETA-ADJUSTED (the whole point): in an up-market every long "works" because the
whole complex pumps — that is BTC beta, not a pattern edge. We already proved with
core/btc_alpha.py that the bot's apparent edge was a net-short BETA artifact. So here a
pattern's payoff is measured as the coin's forward return MINUS beta * BTC's forward
return (beta estimated IN-SAMPLE only). A pattern that "works" only because everything
went up scores ~0 here. That is correct and intended.

GATES (a survivor must clear ALL):
  1. positive OOS mean beta-adjusted NET (after round-trip futures cost) return;
  2. OOS mean significant at a BONFERRONI-corrected level for the TOTAL number of
     (coin x timeframe x pattern x horizon) tests actually run (family-wise alpha 0.05);
  3. consistent sign in-sample and out-of-sample;
  4. >= MIN_OOS out-of-sample occurrences.
On a no-edge market the expected survivor count is ~0; that washout IS the recorded
answer. Any survivor is a candidate for ADVERSARIAL re-check, never a green light.

CAVEATS recorded with the result: (a) forward horizons H>1 use overlapping windows, which
inflates t-stats — H>1 survivors are downweighted in interpretation; (b) BTC itself has
beta=1 by construction so its beta-adjusted return is ~0 (it cannot have alpha vs itself);
(c) decisions at bar t use ONLY bars <= t; the forward window [t, t+H] is the prediction
target. No look-ahead. Read-only.

Records reports/candlestick_screen_<date>.md (+ .json). Reads local parquet cache only.
"""

from __future__ import annotations

import json
import math
import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / "data" / "ohlcv_cache"
REPORTS = ROOT / "reports"

COINS = ["BTC", "ETH", "BNB", "SOL", "LINK", "TRX", "SUI", "ATOM", "DOGE", "ALGO", "ZEC"]
NATIVE_TFS = {"15m", "1h", "4h"}  # present in cache
RESAMPLE_TFS = {"30m": ("15m", 2), "45m": ("15m", 3)}  # built from 15m
ALL_TFS = ["15m", "30m", "45m", "1h", "4h"]
HORIZONS = [1, 2, 4]  # forward bars to hold
COST_RT = 10.0 / 1e4  # ~10 bps round-trip (5 bps/side taker, majors)
IS_FRAC = 0.60
MIN_OOS = 30  # min OOS occurrences for a usable t-stat
FWA = 0.05  # family-wise alpha


def _load_native(coin: str, tf: str) -> pd.DataFrame | None:
    p = CACHE / f"{coin}-USDT_{tf}.parquet"
    if not p.exists():
        return None
    df = pd.read_parquet(p)
    need = {"ts", "open", "high", "low", "close"}
    if not need.issubset(df.columns):
        return None
    df = df[["ts", "open", "high", "low", "close"]].dropna()
    df = df.sort_values("ts").drop_duplicates("ts").reset_index(drop=True)
    return df if len(df) > 0 else None


def _resample(df15: pd.DataFrame, minutes: int) -> pd.DataFrame | None:
    if df15 is None or len(df15) == 0:
        return None
    g = df15.copy()
    sec = minutes * 60
    g["bucket"] = (g["ts"] // sec) * sec
    agg = (
        g.groupby("bucket")
        .agg(
            open=("open", "first"),
            high=("high", "max"),
            low=("low", "min"),
            close=("close", "last"),
            n=("ts", "size"),
        )
        .reset_index()
    )
    # keep only fully-formed buckets (drop partial trailing/leading groups)
    full = agg[agg["n"] == (minutes // 15)].copy()
    full = full.rename(columns={"bucket": "ts"})[["ts", "open", "high", "low", "close"]]
    return full.reset_index(drop=True) if len(full) > 0 else None


def _get(coin: str, tf: str, cache: dict) -> pd.DataFrame | None:
    key = (coin, tf)
    if key in cache:
        return cache[key]
    if tf in NATIVE_TFS:
        out = _load_native(coin, tf)
    else:
        src_tf, mult = RESAMPLE_TFS[tf]
        out = _resample(_load_native(coin, src_tf), mult * 15)
    cache[key] = out
    return out


# ---- candlestick morphology -------------------------------------------------
def _patterns(o, h, l, c):
    """Return list of (name, direction, bool_signal_array). Signal at t uses bars <= t."""
    n = len(c)
    body = np.abs(c - o)
    rng = np.maximum(h - l, 1e-12)
    upper = h - np.maximum(o, c)
    lower = np.minimum(o, c) - l
    green = c > o
    red = c < o

    # shifted (previous-bar) views; index 0 has no prev → False
    def prev(a):
        out = np.empty_like(a)
        out[0] = a[0]
        out[1:] = a[:-1]
        return out

    po, ph, pl, pc = prev(o), prev(h), prev(l), prev(c)
    pbody = np.abs(pc - po)
    pgreen, pred = pc > po, pc < po

    sigs = []

    # 1 bullish engulfing
    s = green & pred & (c >= po) & (o <= pc) & (body > pbody)
    s[0] = False
    sigs.append(("bull_engulf", +1, s))
    # 2 bearish engulfing
    s = red & pgreen & (o >= pc) & (c <= po) & (body > pbody)
    s[0] = False
    sigs.append(("bear_engulf", -1, s))
    # 3 hammer (bullish): long lower wick, small upper, body in upper third
    s = (lower >= 2 * body) & (upper <= body) & (body > 0)
    sigs.append(("hammer", +1, s))
    # 4 shooting star (bearish): long upper wick, small lower
    s = (upper >= 2 * body) & (lower <= body) & (body > 0)
    sigs.append(("shooting_star", -1, s))
    # 5 bullish pin bar: lower wick >= 60% of range
    sigs.append(("bull_pin", +1, (lower / rng >= 0.60)))
    # 6 bearish pin bar: upper wick >= 60% of range
    sigs.append(("bear_pin", -1, (upper / rng >= 0.60)))
    # 7 doji-long / 8 doji-short: tiny body relative to range
    doji = body / rng <= 0.10
    sigs.append(("doji_long", +1, doji.copy()))
    sigs.append(("doji_short", -1, doji.copy()))
    # 9 bullish marubozu: dominant green body (momentum)
    sigs.append(("marubozu_bull", +1, (body / rng >= 0.90) & green))
    # 10 bearish marubozu
    sigs.append(("marubozu_bear", -1, (body / rng >= 0.90) & red))
    # 11 inside bar (bullish continuation)
    s = (h <= ph) & (l >= pl)
    s[0] = False
    sigs.append(("inside_bar", +1, s))
    # 12 three white soldiers
    s = np.zeros(n, dtype=bool)
    if n >= 3:
        for i in range(2, n):
            if (
                c[i] > o[i]
                and c[i - 1] > o[i - 1]
                and c[i - 2] > o[i - 2]
                and c[i] > c[i - 1] > c[i - 2]
            ):
                s[i] = True
    sigs.append(("three_soldiers", +1, s))
    # 13 three black crows
    s = np.zeros(n, dtype=bool)
    if n >= 3:
        for i in range(2, n):
            if (
                c[i] < o[i]
                and c[i - 1] < o[i - 1]
                and c[i - 2] < o[i - 2]
                and c[i] < c[i - 1] < c[i - 2]
            ):
                s[i] = True
    sigs.append(("three_crows", -1, s))
    return sigs


def _beta(coin_close: np.ndarray, btc_close: np.ndarray, cut: int) -> float:
    """OLS beta of coin log-returns on BTC log-returns, IN-SAMPLE only."""
    rc = np.diff(np.log(coin_close[:cut]))
    rb = np.diff(np.log(btc_close[:cut]))
    if len(rb) < 30 or np.var(rb) == 0:
        return 1.0
    return float(np.cov(rc, rb)[0, 1] / np.var(rb))


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    cache: dict = {}
    results = []  # (coin, tf, pat, H, dir, oos_n, oos_mean, oos_t, is_mean)
    coverage = []
    n_tests = 0

    for tf in ALL_TFS:
        btc = _get("BTC", tf, cache)
        if btc is None:
            coverage.append(f"BTC {tf}: MISSING (skip whole tf)")
            continue
        for coin in COINS:
            df = _get(coin, tf, cache)
            if df is None or len(df) < 200:
                coverage.append(f"{coin} {tf}: {0 if df is None else len(df)} bars (skip)")
                continue
            # align coin <-> BTC on shared timestamps for beta-neutralisation
            m = df.merge(
                btc[["ts", "close"]].rename(columns={"close": "btc_close"}), on="ts", how="inner"
            )
            if len(m) < 200:
                coverage.append(f"{coin} {tf}: {len(m)} aligned bars (skip)")
                continue
            o = m["open"].to_numpy(dtype=float)
            h = m["high"].to_numpy(dtype=float)
            l = m["low"].to_numpy(dtype=float)
            c = m["close"].to_numpy(dtype=float)
            bc = m["btc_close"].to_numpy(dtype=float)
            n = len(c)
            cut = int(n * IS_FRAC)
            beta = 1.0 if coin == "BTC" else _beta(c, bc, cut)
            coverage.append(f"{coin} {tf}: {n} bars, beta={beta:+.2f}")

            for pat, direction, sig in _patterns(o, h, l, c):
                for H in HORIZONS:
                    n_tests += 1
                    # signal valid where pattern fires and a full forward window exists
                    idx = np.where(sig)[0]
                    idx = idx[(idx + H) < n]
                    if len(idx) == 0:
                        continue
                    coin_fwd = c[idx + H] / c[idx] - 1.0
                    btc_fwd = bc[idx + H] / bc[idx] - 1.0
                    alpha = direction * (coin_fwd - beta * btc_fwd) - COST_RT
                    is_mask = idx < cut
                    oos_mask = ~is_mask
                    oos = alpha[oos_mask]
                    iss = alpha[is_mask]
                    if len(oos) < MIN_OOS or len(iss) < 10:
                        continue
                    sd = oos.std(ddof=1)
                    if sd == 0:
                        continue
                    t = float(oos.mean() / (sd / math.sqrt(len(oos))))
                    results.append(
                        (
                            coin,
                            tf,
                            pat,
                            H,
                            direction,
                            len(oos),
                            float(oos.mean()),
                            t,
                            float(iss.mean()),
                        )
                    )

    # Bonferroni bar over the full family of tests actually run
    k = max(n_tests, 1)
    alpha_bonf = FWA / k
    # two-sided z critical for the corrected alpha (t ~ z at these sample sizes)
    from statistics import NormalDist

    z_crit = NormalDist().inv_cdf(1.0 - alpha_bonf / 2.0)

    survivors = [r for r in results if r[6] > 0 and r[7] >= z_crit and (r[6] > 0) == (r[8] > 0)]
    nearmiss = sorted(
        [r for r in results if r[6] > 0 and abs(r[7]) >= 2.0], key=lambda r: abs(r[7]), reverse=True
    )[:25]

    today = date.today().isoformat()
    REPORTS.mkdir(exist_ok=True)

    L = [f"# Candlestick morphology edge screen — {today}", ""]
    L += [
        '**Hypothesis under test:** "BTC/ETH/BNB/SOL/LINK/TRX/SUI/ATOM/DOGE/ALGO/ZEC are '
        "going up — find a candlestick pattern, on a 15m/30m/45m/1h/4h sweet-spot, that is "
        'profitably predictive (futures)."',
        "",
        "**Method:** classic candlestick morphology (engulfing, hammer, shooting-star, pin, "
        "doji, marubozu, inside-bar, three-soldiers/crows), each with its textbook direction. "
        "Payoff = **beta-adjusted** forward return (coin_fwd − beta·BTC_fwd, beta estimated "
        "in-sample only) **minus** ~10bps round-trip futures cost. Chronological 60/40 IS/OOS. "
        "A survivor must clear ALL of: positive OOS net mean; OOS t past the **Bonferroni** bar "
        f"for all {k} tests run (family-wise α=0.05 → |t|≥{z_crit:.2f}); same sign IS & OOS; "
        f"≥{MIN_OOS} OOS occurrences.",
        "",
        f"**Tests run:** {k} (coin × timeframe × pattern × horizon). "
        f"**Bonferroni |t| bar:** {z_crit:.2f}.",
        "",
        f"## SURVIVORS: {len(survivors)}",
    ]
    if survivors:
        L += [
            "",
            "| coin | tf | pattern | dir | H | OOS n | OOS net% | OOS t | IS net% |",
            "|---|---|---|---|---|---|---|---|---|",
        ]
        for coin, tf, pat, H, d, nn, mn, t, ism in sorted(
            survivors, key=lambda r: abs(r[7]), reverse=True
        ):
            L.append(
                f"| {coin} | {tf} | {pat} | {'L' if d > 0 else 'S'} | {H} | {nn} | "
                f"{mn * 100:+.3f}% | {t:+.2f} | {ism * 100:+.3f}% |"
            )
        L += [
            "",
            "⚠ Any survivor must be ADVERSARIALLY re-checked (overlap for H>1, "
            "regime-dependence, transaction-cost realism) before it is anything but a "
            "hypothesis. Do NOT trade on this alone.",
        ]
    else:
        L += [
            "",
            "**None.** No candlestick pattern, on any tested timeframe, clears the "
            "beta-adjusted after-cost significance bar. On a no-edge market this washout is "
            "the expected and honest result — consistent with every prior screen "
            "(momentum/MR/breakout, funding, OI, ETF-flow, Kronos, seasonality).",
        ]

    L += [
        "",
        f"## Near-misses (uncorrected |t|≥2, positive OOS) — {len(nearmiss)} shown",
        "_With ~%d tests, ≈%.0f false positives at |t|≥2 are EXPECTED by chance. These are "
        "NOT edges; shown only for transparency._" % (k, k * 0.05),
    ]
    if nearmiss:
        L += [
            "",
            "| coin | tf | pattern | dir | H | OOS n | OOS net% | OOS t |",
            "|---|---|---|---|---|---|---|---|",
        ]
        for coin, tf, pat, H, d, nn, mn, t, ism in nearmiss:
            L.append(
                f"| {coin} | {tf} | {pat} | {'L' if d > 0 else 'S'} | {H} | {nn} | "
                f"{mn * 100:+.3f}% | {t:+.2f} |"
            )

    L += ["", "## Coverage"]
    L += ["- " + c for c in coverage]
    L += [
        "",
        "_Caveats: H>1 forward windows overlap (inflates t — downweight H>1); BTC "
        "beta=1 by construction (≈0 alpha vs itself); local parquet cache, no network._",
    ]

    out_md = REPORTS / f"candlestick_screen_{today}.md"
    out_md.write_text("\n".join(L), encoding="utf-8")
    out_json = REPORTS / f"candlestick_screen_{today}.json"
    out_json.write_text(
        json.dumps(
            {
                "date": today,
                "tests_run": k,
                "bonferroni_t_bar": z_crit,
                "survivors": [
                    {
                        "coin": r[0],
                        "tf": r[1],
                        "pattern": r[2],
                        "horizon": r[3],
                        "dir": r[4],
                        "oos_n": r[5],
                        "oos_net_mean": r[6],
                        "oos_t": r[7],
                        "is_net_mean": r[8],
                    }
                    for r in survivors
                ],
                "n_nearmiss": len(nearmiss),
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    print(f"Tests run: {k} | Bonferroni |t| bar: {z_crit:.2f}")
    print(f"SURVIVORS: {len(survivors)}")
    for coin, tf, pat, H, d, nn, mn, t, ism in sorted(
        survivors, key=lambda r: abs(r[7]), reverse=True
    ):
        print(
            f"  SURVIVOR: {coin} {tf} {pat} {'L' if d > 0 else 'S'} H{H} "
            f"OOS t={t:+.2f} net={mn * 100:+.3f}% n={nn}"
        )
    print(f"Near-misses (|t|>=2): {len(nearmiss)}")
    print(f"Report: {out_md}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
