"""MVRV-Z honest-gate screen — falsification test for an on-chain valuation signal.

The deep-research pass (2026-06-28) found MVRV-Z has real IN-SAMPLE Bitcoin-cycle
timing structure, but NO published study nets transaction costs or runs a true
out-of-sample / PBO / DSR test. This screen runs exactly that test through the
bot's OWN honest-gate machinery (core.stat_tests PBO/DSR, core.decision.monte_carlo
block-bootstrap, core.strategy_readiness) so the result is comparable to every
prior in-house NO_EDGE verdict.

Strategy (long-only, capital-preservation, the way MVRV-Z is actually used):
    long BTC when MVRV-Z <= z_buy (cheap / accumulation), flat when MVRV-Z >=
    z_sell (expensive / distribution), hold between (hysteresis). Decision at the
    close of day t uses z[t] only (causal); the position earns day t->t+1 return.
    Round-trip cost charged on every position change.

Honest discipline:
    * Parameters (z_buy, z_sell) selected on the IN-SAMPLE first 60% by time;
      performance reported OUT-OF-SAMPLE on the last 40%.
    * PBO (CSCV) across the whole (z_buy x z_sell) grid — multiple-testing aware.
    * Deflated Sharpe with HONEST n_trials = grid size.
    * Block-bootstrap Monte Carlo on OOS daily returns (preserves streaks).
    * Benchmark = buy-and-hold (one entry cost).

DATA (keyless): MVRV-Z from bitcoin-data.com (BGeometrics); BTC daily close from
Binance public OHLCV via ccxt. NOTE: the keyless MVRV-Z series is ~4 years
(~1 cycle) — power-limited for a cycle-timing signal. The verdict flags this.

Run:  python scripts/run_mvrv_z_screen.py
"""
from __future__ import annotations

import datetime as dt
import sys
import urllib.request
from itertools import product
from pathlib import Path

import ccxt
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.decision.monte_carlo import monte_carlo_trade_sequence
from core.stat_tests import deflated_sharpe, pbo, sharpe
from core.strategy_readiness import StrategyReadinessThresholds, evaluate_records

MVRVZ_URL = "https://bitcoin-data.com/v1/mvrv-zscore"
COST_BPS_ROUNDTRIP = 12.0          # taker round-trip on a liquid major (fees+spread+slip)
OOS_FRAC = 0.40
Z_BUY_GRID = [-0.2, 0.0, 0.5, 1.0]
Z_SELL_GRID = [2.5, 3.5, 5.0, 7.0]
ANN = np.sqrt(365.0)


def fetch_mvrvz() -> dict[str, float]:
    req = urllib.request.Request(MVRVZ_URL, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=60) as r:
        import json
        rows = json.load(r)
    out = {}
    for row in rows:
        try:
            out[str(row["d"])] = float(row["mvrvZscore"])
        except (KeyError, TypeError, ValueError):
            continue
    return out


def fetch_btc_daily_close(since_date: str) -> dict[str, float]:
    ex = ccxt.binance({"enableRateLimit": True})
    since = ex.parse8601(f"{since_date}T00:00:00Z")
    now = ex.milliseconds()
    day = 86_400_000
    out: dict[str, float] = {}
    cur = since
    while cur < now:
        batch = ex.fetch_ohlcv("BTC/USDT", "1d", since=cur, limit=1000)
        if not batch:
            break
        for ts, _o, _h, _l, c, _v in batch:
            if ts + day <= now:  # closed days only
                d = dt.datetime.fromtimestamp(ts / 1000, dt.timezone.utc).strftime("%Y-%m-%d")
                out[d] = float(c)
        cur = batch[-1][0] + day
        if len(batch) < 1000:
            break
    return out


def build_series():
    z = fetch_mvrvz()
    if not z:
        raise SystemExit("no MVRV-Z data")
    start = min(z)
    close = fetch_btc_daily_close(start)
    # BGeometrics emits some literal NaN mvrvZscore values (json accepts them);
    # drop them so they neither poison the range nor silently carry a position.
    dates = sorted(d for d in (set(z) & set(close)) if np.isfinite(z[d]))
    zs = np.array([z[d] for d in dates], dtype=float)
    px = np.array([close[d] for d in dates], dtype=float)
    # next-day close-to-close return aligned to the decision day t
    ret = np.empty_like(px)
    ret[:-1] = px[1:] / px[:-1] - 1.0
    ret[-1] = 0.0
    return dates, zs, px, ret


def strat_daily(zs, ret, z_buy, z_sell, cost_frac):
    """Long/flat hysteresis daily returns. position[t] from z[t]; earns ret[t]."""
    n = len(zs)
    pos = np.zeros(n)
    cur = 0
    for t in range(n):
        if cur == 0 and zs[t] <= z_buy:
            cur = 1
        elif cur == 1 and zs[t] >= z_sell:
            cur = 0
        pos[t] = cur
    turnover = np.abs(np.diff(np.concatenate([[0.0], pos])))
    return pos * ret - cost_frac * turnover, pos


def episodes_to_trades(pos, ret, dates):
    """Compound each contiguous long episode into one trade return."""
    trades = []
    t = 0
    n = len(pos)
    while t < n:
        if pos[t] == 1:
            start = t
            cum = 1.0
            while t < n and pos[t] == 1:
                cum *= (1.0 + ret[t])
                t += 1
            trades.append({"ts": _date_ts(dates[start]), "pnl": cum - 1.0,
                           "strategy": "mvrv_z"})
        else:
            t += 1
    return trades


def _date_ts(d: str) -> float:
    return dt.datetime.strptime(d, "%Y-%m-%d").replace(tzinfo=dt.timezone.utc).timestamp()


def main():
    cost = COST_BPS_ROUNDTRIP / 10_000.0
    dates, zs, px, ret = build_series()
    n = len(dates)
    cut = int(n * (1.0 - OOS_FRAC))
    span_days = (_date_ts(dates[-1]) - _date_ts(dates[0])) / 86_400.0

    print("=" * 84)
    print("MVRV-Z HONEST-GATE SCREEN  (long-only BTC, keyless data)")
    print("=" * 84)
    print(f"  span: {dates[0]} -> {dates[-1]}  ({n} days, {span_days/365:.1f}y)")
    print(f"  MVRV-Z range: [{zs.min():.2f}, {zs.max():.2f}]   cost={COST_BPS_ROUNDTRIP:.0f}bps round-trip")
    print(f"  IS = first {cut} days, OOS = last {n - cut} days")

    # buy-and-hold benchmark (one entry cost)
    bh = ret.copy()
    bh[0] -= cost
    bh_oos = bh[cut:]
    bh_oos_sh = sharpe(bh_oos)

    grid = list(product(Z_BUY_GRID, Z_SELL_GRID))
    full_mat = np.empty((n, len(grid)))
    rows = []
    for j, (zb, zsell) in enumerate(grid):
        if zb >= zsell:
            full_mat[:, j] = 0.0
            rows.append(None)
            continue
        sret, pos = strat_daily(zs, ret, zb, zsell, cost)
        full_mat[:, j] = sret
        is_sh = sharpe(sret[:cut])
        oos_sh = sharpe(sret[cut:])
        # count ENTRIES (0->1); a buy-and-hold-to-end episode is 1 trade, not 0
        n_trades = int(np.sum(np.diff(np.concatenate([[0.0], pos])) > 0))
        rows.append({"zb": zb, "zsell": zsell, "is_sh": is_sh, "oos_sh": oos_sh,
                     "sret": sret, "pos": pos, "n_trades": n_trades})

    valid = [r for r in rows if r is not None]
    best = max(valid, key=lambda r: r["is_sh"])  # select on IN-SAMPLE only

    print("\n  grid (selected by IN-SAMPLE Sharpe):")
    print(f"  {'z_buy':>6}{'z_sell':>7}{'IS Sh/d':>9}{'OOS Sh/d':>10}{'trades':>8}")
    for r in sorted(valid, key=lambda r: -r["is_sh"])[:8]:
        star = "  <-- best-IS" if r is best else ""
        print(f"  {r['zb']:>6.1f}{r['zsell']:>7.1f}{r['is_sh']:>9.3f}{r['oos_sh']:>10.3f}"
              f"{r['n_trades']:>8}{star}")

    # ---- honest gate on the IS-selected config ----
    oos = best["sret"][cut:]
    oos_sh = sharpe(oos)
    dsr = deflated_sharpe(sr_observed=oos_sh, n_trials=len(valid), n_obs=len(oos))
    keep = [j for j, r in enumerate(rows) if r is not None]
    pbo_val = pbo(full_mat[:, keep], n_partitions=16)
    mc = monte_carlo_trade_sequence(oos, block_len=10, n_resamples=2000, seed=7)
    trades = episodes_to_trades(best["pos"], ret, dates)
    rd = evaluate_records(trades, thresholds=StrategyReadinessThresholds(), name="mvrv_z")

    print("\n" + "=" * 84)
    print("HONEST GATE  (bot's own machinery: stat_tests PBO/DSR + block-MC + readiness)")
    print("=" * 84)
    print(f"  best config        : z_buy={best['zb']}  z_sell={best['zsell']}")
    print(f"  OOS Sharpe/day     : {oos_sh:+.3f}  (annualized {oos_sh*ANN:+.2f})")
    print(f"  buy&hold OOS Sh/day: {bh_oos_sh:+.3f}  (annualized {bh_oos_sh*ANN:+.2f})")
    print(f"  beats buy&hold OOS : {oos_sh > bh_oos_sh}")
    print(f"  Deflated Sharpe    : {dsr:.3f}  (Pr[true SR>0 | {len(valid)} trials]; gate >= 0.10)")
    print(f"  PBO (CSCV)         : {pbo_val:.3f}  (gate <= 0.50; literature-healthy < 0.25)")
    print(f"  MC p(total>0)      : {mc.p_total_positive:.3f}   MC maxDD p95: {mc.max_drawdown_p95:.4f}")
    print(f"  readiness verdict  : {rd['verdict']}  (n_trades={rd['summary']['n']}, "
          f"WR={rd['summary']['win_rate']}, PF={rd['summary']['profit_factor']})")

    # ---- verdict ----
    insufficient = (span_days / 365.0 < 2.0) or (len(oos) < 60) or (best["n_trades"] < 5)
    edge = (oos_sh > bh_oos_sh) and (dsr >= 0.10) and (pbo_val <= 0.50) and (mc.p_total_positive >= 0.95)
    print("\n" + "-" * 84)
    if insufficient:
        print("VERDICT: INSUFFICIENT DATA / POWER-LIMITED -- keyless MVRV-Z spans ~1 cycle;")
        print("  a cycle-timing signal cannot be honestly validated on <2 cycles. Re-run on")
        print("  multi-cycle realized-cap data (paid CoinMetrics/Glassnode) before any verdict.")
    elif edge:
        print("VERDICT: GO (provisional) — beats B&H OOS AND clears DSR/PBO/MC. VERIFY before live.")
    else:
        print("VERDICT: NO_GO — after-cost OOS edge over buy-and-hold NOT established on the honest gate.")
    print("-" * 84)


if __name__ == "__main__":
    main()
