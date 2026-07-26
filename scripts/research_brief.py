"""Per-Asset Research Brief — DESCRIPTIVE record of stored data (not advice).

Thin IO shell over `core.research_brief`. It gathers only what this repository
already stores — local parquet bars, funding CSVs, the unlock calendar, the
open-interest CSVs, the news cache, the harvest artifacts and the warehouse —
assembles one dated, sourced record per asset, and writes
`reports/research_brief_<date>.{md,json}`.

Every number in the output is a Finding: FACT (measured), INFERENCE (derived
from named FACTs of the same asset) or ABSENT (a record of a missing input with
the command that would fix it). It makes no market claim and no directional
statement, it never reaches an order path, and it makes zero language-model
calls — the assembly is deterministic Python.

Modes:
    (default)          offline batch over the bot's spec-derived probe universe.
                       Local artifacts only. DataCoordinator is deliberately NOT
                       used here: its refresh() caps at the first 15 coins, so
                       assets 16-N would silently never populate.
    --symbol BASE      one asset; also reads the persisted per-coin sentiment map.
    --refresh          additionally pull closed 4h bars from public keyless ccxt
                       bybit for the assets in scope (slow; no keys, read-only).

Read-only everywhere: warehouse.sqlite is opened mode=ro, nothing under data/ is
written, and outputs go to reports/.

Usage:
    venv\\Scripts\\python.exe scripts/research_brief.py
    venv\\Scripts\\python.exe scripts/research_brief.py --symbol BTC
    venv\\Scripts\\python.exe scripts/research_brief.py --symbol BTC --refresh
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sqlite3
import sys
import time
from datetime import date, datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core import research_brief as rb  # noqa: E402
from core.pair_dossier import (  # noqa: E402
    compute_indicators,
    funding_coverage,
    parquet_coverage,
)

DATA = ROOT / "data"
_FAILED: list[str] = []  # labels whose probe raised — surfaced honestly in the footer


def _safe(fn, default, label=""):
    try:
        return fn()
    except Exception as e:  # noqa: BLE001 — an unattended report must degrade, not crash
        print(f"  [warn] {label}: {str(e)[:120]}")
        if label:
            _FAILED.append(label)
        return default


def _intel_module():
    """Load scripts/run_intel_synthesis.py by path (it is a script, not a package)."""
    path = ROOT / "scripts" / "run_intel_synthesis.py"
    spec = importlib.util.spec_from_file_location("_rb_intel", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ── local readers ────────────────────────────────────────────────────────────
def resolve_universe_offline() -> tuple[list[str], list[str]]:
    """The bot's spec-derived probe universe, WITHOUT the live-perp filter.

    `core.pair_dossier.resolve_bot_universe` needs ccxt-loaded markets to answer
    `perp_available`; offline every answer would be False and the resolver would
    fail-closed to the frozen 5-major basket. So the offline batch passes the
    liveness probe as satisfied and DISCLOSES that in universe.source. The
    hard-fail on a tiny universe is kept: a missing active-paper spec must be
    loud, never a silent 5-asset brief.
    """
    from core.agents.bundle_mr_probe_agent import resolve_universe

    symbols, skipped = resolve_universe(venue="bybit", perp_available=lambda _s: True)
    if not len(symbols) > 5:
        raise RuntimeError(
            f"probe universe fell back to the frozen basket ({list(symbols)}); "
            "the active-paper spec is missing — refusing to emit a misleading brief"
        )
    return list(symbols), list(skipped)


def read_parquet_bars(base: str, tf: str, now: datetime) -> list | None:
    """Closed bars from the local cache as ccxt rows [ts_ms, o, h, l, c, v]."""
    import pandas as pd

    p = DATA / "ohlcv_cache" / f"{base}-USDT_{tf}.parquet"
    if not p.exists():
        return None
    df = pd.read_parquet(p)
    cols = {c.lower(): c for c in df.columns}
    need = ("ts", "open", "high", "low", "close", "volume")
    if not all(k in cols for k in need):
        return None
    bar_s = 4 * 3600 if tf == "4h" else 3600
    cutoff = now.timestamp() - bar_s
    rows = []
    for r in df[[cols[k] for k in need]].itertuples(index=False):
        ts = float(r[0])
        if ts > 1e11:
            ts /= 1000.0
        if ts > cutoff:
            continue
        rows.append([ts * 1000.0, float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[5])])
    return rows[-300:] or None


def fetch_bars_ccxt(ex, symbol: str) -> list | None:
    """Public keyless bybit 4h bars (only under --refresh)."""
    bars = ex.fetch_ohlcv(symbol, "4h", limit=300)
    return bars or None


def atr_last(bars: list) -> float | None:
    """Latest ATR(14) via the repo's own helper. Never calls the regime classifier."""
    import pandas as pd

    from core.market_regime import _atr

    if not bars or len(bars) < 20:
        return None
    h = pd.Series([b[2] for b in bars], dtype=float)
    low = pd.Series([b[3] for b in bars], dtype=float)
    c = pd.Series([b[4] for b in bars], dtype=float)
    v = _atr(h, low, c, 14)
    val = float(v.iloc[-1])
    return val if val == val else None  # NaN-safe


def warehouse_vol(bases: list[str], now: datetime) -> dict:
    """Latest + 30d sample of `atr_pct_1h` per base from candidates (mode=ro)."""
    out = {b: {"latest": None, "as_of": None, "sample": []} for b in bases}
    path = DATA / "warehouse.sqlite"
    if not path.exists():
        return out
    cutoff = now.timestamp() - 30 * 86400
    con = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    try:
        for base in bases:
            rows = con.execute(
                "select ts, features_json from candidates "
                "where symbol like ? and ts >= ? order by ts desc limit 2000",
                (f"{base}/%", cutoff),
            ).fetchall()
            sample = []
            for ts, fj in rows:
                try:
                    feats = rb.project_feed("warehouse_features", json.loads(fj or "{}"))
                except Exception:  # noqa: BLE001 — a malformed row must not poison the sample
                    continue
                v = feats.get("atr_pct_1h")
                if v is None:
                    continue
                sample.append(float(v))
                if out[base]["latest"] is None:
                    out[base]["latest"] = {"atr_pct_1h": float(v)}
                    out[base]["as_of"] = rb.stamp_from_epoch(ts)
            out[base]["sample"] = sample
    finally:
        con.close()
    return out


def l2_last_hours() -> dict:
    """base -> last harvested hour stamp, from the tail of data/l2_history.jsonl."""
    p = DATA / "l2_history.jsonl"
    if not p.exists():
        return {}
    with p.open("rb") as f:
        f.seek(0, 2)
        f.seek(max(0, f.tell() - 400_000))
        tail = f.read().decode("utf-8", errors="ignore").splitlines()[1:]
    out: dict[str, float] = {}
    for line in tail:
        try:
            rec = json.loads(line)
        except Exception:  # noqa: BLE001
            continue
        sym, hour = rec.get("symbol"), rec.get("hour")
        if not sym or hour is None:
            continue
        base = str(sym).split("/")[0].split(":")[0].upper()
        out[base] = max(out.get(base, 0.0), float(hour))
    return {b: rb.stamp_from_epoch(t) for b, t in out.items()}


def aggtrades_last_stamps() -> dict:
    import pandas as pd

    out: dict[str, str] = {}
    d = DATA / "aggtrades_qh"
    if not d.exists():
        return out
    for p in sorted(d.glob("*_qh_events.parquet")):
        base = p.stem.replace("_qh_events", "").replace("USDT", "").strip("_-")
        try:
            df = pd.read_parquet(p)
        except Exception:  # noqa: BLE001
            continue
        for col in ("ts", "timestamp", "time", "open_time"):
            if col in df.columns and len(df):
                out[base.upper()] = rb.stamp_from_epoch(float(df[col].iloc[-1]))
                break
    return out


def news_snapshot() -> tuple[dict, dict, str | None]:
    """(mentions per base, per-coin sentiment map, fetch stamp) from the cache.

    Read-only. The scanner is NOT run: that would hit the network and rewrite
    files under data/, which the live PAPER bot owns. Headline TEXT is never
    copied into the brief — only per-base counts.
    """
    from core.news_scanner import _extract_coin_symbols

    p = DATA / "news_cache.json"
    if not p.exists():
        return {}, {}, None
    cache = json.loads(p.read_text(encoding="utf-8"))
    stamp = cache.get("fetched_at")
    try:
        stamp = datetime.fromisoformat(str(stamp)).strftime("%Y-%m-%dT%H:%MZ")
    except (ValueError, TypeError):
        stamp = None
    mentions: dict[str, int] = {}
    for art in cache.get("news") or []:
        syms = _extract_coin_symbols(
            art.get("title", ""), art.get("categories", "") or "", art.get("tags") or []
        )
        for s in syms:
            mentions[s.upper()] = mentions.get(s.upper(), 0) + 1
    sentiment = {str(k).upper(): v for k, v in (cache.get("sentiment") or {}).items()}
    return mentions, sentiment, stamp


def unlock_artifact(base: str) -> dict | None:
    p = DATA / "unlock_calendar" / f"{base}.json"
    if not p.exists():
        return None
    return json.loads(p.read_text(encoding="utf-8"))


def funding_rows(base: str) -> list:
    from core.funding_history import load_recent_realized_settlements

    for limit in (21, 6, 1):
        rows = load_recent_realized_settlements("bybit", base, limit=limit)
        if rows:
            return [(s.settlement_ts, s.rate) for s in rows]
    return []


def oi_last_stamp(base: str) -> str | None:
    import csv as _csv

    p = DATA / "funding_oi" / f"{base}_oi.csv"
    if not p.exists():
        return None
    with p.open(encoding="utf-8") as fh:
        rows = list(_csv.DictReader(fh))
    if not rows:
        return None
    return rb.stamp_from_epoch(float(rows[-1]["timestamp"]))


# ── assembly ─────────────────────────────────────────────────────────────────
def gather_asset(symbol: str, base: str, *, now: datetime, ex=None, shared: dict) -> object:
    bars = None
    src = None
    if ex is not None:
        bars = _safe(lambda: fetch_bars_ccxt(ex, symbol), None, f"ccxt 4h {base}")
        if bars:
            src = f"public bybit ccxt fetch_ohlcv({symbol}, 4h)"
    if bars is None:
        bars = _safe(lambda: read_parquet_bars(base, "4h", now), None, "")
        if bars:
            src = f"data/ohlcv_cache/{base}-USDT_4h.parquet"
    ind, _n = (None, 0)
    pctls = None
    atr = None
    if bars:
        ind, _n = _safe(lambda: compute_indicators(bars), (None, 0), f"indicators {base}")
        if ind:
            pctls = _safe(lambda: rb.compute_indicator_percentiles(bars), None, "")
            atr = _safe(lambda: atr_last(bars), None, "")

    coverage = {
        "ohlcv_cache_1h": parquet_coverage(base, "1h", now.timestamp()),
        "ohlcv_cache_4h": parquet_coverage(base, "4h", now.timestamp()),
        "funding_history": funding_coverage(base),
        "orderbook_depth": ("l2_history.jsonl" if base in shared["l2"] else "ABSENT"),
        "trades": ("aggtrades_qh" if base in shared["agg"] else "ABSENT"),
        "onchain": "ABSENT",
    }
    wh = shared["warehouse"].get(base) or {}
    return rb.AssetInputs(
        symbol=symbol,
        base=base,
        coverage=coverage,
        indicators=ind,
        bars_computable=ind is not None,
        live_1h_ok=True,
        n_bars_4h=len(bars) if bars else 0,
        price_source=src,
        indicator_pctls=pctls,
        atr_4h=atr,
        warehouse_features=wh.get("latest"),
        warehouse_as_of=wh.get("as_of"),
        warehouse_vol_sample=wh.get("sample") or [],
        funding_settlements=_safe(lambda: funding_rows(base), [], ""),
        oi=(shared["oi"] or {}).get(base),
        oi_as_of=_safe(lambda: oi_last_stamp(base), None, ""),
        unlock=_safe(lambda: unlock_artifact(base), None, ""),
        news_mentions=shared["news_mentions"].get(base),
        news_as_of=shared["news_stamp"] if base in shared["news_mentions"] else None,
        sentiment_map=shared["sentiment_map"],
        sentiment_as_of=shared["news_stamp"],
        l2_as_of=shared["l2"].get(base),
        aggtrades_as_of=shared["agg"].get(base),
    )


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="Per-asset research brief (descriptive only).")
    ap.add_argument(
        "--symbol",
        default=None,
        help="single BASE (e.g. BTC); also reads the persisted sentiment map",
    )
    ap.add_argument(
        "--refresh",
        action="store_true",
        help="pull closed 4h bars from public keyless ccxt bybit (slower)",
    )
    return ap


def main(argv: list[str] | None = None) -> int:
    try:  # Windows consoles default to cp1252
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass
    args = build_parser().parse_args(argv)
    now = datetime.now(tz=timezone.utc)
    t0 = time.time()

    symbols, skipped = resolve_universe_offline()
    universe_source = (
        "core.agents.bundle_mr_probe_agent.resolve_universe(venue='bybit') over the "
        "active-paper MCP_DIRECTIONAL_PAPER StrategySpec; offline the live-perp liveness "
        "filter is NOT applied, so this list can be wider than a live-resolved one"
    )
    if args.symbol:
        want = args.symbol.upper()
        match = [s for s in symbols if s.split("/", 1)[0].upper() == want]
        symbols = match or [f"{want}/USDT:USDT"]
        skipped = []
        if not match:
            universe_source += f"; {want} requested explicitly (outside the resolved universe)"
    mode = ("symbol" if args.symbol else "batch") + ("+refresh" if args.refresh else "")
    bases = [s.split("/", 1)[0] for s in symbols]
    print(f"universe: {len(symbols)} asset(s), mode={mode}")

    intel_mod = _safe(_intel_module, None, "run_intel_synthesis")
    market_intel = _safe(intel_mod.latest_market_intel, None, "market intel") if intel_mod else None
    market_stale = (
        _safe(lambda: intel_mod.brief_is_stale(market_intel, now), None, "")
        if (intel_mod and market_intel)
        else None
    )
    oi = (
        _safe(lambda: intel_mod.funding_oi_summary(bases), {}, "funding/OI CSVs")
        if intel_mod
        else {}
    )
    mentions, sentiment_map, news_stamp = _safe(news_snapshot, ({}, {}, None), "news cache")
    shared = {
        "l2": _safe(l2_last_hours, {}, "l2_history"),
        "agg": _safe(aggtrades_last_stamps, {}, "aggtrades_qh"),
        "warehouse": _safe(lambda: warehouse_vol(bases, now), {}, "warehouse candidates"),
        "oi": oi,
        "news_mentions": mentions,
        "news_stamp": news_stamp,
        # per-coin sentiment is per the plan a --symbol-mode input only
        "sentiment_map": sentiment_map if args.symbol else None,
    }

    ex = None
    if args.refresh:
        import ccxt

        print("connecting to bybit (public data, no keys)...")
        ex = ccxt.bybit({"enableRateLimit": True, "options": {"defaultType": "swap"}})
        _safe(ex.load_markets, None, "bybit markets")

    assets = []
    for i, sym in enumerate(symbols, 1):
        base = sym.split("/", 1)[0]
        inputs = gather_asset(sym, base, now=now, ex=ex, shared=shared)
        asset = rb.build_asset_brief(inputs, now=now)
        assets.append(asset)
        print(
            f"[{i}/{len(symbols)}] {base}: {asset['data_quality']['verdict']} "
            f"({asset['counts']['facts']} measured / {asset['counts']['absent']} absent)"
        )

    doc = rb.build_doc(
        assets=assets,
        now=now,
        mode=mode,
        universe_source=universe_source,
        skipped_bases=skipped,
        market_intel=market_intel,
        market_intel_stale=market_stale,
        diagnostics={
            "failed": sorted(set(_FAILED)),
            "elapsed_s": round(time.time() - t0, 1),
            "readiness_scope": (
                "data_quality describes the LOCAL artifacts (classify_verdict); under "
                "--refresh the price findings may instead come from the live public fetch "
                "named in their own source field"
                if args.refresh
                else "data_quality and every price finding describe the same local artifacts"
            ),
        },
    )

    out_dir = ROOT / "reports"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = date.today().isoformat()
    out_json = out_dir / f"research_brief_{stamp}.json"
    out_md = out_dir / f"research_brief_{stamp}.md"
    out_json.write_text(json.dumps(doc, indent=1, sort_keys=True), encoding="utf-8")
    out_md.write_text(rb.render_md(doc), encoding="utf-8")

    print(f"\nwrote {out_json}\nwrote {out_md}")
    print(f"readiness: {doc['verdict_counts']}")
    if _FAILED:
        print("Inputs unavailable this run: " + ", ".join(sorted(set(_FAILED))))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
