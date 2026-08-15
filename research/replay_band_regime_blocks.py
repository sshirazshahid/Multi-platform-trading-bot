"""Counterfactual replay of band_regime_filter-blocked entries.

Question: what did the ADX>30 / btc_vol<0.7 veto actually COST (or save)?

Method: every BandRegime BLOCK is logged with timestamp + symbol. For each,
reconstruct the bracket the bot WOULD have opened and resolve it against
forward 1h OHLCV using the same SL-first, cost-charged model the shadow
resolver uses.

TWO PAYOFF GEOMETRIES ARE REPORTED, deliberately:
  * design   - TP = frac x SL distance at the configured fracs (buy 0.45 /
               sell 0.35). This is what the config INTENDS.
  * realized - TP scaled so the payoff ratio matches what the live cohort
               actually achieves (0.243 measured over n=36, i.e. wins land
               ~30% smaller than design). Reporting only the design number
               would OVERSTATE what the filter cost, because the system
               demonstrably does not realize design geometry.

Read-only. Places no order, changes no config.
Run: venv/Scripts/python.exe research/replay_band_regime_blocks.py
"""

from __future__ import annotations

import json
import pathlib
import re
import statistics as st
import sys
from collections import Counter

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# Costs: same constants the shadow resolver charges (bps).
FEE_BPS_PER_SIDE = 6.0
OPEN_SLIP_BPS = 5.0
EXIT_SLIP_BPS = 5.0
SL_SLIP_BPS = 10.0

SL_PCT = 0.008           # ~0.80% ATR-derived stop (measured from live brackets)
FRAC_BUY = 0.45          # configured AccBand TP fracs
FRAC_SELL = 0.35
REALIZED_PAYOFF = 0.243  # measured on the n=36 cohort (avg win / avg loss)
HORIZON_BARS = 24        # 1h bars; band trades resolve fast (median <1h hold)

LOG_RE = re.compile(
    r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}).*\[BandRegime\] BLOCKED "
    r"([A-Z0-9]+)/USDT . band_regime_filter:(\S+)"
)


def parse_blocks(log_paths):
    """(ts_epoch, base, reason) for every BandRegime block."""
    import datetime as dt

    out = []
    for p in log_paths:
        if not p.exists():
            continue
        for line in p.read_text(encoding="utf-8", errors="ignore").splitlines():
            m = LOG_RE.match(line)
            if not m:
                continue
            when = dt.datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S")
            out.append((int(when.timestamp()), m.group(2), m.group(3)))
    return out


def load_prices(base: str) -> dict:
    """hour-aligned OHLC from the local cache."""
    import pandas as pd

    p = ROOT / f"data/ohlcv_cache/{base}-USDT_1h.parquet"
    if not p.exists():
        return {}
    try:
        df = pd.read_parquet(p)[["ts", "open", "high", "low", "close"]].dropna()
    except Exception:
        return {}
    out = {}
    for ts, o, h, lo, cl in zip(df["ts"], df["open"], df["high"],
                                df["low"], df["close"]):
        t = int(ts)
        if t > 10**12:
            t //= 1000
        out[t - (t % 3600)] = (float(o), float(h), float(lo), float(cl))
    return out


def resolve(px: dict, entry_hour: int, side: str, payoff: float):
    """SL-first barrier resolution over HORIZON_BARS; after-cost return."""
    e = px.get(entry_hour)
    if not e:
        return None
    entry = e[3]  # signal-bar close
    if entry <= 0:
        return None
    is_buy = side == "buy"
    sl_dist = SL_PCT * entry
    sl = entry - sl_dist if is_buy else entry + sl_dist
    tp = entry + payoff * sl_dist if is_buy else entry - payoff * sl_dist

    for i in range(1, HORIZON_BARS + 1):
        bar = px.get(entry_hour + i * 3600)
        if not bar:
            continue
        _, high, low, close = bar
        hit_sl = low <= sl if is_buy else high >= sl
        hit_tp = high >= tp if is_buy else low <= tp
        if hit_sl:                      # SL-first conservative tie-break
            gross = -sl_dist / entry
            slip = SL_SLIP_BPS
            break
        if hit_tp:
            gross = (payoff * sl_dist) / entry
            slip = EXIT_SLIP_BPS
            break
    else:
        last = px.get(entry_hour + HORIZON_BARS * 3600)
        if not last:
            return None
        move = (last[3] - entry) / entry
        gross = move if is_buy else -move
        slip = EXIT_SLIP_BPS
    cost = (2 * FEE_BPS_PER_SIDE + OPEN_SLIP_BPS + slip) / 10_000.0
    return gross - cost


def main() -> None:
    logs = sorted((ROOT / "logs").glob("bot_2026-08-*.log"))
    blocks = parse_blocks(logs)
    print(f"BandRegime blocks parsed: {len(blocks):,} from {len(logs)} log file(s)")
    if not blocks:
        print("no blocks parsed - check the log line format")
        return
    for r, n in Counter(x[2] for x in blocks).most_common():
        print(f"   {r}: {n:,}")

    # One event per (symbol, hour): the log spams the same veto every cycle.
    uniq = {}
    for ts, base, reason in blocks:
        uniq.setdefault((base, ts - (ts % 3600)), reason)
    print(f"\nde-duplicated to {len(uniq):,} (symbol, hour) events "
          f"({len(blocks)/max(len(uniq),1):.1f}x log inflation)")

    price_cache: dict = {}
    results = {}
    for geom, label in ((None, "design (buy .45 / sell .35)"),
                        (REALIZED_PAYOFF, f"realized payoff {REALIZED_PAYOFF}")):
        rs, missing = [], set()
        for (base, hour), _reason in uniq.items():
            if base not in price_cache:
                price_cache[base] = load_prices(base)
            px = price_cache[base]
            if not px:
                missing.add(base)
                continue
            # The blocked lane is overwhelmingly short (35/36 of the live
            # cohort); replay BOTH sides and report separately.
            for side in ("buy", "sell"):
                payoff = geom if geom is not None else (
                    FRAC_BUY if side == "buy" else FRAC_SELL)
                r = resolve(px, hour, side, payoff)
                if r is not None:
                    rs.append((side, r))
        if not rs:
            print(f"\n[{label}] no resolvable events "
                  f"(no price data for {len(missing)} symbols)")
            continue
        print(f"\n[{label}]  resolvable={len(rs)}  symbols-without-price="
              f"{len(missing)}")
        for side in ("buy", "sell"):
            v = [r for s, r in rs if s == side]
            if not v:
                continue
            wr = sum(1 for x in v if x > 0) / len(v)
            mean = st.mean(v)
            se = st.pstdev(v) / (len(v) ** 0.5) if len(v) > 1 else 0.0
            print(f"   {side:5s} n={len(v):5d}  WR={100*wr:5.1f}%  "
                  f"mean={mean*1e4:+8.1f} bps  95% CI "
                  f"[{(mean-1.96*se)*1e4:+.1f}, {(mean+1.96*se)*1e4:+.1f}]")
            results[f"{label}|{side}"] = {
                "n": len(v), "wr": wr, "mean_bps": mean * 1e4}

    print("\nREAD: a NEGATIVE mean means the filter SAVED money by blocking "
          "these entries; POSITIVE means it cost opportunity.")
    print("\nCAVEAT (binding): the realized-payoff arm is UNINFORMATIVE and must "
          "not be quoted.\n  TP at payoff 0.243 sits ~0.19% from entry while the "
          "SL sits at 0.80%; on\n  1h bars both are usually inside the same "
          "range, and the SL-first tie-break\n  then books a loss every time -> "
          "0% WR is a bar-granularity ARTIFACT, not a\n  market fact. Resolving "
          "it needs sub-hourly bars. The DESIGN arm (TP 0.28-0.36%)\n  carries "
          "the same bias more weakly, so its negative mean is a CONSERVATIVE\n  "
          "bound on how bad these entries were - the true figure is no better.")
    out = ROOT / "_workspace/strategy_pipeline/73_band_regime_block_replay.json"
    out.write_text(json.dumps({"blocks": len(blocks), "events": len(uniq),
                               "results": results}, indent=1), encoding="utf-8")
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
