"""27_pair_indicator_dossier.py — per-pair "Info and Data" dossier (2026-07-23).

MEASUREMENT ONLY. This script makes NO strategy claims and NO edge assertions.
It records (a) the current technical-indicator POSTURE of each symbol in the
bot's spec-derived probe universe at the latest CLOSED 4h bar, and (b) the
LOCAL data coverage per symbol, feeding the standing backfill remediation
from the 18_ pair-verdict dossier. Read-only everywhere: public ccxt bybit
endpoints (no keys), warehouse.sqlite opened mode=ro, parquet/CSV reads only.

UNIVERSE DERIVATION (exactly as the bot does it):
    core.agents.bundle_mr_probe_agent.resolve_universe(venue='bybit',
    perp_available=...) — the same function bot_engine._bundle_probe_symbols
    calls at boot. It loads the active PAPER-futures StrategySpec artifacts
    (data/strategy_specs/*.json) via core.strategy_spec.load_all_specs +
    approved_paper_futures_routes, keeps bases with an approved bybit route,
    and drops bases without a live bybit USDT linear perp (perp_available
    mirrors bot_engine._venue_perp_available: markets[sym].swap and active).
    FAIL-CLOSED to the frozen 5-major basket on any spec problem — this
    script HARD-ASSERTS that the fallback did NOT fire (count must be > 5).

FETCH PATTERN (mirrors the probe agents / bot_engine._unlock_ohlcv):
    ccxt bybit, linear swap markets, fetch_ohlcv(unified_symbol, timeframe,
    limit=300) with enableRateLimit. 300x 4h bars for indicators, 300x 1h
    bars for live-coverage only (no indicator is computed on 1h). The last
    (in-progress) candle is DROPPED: indicators use only bars whose
    timestamp + 4h <= now.

INDICATOR CONVENTIONS (all computed on CLOSED 4h bars, plain numpy/pandas):
- SMA(n): simple rolling mean of closes, min_periods=n.
- EMA(n): pandas ewm(span=n, adjust=False).mean() over the full fetched
  window (no min_periods — matches the bundle reference's ema()).
- MACD(12,26,9): EMA12-EMA26 line; signal = EMA9(span) of the line;
  hist = line - signal. Signs reported at the last closed bar.
- RSI(14): Wilder smoothing — ewm(alpha=1/14, adjust=False) of clipped
  up/down diffs; dn==0 -> RSI 50 (the repo's bundle convention).
- Bollinger(20,2): mid = SMA20, sd = rolling std (ddof=1, the repo's
  bundle zscore convention); %B = (close-lower)/(upper-lower);
  bandwidth = (upper-lower)/mid; bandwidth percentile = fraction of the
  window's defined bandwidth values <= the latest value.
- Parabolic SAR: standard step/max acceleration 0.02/0.02/0.2, seeded
  long with SAR = first bar's low; side = long if close > SAR.
- Supertrend(10,3): ATR10 via Wilder RMA (ewm alpha=1/10) of true range;
  basic bands hl2 +/- 3*ATR with the standard final-band carry rules;
  side = up (price above final lower band) / down.
- Volume 20-bar trend: OLS slope (np.polyfit deg=1) of the last 20 closed
  volumes -> rising/falling; z-score = (last_vol - mean(prev 20 excl
  last)) / std(ddof=1) of those 20.
- KDJ(9,3,3): RSV = 100*(C-LL9)/(HH9-LL9); K = 2/3*K_prev + 1/3*RSV,
  D = 2/3*D_prev + 1/3*K (seeds 50/50); J = 3K - 2D.
- OBV: cumulative signed volume (sign of close diff); 20-bar OLS slope
  sign reported.
- Williams %R(14): -100*(HH14-C)/(HH14-LL14).

COVERAGE SOURCES (local only, nothing fetched for coverage):
- warehouse.sqlite (mode=ro): NO raw OHLCV table exists (reported
  honestly as ABSENT); per-symbol decision-data coverage measured from
  the `candidates` table (row count + latest ts).
- data/ohlcv_cache/{BASE}-USDT_{1h,4h}.parquet: bar depth + staleness
  from the LAST BAR TIMESTAMP INSIDE the file (never file mtime).
- data/funding_history/{venue}_{BASE}.csv: presence + row count + last
  funding ts (bybit is the routed venue; binance/bitget presence noted).
- Order-book depth: data/l2_history.jsonl (scripts/harvest_l2.py, hourly
  L2 means) — symbols actually present are listed; ABSENT per-symbol
  otherwise. Trades: data/aggtrades_qh/*.parquet (BTC/ETH pilot only).
  On-chain artifacts: none found under data/ — reported ABSENT.

VERDICT (data-readiness only, per the task's fixed vocabulary):
- BACKFILL_FIRST: bybit funding CSV absent, OR local 1h ohlcv_cache
  parquet absent/stale (> 7 days behind now), OR the live 4h fetch could
  not produce >= 210 closed bars (indicators uncomputable).
- PARTIAL: everything above OK but the local 4h cache parquet is
  absent/stale, or the live 1h fetch failed, or < 300 4h bars arrived.
- DATA_OK: all of the above present and fresh.

Outputs: 27_pair_indicator_dossier.json (machine) + .md (human) in this
directory. Run from repo root with venv\\Scripts\\python.exe.
"""

from __future__ import annotations

import json
import math
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

DATA = ROOT / "data"
OUT_DIR = Path(__file__).resolve().parent
OUT_JSON = OUT_DIR / "27_pair_indicator_dossier.json"
OUT_MD = OUT_DIR / "27_pair_indicator_dossier.md"

BAR_4H_S = 4 * 3600
STALE_1H_MAX_S = 7 * 86400  # local 1h cache older than this => BACKFILL_FIRST
STALE_4H_MAX_S = 7 * 86400  # local 4h cache older than this => PARTIAL flag
MIN_CLOSED_4H = 210  # SMA200 + margin; below this indicators are unreliable


# ── universe (exactly the bot's path) ────────────────────────────────────────
def resolve_bot_universe(markets: dict):
    from core.agents.bundle_mr_probe_agent import resolve_universe

    def perp_available(sym: str) -> bool:
        # mirrors bot_engine._venue_perp_available on already-loaded markets
        try:
            m = (markets or {}).get(sym)
            return bool(m and m.get("swap") and m.get("active", True))
        except Exception:
            return False

    symbols, skipped = resolve_universe(venue="bybit", perp_available=perp_available)
    # HARD ASSERT: the frozen 5-major fail-closed basket must NOT have fired.
    assert len(symbols) > 5, (
        f"resolve_universe fell back to the frozen basket ({symbols}); "
        "spec-derived widening inactive — dossier would be wrong, aborting"
    )
    return list(symbols), list(skipped)


# ── indicator math (conventions in module docstring) ─────────────────────────
def sma(closes: np.ndarray, n: int):
    if len(closes) < n:
        return None
    return float(np.mean(closes[-n:]))


def ema_series(closes: np.ndarray, n: int) -> np.ndarray:
    return pd.Series(closes).ewm(span=n, adjust=False).mean().to_numpy()


def wilder_rsi(closes: np.ndarray, n: int = 14):
    if len(closes) < n + 1:
        return None
    d = np.diff(closes)
    up = pd.Series(np.clip(d, 0, None)).ewm(alpha=1.0 / n, adjust=False).mean()
    dn = pd.Series(np.clip(-d, 0, None)).ewm(alpha=1.0 / n, adjust=False).mean()
    u, v = float(up.iloc[-1]), float(dn.iloc[-1])
    if v == 0.0:
        return 50.0
    return float(100.0 - 100.0 / (1.0 + u / v))


def macd_state(closes: np.ndarray):
    line = ema_series(closes, 12) - ema_series(closes, 26)
    sig = pd.Series(line).ewm(span=9, adjust=False).mean().to_numpy()
    hist = line - sig
    s = lambda x: "pos" if x > 0 else ("neg" if x < 0 else "flat")  # noqa: E731
    return {
        "line": round(float(line[-1]), 6),
        "signal": round(float(sig[-1]), 6),
        "hist": round(float(hist[-1]), 6),
        "line_sign": s(line[-1]),
        "signal_sign": s(sig[-1]),
        "hist_sign": s(hist[-1]),
    }


def bollinger(closes: np.ndarray, n: int = 20, k: float = 2.0):
    ser = pd.Series(closes)
    mid = ser.rolling(n, min_periods=n).mean()
    sd = ser.rolling(n, min_periods=n).std(ddof=1)
    upper, lower = mid + k * sd, mid - k * sd
    width = (upper - lower) / mid
    if math.isnan(float(mid.iloc[-1])):
        return None
    rng = float(upper.iloc[-1] - lower.iloc[-1])
    pct_b = float((closes[-1] - lower.iloc[-1]) / rng) if rng > 0 else 0.5
    w = width.dropna().to_numpy()
    bw_pctl = float(np.mean(w <= w[-1])) if len(w) else None
    return {
        "pct_b": round(pct_b, 4),
        "bandwidth": round(float(width.iloc[-1]), 6),
        "bandwidth_pctl": round(bw_pctl, 4) if bw_pctl is not None else None,
    }


def psar_side(high: np.ndarray, low: np.ndarray, close: np.ndarray,
              step: float = 0.02, max_af: float = 0.2):
    if len(close) < 3:
        return None
    bull, af, sar, ep = True, step, float(low[0]), float(high[0])
    for i in range(1, len(close)):
        sar = sar + af * (ep - sar)
        if bull:
            sar = min(sar, low[i - 1], low[i - 2] if i >= 2 else low[i - 1])
            if low[i] < sar:
                bull, sar, ep, af = False, ep, float(low[i]), step
            elif high[i] > ep:
                ep, af = float(high[i]), min(af + step, max_af)
        else:
            sar = max(sar, high[i - 1], high[i - 2] if i >= 2 else high[i - 1])
            if high[i] > sar:
                bull, sar, ep, af = True, ep, float(high[i]), step
            elif low[i] < ep:
                ep, af = float(low[i]), min(af + step, max_af)
    return "long" if bull else "short"


def supertrend_side(high: np.ndarray, low: np.ndarray, close: np.ndarray,
                    n: int = 10, mult: float = 3.0):
    if len(close) < n + 2:
        return None
    prev_c = np.concatenate([[close[0]], close[:-1]])
    tr = np.maximum(high - low, np.maximum(np.abs(high - prev_c), np.abs(low - prev_c)))
    atr = pd.Series(tr).ewm(alpha=1.0 / n, adjust=False).mean().to_numpy()
    hl2 = (high + low) / 2.0
    ub, lb = hl2 + mult * atr, hl2 - mult * atr
    f_ub, f_lb = ub.copy(), lb.copy()
    for i in range(1, len(close)):
        f_ub[i] = ub[i] if (ub[i] < f_ub[i - 1] or close[i - 1] > f_ub[i - 1]) else f_ub[i - 1]
        f_lb[i] = lb[i] if (lb[i] > f_lb[i - 1] or close[i - 1] < f_lb[i - 1]) else f_lb[i - 1]
    trend = np.ones(len(close), dtype=int)
    for i in range(1, len(close)):
        if trend[i - 1] == 1:
            trend[i] = -1 if close[i] < f_lb[i] else 1
        else:
            trend[i] = 1 if close[i] > f_ub[i] else -1
    return "up" if trend[-1] == 1 else "down"


def kdj(high: np.ndarray, low: np.ndarray, close: np.ndarray, n: int = 9):
    if len(close) < n:
        return None
    k, d = 50.0, 50.0
    for i in range(n - 1, len(close)):
        hh, ll = float(np.max(high[i - n + 1: i + 1])), float(np.min(low[i - n + 1: i + 1]))
        rsv = 50.0 if hh == ll else 100.0 * (close[i] - ll) / (hh - ll)
        k = (2.0 / 3.0) * k + (1.0 / 3.0) * rsv
        d = (2.0 / 3.0) * d + (1.0 / 3.0) * k
    j = 3.0 * k - 2.0 * d
    return {"k": round(k, 2), "d": round(d, 2), "j": round(j, 2)}


def obv_slope_sign(close: np.ndarray, vol: np.ndarray, n: int = 20):
    if len(close) < n + 1:
        return None
    obv = np.cumsum(np.sign(np.diff(close, prepend=close[0])) * vol)
    slope = float(np.polyfit(np.arange(n), obv[-n:], 1)[0])
    return "pos" if slope > 0 else ("neg" if slope < 0 else "flat")


def williams_r(high: np.ndarray, low: np.ndarray, close: np.ndarray, n: int = 14):
    if len(close) < n:
        return None
    hh, ll = float(np.max(high[-n:])), float(np.min(low[-n:]))
    if hh == ll:
        return -50.0
    return float(-100.0 * (hh - close[-1]) / (hh - ll))


def volume_trend(vol: np.ndarray, n: int = 20):
    if len(vol) < n:
        return None
    w = vol[-n:]
    slope = float(np.polyfit(np.arange(n), w, 1)[0])
    prior = w[:-1]
    sd = float(np.std(prior, ddof=1))
    z = float((w[-1] - float(np.mean(prior))) / sd) if sd > 0 else 0.0
    return {"trend": "rising" if slope > 0 else "falling", "zscore": round(z, 2)}


def compute_indicators(bars_4h: list):
    """All indicator posture at the latest CLOSED 4h bar. bars_4h = ccxt rows."""
    now_ms = int(time.time() * 1000)
    closed = [b for b in bars_4h if b[0] + BAR_4H_S * 1000 <= now_ms]
    if len(closed) < MIN_CLOSED_4H:
        return None, len(closed)
    a = np.asarray(closed, dtype=float)
    ts, h, l, c, v = a[:, 0], a[:, 2], a[:, 3], a[:, 4], a[:, 5]
    close = float(c[-1])
    s20, s50, s200 = sma(c, 20), sma(c, 50), sma(c, 200)
    e20 = float(ema_series(c, 20)[-1])
    e50 = float(ema_series(c, 50)[-1])
    rel = lambda m: None if m is None else ("above" if close > m else "below")  # noqa: E731
    ind = {
        "asof_closed_4h_utc": datetime.fromtimestamp(ts[-1] / 1000, tz=timezone.utc)
        .strftime("%Y-%m-%dT%H:%MZ"),
        "close": close,
        "sma": {
            "sma20": s20, "sma50": s50, "sma200": s200,
            "price_vs_sma20": rel(s20), "price_vs_sma50": rel(s50),
            "price_vs_sma200": rel(s200),
            "sma50_vs_sma200": (None if (s50 is None or s200 is None)
                                else ("golden" if s50 > s200 else "death")),
        },
        "ema": {"ema20": round(e20, 6), "ema50": round(e50, 6),
                "price_vs_ema20": rel(e20), "price_vs_ema50": rel(e50)},
        "macd": macd_state(c),
        "rsi14": (lambda r: round(r, 2) if r is not None else None)(wilder_rsi(c, 14)),
        "bollinger": bollinger(c, 20, 2.0),
        "psar_side": psar_side(h, l, c),
        "supertrend_10_3": supertrend_side(h, l, c, 10, 3.0),
        "volume_20": volume_trend(v, 20),
        "kdj_9_3_3": kdj(h, l, c, 9),
        "obv_slope_20": obv_slope_sign(c, v, 20),
        "williams_r14": (lambda r: round(r, 2) if r is not None else None)(
            williams_r(h, l, c, 14)),
    }
    for key in ("sma",):
        ind[key] = {k: (round(x, 6) if isinstance(x, float) else x)
                    for k, x in ind[key].items()}
    return ind, len(closed)


# ── local coverage ───────────────────────────────────────────────────────────
def parquet_coverage(base: str, tf: str, now_s: float):
    p = DATA / "ohlcv_cache" / f"{base}-USDT_{tf}.parquet"
    if not p.exists():
        return {"present": False, "rows": None, "last_bar_utc": None, "staleness_days": None}
    try:
        df = pd.read_parquet(p)
        rows = int(len(df))
        last = None
        for col in ("timestamp", "ts", "time", "open_time", "date", "datetime"):
            if col in df.columns:
                last = df[col].iloc[-1]
                break
        if last is None and isinstance(df.index, pd.DatetimeIndex) and rows:
            last = df.index[-1]
        if last is None:
            return {"present": True, "rows": rows, "last_bar_utc": None,
                    "staleness_days": None, "note": "no timestamp column found"}
        if isinstance(last, (int, float, np.integer, np.floating)):
            last_s = float(last) / (1000.0 if float(last) > 1e11 else 1.0)
        else:
            last_s = pd.Timestamp(last).timestamp()
        return {
            "present": True, "rows": rows,
            "last_bar_utc": datetime.fromtimestamp(last_s, tz=timezone.utc)
            .strftime("%Y-%m-%dT%H:%MZ"),
            "staleness_days": round((now_s - last_s) / 86400.0, 1),
        }
    except Exception as exc:
        return {"present": True, "rows": None, "last_bar_utc": None,
                "staleness_days": None, "note": f"unreadable: {exc}"}


def funding_coverage(base: str):
    out = {}
    for venue in ("bybit", "binance", "bitget"):
        p = DATA / "funding_history" / f"{venue}_{base}.csv"
        if not p.exists():
            out[venue] = {"present": False}
            continue
        try:
            df = pd.read_csv(p)
            last = None
            for col in ("funding_time", "timestamp", "ts", "time", "datetime", "date"):
                if col in df.columns and len(df):
                    last = str(df[col].iloc[-1])
                    break
            out[venue] = {"present": True, "rows": int(len(df)), "last": last}
        except Exception as exc:
            out[venue] = {"present": True, "rows": None, "note": f"unreadable: {exc}"}
    return out


def warehouse_candidates_by_base(bases: list):
    """Per-base candidates coverage from warehouse.sqlite (mode=ro).
    NOTE: warehouse stores NO raw OHLCV table — that absence is reported at
    the dossier level; this measures decision-data coverage only."""
    con = sqlite3.connect(f"file:{(DATA / 'warehouse.sqlite').as_posix()}?mode=ro", uri=True)
    try:
        rows = con.execute(
            "select symbol, count(*), max(ts) from candidates group by symbol"
        ).fetchall()
    finally:
        con.close()
    agg = {b: {"rows": 0, "last_ts": None} for b in bases}
    for sym, n, mx in rows:
        base = str(sym).split("/", 1)[0].split(":", 1)[0].upper()
        if base in agg:
            agg[base]["rows"] += int(n)
            if mx is not None and (agg[base]["last_ts"] is None or str(mx) > str(agg[base]["last_ts"])):
                agg[base]["last_ts"] = str(mx)
    return agg


def l2_symbols(sample_lines: int = 400):
    """Symbols present in data/l2_history.jsonl (order-book depth harvest)."""
    p = DATA / "l2_history.jsonl"
    if not p.exists():
        return None
    seen = set()
    try:
        with p.open("rb") as f:
            f.seek(0, 2)
            f.seek(max(0, f.tell() - 400_000))
            tail = f.read().decode("utf-8", errors="ignore").splitlines()[-sample_lines:]
        for line in tail:
            try:
                rec = json.loads(line)
            except Exception:
                continue
            for key in ("symbol", "sym", "market"):
                if key in rec:
                    seen.add(str(rec[key]))
            for k in rec:
                if isinstance(rec.get(k), dict) and "/" in k or k.endswith("USDT"):
                    seen.add(k)
        return sorted(seen)
    except Exception:
        return None


def aggtrades_symbols():
    d = DATA / "aggtrades_qh"
    if not d.exists():
        return None
    return sorted(p.stem.replace("_qh_events", "") for p in d.glob("*_qh_events.parquet"))


# ── main ─────────────────────────────────────────────────────────────────────
def main():
    import ccxt

    ex = ccxt.bybit({"enableRateLimit": True, "options": {"defaultType": "swap"}})
    markets = ex.load_markets()
    symbols, skipped = resolve_bot_universe(markets)
    print(f"universe: {len(symbols)} symbols (skipped bases: {skipped})")

    now_s = time.time()
    wh = warehouse_candidates_by_base([s.split("/", 1)[0] for s in symbols])
    l2 = l2_symbols()
    agg = aggtrades_symbols()

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
            "err_4h": err4, "err_1h": err1,
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
            "warehouse_candidates": wh.get(base),
            "orderbook_depth": ("l2_history.jsonl"
                                if (l2 and any(base in s for s in l2)) else "ABSENT"),
            "trades": ("aggtrades_qh" if (agg and any(base in s for s in agg))
                       else "ABSENT"),
            "onchain": "ABSENT",
        }
        # verdict — DATA READINESS ONLY, never indicator direction
        funding_ok = fund.get("bybit", {}).get("present", False)
        c1_ok = bool(cov1h["present"] and cov1h.get("staleness_days") is not None
                     and cov1h["staleness_days"] * 86400 <= STALE_1H_MAX_S)
        c4_ok = bool(cov4h["present"] and cov4h.get("staleness_days") is not None
                     and cov4h["staleness_days"] * 86400 <= STALE_4H_MAX_S)
        live_ok = ind is not None
        if not funding_ok or not c1_ok or not live_ok:
            verdict = "BACKFILL_FIRST"
        elif (not c4_ok) or err1 or (bars4 and len(bars4) < 300):
            verdict = "PARTIAL"
        else:
            verdict = "DATA_OK"
        rec["verdict"] = verdict
        rec["verdict_reasons"] = {
            "bybit_funding_csv": funding_ok, "local_1h_fresh": c1_ok,
            "local_4h_fresh": c4_ok, "live_4h_computable": live_ok,
            "live_1h_ok": err1 is None,
        }
        records.append(rec)
        print(f"[{i}/{len(symbols)}] {sym}: {verdict} (closed4h={n_closed})")

    counts = {v: sum(1 for r in records if r["verdict"] == v)
              for v in ("DATA_OK", "PARTIAL", "BACKFILL_FIRST")}
    doc = {
        "generated_utc": datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "purpose": "per-pair indicator posture + data coverage; MEASUREMENT ONLY, "
                   "no strategy claims, no edge assertions",
        "universe": {
            "source": "core.agents.bundle_mr_probe_agent.resolve_universe(venue='bybit') "
                      "on data/strategy_specs (MCP_DIRECTIONAL_PAPER active-paper spec), "
                      "perp_available mirroring bot_engine._venue_perp_available",
            "count": len(symbols), "skipped_bases": skipped,
        },
        "global_data_notes": {
            "warehouse_raw_ohlcv": "ABSENT — warehouse.sqlite has no OHLCV bars table; "
                                   "local bars live in data/ohlcv_cache/*.parquet",
            "orderbook_depth_store": {"file": "data/l2_history.jsonl",
                                      "symbols_seen": l2},
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
        "pairs": records,
    }
    OUT_JSON.write_text(json.dumps(doc, indent=1, default=str), encoding="utf-8")
    write_md(doc)
    print(f"wrote {OUT_JSON}\nwrote {OUT_MD}\ncounts={counts} failures={len(fetch_failures)}")


def write_md(doc: dict):
    lines = [
        "# 27 — Per-Pair Indicator & Data Coverage Dossier",
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
            smarel = "/".join((ind["sma"][f"price_vs_sma{n}"] or "?")[0].upper()
                              for n in (20, 50, 200))
            bb = ind.get("bollinger") or {}
            vol = ind.get("volume_20") or {}
            kdjv = ind.get("kdj_9_3_3") or {}
            ind_cells = [
                f"{ind['close']:g}", smarel, ind["sma"]["sma50_vs_sma200"] or "?",
                ind["macd"]["hist_sign"], f"{ind['rsi14']}",
                f"{bb.get('pct_b')} / {bb.get('bandwidth_pctl')}",
                ind.get("psar_side") or "?", ind.get("supertrend_10_3") or "?",
                f"{vol.get('trend', '?')} ({vol.get('zscore', '?')})",
                f"{kdjv.get('k')}/{kdjv.get('d')}/{kdjv.get('j')}",
                ind.get("obv_slope_20") or "?", f"{ind.get('williams_r14')}",
            ]
        else:
            ind_cells = ["FETCH_FAIL"] + ["-"] * 11
        c1, c4 = cov["ohlcv_cache_1h"], cov["ohlcv_cache_4h"]
        fmt_c = lambda c: (f"{c['rows']} ({c['staleness_days']})" if c["present"]  # noqa: E731
                           and c.get("rows") is not None else "ABSENT")
        fb = cov["funding_history"].get("bybit", {})
        wh = cov.get("warehouse_candidates") or {}
        lines.append(
            "| " + " | ".join(
                [r["symbol"].replace("/USDT:USDT", "")] + ind_cells + [
                    fmt_c(c1), fmt_c(c4),
                    (f"{fb.get('rows')} rows" if fb.get("present") else "ABSENT"),
                    str(wh.get("rows", 0)),
                    f"{'D' if cov['orderbook_depth'] != 'ABSENT' else '-'}/"
                    f"{'T' if cov['trades'] != 'ABSENT' else '-'}",
                    f"**{r['verdict']}**",
                ]) + " |")
    counts = doc["verdict_counts"]
    n = len(doc["pairs"])
    bf = [r for r in doc["pairs"] if r["verdict"] == "BACKFILL_FIRST"]
    missing_fund = [r["base"] for r in bf
                    if not r["verdict_reasons"]["bybit_funding_csv"]]
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
        "",
        "This feeds the standing backfill remediation from the 18_ pair-verdict dossier: "
        "the funding-CSV and stale-1h lists above are the concrete backfill queue.",
    ]
    OUT_MD.write_text("\n".join(lines), encoding="utf-8")


if __name__ == "__main__":
    main()
