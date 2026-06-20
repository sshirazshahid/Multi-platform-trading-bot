"""Fit trailing-stop activation/lock from realized winner OHLC (Phase 2 / Task D).

Per memory `project_trailing_clips_winners_2026_04_21.md`: 50 trailing wins
averaged $0.57 vs 4 take-profit hits at $2.84. The hand-tuned
(activation=0.8%, lock=0.7) config is clipping the right tail. This script
grid-searches over plausible (activation, lock) pairs, replays each
historical winner against its 1h OHLC, sums simulated realized PnL, and
persists the best cell to `data/trailing_params.json`.

Usage
-----
    python scripts/fit_trailing.py [--report] [--limit N]

Output
------
    data/trailing_params.json — { activation_pct, lock_fraction, n_winners,
                                  best_pnl, baseline_pnl, grid: [...] }

Caveats
-------
- OHLC is fetched from Binance public endpoint (no auth needed). Symbols
  not on Binance are skipped (logged, not fatal).
- Replay uses 1h candles; intra-bar trailing-stop hits are approximated by
  testing high/low against the SL each bar (in the side-correct order).
- The current config also has a graduated `_lock_fraction(peak_pnl)` table
  inside trailing_stop_manager — this script only fits the BASE pair, which
  is the dominant lever. Graduated tiers stay as-is.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.warehouse import get_warehouse  # noqa: E402

GRID_ACTIVATION = [0.005, 0.008, 0.012, 0.015, 0.020]  # 5 cells
GRID_LOCK = [0.50, 0.60, 0.70, 0.80, 0.85]  # 5 cells
DEFAULT_ACTIVATION = 0.008  # current production
DEFAULT_LOCK = 0.70  # current production
OUT_PATH = ROOT / "data" / "trailing_params.json"


def _fetch_ohlc(symbol: str, since_ms: int, until_ms: int) -> list[list[float]]:
    """Fetch 1h candles from Binance public API. Returns ccxt-style rows
    [ts, open, high, low, close, volume]. Empty list on any failure."""
    try:
        import ccxt

        ex = ccxt.binance({"enableRateLimit": True})
        # Strip futures suffix for spot endpoint
        spot_sym = symbol.replace(":USDT", "").replace("/USDT", "/USDT")
        rows = []
        cursor = since_ms
        while cursor < until_ms:
            chunk = ex.fetch_ohlcv(spot_sym, timeframe="1h", since=cursor, limit=500)
            if not chunk:
                break
            rows.extend(chunk)
            cursor = int(chunk[-1][0]) + 60 * 60 * 1000
            if len(chunk) < 500:
                break
        # Trim to window
        return [r for r in rows if since_ms <= int(r[0]) <= until_ms]
    except Exception:
        return []


def _replay_winner(
    side: str,
    entry: float,
    exit_: float,
    ohlc: list[list[float]],
    *,
    activation_pct: float,
    lock_fraction: float,
) -> float:
    """Simulate trailing-stop on a single winner. Returns simulated exit
    price (>= 0); caller multiplies by size for PnL.

    Mirrors trailing_stop_manager.update without the breakeven safety net,
    since our fit space is the activation/lock pair only. We assume the
    candle-level trailing rule on close-of-bar — entry/exit between bars
    are not modeled (closed-form on bar high/low is good enough for this
    sample size)."""
    if entry <= 0 or not ohlc:
        return exit_

    side = side.lower()
    is_long = side in ("buy", "long")
    peak = entry
    trough = entry
    activated = False
    sl = 0.0

    for row in ohlc:
        _ts, o, h, lo, c, _v = row
        h = float(h)
        lo = float(lo)
        c = float(c)

        if is_long:
            if h > peak:
                peak = h
                peak_pnl = (peak - entry) / entry
                if peak_pnl >= activation_pct:
                    activated = True
                if activated:
                    new_sl = entry + (peak - entry) * lock_fraction
                    sl = max(sl, new_sl)
            if activated and lo <= sl:
                return sl
        else:
            if lo < trough:
                trough = lo
                peak_pnl = (entry - trough) / entry
                if peak_pnl >= activation_pct:
                    activated = True
                if activated:
                    new_sl = entry - (entry - trough) * lock_fraction
                    sl = sl and min(sl, new_sl) or new_sl
            if activated and h >= sl > 0:
                return sl

    return exit_  # never trailed; close at original exit


def _winners_from_warehouse(limit: int | None = None) -> list[dict]:
    wh = get_warehouse()
    sql = (
        "SELECT id, symbol, side, entry_px, exit_px, size, ts_entry, ts_exit, "
        "       realized_pnl "
        "FROM trades WHERE status='CLOSED' AND realized_pnl > 0 "
        "  AND entry_px > 0 AND exit_px > 0 "
        "  AND ts_entry > 0 AND ts_exit > 0 "
        "ORDER BY ts_exit DESC"
    )
    rows = wh.query(sql)
    if limit:
        rows = rows[: int(limit)]
    return rows


def fit(limit: int | None = None, report: bool = False) -> dict:
    winners = _winners_from_warehouse(limit=limit)
    if not winners:
        return {"error": "no_winners", "n_winners": 0}

    # Cache OHLC per (symbol, since, until) so the same-symbol streak doesn't
    # re-fetch.
    ohlc_cache: dict = {}

    def _ohlc(symbol, ts_entry, ts_exit):
        key = (symbol, int(ts_entry), int(ts_exit))
        if key in ohlc_cache:
            return ohlc_cache[key]
        ohlc = _fetch_ohlc(
            symbol,
            int(ts_entry * 1000) - 3600_000,
            int(ts_exit * 1000) + 3600_000,
        )
        ohlc_cache[key] = ohlc
        return ohlc

    grid_results: list[dict] = []
    for a in GRID_ACTIVATION:
        for L in GRID_LOCK:
            total_pnl = 0.0
            n_replayed = 0
            for w in winners:
                ohlc = _ohlc(w["symbol"], w["ts_entry"], w["ts_exit"])
                if not ohlc:
                    continue
                sim_exit = _replay_winner(
                    side=w["side"],
                    entry=float(w["entry_px"]),
                    exit_=float(w["exit_px"]),
                    ohlc=ohlc,
                    activation_pct=a,
                    lock_fraction=L,
                )
                size = float(w.get("size") or 0.0)
                if w["side"].lower() in ("buy", "long"):
                    pnl = (sim_exit - float(w["entry_px"])) * size
                else:
                    pnl = (float(w["entry_px"]) - sim_exit) * size
                total_pnl += pnl
                n_replayed += 1
            grid_results.append(
                {
                    "activation_pct": a,
                    "lock_fraction": L,
                    "total_pnl": total_pnl,
                    "n_replayed": n_replayed,
                }
            )
            if report:
                print(f"  a={a:.3f} L={L:.2f}: pnl=${total_pnl:.2f} (n={n_replayed})")

    if not grid_results:
        return {"error": "no_replays", "n_winners": len(winners)}

    best = max(grid_results, key=lambda r: r["total_pnl"])
    baseline = next(
        (
            r
            for r in grid_results
            if r["activation_pct"] == DEFAULT_ACTIVATION and r["lock_fraction"] == DEFAULT_LOCK
        ),
        None,
    )
    payload = {
        "activation_pct": best["activation_pct"],
        "lock_fraction": best["lock_fraction"],
        "n_winners": len(winners),
        "best_pnl": best["total_pnl"],
        "baseline_pnl": (baseline or {}).get("total_pnl"),
        "grid": grid_results,
        "fitted_at": int(time.time()),
    }
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    if report:
        print()
        print(
            f"BEST: a={payload['activation_pct']:.3f} "
            f"L={payload['lock_fraction']:.2f} -> "
            f"pnl=${payload['best_pnl']:.2f}"
        )
        if payload["baseline_pnl"] is not None:
            delta = payload["best_pnl"] - payload["baseline_pnl"]
            print(
                f"vs baseline (a=0.008 L=0.70): ${payload['baseline_pnl']:.2f}  delta=${delta:+.2f}"
            )
        print(f"persisted -> {OUT_PATH}")
    return payload


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--limit", type=int, default=None)
    args = ap.parse_args()
    out = fit(limit=args.limit, report=args.report)
    if not args.report:
        print(json.dumps(out, indent=2))
