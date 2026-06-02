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


def _get_json(url, timeout=20):
    req = urllib.request.Request(url, headers={"User-Agent": "crypto-research/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def _fear_greed():
    d = _get_json("https://api.alternative.me/fng/?limit=1", timeout=15)
    return d["data"][0]


def _defillama_chains():
    """Total DeFi TVL + top chains by TVL (where on-chain capital sits). Free, no key."""
    data = _get_json("https://api.llama.fi/v2/chains")
    chains = sorted(((d.get("name"), float(d.get("tvl") or 0)) for d in data),
                    key=lambda x: x[1], reverse=True)
    total = sum(t for _, t in chains)
    return total, chains[:8]


def _defillama_stablecoins():
    """Total stablecoin circulating supply (USD) — 'dry powder' on the sidelines. Free."""
    data = _get_json("https://stablecoins.llama.fi/stablecoins?includePrices=false")
    total = 0.0
    for a in data.get("peggedAssets", []):
        total += float((a.get("circulating") or {}).get("peggedUSD") or 0)
    return total


def _coingecko_categories():
    """Crypto sectors ranked by 24h market-cap change (narrative rotation). Free, no key."""
    data = _get_json("https://api.coingecko.com/api/v3/coins/categories", timeout=25)
    cats = [(c.get("name"), c.get("market_cap_change_24h"))
            for c in data if c.get("market_cap_change_24h") is not None]
    cats.sort(key=lambda x: x[1], reverse=True)
    return cats


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
    total_tvl, top_chains = _safe(_defillama_chains, (0.0, []), "defillama_chains")
    stable_total = _safe(_defillama_stablecoins, 0.0, "defillama_stables")
    cats = _safe(_coingecko_categories, [], "coingecko_categories")

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

    if top_chains:
        L.append("## Macro / on-chain — where DeFi capital sits")
        head = f"**Total DeFi TVL:** ${total_tvl:,.0f}"
        if stable_total:
            head += f"  |  **Stablecoin supply (sideline dry powder):** ${stable_total:,.0f}"
        L += [head, "", "| chain | TVL (USD) |", "|---|---|"]
        for name, tvl in top_chains:
            L.append(f"| {name} | ${tvl:,.0f} |")
        L.append("")

    if cats:
        hot = cats[:8]
        cold = cats[-5:][::-1]
        L.append("## Narrative rotation — sectors by 24h market-cap change")
        L += ["| hot sectors (24h) | cold sectors (24h) |", "|---|---|"]
        for i in range(max(len(hot), len(cold))):
            lft = f"{hot[i][0]} {hot[i][1]:+.1f}%" if i < len(hot) else ""
            rgt = f"{cold[i][0]} {cold[i][1]:+.1f}%" if i < len(cold) else ""
            L.append(f"| {lft} | {rgt} |")
        L.append("")

    L.append("---")
    L.append("_Sources (all public/free): Binance (movers/volume/funding), "
             "alternative.me (Fear & Greed), DefiLlama (TVL + stablecoin supply), "
             "CoinGecko (sector rotation). Descriptive awareness, NOT a trade signal. "
             "Planned: news headlines + daily auto-run._")

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
    if cats:
        print("Hot narratives (24h):     " + ", ".join(f"{n} {p:+.0f}%" for n, p in cats[:5]))
    if top_chains:
        print(f"DeFi TVL: ${total_tvl:,.0f}  | top chain: {top_chains[0][0]} (${top_chains[0][1]:,.0f})")
    print(f"\nFull brief written to: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
