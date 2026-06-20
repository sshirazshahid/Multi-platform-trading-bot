"""Lever C - spot portfolio review."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
TRIM_WEIGHT = 0.25
DUST_USD = 10.0
MED_BETA = {"ETH", "BNB"}
STABLES = {"USDT", "USDC", "BUSD", "DAI"}


def fetch_live_holdings():
    import config
    from exchanges.binance_client import BinanceClient

    holdings = {}
    try:
        c = BinanceClient(config.BINANCE_API_KEY, config.BINANCE_SECRET_KEY)
    except Exception as e:
        print(f"  (binance client unavailable: {e})")
        return holdings
    if getattr(c, "exchange", None) is None:
        return holdings
    try:
        bal = c.exchange.fetch_balance()
        tickers = c.exchange.fetch_tickers()
    except Exception as e:
        print(f"  (fetch failed: {e})")
        return holdings
    for asset, amt in (bal.get("total") or {}).items():
        if not amt or amt <= 0:
            continue
        if asset in STABLES:
            usd = float(amt)
        else:
            t = tickers.get(f"{asset}/USDT") or {}
            usd = float(amt) * float(t.get("last") or 0.0)
        if usd > 0:
            holdings[asset] = holdings.get(asset, 0.0) + usd
    return holdings


def analyze(holdings):
    holdings = {k: v for k, v in holdings.items() if v > 0}
    total = sum(holdings.values())
    if total <= 0:
        print("No holdings.")
        return 2
    ranked = sorted(holdings.items(), key=lambda kv: -kv[1])
    weights = {k: v / total for k, v in holdings.items()}
    hhi = sum(w * w for w in weights.values())
    eff_n = 1.0 / hhi
    top1 = ranked[0][1] / total
    top3 = sum(v for _, v in ranked[:3]) / total
    beta = 0.0
    for k, w in weights.items():
        b = 0.0 if k in STABLES else (1.0 if k == "BTC" else (0.8 if k in MED_BETA else 1.2))
        beta += w * b
    print("=" * 64)
    print(f"SPOT PORTFOLIO REVIEW   total = ${total:,.2f}   ({len(holdings)} holdings)")
    print("=" * 64)
    print(f"  {'asset':8s} {'USD':>10s} {'weight':>8s}   flags")
    for k, v in ranked:
        w = v / total
        flags = []
        if w > TRIM_WEIGHT:
            flags.append(f"TRIM (>{TRIM_WEIGHT * 100:.0f}%)")
        if v < DUST_USD:
            flags.append("DUST")
        print(f"  {k:8s} {v:10.2f} {w * 100:7.1f}%   {', '.join(flags)}")
    print("\n  CONCENTRATION:")
    print(f"    top-1 = {top1 * 100:.1f}%   top-3 = {top3 * 100:.1f}%")
    print(f"    HHI = {hhi:.3f}   effective holdings = {eff_n:.1f}")
    print(f"    crypto-beta (vs BTC) = {beta:.2f}  ({'high' if beta > 0.9 else 'moderate'})")
    print("\n  READ:")
    msgs = []
    if top1 > TRIM_WEIGHT:
        msgs.append(
            f"- {ranked[0][0]} is {top1 * 100:.0f}% of the book - one coin dominates P&L. Trim toward 15-20%."
        )
    if eff_n < len(holdings) * 0.6:
        msgs.append(f"- {len(holdings)} coins but only ~{eff_n:.0f} effective - heavy overlap.")
    if beta > 0.9:
        msgs.append("- Near-full crypto beta: whole bag falls together in a BTC drawdown.")
    dust = [k for k, v in holdings.items() if v < DUST_USD]
    if dust:
        msgs.append(f"- Dust {dust}: consolidate.")
    if not msgs:
        msgs.append("- Reasonably balanced.")
    for m in msgs:
        print("  " + m)
    print("\n  NOTE: risk read, not a forecast. Nothing here places orders.")
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--snapshot", default="")
    args = ap.parse_args()
    if args.snapshot:
        holdings = json.loads(Path(args.snapshot).read_text())
    else:
        print("Fetching live spot holdings...")
        holdings = fetch_live_holdings()
        if not holdings:
            print("[BLOCKED] no live holdings. Use --snapshot file.json")
            return 2
    return analyze(holdings)


if __name__ == "__main__":
    raise SystemExit(main())
