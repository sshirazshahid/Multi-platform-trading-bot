"""Rigorous per-pair time-series STRATEGY/PATTERN sweep — go-live falsification.

Tests a battery of classic per-pair strategies on liquid majors and asks: does ANY
(pair, strategy, param, timeframe) combination produce a profitable, repeatable edge
that would justify trading it LIVE? Built to FALSIFY, not to flatter.

Strategies: time-series momentum (5/10/20/40/60), short-term mean-reversion (1/3/5),
breakout (10/20). Long/short (mom, mr) or long/flat (breakout). Decisions at bar t use
ONLY data through t-1 (no look-ahead). Returns are net of a per-side cost on turnover.

GATES (a "survivor" must clear ALL):
  1. positive OOS net mean (after cost);
  2. OOS mean statistically significant at a BONFERRONI-corrected level for the TOTAL
     number of (pair x strategy x param x timeframe) tests run (family-wise alpha 0.05);
  3. consistent sign in-sample and out-of-sample.
On a no-edge market the expected survivor count is ~0; that washout is the honest,
recorded go-live answer. Any survivor is a candidate to be ADVERSARIALLY re-checked
(see the workflow audit), never an automatic green light.

Records reports/strategy_search_<date>.md. Read-only.
"""
from __future__ import annotations

import math
import sys
import time
from datetime import date, datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.feature_store import load_ohlcv_window  # noqa: E402

PAIRS = ["BTC", "ETH", "SOL", "BNB", "XRP", "ADA", "AVAX", "LINK", "DOT", "LTC", "ATOM", "DOGE"]
COST_ONEWAY = 5.0 / 1e4          # ~5 bps per side (liquid majors, taker)
IS_FRAC = 0.60
MIN_OOS = 50                     # min OOS observations for a usable t-stat


def _signals(closes):
    """Yield (name, position_array) for each strategy. pos[t] uses data <= t-1."""
    import numpy as np
    r = np.diff(np.log(closes))           # r[i] = return over bar i+1
    n = len(r)
    out = []

    def _mom(look):
        pos = np.zeros(n)
        for t in range(look, n):
            pos[t] = np.sign(np.sum(r[t - look:t]))   # trailing return through t-1
        return pos

    def _mr(look):
        pos = np.zeros(n)
        for t in range(look, n):
            pos[t] = -np.sign(np.sum(r[t - look:t]))
        return pos

    def _breakout(look):
        pos = np.zeros(n)
        for t in range(look + 1, n):
            window = closes[t - look:t]                # closes through t-1 (closes index aligns to r[t-1])
            pos[t] = 1.0 if closes[t] >= window.max() else 0.0
        return pos

    for L in (5, 10, 20, 40, 60):
        out.append((f"mom{L}", _mom(L)))
    for L in (1, 3, 5):
        out.append((f"mr{L}", _mr(L)))
    for L in (10, 20):
        out.append((f"brk{L}", _breakout(L)))
    return r, out


def _eval(r, pos, warm):
    """Net (after-cost) IS/OOS stats. Returns dict or None."""
    import numpy as np
    turn = np.abs(np.diff(np.concatenate([[0.0], pos])))
    net = pos * r - turn * COST_ONEWAY
    seg = net[warm:]
    if len(seg) < 100:
        return None
    cut = int(len(seg) * IS_FRAC)
    is_s, oos_s = seg[:cut], seg[cut:]
    if len(oos_s) < MIN_OOS or oos_s.std() == 0 or is_s.std() == 0:
        return None
    oos_t = oos_s.mean() / (oos_s.std() / math.sqrt(len(oos_s)))
    return {
        "is_mean": float(is_s.mean()), "oos_mean": float(oos_s.mean()),
        "oos_t": float(oos_t), "oos_n": len(oos_s),
        "is_sign": np.sign(is_s.mean()), "oos_sign": np.sign(oos_s.mean()),
    }


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    import ccxt
    import numpy as np
    print("Connecting to Binance (public OHLCV)...")
    ex = ccxt.binance({"enableRateLimit": True, "timeout": 30000})
    ex.load_markets()

    def _fetch(symbol, tf, since_ms, limit):
        return ex.fetch_ohlcv(symbol, tf, since=int(since_ms), limit=int(limit)) or []

    now = int(time.time())
    tfs = [("1d", 3 * 365 * 24 * 3600), ("4h", 365 * 24 * 3600)]

    results = []   # (pair, tf, strat, stats)
    coverage = []
    for tf, lookback in tfs:
        for p in PAIRS:
            df = load_ohlcv_window(f"{p}/USDT", tf, now - lookback, now, fetcher=_fetch)
            closes = df["close"].to_numpy() if len(df) else np.array([])
            if len(closes) < 250:
                coverage.append(f"{p} {tf}: {len(closes)} bars (skipped)")
                continue
            r, sigs = _signals(closes)
            for name, pos in sigs:
                warm = 60  # max lookback
                st = _eval(r, pos, warm)
                if st:
                    results.append((p, tf, name, st))

    k = len(results)
    # Bonferroni two-sided t threshold for family-wise alpha 0.05 across k tests.
    # Use normal approx to the per-test alpha: z for p = 0.05/k / 2.
    if k > 0:
        per = 0.05 / k
        # inverse normal (Acklam approx is overkill); use a small table / formula
        # z ~ sqrt(2)*erfinv(1-per); approximate erfinv via scipy if available else fallback
        try:
            from scipy.stats import norm
            t_bonf = float(norm.ppf(1 - per / 2))
        except Exception:
            t_bonf = 3.6 if k > 100 else (3.2 if k > 30 else 2.6)
    else:
        t_bonf = 3.0

    survivors = [(p, tf, name, st) for (p, tf, name, st) in results
                 if st["oos_mean"] > 0 and abs(st["oos_t"]) >= t_bonf
                 and st["is_sign"] == st["oos_sign"]]

    # also: uncorrected p<0.05 hits (for transparency about multiple comparisons)
    uncorrected = [(p, tf, name, st) for (p, tf, name, st) in results
                   if st["oos_mean"] > 0 and abs(st["oos_t"]) >= 1.96 and st["is_sign"] == st["oos_sign"]]

    now_s = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    L = [f"# Per-pair strategy / pattern sweep — go-live falsification ({now_s})", "",
         "_Brutal bar by design: a survivor must be after-cost positive OOS, sign-consistent "
         "IS↔OOS, AND statistically significant at a BONFERRONI-corrected level across ALL "
         "tests. On a no-edge market the expected survivor count is ~0._", "",
         f"**Tests run:** {k} (pairs x strategies x params x timeframes) | "
         f"Bonferroni t-threshold ≈ {t_bonf:.2f} (family-wise α=0.05) | cost {COST_ONEWAY*1e4:.0f}bps/side", "",
         f"## Verdict: {len(survivors)} survivor(s) cleared the Bonferroni bar", ""]

    if survivors:
        L.append("### Cleared (MUST be adversarially re-checked before any go-live)")
        L += ["| pair | tf | strategy | OOS n | OOS mean% | OOS t |", "|---|---|---|---|---|---|"]
        for p, tf, name, st in sorted(survivors, key=lambda x: abs(x[3]["oos_t"]), reverse=True):
            L.append(f"| {p} | {tf} | {name} | {st['oos_n']} | {st['oos_mean']*100:+.3f}% | {st['oos_t']:+.2f} |")
        L.append("")
    else:
        L.append("### None. No per-pair strategy cleared the multiple-comparisons-corrected bar.")
        L.append("_The honest, expected go-live answer: there is no per-pair momentum / "
                 "mean-reversion / breakout configuration here with a repeatable, after-cost, "
                 "statistically-robust edge. Consistent with every prior falsification._")
        L.append("")

    L.append(f"### Transparency: {len(uncorrected)} hit(s) at UNCORRECTED p<0.05 "
             f"(of {k} tests — expect ~{round(0.025*k)} by chance even with NO edge)")
    if uncorrected:
        L += ["| pair | tf | strategy | OOS n | OOS mean% | OOS t |", "|---|---|---|---|---|---|"]
        for p, tf, name, st in sorted(uncorrected, key=lambda x: abs(x[3]["oos_t"]), reverse=True)[:20]:
            L.append(f"| {p} | {tf} | {name} | {st['oos_n']} | {st['oos_mean']*100:+.3f}% | {st['oos_t']:+.2f} |")
        L.append("")
        L.append("_These are NOT findings — at this many tests you expect this many false "
                 "positives by chance. They're shown so the Bonferroni gate's necessity is visible._")
        L.append("")

    if coverage:
        L += ["### Coverage notes"] + [f"- {c}" for c in coverage] + [""]
    L.append("---")
    L.append("_scripts/strategy_pattern_search.py — falsification harness (no look-ahead, IS/OOS, "
             "after-cost, Bonferroni). Survivors are candidates to refute, not signals. For the "
             "go-live record._")

    out = ROOT / "reports" / f"strategy_search_{date.today().isoformat()}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(L), encoding="utf-8")

    print(f"=== Strategy/pattern sweep ({now_s}) ===")
    print(f"Tests: {k} | Bonferroni t≈{t_bonf:.2f}")
    print(f"VERDICT: {len(survivors)} survivor(s) cleared the corrected bar; "
          f"{len(uncorrected)} uncorrected p<0.05 hits (~{round(0.025*k)} expected by chance)")
    for p, tf, name, st in sorted(survivors, key=lambda x: abs(x[3]['oos_t']), reverse=True):
        print(f"  SURVIVOR: {p} {tf} {name}  OOS t={st['oos_t']:+.2f} mean={st['oos_mean']*100:+.3f}%")
    print(f"\nFull record: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
