"""Crypto Market Intelligence Brief — DESCRIPTIVE market awareness (NOT a buy signal).

Aggregates PUBLIC market data into a readable "where is the action right now"
snapshot you can run anytime (no Claude session needed):
  - regime: Fear & Greed, BTC/ETH dominance, total-mcap 24h, market breadth, ETH/BTC
  - "what changed since last brief" delta (state vars + set-membership)
  - 24h volume leaders, top gainers / losers
  - closed-bar momentum (1h/4h/24h) for today's top gainers
  - funding-rate extremes (crowded long / short positioning — crypto-only & liquid)
  - DeFi TVL by chain + total stablecoin circulating supply (with 7d/30d trend)
  - narrative rotation (sectors by 24h mcap change)
  - recent headlines (context)

HONEST SCOPE: descriptive — shows what is ALREADY moving. Public data is priced in,
so it does NOT predict what WILL move; it is for awareness/research, not a trade
signal. Runs ~every 4h via the TradingBot-MarketIntel task: appends a compact
snapshot to data/market_intel_history.jsonl (durable history) and writes the latest
human-readable brief to reports/market_intel_<date>.md. Exchange net-flows are
intentionally omitted — there is no free, honest source.

Usage:
    python scripts/market_intel_report.py
    python scripts/market_intel_report.py --min-vol 10000000 --top 10
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.request
from datetime import date, datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

HISTORY = ROOT / "data" / "market_intel_history.jsonl"
_FAILED: list[str] = []  # labels whose fetch raised — surfaced honestly in the footer


def _safe(fn, default, label=""):
    try:
        return fn()
    except Exception as e:
        print(f"  [warn] {label}: {str(e)[:100]}")
        if label:
            _FAILED.append(label)
        return default


def _get_json(url, timeout=20, retries=2):
    last = None
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "crypto-research/1.0"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode())
        except Exception as e:
            last = e
            if attempt < retries:
                time.sleep(1 + attempt)
    raise last


def _fear_greed():
    return _get_json("https://api.alternative.me/fng/?limit=1", timeout=15)["data"][0]


def _global_market():
    """BTC/ETH dominance + total-mcap 24h change (regime). Free, no key."""
    d = _get_json("https://api.coingecko.com/api/v3/global", timeout=20).get("data", {})
    mcp = d.get("market_cap_percentage", {}) or {}
    return {
        "btc_dom": mcp.get("btc"),
        "eth_dom": mcp.get("eth"),
        "mcap_24h": d.get("market_cap_change_percentage_24h_usd"),
    }


def _defillama_chains():
    data = _get_json("https://api.llama.fi/v2/chains")
    chains = sorted(
        ((d.get("name"), float(d.get("tvl") or 0)) for d in data), key=lambda x: x[1], reverse=True
    )
    return sum(t for _, t in chains), chains[:8]


def _defillama_stablecoins():
    """Total stablecoin circulating supply (USD) + 7d/30d trend. Free, no key."""
    data = _get_json("https://stablecoins.llama.fi/stablecoincharts/all")

    def _val(pt):
        for k in ("totalCirculating", "totalCirculatingUSD"):
            v = (pt.get(k) or {}).get("peggedUSD")
            if v is not None:
                return float(v)
        return None

    series = [v for v in (_val(p) for p in data) if v is not None]
    if not series:
        return None
    cur = series[-1]
    d7 = (cur / series[-8] - 1) * 100 if len(series) >= 8 and series[-8] else None
    d30 = (cur / series[-31] - 1) * 100 if len(series) >= 31 and series[-31] else None
    return {"total": cur, "d7": d7, "d30": d30}


def _coingecko_categories():
    data = _get_json("https://api.coingecko.com/api/v3/coins/categories", timeout=25)
    cats = [
        (c.get("name"), c.get("market_cap_change_24h"))
        for c in data
        if c.get("market_cap_change_24h") is not None
    ]
    cats.sort(key=lambda x: x[1], reverse=True)
    return cats


def _news_headlines(limit=8):
    """Recent crypto headlines from free RSS (namespace-tolerant: RSS item + Atom entry)."""
    import xml.etree.ElementTree as ET

    feeds = [
        ("CoinDesk", "https://www.coindesk.com/arc/outboundfeeds/rss/"),
        ("Cointelegraph", "https://cointelegraph.com/rss"),
    ]
    out, per = [], max(1, limit // len(feeds) + 1)
    for src, url in feeds:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "crypto-research/1.0"})
            with urllib.request.urlopen(req, timeout=15) as r:
                root = ET.fromstring(r.read())
            titles = []
            for el in root.iter():
                if el.tag.split("}")[-1] in ("item", "entry"):
                    for ch in el.iter():
                        if ch.tag.split("}")[-1] == "title" and ch.text:
                            titles.append(ch.text.strip())
                            break
            if not titles:
                print(f"  [warn] news {src}: 0 titles parsed (feed format change?)")
            out += [(src, t) for t in titles[:per]]
        except Exception as e:
            print(f"  [warn] news {src}: {str(e)[:80]}")
            continue
    return out[:limit]


def _multi_tf_change(ex, base):
    """1h/4h/24h % change from COMPLETED 1h candles (drops the in-progress bar)."""
    o = ex.fetch_ohlcv(f"{base}/USDT", "1h", limit=26)
    o = (o or [])[:-1]  # drop the still-forming current bar -> closed bars only
    closes = [c[4] for c in o if c and c[4]]
    if len(closes) < 25:
        return None
    last = closes[-1]

    def pct(n):
        ref = closes[-1 - n]
        return (last / ref - 1) * 100 if ref else 0.0

    return pct(1), pct(4), pct(24)


def _prev_snapshot():
    try:
        if HISTORY.exists():
            lines = HISTORY.read_text(encoding="utf-8").strip().splitlines()
            if lines:
                return json.loads(lines[-1])
    except Exception:
        pass
    return None


def _build_delta(prev, fng, glob, total_tvl, stable, byvol):
    """'What changed since last brief' — state-variable deltas + set-membership changes."""
    if not prev:
        return []
    items = []
    pv, cv = prev.get("fng_value"), (fng or {}).get("value")
    if pv and cv and str(pv) != str(cv):
        items.append(
            f"Fear & Greed {pv} → {cv} "
            f"({prev.get('fng_class')} → {(fng or {}).get('value_classification')})"
        )
    pb, cb = prev.get("btc_dom"), (glob or {}).get("btc_dom")
    if pb and cb and abs(cb - pb) >= 0.05:
        items.append(f"BTC dominance {pb:.1f}% → {cb:.1f}% ({cb - pb:+.1f}pp)")
    pt = prev.get("total_tvl")
    if pt and total_tvl:
        items.append(
            f"DeFi TVL ${pt:,.0f} → ${total_tvl:,.0f} ({100 * (total_tvl / pt - 1):+.1f}%)"
        )
    ps, cs = prev.get("stable_total"), (stable or {}).get("total")
    if ps and cs and abs(cs / ps - 1) >= 0.0005:
        items.append(f"Stablecoin supply {100 * (cs / ps - 1):+.2f}%")
    prev_mv = set(prev.get("top_movers") or [])
    cur_mv = {b for b, _, _, _ in byvol}
    new_in, dropped = cur_mv - prev_mv, prev_mv - cur_mv
    if new_in:
        items.append("New in top-volume: " + ", ".join(sorted(new_in)))
    if dropped:
        items.append("Dropped from top-volume: " + ", ".join(sorted(dropped)))
    if not items:
        return []
    return (
        ["## What changed since last brief", f"_(prior brief: {prev.get('ts_utc', '?')})_", ""]
        + [f"- {it}" for it in items]
        + [""]
    )


def main() -> int:
    try:  # Windows consoles default to cp1252 and crash on non-ASCII tickers
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--min-vol", type=float, default=5_000_000.0, help="min 24h quote volume (USDT)"
    )
    ap.add_argument("--top", type=int, default=10)
    args = ap.parse_args()

    prev = _prev_snapshot()

    import ccxt

    print("Connecting to Binance (public data)...")
    ex = ccxt.binance({"enableRateLimit": True, "timeout": 30000})
    binance_ok = True
    try:
        for attempt in range(2):
            try:
                ex.load_markets()
                break
            except Exception:
                if attempt == 0:
                    time.sleep(2)
                else:
                    raise
    except Exception as e:
        print(f"  [warn] Binance load_markets failed: {str(e)[:100]}")
        _FAILED.append("Binance markets/movers")
        binance_ok = False

    tickers = _safe(lambda: ex.fetch_tickers(), {}, "Binance tickers") if binance_ok else {}
    rows = []
    for sym, t in (tickers or {}).items():
        m = (ex.markets or {}).get(sym, {})
        if m.get("quote") != "USDT" or not m.get("spot"):
            continue
        pct, qv = t.get("percentage"), t.get("quoteVolume") or 0
        if pct is None or qv < args.min_vol:
            continue
        rows.append((sym.replace("/USDT", ""), float(pct), float(qv), t.get("last")))

    gainers = sorted(rows, key=lambda r: r[1], reverse=True)[: args.top]
    losers = sorted(rows, key=lambda r: r[1])[: args.top]
    byvol = sorted(rows, key=lambda r: r[2], reverse=True)[: args.top]
    liquid_bases = {r[0] for r in rows}
    breadth = (sum(1 for r in rows if r[1] > 0), len(rows)) if rows else None
    px = {r[0]: r[3] for r in rows}
    ethbtc = (px["ETH"] / px["BTC"]) if (px.get("ETH") and px.get("BTC")) else None

    mtf = {}
    if binance_ok:
        for b, _, _, _ in gainers[:6]:
            res = _safe(lambda b=b: _multi_tf_change(ex, b), None, f"mtf {b}")
            if res:
                mtf[b] = res

    fund = _safe(lambda: ex.fetch_funding_rates(), {}, "Binance funding") if binance_ok else {}
    frows = []
    for s, f in (fund or {}).items():
        base = s.split("/")[0].split(":")[0]
        fr = f.get("fundingRate")
        if fr is not None and base in liquid_bases:  # crypto-only AND liquid
            frows.append((base, fr))
    pos_fund = sorted([r for r in frows if r[1] > 0], key=lambda r: r[1], reverse=True)[:8]
    neg_fund = sorted([r for r in frows if r[1] < 0], key=lambda r: r[1])[:8]

    fng = _safe(_fear_greed, None, "Fear & Greed")
    glob = _safe(_global_market, None, "global market")
    total_tvl, top_chains = _safe(_defillama_chains, (0.0, []), "DeFi TVL")
    stable = _safe(_defillama_stablecoins, None, "stablecoin supply")
    cats = _safe(_coingecko_categories, [], "sector rotation")
    news = _safe(_news_headlines, [], "news")

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    L = [
        f"# Crypto Market Intelligence Brief — {now}",
        "",
        "_Descriptive snapshot of what is ALREADY moving. Public data is priced in — "
        "this is for awareness/research, NOT a prediction or trade signal._",
        "",
    ]

    reg = []
    if fng:
        reg.append(f"**Fear & Greed:** {fng.get('value')}/100 — {fng.get('value_classification')}")
    if glob and glob.get("btc_dom") is not None:
        line = (
            f"**BTC dom:** {glob['btc_dom']:.1f}% | **ETH dom:** {(glob.get('eth_dom') or 0):.1f}%"
        )
        if glob.get("mcap_24h") is not None:
            line += f" | **total mcap 24h:** {glob['mcap_24h']:+.1f}%"
        reg.append(line)
    if breadth:
        reg.append(
            f"**Breadth:** {breadth[0]}/{breadth[1]} liquid pairs green "
            f"({100 * breadth[0] / breadth[1]:.0f}%)"
        )
    if ethbtc:
        reg.append(f"**ETH/BTC:** {ethbtc:.5f}")
    if reg:
        L += reg + [""]

    L += _build_delta(prev, fng, glob, total_tvl, stable, byvol)

    if byvol:
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
    else:
        L += [
            "## Market movers",
            "_Binance movers/volume unavailable this run (source error or rate limit)._",
            "",
        ]

    if mtf:
        L.append("## Closed-bar momentum — TODAY'S TOP GAINERS across 1h / 4h / 24h")
        L.append(
            "_Selection is gainers-only (not a neutral scan). Completed-bar % — may "
            "differ from the rolling-24h ticker above._"
        )
        L += ["", "| coin | 1h | 4h | 24h (candle) |", "|---|---|---|---|"]
        for b, _, _, _ in gainers[:6]:
            if b in mtf:
                h1, h4, h24 = mtf[b]
                L.append(f"| {b} | {h1:+.1f}% | {h4:+.1f}% | {h24:+.1f}% |")
        L.append("")

    if frows:
        L.append("## Positioning — funding-rate extremes (perps, crypto-only & liquid)")
        L.append(
            "_Positive funding = longs pay shorts (crowded longs, squeeze-down risk); "
            "negative = crowded shorts (squeeze-up risk). Positioning, not a signal._"
        )
        L += ["", "| crowded LONGS (funding) | crowded SHORTS (funding) |", "|---|---|"]
        rng = max(len(pos_fund), len(neg_fund), 1)
        for i in range(rng):
            lft = f"{pos_fund[i][0]} {pos_fund[i][1] * 100:+.3f}%" if i < len(pos_fund) else ""
            if i < len(neg_fund):
                rgt = f"{neg_fund[i][0]} {neg_fund[i][1] * 100:+.3f}%"
            else:
                rgt = "(none — all funding positive)" if i == 0 else ""
            L.append(f"| {lft} | {rgt} |")
        L.append("")

    if top_chains:
        L.append("## Macro / on-chain — where DeFi capital sits")
        head = f"**Total DeFi TVL:** ${total_tvl:,.0f}"
        if stable:
            trend = []
            if stable.get("d7") is not None:
                trend.append(f"7d {stable['d7']:+.1f}%")
            if stable.get("d30") is not None:
                trend.append(f"30d {stable['d30']:+.1f}%")
            head += f"  |  **Total stablecoin circulating supply:** ${stable['total']:,.0f}"
            if trend:
                head += f" ({', '.join(trend)})"
        L += [head, "", "| chain | TVL (USD) |", "|---|---|"]
        for name, tvl in top_chains:
            L.append(f"| {name} | ${tvl:,.0f} |")
        L.append("")

    if cats:
        hot, cold = cats[:8], cats[-5:][::-1]
        L.append("## Narrative rotation — sectors by 24h market-cap change")
        L += ["| hot sectors (24h) | cold sectors (24h) |", "|---|---|"]
        for i in range(max(len(hot), len(cold))):
            lft = f"{hot[i][0]} {hot[i][1]:+.1f}%" if i < len(hot) else ""
            rgt = f"{cold[i][0]} {cold[i][1]:+.1f}%" if i < len(cold) else ""
            L.append(f"| {lft} | {rgt} |")
        L.append("")

    if news:
        L.append("## Recent headlines (context — not confirmed cause)")
        for src, title in news:
            L.append(f"- _{src}_ — {title}")
        L.append("")

    if _FAILED:
        L.append(
            f"> ⚠ Sources unavailable this run (fetch failed): {', '.join(sorted(set(_FAILED)))}"
        )
        L.append("")

    L.append("---")
    L.append(
        "_Sources (public/free): Binance (movers/volume/funding/candles), alternative.me "
        "(Fear & Greed), CoinGecko (global + sector rotation), DefiLlama (TVL + stablecoin "
        "supply), CoinDesk/Cointelegraph RSS (news). Descriptive awareness, NOT a trade "
        "signal. Runs ~every 4h via TradingBot-MarketIntel; durable history in "
        "data/market_intel_history.jsonl. Exchange net-flows omitted — no free honest source._"
    )

    report = "\n".join(L)
    out = ROOT / "reports" / f"market_intel_{date.today().isoformat()}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(report, encoding="utf-8")

    snap = {
        "ts_utc": now,
        "fng_value": (fng or {}).get("value"),
        "fng_class": (fng or {}).get("value_classification"),
        "btc_dom": (glob or {}).get("btc_dom"),
        "eth_dom": (glob or {}).get("eth_dom"),
        "total_tvl": total_tvl or None,
        "stable_total": (stable or {}).get("total"),
        "breadth_green": breadth[0] if breadth else None,
        "breadth_total": breadth[1] if breadth else None,
        "top_movers": [b for b, _, _, _ in byvol],
        "gainers": [b for b, _, _, _ in gainers],
        "losers": [b for b, _, _, _ in losers],
        "pos_fund": [b for b, _ in pos_fund],
        "neg_fund": [b for b, _ in neg_fund],
        "failed": sorted(set(_FAILED)),
    }
    try:
        HISTORY.parent.mkdir(parents=True, exist_ok=True)
        with open(HISTORY, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(snap) + "\n")
    except Exception as e:
        print(f"  [warn] history append: {str(e)[:80]}")

    print(f"\n=== Market Intelligence Brief ({now}) ===")
    print("(descriptive market awareness — public data is priced in; NOT a trade signal)")
    if fng:
        print(f"Sentiment: {fng.get('value')}/100 {fng.get('value_classification')}")
    if glob and glob.get("btc_dom") is not None:
        print(f"BTC dom: {glob['btc_dom']:.1f}% | total mcap 24h: {glob.get('mcap_24h')}")
    if breadth:
        print(f"Breadth: {breadth[0]}/{breadth[1]} green ({100 * breadth[0] / breadth[1]:.0f}%)")
    if gainers:
        print("Top movers (24h): " + ", ".join(f"{b} {p:+.0f}%" for b, p, _, _ in gainers[:5]))
    if cats:
        print("Hot narratives: " + ", ".join(f"{n} {p:+.0f}%" for n, p in cats[:5]))
    if _FAILED:
        print("Unavailable this run: " + ", ".join(sorted(set(_FAILED))))
    print(f"\nFull brief written to: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
