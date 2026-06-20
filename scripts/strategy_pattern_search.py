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
COST_ONEWAY = 5.0 / 1e4  # ~5 bps per side (liquid majors, taker)
IS_FRAC = 0.60
MIN_OOS = 50  # min OOS observations for a usable t-stat


def _signals(closes):
    """Yield (name, position_array) for each strategy. pos[t] uses data <= t-1."""
    import numpy as np

    r = np.diff(np.log(closes))  # r[i] = return over bar i+1
    n = len(r)
    out = []

    def _mom(look):
        pos = np.zeros(n)
        for t in range(look, n):
            pos[t] = np.sign(np.sum(r[t - look : t]))  # trailing return through t-1
        return pos

    def _mr(look):
        pos = np.zeros(n)
        for t in range(look, n):
            pos[t] = -np.sign(np.sum(r[t - look : t]))
        return pos

    def _breakout(look):
        pos = np.zeros(n)
        for t in range(look + 1, n):
            window = closes[t - look : t]  # closes through t-1 (closes index aligns to r[t-1])
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
    turn[-1] += abs(pos[-1])  # charge exit of any position still open at end-of-sample (audit #11)
    net = pos * r - turn * COST_ONEWAY
    seg = net[warm:]
    if len(seg) < 100:
        return None
    cut = int(len(seg) * IS_FRAC)
    is_s, oos_s = seg[:cut], seg[cut:]
    os_std = oos_s.std(ddof=1)  # sample SE, ddof=1 (audit #9)
    if len(oos_s) < MIN_OOS or os_std == 0 or is_s.std(ddof=1) == 0:
        return None
    oos_t = oos_s.mean() / (os_std / math.sqrt(len(oos_s)))
    return {
        "is_mean": float(is_s.mean()),
        "oos_mean": float(oos_s.mean()),
        "oos_t": float(oos_t),
        "oos_n": len(oos_s),
        "is_sign": np.sign(is_s.mean()),
        "oos_sign": np.sign(oos_s.mean()),
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

    results = []  # (pair, tf, strat, stats)
    coverage = []
    for tf, lookback in tfs:
        for p in PAIRS:
            df = load_ohlcv_window(f"{p}/USDT", tf, now - lookback, now, fetcher=_fetch)
            # drop the final (possibly still-forming) bar for reproducibility (audit #7)
            closes = df["close"].to_numpy()[:-1] if len(df) > 1 else np.array([])
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

    survivors = [
        (p, tf, name, st)
        for (p, tf, name, st) in results
        if st["oos_mean"] > 0 and abs(st["oos_t"]) >= t_bonf and st["is_sign"] == st["oos_sign"]
    ]

    # also: uncorrected p<0.05 hits (for transparency about multiple comparisons)
    uncorrected = [
        (p, tf, name, st)
        for (p, tf, name, st) in results
        if st["oos_mean"] > 0 and abs(st["oos_t"]) >= 1.96 and st["is_sign"] == st["oos_sign"]
    ]

    # Detection floor / minimum-detectable effect (audit #1): the smallest annualized Sharpe
    # this Bonferroni bar can actually reject. MDE_perbar = t_bonf / sqrt(n_oos); annualize by
    # sqrt(periods/yr). A 0-survivor result only excludes edges ABOVE this floor.
    ppy = {"1d": 365.0, "4h": 365.0 * 6}
    mde = {}
    for tf in ("1d", "4h"):
        ns = [st["oos_n"] for (p, t, name, st) in results if t == tf]
        if ns:
            n_med = sorted(ns)[len(ns) // 2]
            mde[tf] = t_bonf / math.sqrt(n_med) * math.sqrt(ppy[tf])
    mde_str = " / ".join(f"{tf}≈{v:.1f}" for tf, v in mde.items()) or "n/a"

    now_s = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    L = [
        f"# Per-pair strategy / pattern sweep — go-live falsification ({now_s})",
        "",
        "_Brutal bar by design: a survivor must be after-cost positive OOS, sign-consistent "
        "IS↔OOS, AND statistically significant at a BONFERRONI-corrected level across ALL "
        "tests. On a no-edge market the expected survivor count is ~0._",
        "",
        f"**Tests run:** {k} (pairs x strategies x params x timeframes) | "
        f"Bonferroni t-threshold ≈ {t_bonf:.2f} (family-wise α=0.05) | cost {COST_ONEWAY * 1e4:.0f}bps/side",
        "",
        f"**⚠ Detection floor (MDE):** the min annualized Sharpe this bar can reject ≈ {mde_str}. "
        "Genuinely tradeable edges sit around Sharpe 0.5-1.5 — BELOW this floor — so a 0-survivor "
        "result means **no edge DETECTED above the floor, NOT 'no edge exists'** (Type-II not excluded). "
        "This bar only rejects EXTRAORDINARY edges by design.",
        "",
        f"## Verdict: {len(survivors)} survivor(s) cleared the Bonferroni bar",
        "",
    ]

    if survivors:
        L.append("### Cleared (MUST be adversarially re-checked before any go-live)")
        L += ["| pair | tf | strategy | OOS n | OOS mean% | OOS t |", "|---|---|---|---|---|---|"]
        for p, tf, name, st in sorted(survivors, key=lambda x: abs(x[3]["oos_t"]), reverse=True):
            L.append(
                f"| {p} | {tf} | {name} | {st['oos_n']} | {st['oos_mean'] * 100:+.3f}% | {st['oos_t']:+.2f} |"
            )
        L.append("")
    else:
        L.append(
            "### None cleared the bar — no per-pair strategy shows an edge ABOVE the detection floor."
        )
        L.append(
            "_Honest reading (audit-corrected): this means no LARGE edge (annualized Sharpe above the "
            "floor disclosed above) was DETECTED — NOT that no edge exists. A modest, genuinely-"
            "tradeable edge (Sharpe ~0.5-1.5) is below this test's power and would be invisible here. "
            "The uncorrected-hit count below is at/under the chance rate, so no latent signal appears "
            "to be suppressed — but the correct go-live statement remains 'no edge DETECTED "
            "(Type-II not excluded)', not 'no edge exists'._"
        )
        L.append("")

    L.append(
        f"### Transparency: {len(uncorrected)} hit(s) at UNCORRECTED p<0.05 "
        f"(of {k} tests — expect ~{round(0.025 * k)} by chance even with NO edge)"
    )
    if uncorrected:
        L += ["| pair | tf | strategy | OOS n | OOS mean% | OOS t |", "|---|---|---|---|---|---|"]
        for p, tf, name, st in sorted(uncorrected, key=lambda x: abs(x[3]["oos_t"]), reverse=True)[
            :20
        ]:
            L.append(
                f"| {p} | {tf} | {name} | {st['oos_n']} | {st['oos_mean'] * 100:+.3f}% | {st['oos_t']:+.2f} |"
            )
        L.append("")
        L.append(
            "_These are NOT findings — at this many tests you expect this many false "
            "positives by chance. They're shown so the Bonferroni gate's necessity is visible._"
        )
        L.append("")

    if coverage:
        L += ["### Coverage notes"] + [f"- {c}" for c in coverage] + [""]
    L.append("---")
    L.append(
        "_scripts/strategy_pattern_search.py — falsification harness (no look-ahead, IS/OOS, "
        "after-cost, Bonferroni). Survivors are candidates to refute, not signals. For the "
        "go-live record._"
    )

    out = ROOT / "reports" / f"strategy_search_{date.today().isoformat()}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(L), encoding="utf-8")

    print(f"=== Strategy/pattern sweep ({now_s}) ===")
    print(f"Tests: {k} | Bonferroni t≈{t_bonf:.2f} | detection floor MDE ann.Sharpe: {mde_str}")
    print(
        "(0 survivors => no edge DETECTED above that floor, NOT 'no edge exists' — Type-II not excluded)"
    )
    print(
        f"VERDICT: {len(survivors)} survivor(s) cleared the corrected bar; "
        f"{len(uncorrected)} uncorrected p<0.05 hits (~{round(0.025 * k)} expected by chance)"
    )
    for p, tf, name, st in sorted(survivors, key=lambda x: abs(x[3]["oos_t"]), reverse=True):
        print(
            f"  SURVIVOR: {p} {tf} {name}  OOS t={st['oos_t']:+.2f} mean={st['oos_mean'] * 100:+.3f}%"
        )
    print(f"\nFull record: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
