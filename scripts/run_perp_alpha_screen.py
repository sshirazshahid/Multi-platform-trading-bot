"""Run the FROZEN alpha screen on the analysis-only commodity/equity PERPS only.

Uses the EXACT pre-registered two-stage screen from run_alpha_search
(IR>=0.50 -> DSR>=0.10 + PBO<=0.50 + BH-FDR) — NONE of the gates change; that
would be p-hacking. The only difference is the universe: the perp cache
(data/ohlcv_cache/*USDTUSDT_<tf>.parquet) instead of crypto, so the cross-section
is the perps themselves.

HONEST CAVEATS (printed with the verdict):
  * Small universe (~14 names) — cross-sectional power is low; QUANTILE 0.20 is
    only 2-3 names per side.
  * Short calendar history (~2-6 months) — these are new listings.
  * Equity perps (TSLA/NVDA/...) drift/gap when Wall Street is closed, which
    contaminates intraday cross-sectional returns.
A NO_EDGE here is expected and weakly informative; an apparent survivor on this
little data should be treated as a hypothesis to re-test with more history, NOT
a green light.
"""
from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

import pandas as pd  # noqa: E402

import run_alpha_search as ras  # noqa: E402
from core.alpha_zoo.panel import build_panel  # noqa: E402


def load_perp_raw(timeframe: str) -> dict:
    cache = ROOT / "data" / "ohlcv_cache"
    raw = {}
    for p in sorted(cache.glob(f"*USDTUSDT_{timeframe}.parquet")):
        base = p.name[: -len(f"_{timeframe}.parquet")]      # e.g. 'XAU-USDTUSDT'
        b = base.replace("-USDTUSDT", "")
        raw[f"{b}/USDT:USDT"] = pd.read_parquet(p)
    return raw


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--timeframe", default="1h")
    args = ap.parse_args()

    raw = load_perp_raw(args.timeframe)
    if not raw:
        print(f"No perp cache for timeframe {args.timeframe}. Run backfill_perps_ohlcv.py first.")
        return 1
    print(f"Perp universe ({args.timeframe}): {len(raw)} symbols")
    for s in sorted(raw):
        print(f"  {s:<22} {len(raw[s]):6d} bars")

    panel = build_panel(raw, timeframe=args.timeframe, horizon=ras.HORIZON)
    print(f"\nPanel: {len(panel.ts)} bars x {len(panel.symbols)} symbols "
          f"(min_width gate = {ras.MIN_WIDTH})")

    result = ras.run_search(panel, report_dir=None)  # no clobber of the crypto report

    print(f"\n=== VERDICT: {result['verdict']} ===")
    print(f"survivors={result['survivors']}  PBO={result['pbo']}  N_eff={result['n_eff']}  "
          f"panel={result['panel']}")
    print("Top alphas by in-sample IR:")
    print(f"  {'id':<16}{'IR_is':>8}{'category':>14}{'OOS_Shrp':>10}{'DSR':>7}{'FDR_p':>9}  surv")
    for r in result["table"][:12]:
        print(f"  {r['id']:<16}{r['ir_is']:>8}{r['category']:>14}"
              f"{r['oos_sharpe']:>10}{r['dsr']:>7}{r['fdr_p']:>9}  {r['survivor']}")

    # write a clearly-labeled perp report (separate from the crypto alpha_search_*)
    rep = ROOT / "reports" / f"alpha_search_perps_{date.today().isoformat()}_{args.timeframe}.json"
    rep.parent.mkdir(parents=True, exist_ok=True)
    import json
    rep.write_text(json.dumps(result, indent=2, default=float), encoding="utf-8")
    print(f"\nReport: {rep}")
    print("CAVEATS: ~14-name universe (low cross-sectional power), ~2-6mo history, "
          "equity perps gap when Wall St closed. Treat any survivor as a hypothesis, not a go-signal.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
