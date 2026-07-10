"""
research/scan_rsi_vol_replay.py — RSI+Volume confluence SCAN + production-engine
REPLAY + first-touch OUTCOME simulation, over an arbitrary --days window.

Owner request (verbatim):
  "Find all Futures coin pairs with RSI below 30 and volume +200% at the same time
   then replay 120 days and show me where this trading bot would have entered"

This is RESEARCH / situational intelligence only. It writes to reports/ and touches
no live code, config, or core state. See PART C honesty notes in the report:
RSI mean-reversion is a REFUTED family for this bot (NO_EDGE, 2026-06).

PART A — RSI+VOLUME CONFLUENCE SCAN
  Signal bar = RSI(14, closes) < 30  AND  bar volume >= 3.0x the trailing 20-bar
  mean volume (trailing = the 20 bars BEFORE the spike bar; "+200% vs baseline").
  Each event is hindsight-labeled with its +24h and +72h forward move.

PART B — BOT ENTRY REPLAY + OUTCOME (production-faithful)
  Replays the real scoring engine (backtest_v3.score_bar with the DistFitSL TP
  path). Entry = the engine's own rule (score >= 65, 4 required conditions).
  Reports the engine SL/TP AND the ACCURACY_TARGET_MODE geometry (tp = 0.5 x sl,
  floor 0.5%), then simulates each would-be entry's first-touch outcome against
  the forward candles under BOTH TP geometries via core.shadow_resolver.resolve_one
  (SL-first tie-break, 6bps/side taker fee + 5/10bps slippage). The headline is the
  would-be WIN RATE under both geometries, overall and per-month.

PART C — OVERLAP + HONESTY
  Cross-references A and B (same symbol within +/-6h). The engine's required RSI
  sweet-spot band (long 38-68 / short 32-62) intentionally REJECTS oversold bars,
  so little/no overlap is expected — measured and stated.

Data: reuses data/ohlcv_cache/*_1h.parquet (the bot's harvested futures universe,
history back to ~2023), freshened through the latest closed bar with a keyless ccxt
binanceusdm PAGINATED tail fetch (bridges the cache-end -> now gap; the cache tops
out ~2026-06-14 so 120d windows need ~26 days of freshening). Never synthesizes
prices. Symbols whose candles are unavailable are noted and skipped.

Re-runnable:  venv/Scripts/python.exe research/scan_rsi_vol_replay.py --days 120
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from utils.indicators import rsi as rsi_series  # noqa: E402

# ----------------------------------------------------------------------------
# Parameters (Part A signal definition — stated verbatim in the report)
# ----------------------------------------------------------------------------
RSI_PERIOD = 14
RSI_THRESHOLD = 30.0          # RSI(14) strictly below 30 = oversold
VOL_WINDOW = 20               # trailing baseline length (bars)
VOL_MULT = 3.0                # >= 3.0x baseline == +200% vs baseline
FWD_BARS_24H = 24             # 24h-later forward return (hindsight, informational)
FWD_BARS_72H = 72             # 72h-later forward return (hindsight, informational)
SCORE_MIN = 65               # engine entry threshold (backtest_v3 / mcp_brain)
OVERLAP_HOURS = 6            # Part C proximity window
ACCURACY_TP_FRAC_OF_SL = 0.45  # ACCURACY_TARGET_MODE geometry (0.45 x SL)
ACCURACY_TP_FLOOR_PCT = 0.5   # ACCURACY_TARGET_MODE floor (0.5%)
RESOLVE_HORIZON_BARS = 72     # first-touch outcome horizon (3 days of 1h bars)
RESOLVE_NOTIONAL = 1000.0     # nominal notional for net-PnL sign (WR only needs sign)
WARMUP_BUFFER_BARS = 420      # pre-window bars kept for indicator/4h warmup

CACHE_DIR = ROOT / "data" / "ohlcv_cache"


def _report_paths(days: int) -> tuple[Path, Path]:
    """Deliverable filenames are --days-tagged so 7d and 120d runs coexist."""
    stem = f"rsi_volume_scan_replay_{days}d_2026-07-10"
    return ROOT / "reports" / f"{stem}.md", ROOT / "reports" / f"{stem}.json"


# ----------------------------------------------------------------------------
# Pure signal functions (unit-tested in tests/test_scan_rsi_vol_replay.py)
# ----------------------------------------------------------------------------
def compute_rsi(closes: pd.Series, period: int = RSI_PERIOD) -> pd.Series:
    """RSI(14) via the bot's own utils.indicators.rsi (Wilder EMA)."""
    return rsi_series(closes, period)


def trailing_vol_ratio(volume: pd.Series, window: int = VOL_WINDOW) -> pd.Series:
    """bar volume / mean of the `window` bars BEFORE it (baseline excludes the
    spike bar). NaN until `window` prior bars exist. A value of 3.0 == +200%."""
    baseline = volume.shift(1).rolling(window).mean()
    return volume / baseline.replace(0, np.nan)


def confluence_mask(
    closes: pd.Series,
    volume: pd.Series,
    rsi_threshold: float = RSI_THRESHOLD,
    vol_mult: float = VOL_MULT,
) -> pd.Series:
    """Boolean Series: RSI(14) < threshold AND trailing vol ratio >= vol_mult."""
    r = compute_rsi(closes)
    vr = trailing_vol_ratio(volume)
    return (r < rsi_threshold) & (vr >= vol_mult)


def entry_barriers(
    side: str, close: float, sl_pct: float, tp_pct: float
) -> tuple[float, float]:
    """(sl_px, tp_px) for a signal at `close` given SL/TP as percentages.

    Pure + unit-tested. `side` is 'buy' or 'sell'. Longs stop below / target
    above; shorts the mirror. Used to feed shadow_resolver.resolve_one, which
    models its own entry/exit slippage off these decision-price barriers.
    """
    if side == "buy":
        return close * (1 - sl_pct / 100.0), close * (1 + tp_pct / 100.0)
    return close * (1 + sl_pct / 100.0), close * (1 - tp_pct / 100.0)


def accuracy_tp_pct(sl_pct: float) -> float:
    """Live ACCURACY_TARGET_MODE geometry: tp = 0.45 x SL, floored at 0.5%."""
    return max(ACCURACY_TP_FLOOR_PCT, sl_pct * ACCURACY_TP_FRAC_OF_SL)


# ----------------------------------------------------------------------------
# Data loading (cache + PAGINATED keyless freshening) — never synthesizes
# ----------------------------------------------------------------------------
def _cache_path(base: str) -> Path:
    return CACHE_DIR / f"{base}-USDT_1h.parquet"


def _paginated_tail(exchange, base: str, since_ms: int, cap_calls: int = 4) -> list:
    """Keyless ccxt 1h bars from `since_ms` up to now, paginating (<=1000/call).

    Bridges the cache-end -> now gap (~26 days for a 120d window). Returns raw
    [ms,o,h,l,c,v] rows; [] on any failure (caller keeps cache-only, noted)."""
    out: list = []
    cursor = since_ms
    try:
        for _ in range(cap_calls):
            raw = exchange.fetch_ohlcv(f"{base}/USDT", "1h", since=cursor, limit=1000)
            if not raw:
                break
            out.extend(raw)
            last = raw[-1][0]
            if last <= cursor or len(raw) < 1000:
                break
            cursor = last + 3_600_000  # next hour
    except Exception:
        return out  # partial tail is still useful
    return out


def load_symbol_df(base: str, exchange=None) -> tuple[pd.DataFrame | None, str]:
    """Load 1h OHLCV for `base`: cached history + keyless PAGINATED tail freshen.

    Returns (df, source_note). df has a naive-UTC DatetimeIndex and columns
    open/high/low/close/volume (+ ts seconds). None if no data available.
    """
    df = None
    src = "none"
    p = _cache_path(base)
    if p.exists():
        try:
            df = pd.read_parquet(p)
            src = "cache"
        except Exception:
            df = None

    # Freshen the tail through the latest closed bar via keyless ccxt pagination.
    if exchange is not None:
        if df is not None and len(df) and "ts" in df:
            since_ms = int(df["ts"].max()) * 1000  # from last cached bar
        else:
            # No cache: pull a generous keyless window (~60 days).
            since_ms = int(
                (datetime.now(timezone.utc) - timedelta(days=60)).timestamp() * 1000
            )
        raw = _paginated_tail(exchange, base, since_ms)
        if raw:
            tail = pd.DataFrame(
                raw, columns=["ms", "open", "high", "low", "close", "volume"]
            )
            tail["ts"] = (tail["ms"] // 1000).astype("int64")
            tail = tail[["ts", "open", "high", "low", "close", "volume"]]
            if df is None:
                df, src = tail, "keyless"
            else:
                df = pd.concat([df, tail], ignore_index=True)
                src = "cache+keyless"

    if df is None or len(df) == 0:
        return None, src

    df = df.drop_duplicates(subset="ts", keep="last").sort_values("ts").reset_index(drop=True)
    df = df.astype({"open": float, "high": float, "low": float, "close": float, "volume": float})
    idx = pd.to_datetime(df["ts"].to_numpy(), unit="s", utc=True)  # DatetimeIndex
    out = df.copy()
    out.index = idx.tz_localize(None)  # naive UTC (matches cache convention)
    return out, src


def _slice_for_window(df: pd.DataFrame, w0: datetime, buffer_bars: int) -> pd.DataFrame:
    """Trim to [w0 - buffer_bars*1h, end] so heavy indicator work stays cheap
    on a 120d window instead of the full ~3yr cache."""
    cutoff = pd.Timestamp(w0.replace(tzinfo=None)) - pd.Timedelta(hours=buffer_bars)
    return df[df.index >= cutoff]


# ----------------------------------------------------------------------------
# PART A — confluence scan (24h + 72h hindsight labels)
# ----------------------------------------------------------------------------
def scan_confluence(df: pd.DataFrame, w0: datetime, w1: datetime) -> list[dict]:
    closes = df["close"]
    volume = df["volume"]
    r = compute_rsi(closes)
    vr = trailing_vol_ratio(volume)
    mask = (r < RSI_THRESHOLD) & (vr >= VOL_MULT)
    hits = []
    idx = df.index
    n = len(df)
    for pos in np.where(mask.to_numpy())[0]:
        t = idx[pos]
        tpy = t.to_pydatetime()
        if not (w0 <= tpy.replace(tzinfo=timezone.utc) <= w1):
            continue
        c0 = float(closes.iloc[pos])

        def fwd(nbars: int, pos: int = pos, c0: float = c0) -> float | None:
            if pos + nbars < n and c0 > 0:
                return round((float(closes.iloc[pos + nbars]) - c0) / c0 * 100.0, 2)
            return None

        hits.append(
            {
                "bar_time": tpy.strftime("%Y-%m-%d %H:%M"),
                "month": tpy.strftime("%Y-%m"),
                "rsi": round(float(r.iloc[pos]), 1),
                "vol_ratio": round(float(vr.iloc[pos]), 2),
                "close": c0,
                "fwd_24h_pct": fwd(FWD_BARS_24H),
                "fwd_72h_pct": fwd(FWD_BARS_72H),
            }
        )
    return hits


# ----------------------------------------------------------------------------
# PART B — production engine replay + first-touch outcome (both geometries)
# ----------------------------------------------------------------------------
def replay_and_resolve(
    df: pd.DataFrame, full_symbol: str, w0: datetime, w1: datetime
) -> list[dict]:
    import backtest_v3 as bt
    from core.shadow_resolver import resolve_one

    d = df[["open", "high", "low", "close", "volume"]].copy()
    if not isinstance(d.index, pd.DatetimeIndex):
        d.index = pd.to_datetime(d.index)
    d1 = bt.compute_indicators(d)
    d4 = bt.resample_to_4h(d1)
    if len(d1) < 90 or len(d4) == 0:
        return []

    # searchsorted 4h lookup: most-recent CLOSED 4h bar per 1h decision bar
    # (audit 2026-07-10 finding A: `index <= ts` read a still-forming 4h bar —
    # up to 3h of future data — on 3 of 4 bars; closed means start <= ts-3h,
    # mirroring backtest_v3.closed_4h_bar).
    d4_ts = d4.index.to_numpy()
    d4_rows = [row.to_dict() for _, row in d4.iterrows()]

    ts_ms = (d1.index.astype("int64") // 1_000_000).to_numpy()
    o = d1["open"].to_numpy()
    h = d1["high"].to_numpy()
    lo = d1["low"].to_numpy()
    c = d1["close"].to_numpy()
    v = d1["volume"].to_numpy()

    w0n = pd.Timestamp(w0.replace(tzinfo=None))
    w1n = pd.Timestamp(w1.replace(tzinfo=None))
    d1_ts = d1.index.to_numpy()
    entries = []
    warmup = 60
    n = len(d1)
    for i in range(warmup, n):
        ts = d1_ts[i]
        if ts < w0n.to_datetime64() or ts > w1n.to_datetime64():
            continue
        j = int(np.searchsorted(
            d4_ts, ts - np.timedelta64(3, "h"), side="right")) - 1
        if j < 0:
            continue
        res = bt.score_bar(d1.iloc[i].to_dict(), d4_rows[j], symbol=full_symbol)
        if not (res and res.get("score", 0) >= SCORE_MIN and res.get("side")):
            continue

        side = res["side"]  # 'buy' / 'sell'
        sl_pct = float(res["sl_pct"])
        tp_eng = float(res["tp_pct"])
        tp_acc = accuracy_tp_pct(sl_pct)
        close = float(c[i])
        sl_px, tp_px_eng = entry_barriers(side, close, sl_pct, tp_eng)
        _, tp_px_acc = entry_barriers(side, close, sl_pct, tp_acc)

        fwd = [
            [int(ts_ms[k]), float(o[k]), float(h[k]), float(lo[k]), float(c[k]), float(v[k])]
            for k in range(i + 1, min(n, i + 1 + RESOLVE_HORIZON_BARS))
        ]
        base_row = {
            "side": side,
            "entry_px": close,
            "sl_px": sl_px,
            "horizon_bars": RESOLVE_HORIZON_BARS,
            "projected_notional_current": RESOLVE_NOTIONAL,
        }
        out_e = resolve_one(fwd and {**base_row, "tp_px": tp_px_eng} or {}, fwd)
        out_a = resolve_one(fwd and {**base_row, "tp_px": tp_px_acc} or {}, fwd)

        def outcome(o_):
            if o_ is None:
                return "pending", None, None
            win = "win" if float(o_["net_pnl"]) > 0 else "loss"
            return win, round(float(o_["net_pnl"]), 4), int(o_["bars_held"])

        oc_e, pnl_e, held_e = outcome(out_e)
        oc_a, pnl_a, held_a = outcome(out_a)
        tpy = pd.Timestamp(ts).to_pydatetime()
        entries.append(
            {
                "time": tpy.strftime("%Y-%m-%d %H:%M"),
                "month": tpy.strftime("%Y-%m"),
                "side": "LONG" if side == "buy" else "SHORT",
                "score": int(res["score"]),
                "sl_pct": round(sl_pct, 2),
                "tp_pct_engine": round(tp_eng, 2),
                "tp_pct_accuracy": round(tp_acc, 2),
                "outcome_engine": oc_e,
                "net_pnl_engine": pnl_e,
                "bars_held_engine": held_e,
                "outcome_accuracy": oc_a,
                "net_pnl_accuracy": pnl_a,
                "bars_held_accuracy": held_a,
                "reason": res.get("reason", ""),
            }
        )
    return entries


# ----------------------------------------------------------------------------
# WR / monthly aggregation
# ----------------------------------------------------------------------------
def _wr(entries: list[dict], geom: str) -> dict:
    key = f"outcome_{geom}"
    wins = sum(1 for e in entries if e[key] == "win")
    losses = sum(1 for e in entries if e[key] == "loss")
    pending = sum(1 for e in entries if e[key] == "pending")
    resolved = wins + losses
    return {
        "wins": wins,
        "losses": losses,
        "pending": pending,
        "resolved": resolved,
        "wr_pct": round(wins / resolved * 100.0, 1) if resolved else None,
    }


def _monthly(part_a: list[dict], part_b: list[dict]) -> list[dict]:
    months = sorted({r["month"] for r in part_a} | {r["month"] for r in part_b})
    rows = []
    for m in months:
        a_m = [r for r in part_a if r["month"] == m]
        b_m = [r for r in part_b if r["month"] == m]
        we = _wr(b_m, "engine")
        wa = _wr(b_m, "accuracy")
        rows.append(
            {
                "month": m,
                "events": len(a_m),
                "entries": len(b_m),
                "wr_engine_pct": we["wr_pct"],
                "wr_engine_n": we["resolved"],
                "wr_accuracy_pct": wa["wr_pct"],
                "wr_accuracy_n": wa["resolved"],
            }
        )
    return rows


# ----------------------------------------------------------------------------
# Venue availability (keyless markets check)
# ----------------------------------------------------------------------------
def load_venue_bases() -> dict[str, set]:
    import ccxt

    out = {}
    for name, cls in (("binance", "binanceusdm"), ("bybit", "bybit"), ("bitget", "bitget")):
        try:
            ex = getattr(ccxt, cls)({"enableRateLimit": True, "timeout": 20000})
            ex.load_markets()
            bset = {
                m["base"]
                for m in ex.markets.values()
                if m.get("swap")
                and m.get("quote") == "USDT"
                and m.get("active")
                and m.get("settle") == "USDT"
            }
            out[name] = bset
        except Exception:
            out[name] = set()
    return out


def venues_for(base: str, venue_bases: dict[str, set]) -> list[str]:
    return [v for v in ("binance", "bybit", "bitget") if base in venue_bases.get(v, set())]


# ----------------------------------------------------------------------------
# Driver
# ----------------------------------------------------------------------------
def build_universe(binance_bases: set) -> list[str]:
    """Bot's harvested futures universe: cached 1h parquets that are also live
    binance USDT-M perps."""
    cached = set()
    for f in CACHE_DIR.glob("*-USDT_1h.parquet"):
        b = f.name.replace("-USDT_1h.parquet", "")
        if b:
            cached.add(b)
    return sorted(cached & binance_bases) if binance_bases else sorted(cached)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--days", type=int, default=120, help="lookback window (full days through now)")
    ap.add_argument("--limit", type=int, default=0, help="cap universe size (0 = all, for debugging)")
    ap.add_argument("--no-net", action="store_true", help="skip keyless freshening (cache only)")
    args = ap.parse_args()

    report_md, report_json = _report_paths(args.days)

    now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    w1 = now
    w0 = (now - timedelta(days=args.days)).replace(hour=0)

    print(f"[scan] window {w0.isoformat()} -> {w1.isoformat()}  (--days {args.days})")

    exchange = None
    venue_bases = {"binance": set(), "bybit": set(), "bitget": set()}
    if not args.no_net:
        try:
            import ccxt

            exchange = ccxt.binanceusdm({"enableRateLimit": True, "timeout": 20000})
            exchange.load_markets()
            print("[scan] keyless binanceusdm connected; loading venue markets...")
            venue_bases = load_venue_bases()
        except Exception as e:  # noqa: BLE001
            print(f"[scan] keyless ccxt unavailable ({type(e).__name__}); cache-only mode")
            exchange = None

    universe = build_universe(venue_bases.get("binance", set()))
    if args.limit:
        universe = universe[: args.limit]
    print(f"[scan] universe: {len(universe)} symbols")

    part_a: list[dict] = []
    part_b: list[dict] = []
    skipped: list[str] = []
    src_counts: dict[str, int] = {}
    coverage_ok = 0

    for n, base in enumerate(universe, 1):
        df, src = load_symbol_df(base, exchange)
        src_counts[src] = src_counts.get(src, 0) + 1
        if df is None or len(df) < 90:
            skipped.append(base)
            continue
        df = _slice_for_window(df, w0, WARMUP_BUFFER_BARS)
        if len(df) < 90:
            skipped.append(base)
            continue
        # Coverage: does data reach within ~2 days of window end?
        if (w1.replace(tzinfo=None) - df.index[-1].to_pydatetime()) <= timedelta(days=2):
            coverage_ok += 1
        venues = venues_for(base, venue_bases) or ["binance?"]
        full_symbol = f"{base}/USDT:USDT"

        for hit in scan_confluence(df, w0, w1):
            hit["symbol"] = base
            hit["venues"] = "/".join(venues)
            part_a.append(hit)

        for ent in replay_and_resolve(df, full_symbol, w0, w1):
            ent["symbol"] = base
            ent["venues"] = "/".join(venues)
            part_b.append(ent)

        if n % 25 == 0:
            print(f"[scan] {n}/{len(universe)} processed "
                  f"(A={len(part_a)} B={len(part_b)} skip={len(skipped)})")

    # PART C — overlap (same symbol within +/-OVERLAP_HOURS)
    overlaps = []
    b_by_sym: dict[str, list] = {}
    for e in part_b:
        b_by_sym.setdefault(e["symbol"], []).append(e)
    for a in part_a:
        for e in b_by_sym.get(a["symbol"], []):
            ta = datetime.strptime(a["bar_time"], "%Y-%m-%d %H:%M")
            te = datetime.strptime(e["time"], "%Y-%m-%d %H:%M")
            if abs((ta - te).total_seconds()) <= OVERLAP_HOURS * 3600:
                overlaps.append(
                    {
                        "symbol": a["symbol"],
                        "scan_time": a["bar_time"],
                        "scan_rsi": a["rsi"],
                        "entry_time": e["time"],
                        "entry_side": e["side"],
                        "entry_score": e["score"],
                    }
                )

    part_a.sort(key=lambda x: x["bar_time"])
    part_b.sort(key=lambda x: x["time"])

    def top_fwd(rows, key, n=3):
        vals = [r for r in rows if r.get(key) is not None]
        return sorted(vals, key=lambda r: r[key], reverse=True)[:n]

    wr_engine = _wr(part_b, "engine")
    wr_accuracy = _wr(part_b, "accuracy")
    monthly = _monthly(part_a, part_b)

    summary = {
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "window_start": w0.isoformat(),
        "window_end": w1.isoformat(),
        "days": args.days,
        "signal_definition": (
            f"RSI({RSI_PERIOD})<{RSI_THRESHOLD:g} AND bar volume >= {VOL_MULT:g}x "
            f"trailing {VOL_WINDOW}-bar mean (baseline excludes the spike bar; "
            f"'+200% vs baseline')"
        ),
        "entry_rule": f"backtest_v3.score_bar score >= {SCORE_MIN} (4 required conditions)",
        "outcome_model": (
            f"core.shadow_resolver.resolve_one first-touch, SL-first tie-break, "
            f"6bps/side fee + 5/10bps slippage, horizon {RESOLVE_HORIZON_BARS} bars; "
            f"engine TP vs accuracy TP ({ACCURACY_TP_FRAC_OF_SL:g}xSL, floor "
            f"{ACCURACY_TP_FLOOR_PCT:g}%)"
        ),
        "universe_count": len(universe),
        "symbols_skipped": len(skipped),
        "coverage_reached_window_end": coverage_ok,
        "data_sources": src_counts,
        "confluence_events": len(part_a),
        "would_be_entries": len(part_b),
        "wr_engine": wr_engine,
        "wr_accuracy": wr_accuracy,
        "overlap_count": len(overlaps),
        "monthly": monthly,
        "top3_fwd_24h_partA": top_fwd(part_a, "fwd_24h_pct"),
    }

    report_json.parent.mkdir(parents=True, exist_ok=True)
    with open(report_json, "w", encoding="utf-8") as f:
        json.dump(
            {
                "summary": summary,
                "part_a_confluence": part_a,
                "part_b_entries": part_b,
                "part_c_overlap": overlaps,
                "skipped": skipped,
            },
            f,
            indent=2,
        )

    _write_md(summary, part_a, part_b, overlaps, skipped, top_fwd, report_md)
    print(f"[scan] wrote {report_md}")
    print(f"[scan] wrote {report_json}")
    print(
        f"[scan] DONE  universe={len(universe)} events={len(part_a)} "
        f"entries={len(part_b)} overlap={len(overlaps)} skipped={len(skipped)}\n"
        f"[scan] WR engine={wr_engine['wr_pct']}% (n={wr_engine['resolved']}) "
        f"accuracy={wr_accuracy['wr_pct']}% (n={wr_accuracy['resolved']})"
    )


def _md_table(rows: list[dict], cols: list[tuple[str, str]]) -> str:
    head = "| " + " | ".join(h for _, h in cols) + " |"
    sep = "| " + " | ".join("---" for _ in cols) + " |"
    lines = [head, sep]
    for r in rows:
        cells = []
        for k, _ in cols:
            v = r.get(k)
            cells.append("n/a" if v is None else str(v))
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


def _write_md(summary, part_a, part_b, overlaps, skipped, top_fwd, report_md) -> None:
    a_cols = [
        ("symbol", "symbol"), ("venues", "venues"), ("bar_time", "bar time UTC"),
        ("rsi", "RSI"), ("vol_ratio", "vol x"), ("close", "close"),
        ("fwd_24h_pct", "+24h % (hindsight)"), ("fwd_72h_pct", "+72h % (hindsight)"),
    ]
    b_cols = [
        ("symbol", "symbol"), ("venues", "venues"), ("time", "time UTC"),
        ("side", "side"), ("score", "score"), ("sl_pct", "sl %"),
        ("tp_pct_engine", "tp% eng"), ("tp_pct_accuracy", "tp% acc"),
        ("outcome_engine", "eng outcome"), ("outcome_accuracy", "acc outcome"),
    ]
    a_top = top_fwd(part_a, "fwd_24h_pct")
    we, wa = summary["wr_engine"], summary["wr_accuracy"]

    lines = []
    lines.append("# RSI(14)<30 + Volume +200% Confluence Scan & Bot Entry Replay (120d)")
    lines.append("")
    lines.append(f"_Generated {summary['generated_utc']} — RESEARCH / situational "
                 "intelligence only._")
    lines.append("")
    lines.append("## Honesty / ledger note")
    lines.append("")
    lines.append("> **RSI mean-reversion is a REFUTED family for this bot** "
                 "(5 coins x 3yr, NO_EDGE, 2026-06). PART A below is *situational "
                 "intelligence, not a trade signal.* All +24h/+72h moves are "
                 "**hindsight** forward returns, labeled as such, and were not "
                 "knowable at the signal bar. The live bot did **not** place the "
                 "PART B trades: it ran `SIGNAL_SOURCE=none` (carry-first, no "
                 "directional entries) from ~2026-07-02 and the mcp directional lane "
                 "only resumed 2026-07-09. PART B is a replay of **would-be** entries "
                 "under today's DistFitSL TP policy (current-policy replay, NOT an "
                 "out-of-sample backtest).")
    lines.append("")
    lines.append("## Headline — would-be win rate under BOTH TP geometries")
    lines.append("")
    lines.append("| geometry | wins | losses | pending | resolved | WR |")
    lines.append("| --- | --- | --- | --- | --- | --- |")
    lines.append(f"| engine (DistFitSL TP) | {we['wins']} | {we['losses']} | "
                 f"{we['pending']} | {we['resolved']} | "
                 f"{'n/a' if we['wr_pct'] is None else str(we['wr_pct']) + '%'} |")
    lines.append(f"| accuracy (0.45xSL, floor 0.5%) | {wa['wins']} | {wa['losses']} | "
                 f"{wa['pending']} | {wa['resolved']} | "
                 f"{'n/a' if wa['wr_pct'] is None else str(wa['wr_pct']) + '%'} |")
    lines.append("")
    lines.append("_Outcome model: " + summary["outcome_model"] + ". `pending` = "
                 "fewer than the horizon of forward closed bars existed (censored, "
                 "excluded from WR) — expected near the window end._")
    lines.append("")
    lines.append("## Parameters")
    lines.append("")
    lines.append(f"- **Window:** {summary['window_start']} -> {summary['window_end']} "
                 f"(last {summary['days']} full days, 1h timeframe)")
    lines.append(f"- **Part A signal:** {summary['signal_definition']}")
    lines.append(f"- **Part B entry rule:** {summary['entry_rule']}; SL/TP from the "
                 "production DistFitSL fit (current-policy replay, not OOS).")
    lines.append(f"- **Universe:** {summary['universe_count']} symbols — the bot's "
                 "harvested futures cache intersected with live binance USDT-M perps "
                 f"({summary['symbols_skipped']} skipped for insufficient candles; "
                 f"{summary['coverage_reached_window_end']} reached within 2d of "
                 "window end after keyless freshening).")
    lines.append(f"- **Data sources:** {summary['data_sources']} "
                 "(cache reused, freshened to the latest closed bar via keyless ccxt "
                 "pagination).")
    lines.append("")
    lines.append("## Monthly aggregates")
    lines.append("")
    m_cols = [
        ("month", "month"), ("events", "Part A events"), ("entries", "Part B entries"),
        ("wr_engine_pct", "WR engine %"), ("wr_engine_n", "n(eng)"),
        ("wr_accuracy_pct", "WR accuracy %"), ("wr_accuracy_n", "n(acc)"),
    ]
    lines.append(_md_table(summary["monthly"], m_cols) if summary["monthly"]
                 else "_No months in window._")
    lines.append("")
    lines.append("## Summary")
    lines.append("")
    lines.append(f"- **Total confluence events (Part A):** {summary['confluence_events']}")
    lines.append(f"- **Total would-be engine entries (Part B):** {summary['would_be_entries']}")
    lines.append(f"- **A/B overlap (same symbol within +/-6h):** {summary['overlap_count']}")
    lines.append("")
    lines.append("### 3 largest +24h moves among confluence events (hindsight)")
    lines.append("")
    lines.append(_md_table(a_top, a_cols) if a_top
                 else "_None (no confluence events with a complete forward window)._")
    lines.append("")
    lines.append(f"## PART A — RSI<30 + Volume >=3x confluence events (top 30 of "
                 f"{len(part_a)})")
    lines.append("")
    lines.append("_+24h/+72h are hindsight, not signals. Full list in the JSON._")
    lines.append("")
    lines.append(_md_table(part_a[:30], a_cols) if part_a else "_No events in window._")
    lines.append("")
    lines.append(f"## PART B — Production would-be entries + outcome (top 30 of "
                 f"{len(part_b)})")
    lines.append("")
    lines.append("_Outcome under engine TP and accuracy TP (both vs the same SL). "
                 "Full list in the JSON._")
    lines.append("")
    lines.append(_md_table(part_b[:30], b_cols) if part_b else "_No entries in window._")
    lines.append("")
    lines.append("## PART C — Overlap between scan and engine entries")
    lines.append("")
    lines.append("The engine's **required RSI sweet-spot** (long 38-68 / short 32-62) "
                 "intentionally rejects oversold bars, so an RSI<30 confluence bar can "
                 "essentially never be a same-bar engine entry. Overlap is measured "
                 "within +/-6h to catch near-miss recoveries.")
    lines.append("")
    if overlaps:
        oc = [
            ("symbol", "symbol"), ("scan_time", "scan bar"), ("scan_rsi", "scan RSI"),
            ("entry_time", "entry"), ("entry_side", "side"), ("entry_score", "score"),
        ]
        lines.append(_md_table(overlaps[:30], oc))
    else:
        lines.append(f"_Overlap count: {summary['overlap_count']} — confirms the "
                     "engine and the oversold-confluence scan are orthogonal._")
    lines.append("")

    report_md.parent.mkdir(parents=True, exist_ok=True)
    with open(report_md, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


if __name__ == "__main__":
    main()
