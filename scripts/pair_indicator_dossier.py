"""Per-pair indicator & data-coverage dossier — MEASUREMENT ONLY (not a trade signal).

Thin IO shell over `core.pair_dossier`. For every symbol in the bot's
spec-derived probe universe it records (a) the technical-indicator POSTURE at
the latest CLOSED 4h bar and (b) the LOCAL data coverage, then classifies each
pair's DATA READINESS (DATA_OK / PARTIAL / BACKFILL_FIRST). It makes NO strategy
claims and NO edge assertions: the verdict is about whether the data exists and
is fresh, never about direction.

All math, coverage probes and the verdict live in `core/pair_dossier.py`
(promoted verbatim from `_workspace/strategy_pipeline/27_pair_indicator_dossier.py`,
the 2026-07-23 provenance run). This file only fetches, renders and writes.

Read-only everywhere: public ccxt bybit endpoints (no keys), warehouse.sqlite
opened mode=ro, parquet/CSV reads. Nothing under data/ is written; outputs go to
reports/pair_indicator_dossier_<date>.{md,json}.

Usage:
    venv\\Scripts\\python.exe scripts/pair_indicator_dossier.py
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import date, datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.pair_dossier import (  # noqa: E402
    MIN_CLOSED_4H,
    STALE_1H_MAX_S,
    aggtrades_symbols,
    classify_verdict,
    compute_indicators,
    funding_coverage,
    l2_symbols,
    parquet_coverage,
    resolve_bot_universe,
    warehouse_candidates_by_base,
)

_FAILED: list[str] = []  # labels whose probe raised — surfaced honestly in the footer


def _safe(fn, default, label=""):
    try:
        return fn()
    except Exception as e:
        print(f"  [warn] {label}: {str(e)[:100]}")
        if label:
            _FAILED.append(label)
        return default


def build_doc(ex, symbols: list, skipped: list) -> dict:
    """Fetch + assemble the dossier document (same schema as the 27_ artifact)."""
    now_s = time.time()
    wh = _safe(
        lambda: warehouse_candidates_by_base([s.split("/", 1)[0] for s in symbols]),
        {},
        "warehouse candidates",
    )
    l2 = _safe(l2_symbols, None, "l2_history")
    agg = _safe(aggtrades_symbols, None, "aggtrades_qh")

    records, fetch_failures = [], []
    for i, sym in enumerate(symbols, 1):
        base = sym.split("/", 1)[0]
        rec = {"symbol": sym, "base": base}
        # live fetch (probe-agent pattern: ccxt fetch_ohlcv, limit=300)
        bars4, bars1, err4, err1 = None, None, None, None
        try:
            bars4 = ex.fetch_ohlcv(sym, "4h", limit=300)
        except Exception as exc:
            err4 = f"{type(exc).__name__}: {exc}"
        try:
            bars1 = ex.fetch_ohlcv(sym, "1h", limit=300)
        except Exception as exc:
            err1 = f"{type(exc).__name__}: {exc}"
        if err4 or err1:
            fetch_failures.append({"symbol": sym, "4h": err4, "1h": err1})
        ind, n_closed = (None, 0)
        if bars4:
            ind, n_closed = compute_indicators(bars4)
        rec["live_fetch"] = {
            "bars_4h": len(bars4) if bars4 else 0,
            "closed_4h_used": n_closed,
            "bars_1h": len(bars1) if bars1 else 0,
            "err_4h": err4,
            "err_1h": err1,
        }
        rec["indicators_4h"] = ind
        # local coverage
        cov1h = parquet_coverage(base, "1h", now_s)
        cov4h = parquet_coverage(base, "4h", now_s)
        fund = funding_coverage(base)
        rec["coverage"] = {
            "ohlcv_cache_1h": cov1h,
            "ohlcv_cache_4h": cov4h,
            "funding_history": fund,
            "warehouse_candidates": (wh or {}).get(base),
            "orderbook_depth": (
                "l2_history.jsonl" if (l2 and any(base in s for s in l2)) else "ABSENT"
            ),
            "trades": ("aggtrades_qh" if (agg and any(base in s for s in agg)) else "ABSENT"),
            "onchain": "ABSENT",
        }
        # verdict — DATA READINESS ONLY, never indicator direction
        verdict, reasons = classify_verdict(
            rec["coverage"],
            bars_computable=ind is not None,
            live_1h_ok=err1 is None,
            n_bars_4h=len(bars4) if bars4 else 0,
        )
        rec["verdict"] = verdict
        rec["verdict_reasons"] = reasons
        records.append(rec)
        print(f"[{i}/{len(symbols)}] {sym}: {verdict} (closed4h={n_closed})")

    counts = {
        v: sum(1 for r in records if r["verdict"] == v)
        for v in ("DATA_OK", "PARTIAL", "BACKFILL_FIRST")
    }
    return {
        "generated_utc": datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "purpose": "per-pair indicator posture + data coverage; MEASUREMENT ONLY, "
        "no strategy claims, no edge assertions",
        "universe": {
            "source": "core.agents.bundle_mr_probe_agent.resolve_universe(venue='bybit') "
            "on data/strategy_specs (MCP_DIRECTIONAL_PAPER active-paper spec), "
            "perp_available mirroring bot_engine._venue_perp_available",
            "count": len(symbols),
            "skipped_bases": skipped,
        },
        "global_data_notes": {
            "warehouse_raw_ohlcv": "ABSENT — warehouse.sqlite has no OHLCV bars table; "
            "local bars live in data/ohlcv_cache/*.parquet",
            "orderbook_depth_store": {"file": "data/l2_history.jsonl", "symbols_seen": l2},
            "trades_store": {"dir": "data/aggtrades_qh", "symbols": agg},
            "onchain_store": "ABSENT — no on-chain artifacts found under data/",
            "feed_persistence": "core/data_feeds/orderbook_depth_feed.py and "
            "smart_money_feed.py persist NOTHING to disk "
            "(in-memory/HTTP only); l2_history.jsonl and "
            "aggtrades_qh come from the separate harvesters "
            "scripts/harvest_l2.py and "
            "scripts/harvest_binance_aggtrades_qh.py",
            "news_sentiment_store": "global only (data/news_cache.json, "
            "sentiment_history.json) — not per-symbol",
        },
        "verdict_counts": counts,
        "fetch_failures": fetch_failures,
        "local_probe_failures": sorted(set(_FAILED)),
        "pairs": records,
    }


def render_md(doc: dict) -> str:
    lines = [
        "# Per-Pair Indicator & Data Coverage Dossier",
        "",
        f"Generated: {doc['generated_utc']}  |  MEASUREMENT ONLY — no strategy claims, "
        "no edge assertions. Verdicts are data-readiness only.",
        "",
        f"Universe: {doc['universe']['count']} bybit USDT-perps, derived exactly as the "
        "bot does (active-paper MCP_DIRECTIONAL_PAPER spec -> approved bybit routes -> "
        f"live-perp filter). Skipped bases: {', '.join(doc['universe']['skipped_bases']) or 'none'}.",
        "",
        "| Symbol | Close (4h) | vs SMA20/50/200 | 50v200 | MACD hist | RSI14 | BB %B / bw pctl | "
        "PSAR | Supertrend | Vol20 (z) | KDJ K/D/J | OBV | W%R | 1h cache (stale d) | "
        "4h cache (stale d) | Funding (bybit) | Wh cand | Depth/Trades | Verdict |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r in doc["pairs"]:
        ind, cov = r.get("indicators_4h"), r["coverage"]
        if ind:
            smarel = "/".join(
                (ind["sma"][f"price_vs_sma{n}"] or "?")[0].upper() for n in (20, 50, 200)
            )
            bb = ind.get("bollinger") or {}
            vol = ind.get("volume_20") or {}
            kdjv = ind.get("kdj_9_3_3") or {}
            ind_cells = [
                f"{ind['close']:g}",
                smarel,
                ind["sma"]["sma50_vs_sma200"] or "?",
                ind["macd"]["hist_sign"],
                f"{ind['rsi14']}",
                f"{bb.get('pct_b')} / {bb.get('bandwidth_pctl')}",
                ind.get("psar_side") or "?",
                ind.get("supertrend_10_3") or "?",
                f"{vol.get('trend', '?')} ({vol.get('zscore', '?')})",
                f"{kdjv.get('k')}/{kdjv.get('d')}/{kdjv.get('j')}",
                ind.get("obv_slope_20") or "?",
                f"{ind.get('williams_r14')}",
            ]
        else:
            ind_cells = ["FETCH_FAIL"] + ["-"] * 11
        c1, c4 = cov["ohlcv_cache_1h"], cov["ohlcv_cache_4h"]
        fmt_c = lambda c: (
            f"{c['rows']} ({c['staleness_days']})"
            if c["present"]  # noqa: E731
            and c.get("rows") is not None
            else "ABSENT"
        )
        fb = cov["funding_history"].get("bybit", {})
        wh = cov.get("warehouse_candidates") or {}
        lines.append(
            "| "
            + " | ".join(
                [r["symbol"].replace("/USDT:USDT", "")]
                + ind_cells
                + [
                    fmt_c(c1),
                    fmt_c(c4),
                    (f"{fb.get('rows')} rows" if fb.get("present") else "ABSENT"),
                    str(wh.get("rows", 0)),
                    f"{'D' if cov['orderbook_depth'] != 'ABSENT' else '-'}/"
                    f"{'T' if cov['trades'] != 'ABSENT' else '-'}",
                    f"**{r['verdict']}**",
                ]
            )
            + " |"
        )
    counts = doc["verdict_counts"]
    n = len(doc["pairs"])
    bf = [r for r in doc["pairs"] if r["verdict"] == "BACKFILL_FIRST"]
    missing_fund = [r["base"] for r in bf if not r["verdict_reasons"]["bybit_funding_csv"]]
    stale_1h = [r["base"] for r in bf if not r["verdict_reasons"]["local_1h_fresh"]]
    no_live = [r["base"] for r in bf if not r["verdict_reasons"]["live_4h_computable"]]
    lines += [
        "",
        "Legend: vs SMA cells A=above B=below; Depth/Trades: D = order-book depth rows "
        "exist in l2_history.jsonl, T = aggtrades_qh parquet exists, '-' = ABSENT. "
        "Wh cand = warehouse `candidates` rows for the base (warehouse stores NO raw OHLCV).",
        "",
        "## Honest coverage summary",
        "",
        f"- Pairs measured: {n}. Fully computable live (>= {MIN_CLOSED_4H} closed 4h bars "
        f"fetched, all indicators computed): "
        f"{sum(1 for r in doc['pairs'] if r.get('indicators_4h'))}/{n}.",
        f"- Verdicts: DATA_OK {counts['DATA_OK']}, PARTIAL {counts['PARTIAL']}, "
        f"BACKFILL_FIRST {counts['BACKFILL_FIRST']}.",
        f"- Missing bybit funding-history CSV ({len(missing_fund)}): "
        f"{', '.join(missing_fund) or 'none'}.",
        f"- Local 1h ohlcv_cache absent or stale > {STALE_1H_MAX_S // 86400}d "
        f"({len(stale_1h)}): {', '.join(stale_1h) or 'none'}.",
        f"- Live 4h fetch not computable ({len(no_live)}): {', '.join(no_live) or 'none'}.",
        f"- Fetch failures this run: {len(doc['fetch_failures'])} "
        f"({', '.join(f['symbol'] for f in doc['fetch_failures']) or 'none'}).",
        "- Order-book depth artifacts: only symbols listed in global_data_notes "
        "(l2_history.jsonl harvest) — ABSENT for all other pairs. Trades artifacts: "
        "BTC/ETH quarter-hour pilot only. On-chain artifacts: ABSENT for all pairs.",
        "- The runtime feeds core/data_feeds/orderbook_depth_feed.py and "
        "smart_money_feed.py persist NOTHING to disk (in-memory/HTTP only); the "
        "depth/trades artifacts above come from the separate harvesters "
        "scripts/harvest_l2.py and scripts/harvest_binance_aggtrades_qh.py.",
        "- Warehouse.sqlite stores NO raw OHLCV bars; per-pair bar depth/staleness above "
        "is from data/ohlcv_cache parquet files (last bar timestamp, not file mtime).",
    ]
    if doc.get("local_probe_failures"):
        lines.append(
            f"- ⚠ Local coverage probes unavailable this run: "
            f"{', '.join(doc['local_probe_failures'])} — the affected columns read as "
            "ABSENT/0 for every pair and must NOT be read as real absence."
        )
    lines += [
        "",
        "This feeds the standing backfill remediation from the 18_ pair-verdict dossier: "
        "the funding-CSV and stale-1h lists above are the concrete backfill queue.",
    ]
    return "\n".join(lines)


def main() -> int:
    try:  # Windows consoles default to cp1252 and crash on non-ASCII output
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    argparse.ArgumentParser(description=__doc__.splitlines()[0]).parse_args()

    import ccxt

    print("Connecting to Bybit (public data, no keys)...")
    ex = ccxt.bybit({"enableRateLimit": True, "options": {"defaultType": "swap"}})
    markets = ex.load_markets()  # fail loudly: the universe cannot be derived without it
    symbols, skipped = resolve_bot_universe(markets)  # raises if the frozen basket fired
    print(f"universe: {len(symbols)} symbols (skipped bases: {skipped})")

    doc = build_doc(ex, symbols, skipped)

    out_dir = ROOT / "reports"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = date.today().isoformat()
    out_json = out_dir / f"pair_indicator_dossier_{stamp}.json"
    out_md = out_dir / f"pair_indicator_dossier_{stamp}.md"
    out_json.write_text(json.dumps(doc, indent=1, default=str), encoding="utf-8")
    out_md.write_text(render_md(doc), encoding="utf-8")

    counts = doc["verdict_counts"]
    print(f"wrote {out_json}\nwrote {out_md}")
    print(f"counts={counts} fetch_failures={len(doc['fetch_failures'])}")
    if _FAILED:
        print("Local probes unavailable this run: " + ", ".join(sorted(set(_FAILED))))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
