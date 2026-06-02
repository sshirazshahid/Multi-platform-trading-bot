"""Crypto Market Intelligence Brief — DESCRIPTIVE market awareness (NOT a buy signal).

Aggregates PUBLIC market data into a readable "where is the action right now"
snapshot you can run anytime (no Claude session needed):
  - 24h top gainers / losers (liquid USDT pairs)
  - highest-volume pairs (where money is flowing)
  - funding-rate extremes (crowded long / short positioning)
  - Fear & Greed index (crowd sentiment)

HONEST SCOPE: this shows what is ALREADY moving (descriptive). Because the data
is public it is priced in, so it does NOT predict what WILL move next. Use it for
situational awareness and as the raw material for research output/content — not as
a trade signal. (v1 — on-chain exchange flows, token-unlock calendar, and news are
planned add-ons.)

Usage:
    python scripts/market_intel_report.py            # print + write reports/market_intel_<date>.md
    python scripts/market_intel_report.py --min-vol 10000000
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from datetime import date, datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _safe(fn, default, label=""):
    try:
        return fn()
    except Exception as e:
        print(f"  [warn] {label}: {str(e)[:100]}")
        return default


def _fear_greed():
    url = "https://api.alternative.me/fng/?limit=1"
    with urllib.request.urlopen(url, timeout=15) as r:
        d = json.loads(r.read().decode())
        return d["data"][0]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-vol", type=float, default=5_000_000.0, help="min 24h quote volume (USDT) for movers")
    ap.add_argument("--top", type=int, default=10)
    args = ap.parse_args()

    import ccxt
    print("Connecting to Binance (public data)...")
    ex = ccxt.binance({"enableRateLimit": True, "timeout": 30000})
    ex.load_markets()

    tickers = _safe(lambda: ex.fetch_tickers(), {}, "fetch_tickers")
    rows = []
    for sym, t in (tickers or {}).items():
        m = ex.markets.get(sym, {})
        if m.get("quote") != "USDT" or not m.get("spot"):
            continue
        pct = t.get("percentage")
        qv = t.get("quoteVolume") or 0
        if pct is None or qv < args.min_vol:
            continue
        rows.append((sym.replace("/USDT", ""), float(pct), float(qv), t.get("last")))

    gainers = sorted(rows, key=lambda r: r[1], reverse=True)[: args.top]
    losers = sorted(rows, key=lambda r: r[1])[: args.top]
    byvol = sorted(rows, key=lambda r: r[2], reverse=True)[: args.top]

    fund = _safe(lambda: ex.fetch_funding_rates(), {}, "fetch_funding_rates")
    frows = [(s.split("/")[0].split(":")[0], f.get("fundingRate"))
             for s, f in (fund or {}).items() if f.get("fundingRate") is not None]
    pos_fund = sorted(frows, key=lambda r: r[1], reverse=True)[:8]
    neg_fund = sorted(frows, key=lambda r: r[1])[:8]

    fng = _safe(_fear_greed, None, "fear_greed")

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    L = [f"# Crypto Market Intelligence Brief — {now}", "",
         "_Descriptive snapshot of what is ALREADY moving. Public data is priced in — "
         "this is for awareness/research, NOT a prediction or trade signal._", ""]

    if fng:
        L.append(f"**Crowd sentiment (Fear & Greed):** {fng.get('value')}/100 — {fng.get('value_classification')}")
        L.append("")

    L.append(f"## Where money is flowing — top {args.top} by 24h volume")
    L += ["| coin | 24h % | 24h volume (USDT) | last |", "|---|---|---|---|"]
    for b, p, v, last in byvol:
        L.append(f"| {b} | {p:+.1f}% | ${v:,.0f} | {last} |")
    L.append("")

    L.append(f"## Top {args.top} gainers (24h, liquid)")
    L += ["| coin | 24h % | volume |", "|---|---|---|"]
    for b, p, v, _ in gainers:
        L.append(f"| {b} | {p:+.1f}% | ${v:,.0f} |")
    L.append("")

    L.append(f"## Top {args.top} losers (24h, liquid)")
    L += ["| coin | 24h % | volume |", "|---|---|---|"]
    for b, p, v, _ in losers:
        L.append(f"| {b} | {p:+.1f}% | ${v:,.0f} |")
    L.append("")

    if frows:
        L.append("## Positioning — funding-rate extremes (perps)")
        L.append("_High positive = crowded longs (over-eager, squeeze risk down); "
                 "high negative = crowded shorts (squeeze risk up). Positioning, not a signal._")
        L += ["", "| crowded LONGS (funding) | crowded SHORTS (funding) |", "|---|---|"]
        for i in range(max(len(pos_fund), len(neg_fund))):
            lft = f"{pos_fund[i][0]} {pos_fund[i][1]*100:+.3f}%" if i < len(pos_fund) else ""
            rgt = f"{neg_fund[i][0]} {neg_fund[i][1]*100:+.3f}%" if i < len(neg_fund) else ""
            L.append(f"| {lft} | {rgt} |")
        L.append("")

    L.append("---")
    L.append("_v1: movers + volume + funding + sentiment. Planned: on-chain exchange "
             "in/outflows (DefiLlama/explorers), token-unlock & listing calendar "
             "(CoinMarketCal), news headlines, sector/narrative rotation._")

    report = "\n".join(L)
    out = ROOT / "reports" / f"market_intel_{date.today().isoformat()}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(report, encoding="utf-8")

    # console summary
    print(f"\n=== Market Intelligence Brief ({now}) ===")
    if fng:
        print(f"Sentiment: {fng.get('value')}/100 {fng.get('value_classification')}")
    print("Top movers (24h, liquid): " + ", ".join(f"{b} {p:+.0f}%" for b, p, _, _ in gainers[:5]))
    print("Worst (24h):              " + ", ".join(f"{b} {p:+.0f}%" for b, p, _, _ in losers[:5]))
    print("Most crowded longs:       " + ", ".join(f"{b}" for b, _ in pos_fund[:5]))
    print(f"\nFull brief written to: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
