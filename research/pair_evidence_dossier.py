"""Per-pair evidence dossier for the 2026-07-22 dual-model FUTURES verdict run.

Phase-2 MEASUREMENT ONLY: reads existing local stores and aggregates them into
one JSON object per pair of the MCP_DIRECTIONAL_PAPER universe. No backtests,
no strategy claims, no writes to any data store. Missing data => null + flag.

Sources (all read-only):
  data/strategy_specs/MCP_DIRECTIONAL_PAPER.json  -- universe + venue routes
  data/warehouse.sqlite (mode=ro)                 -- closed futures trades
  data/heartbeat.json                             -- paper profile + epoch
  _workspace/strategy_pipeline/13_band_outcome_cache.json -- 14,555 resolved
      band outcomes (2026-07-12 screen, frac 0.35, overwhelmingly binance)
  data/ohlcv_cache/{BASE}-USDT_1h.parquet         -- 1h OHLCV coverage + cost proxy
  data/funding_history/{venue}_{BASE}.csv         -- funding coverage per venue
  core/pair_discovery.py (ast-parsed, not imported) -- ANALYSIS_ONLY/tradfi sets
  .env                                            -- INCIDENT_QUARANTINE_BASES
  config.py (ast-parsed, not imported)            -- FEE + SLIPPAGE constants
  core/strategy_spec.py (imported read-only)      -- approved route helper

Outputs:
  _workspace/strategy_pipeline/18_pair_dossier.json
  _workspace/strategy_pipeline/18_pair_dossier.md

Usage:
  venv/Scripts/python.exe research/pair_evidence_dossier.py [--selftest]
"""

from __future__ import annotations

import ast
import datetime as dt
import json
import sqlite3
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC_PATH = ROOT / "data" / "strategy_specs" / "MCP_DIRECTIONAL_PAPER.json"
WAREHOUSE = ROOT / "data" / "warehouse.sqlite"
HEARTBEAT = ROOT / "data" / "heartbeat.json"
BAND_CACHE = ROOT / "_workspace" / "strategy_pipeline" / "13_band_outcome_cache.json"
OHLCV_DIR = ROOT / "data" / "ohlcv_cache"
FUNDING_DIR = ROOT / "data" / "funding_history"
ENV_FILE = ROOT / ".env"
OUT_JSON = ROOT / "_workspace" / "strategy_pipeline" / "18_pair_dossier.json"
OUT_MD = ROOT / "_workspace" / "strategy_pipeline" / "18_pair_dossier.md"

VENUES = ("binance", "bybit", "bitget")
BAND_CACHE_FRAC = 0.35  # research/screen_band_conditional.py FRAC (frozen)
COST_WINDOW_BARS = 720  # ~30d of 1h bars for the cost proxy
RECENT_WINDOW_DAYS = 14

# Bookkeeping rows, not directional engine decisions (see trades.strategy_family).
NON_DIRECTIONAL_FAMILIES = ("reconcile", "reconciled_exchange", "manual")

# Static probe membership (mirrors the probe agents' frozen constants).
TSMOM_BASES = {"BTC", "ETH", "SOL"}  # core/agents/tsmom_probe_agent.py SYMBOLS
BREAKOUT_BASES = {  # core/agents/breakout_probe_agent.py SYMBOLS (10 majors)
    "BTC", "ETH", "SOL", "XRP", "BNB", "ADA", "DOGE", "LINK", "AVAX", "DOT",
}
# Runtime bybit-perp skip observed at the 2026-07-20 boot (journal/2026-07-20.md
# 22:35Z: bundle-MR universe 43, "skipped only FET, no bybit perp"). Static
# mirror only -- this script never hits exchanges.
BUNDLE_MR_RUNTIME_SKIPPED = {"FET"}


# ── pure aggregation helpers (selftest targets) ──────────────────────────────
def agg_trades(rows):
    """rows: iterable of (realized_pnl, fee). Returns count/WR/net/fees.

    WR counts realized_pnl > 0 as a win; rows with realized_pnl None are
    excluded from the WR denominator but counted in n_rows.
    """
    n_rows, wins, resolved, net, fees = 0, 0, 0, 0.0, 0.0
    for pnl, fee in rows:
        n_rows += 1
        fees += float(fee or 0.0)
        if pnl is None:
            continue
        resolved += 1
        net += float(pnl)
        if pnl > 0:
            wins += 1
    return {
        "n": n_rows,
        "wr": round(wins / resolved, 4) if resolved else None,
        "net_pnl_usd": round(net, 4),
        "fees_usd": round(fees, 4),
    }


def agg_band(records):
    """records: iterable of dicts with net_pnl + r_multiple (band cache rows)."""
    pnls = [float(r["net_pnl"]) for r in records]
    rs = [float(r["r_multiple"]) for r in records]
    n = len(pnls)
    if not n:
        return None
    return {
        "n": n,
        "wr": round(sum(1 for p in pnls if p > 0) / n, 4),
        "mean_r": round(statistics.fmean(rs), 4),
        "mean_net_pnl_usd": round(statistics.fmean(pnls), 4),
        "frac": BAND_CACHE_FRAC,
    }


def span_days(ts_values):
    """ts_values: iterable of unix seconds. Returns (days, first_iso, last_iso)."""
    ts = [float(t) for t in ts_values]
    if not ts:
        return None
    lo, hi = min(ts), max(ts)
    return (
        round((hi - lo) / 86400.0, 1),
        _iso(lo),
        _iso(hi),
    )


def median_move_bps(opens, closes):
    """Median 1h |close-open|/close in bps over paired arrays."""
    moves = [
        abs(c - o) / c * 10_000.0
        for o, c in zip(opens, closes)
        if c and c > 0
    ]
    return round(statistics.median(moves), 2) if moves else None


def _iso(ts: float) -> str:
    return dt.datetime.fromtimestamp(ts, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ── source loaders ───────────────────────────────────────────────────────────
def load_spec():
    spec = json.loads(SPEC_PATH.read_text(encoding="utf-8"))
    return spec


def load_heartbeat_profile():
    hb = json.loads(HEARTBEAT.read_text(encoding="utf-8"))
    epoch = float(hb["paper_profile_started_at"])
    return {
        "profile": hb.get("paper_trading_profile"),
        "epoch": epoch,
        "epoch_utc": _iso(epoch),
    }


def _ast_literal_assigns(py_path: Path, names: set[str]) -> dict:
    """Extract literal (set/dict) assignments by name from a module WITHOUT
    importing it. Non-literal assignments for a requested name are skipped."""
    tree = ast.parse(py_path.read_text(encoding="utf-8"))
    out = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        for tgt in node.targets:
            if isinstance(tgt, ast.Name) and tgt.id in names:
                try:
                    out[tgt.id] = ast.literal_eval(node.value)
                except (ValueError, SyntaxError):
                    pass
    return out


def load_disabled_base_sets():
    got = _ast_literal_assigns(
        ROOT / "core" / "pair_discovery.py",
        {"_COMMODITY_BASES_DISABLED", "_STOCK_BASES_DISABLED"},
    )
    commodity = {str(b).upper() for b in got.get("_COMMODITY_BASES_DISABLED", set())}
    stock = {str(b).upper() for b in got.get("_STOCK_BASES_DISABLED", set())}
    return commodity, stock


def load_cost_constants():
    got = _ast_literal_assigns(ROOT / "config.py", {"FEE", "SLIPPAGE"})
    fee = got.get("FEE") or {}
    slip = got.get("SLIPPAGE") or {}
    fee_side = float(fee.get("futures_taker", 0.0005))  # binance futures taker
    slip_side = float(slip.get("pct_open", 0.0005))  # sim model, 5 bps/side
    return {
        "fee_side_frac": fee_side,
        "slip_side_frac": slip_side,
        "roundtrip_cost_bps": round((fee_side + slip_side) * 2 * 10_000.0, 2),
        "provenance": "config.py FEE['futures_taker'] + SLIPPAGE['pct_open'] "
        "x2 sides (binance taker; band cache is 14,551/14,555 binance)",
    }


def load_quarantine_bases():
    """INCIDENT_QUARANTINE_BASES from .env (comma separated, may be absent)."""
    if not ENV_FILE.exists():
        return set(), "env_file_missing"
    for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.startswith("INCIDENT_QUARANTINE_BASES="):
            raw = line.split("=", 1)[1]
            return {b.strip().upper() for b in raw.split(",") if b.strip()}, ".env"
    return set(), ".env (key absent)"


def load_routes(spec):
    """base -> sorted venue list via core.strategy_spec helpers; falls back to
    the spec JSON's venues array (source flagged) if the import fails."""
    try:
        sys.path.insert(0, str(ROOT))
        from core.strategy_spec import approved_paper_futures_routes, load_all_specs

        specs = load_all_specs()
        if specs.errors:
            raise ValueError(f"spec parse errors: {'; '.join(specs.errors)}")
        routes = approved_paper_futures_routes(specs)
        return {b: sorted(v) for b, v in routes.items()}, "core.strategy_spec"
    except Exception as exc:  # fall back, never fabricate
        venues = sorted(spec.get("venues", []))
        bases = [s.split("/")[0] for s in spec.get("symbols", [])]
        return {b: venues for b in bases}, f"spec_json_fallback ({exc})"


def warehouse_symbol_stats(symbols, profile_epoch, now_ts):
    """Per-symbol closed-futures directional aggregates from warehouse.sqlite."""
    con = sqlite3.connect(f"file:{WAREHOUSE.as_posix()}?mode=ro", uri=True)
    try:
        cur = con.cursor()
        placeholders = ",".join("?" for _ in NON_DIRECTIONAL_FAMILIES)
        base_sql = (
            "SELECT realized_pnl, fee FROM trades "
            "WHERE status='CLOSED' AND market_type='futures' "
            f"AND (strategy_family IS NULL OR strategy_family NOT IN ({placeholders})) "
            "AND symbol=?"
        )
        out = {}
        recent_cut = now_ts - RECENT_WINDOW_DAYS * 86400
        for sym in symbols:
            args = list(NON_DIRECTIONAL_FAMILIES) + [sym]
            all_rows = cur.execute(base_sql, args).fetchall()
            cohort_rows = cur.execute(
                base_sql + " AND ts_entry >= ?", args + [profile_epoch]
            ).fetchall()
            recent_rows = cur.execute(
                base_sql + " AND ts_entry >= ?", args + [recent_cut]
            ).fetchall()
            out[sym] = {
                "all_time": agg_trades(all_rows),
                "profile_cohort": agg_trades(cohort_rows),
                "recent_14d": agg_trades(recent_rows),
            }
        return out
    finally:
        con.close()


def band_symbol_stats(symbols):
    cache = json.loads(BAND_CACHE.read_text(encoding="utf-8"))
    resolved = cache.get("resolved", [])
    by_sym = {}
    for rec in resolved:
        by_sym.setdefault(rec["symbol"], []).append(rec)
    return {sym: agg_band(by_sym.get(sym, [])) for sym in symbols}, len(resolved)


def ohlcv_coverage_and_cost(base, roundtrip_cost_bps):
    """1h parquet span + tail cost proxy. Returns (coverage|None, cost|None, flags)."""
    flags = []
    path = OHLCV_DIR / f"{base}-USDT_1h.parquet"
    if not path.exists():
        return None, None, [f"ohlcv_1h_missing:{path.name}"]
    import pandas as pd

    df = pd.read_parquet(path, columns=["ts", "open", "close"])
    if df.empty:
        return None, None, [f"ohlcv_1h_empty:{path.name}"]
    days, first, last = span_days(df["ts"].tolist())
    coverage = {"days": days, "rows": int(len(df)), "first_utc": first, "last_utc": last}
    tail = df.tail(COST_WINDOW_BARS)
    move = median_move_bps(tail["open"].tolist(), tail["close"].tolist())
    cost = None
    if move is not None:
        cost = {
            "median_1h_move_bps": move,
            "roundtrip_cost_bps": roundtrip_cost_bps,
            "move_to_cost_ratio": round(move / roundtrip_cost_bps, 3)
            if roundtrip_cost_bps
            else None,
            "window_bars": int(len(tail)),
        }
    stale_days = (dt.datetime.now(dt.timezone.utc).timestamp() - float(df["ts"].iloc[-1])) / 86400
    if stale_days > 7:
        flags.append(f"ohlcv_1h_stale:{round(stale_days, 1)}d")
    return coverage, cost, flags


def funding_coverage(base):
    """Per-venue funding CSV span. Missing file => null + flag."""
    import pandas as pd

    out, flags = {}, []
    for venue in VENUES:
        path = FUNDING_DIR / f"{venue}_{base}.csv"
        if not path.exists():
            out[venue] = None
            flags.append(f"funding_missing:{venue}")
            continue
        try:
            ts = pd.read_csv(path, usecols=["ts"])["ts"]
        except Exception as exc:
            out[venue] = None
            flags.append(f"funding_unreadable:{venue}:{type(exc).__name__}")
            continue
        if ts.empty:
            out[venue] = None
            flags.append(f"funding_empty:{venue}")
            continue
        days, first, last = span_days(ts.tolist())
        out[venue] = {"days": days, "first_utc": first, "last_utc": last, "rows": int(len(ts))}
    return out, flags


# ── selftest (TDD-lite: synthetic rows, no real stores touched) ──────────────
def selftest():
    # 1. trade aggregation: 3 rows, one win, one loss, one unresolved (None pnl)
    t = agg_trades([(2.0, 0.1), (-1.0, 0.2), (None, 0.3)])
    assert t["n"] == 3 and t["wr"] == 0.5, t
    assert abs(t["net_pnl_usd"] - 1.0) < 1e-9 and abs(t["fees_usd"] - 0.6) < 1e-9, t

    # 2. band aggregation: 4 records, 3 wins -> WR 0.75, mean_r exact
    b = agg_band(
        [
            {"net_pnl": 1.0, "r_multiple": 0.5},
            {"net_pnl": 2.0, "r_multiple": 1.0},
            {"net_pnl": -1.0, "r_multiple": -0.5},
            {"net_pnl": 0.5, "r_multiple": 0.25},
        ]
    )
    assert b["n"] == 4 and b["wr"] == 0.75 and abs(b["mean_r"] - 0.3125) < 1e-9, b
    assert agg_band([]) is None

    # 3. span math: exactly 2 days between endpoints regardless of order
    days, first, last = span_days([1_700_172_800, 1_700_000_000])
    assert days == 2.0 and first == "2023-11-14T22:13:20Z", (days, first, last)

    # 4. cost proxy: |close-open|/close median in bps
    m = median_move_bps([100.0, 100.0, 100.0], [101.0, 99.0, 100.0])
    # moves: 1/101*1e4=99.01, 1/99*1e4=101.01, 0 -> median 99.01
    assert m == 99.01, m
    print("selftest: 4/4 assertions passed")


# ── main ─────────────────────────────────────────────────────────────────────
def build():
    now_ts = dt.datetime.now(dt.timezone.utc).timestamp()
    spec = load_spec()
    symbols = list(spec["symbols"])
    hb = load_heartbeat_profile()
    cost_model = load_cost_constants()
    commodity, stock = load_disabled_base_sets()
    quarantine, quarantine_src = load_quarantine_bases()
    routes, route_source = load_routes(spec)

    wh = warehouse_symbol_stats(symbols, hb["epoch"], now_ts)
    band, band_total = band_symbol_stats(symbols)

    pairs = {}
    for sym in symbols:
        base = sym.split("/")[0]
        cov_ohlcv, cost, flags = ohlcv_coverage_and_cost(
            base, cost_model["roundtrip_cost_bps"]
        )
        cov_funding, f_flags = funding_coverage(base)
        flags += f_flags
        if band.get(sym) is None:
            flags.append("band_cache_no_outcomes")

        venues = routes.get(base, [])
        bundle_member = "bybit" in venues and base not in BUNDLE_MR_RUNTIME_SKIPPED
        entry = {
            "symbol": sym,
            "base": base,
            "warehouse": {
                **wh[sym],
                "cohort_profile": hb["profile"],
                "cohort_epoch_utc": hb["epoch_utc"],
            },
            "band_cache": band.get(sym),
            "coverage": {"ohlcv_1h": cov_ohlcv, "funding": cov_funding},
            "routing": {
                "venues": venues,
                "source": route_source,
                "quarantined": base in quarantine,
                "analysis_only_tradfi": base in commodity or base in stock,
            },
            "probes": {
                "bundle_mr_zfade_rsi2": bundle_member,
                "bundle_mr_note": (
                    "runtime-skipped at 2026-07-20 boot: no live bybit USDT-perp"
                    if base in BUNDLE_MR_RUNTIME_SKIPPED
                    else None
                ),
                "tsmom": base in TSMOM_BASES,
                "breakout_60d": base in BREAKOUT_BASES,
                "listing_unlock": "event_driven_na",
            },
            "cost_proxy": cost,
            "flags": flags,
        }
        pairs[sym] = entry

    doc = {
        "generated_utc": _iso(now_ts),
        "script": "research/pair_evidence_dossier.py",
        "universe_spec": str(SPEC_PATH.relative_to(ROOT)),
        "n_pairs": len(pairs),
        "heartbeat_profile": hb,
        "band_cache_total_resolved": band_total,
        "band_cache_frac": BAND_CACHE_FRAC,
        "cost_model": cost_model,
        "quarantine_source": quarantine_src,
        "quarantine_bases": sorted(quarantine),
        "non_directional_families_excluded": list(NON_DIRECTIONAL_FAMILIES),
        "pairs": pairs,
    }
    return doc


def write_md(doc):
    hb = doc["heartbeat_profile"]
    lines = [
        "# 18 — Per-Pair Evidence Dossier (2026-07-22)",
        "",
        "MEASUREMENT of existing local data for the Phase-3 dual-model per-pair",
        "verdicts. No backtests, no strategy claims. Every number is reproducible",
        "via `venv/Scripts/python.exe research/pair_evidence_dossier.py`.",
        "",
        "## Schema (per pair, in 18_pair_dossier.json)",
        "",
        "```",
        "warehouse.all_time / .profile_cohort / .recent_14d:",
        "  n, wr (share realized_pnl>0), net_pnl_usd, fees_usd — CLOSED futures",
        "  trades, strategy_family NOT IN " + str(doc["non_directional_families_excluded"]),
        "  profile_cohort = ts_entry >= heartbeat paper_profile_started_at",
        "band_cache: n, wr, mean_r, mean_net_pnl_usd at frac "
        + str(doc["band_cache_frac"])
        + " (13_band_outcome_cache.json, 2026-07-12 screen)",
        "coverage.ohlcv_1h: parquet span days/rows; coverage.funding.<venue>: CSV span days",
        "routing: spec-approved venues, quarantine (.env), ANALYSIS_ONLY/tradfi sets",
        "probes: bundle_mr_zfade_rsi2 (spec x bybit, static mirror), tsmom, breakout_60d",
        "cost_proxy: median 1h |close-open|/close bps over last "
        + str(COST_WINDOW_BARS)
        + " bars vs roundtrip "
        + str(doc["cost_model"]["roundtrip_cost_bps"])
        + " bps",
        "```",
        "",
        f"Heartbeat profile at generation: **{hb['profile']}**, epoch {hb['epoch_utc']}.",
        "",
        "## Table",
        "",
        "| pair | AT n | AT WR | AT netPnL$ | band n | band WR | band meanR | 1h days |"
        " fund b/y/g d | probes | move/cost | flags |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for sym, p in doc["pairs"].items():
        at = p["warehouse"]["all_time"]
        bc = p["band_cache"]
        cov = p["coverage"]
        f = cov["funding"]

        def fd(v):
            return str(int(v["days"])) if v else "—"

        probes = []
        if p["probes"]["bundle_mr_zfade_rsi2"]:
            probes.append("MR")
        if p["probes"]["tsmom"]:
            probes.append("TSM")
        if p["probes"]["breakout_60d"]:
            probes.append("BRK")
        cp = p["cost_proxy"]
        lines.append(
            "| {} | {} | {} | {} | {} | {} | {} | {} | {}/{}/{} | {} | {} | {} |".format(
                sym.split("/")[0],
                at["n"],
                f"{at['wr']:.2f}" if at["wr"] is not None else "—",
                f"{at['net_pnl_usd']:.1f}",
                bc["n"] if bc else "—",
                f"{bc['wr']:.3f}" if bc else "—",
                f"{bc['mean_r']:.3f}" if bc else "—",
                int(cov["ohlcv_1h"]["days"]) if cov["ohlcv_1h"] else "—",
                fd(f.get("binance")),
                fd(f.get("bybit")),
                fd(f.get("bitget")),
                "+".join(probes) or "—",
                f"{cp['move_to_cost_ratio']:.2f}" if cp else "—",
                "; ".join(p["flags"]) or "—",
            )
        )
    lines += [
        "",
        "## Caveats (binding)",
        "",
        f"- **Profile-cohort is empty for every pair.** The heartbeat profile is "
        f"`{hb['profile']}` with epoch {hb['epoch_utc']} (reset at the last bot "
        "boot). Zero trades have ts_entry after that epoch, so no MAX_FLOW_BAND "
        "cohort is derivable from the current heartbeat field. `recent_14d` is "
        "provided as the objective recent-activity window instead.",
        "- Band-cache outcomes were resolved at frac "
        + str(doc["band_cache_frac"])
        + " by the 2026-07-12 screen and are 14,551/14,555 binance — treat as "
        "binance-only. Band WR is GEOMETRY, not edge; every screen-13 bucket was "
        "after-cost negative.",
        "- Cost proxy uses binance futures taker ("
        + str(doc["cost_model"]["fee_side_frac"])
        + "/side) + sim slippage ("
        + str(doc["cost_model"]["slip_side_frac"])
        + "/side) => "
        + str(doc["cost_model"]["roundtrip_cost_bps"])
        + " bps roundtrip; a median 1h bar move below ~1x this roundtrip means "
        "1h-scale geometry is structurally cost-dominated on that pair.",
        "- Probe membership is a STATIC mirror of the agents' frozen constants + "
        "spec routes; the FET bybit runtime skip is sourced from the 2026-07-20 "
        "boot log, not re-verified against exchanges (this script never hits "
        "exchange APIs).",
        "- Warehouse WR/netPnL aggregates directional CLOSED futures trades across "
        "ALL historical engines/profiles (claude, claude_portfolio, algo, "
        "algo_det, systematic_v3_1, ...) — an honest all-history measure, NOT a "
        "current-engine performance claim.",
        "- listing/unlock probes are event-driven (n/a per pair).",
        "",
        f"Generated {doc['generated_utc']} by {doc['script']}; universe "
        f"{doc['universe_spec']} (n={doc['n_pairs']}).",
        "",
    ]
    OUT_MD.write_text("\n".join(lines), encoding="utf-8")


def main():
    if "--selftest" in sys.argv:
        selftest()
        return
    doc = build()
    OUT_JSON.write_text(json.dumps(doc, indent=1), encoding="utf-8")
    write_md(doc)
    n_gap = sum(1 for p in doc["pairs"].values() if p["flags"])
    print(f"wrote {OUT_JSON} and {OUT_MD}: {doc['n_pairs']} pairs, {n_gap} with flags")


if __name__ == "__main__":
    main()
