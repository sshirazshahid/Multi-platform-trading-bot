"""Triple-barrier labels for warehouse candidates.

For each candidate row with a populated features_json snapshot, recover
sl_pct / tp_pct / side / entry_px and label the candidate via
`core.labeler.triple_barrier` against forward-looking OHLCV.

Persists rows into the new `labels` table (PK=candidate_id, idempotent).
Time-bar firings (label==0) are dropped — they bias toward neutral and
muddy the binary classification problem.

Usage:
    python scripts/build_labels.py --market futures --time-bars 36
    python scripts/build_labels.py --market spot --time-bars 96
    python scripts/build_labels.py --market futures --time-bars 36 --tf 15m
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import ccxt  # noqa: E402
import numpy as np  # noqa: E402

from core.feature_store import _TF_TO_SECONDS, load_ohlcv_window  # noqa: E402
from core.labeler import triple_barrier  # noqa: E402
from core.warehouse import get_warehouse  # noqa: E402

# Defaults: 24h horizon on 15m for futures, 4d on 1h for spot. The labeler
# horizon should be long enough that barriers RESOLVE rather than time-out;
# real positions are typically held much shorter than this.
DEFAULT_TIME_BARS = {"futures": 96, "spot": 96}
DEFAULT_TF        = {"futures": "15m", "spot": "1h"}


def _binance_public() -> ccxt.binance:
    return ccxt.binance({"enableRateLimit": True, "timeout": 15_000})


def _fetcher(ex):
    def f(symbol, timeframe, since_ms, limit):
        base = symbol.split(":")[0]
        return ex.fetch_ohlcv(base, timeframe, since=int(since_ms), limit=int(limit))
    return f


def _coin_to_pair(symbol: str) -> str:
    return symbol.split(":")[0]


def _decode_features(blob: str | None) -> dict:
    if not blob:
        return {}
    try:
        d = json.loads(blob)
        return d if isinstance(d, dict) else {}
    except (TypeError, ValueError):
        return {}


def _resolve_side(cand: dict, feats: dict) -> str | None:
    """Return 'long' / 'short' / None."""
    side = (cand.get("side") or feats.get("side") or "").lower()
    if side in ("buy", "long"):
        return "long"
    if side in ("sell", "short"):
        return "short"
    return None


def _resolve_pct(feats: dict, cand: dict, key: str) -> float | None:
    """Pull sl_pct / tp_pct from the snapshot; coerce to fraction (0.025)."""
    raw = feats.get(key)
    if raw is None:
        raw = cand.get(key)
    if raw is None:
        return None
    try:
        v = float(raw)
    except (TypeError, ValueError):
        return None
    if not np.isfinite(v) or v <= 0:
        return None
    # Heuristic: snapshots store sl_pct/tp_pct as percent (e.g. 2.5 for 2.5%).
    # Anything < 0.5 we assume already a fraction.
    return v / 100.0 if v >= 0.5 else v


def _label_one(
    *,
    cand: dict,
    market_type: str,
    time_bars: int,
    tf: str,
    fetcher,
) -> tuple[int, int | None] | None:
    """Returns (y_in_{0,1}, exit_bars) or None if labeling not possible."""
    feats = _decode_features(cand.get("features_json"))
    side = _resolve_side(cand, feats)
    if side is None:
        return None
    sl_pct = _resolve_pct(feats, cand, "sl_pct")
    tp_pct = _resolve_pct(feats, cand, "tp_pct")
    if sl_pct is None or tp_pct is None:
        # Fallback per market_type: futures default 2.5% / 5%, spot 4% / 8%.
        if market_type == "futures":
            sl_pct, tp_pct = 0.025, 0.05
        else:
            sl_pct, tp_pct = 0.04, 0.08

    sym = str(cand["symbol"])
    pair = _coin_to_pair(sym)
    ts = int(cand["ts"])
    tf_sec = _TF_TO_SECONDS[tf]
    # Need at least time_bars forward bars + 5-bar headroom for the entry bar.
    df = load_ohlcv_window(
        pair, tf,
        ts - tf_sec * 5,
        ts + tf_sec * (time_bars + 5),
        fetcher=fetcher,
    )
    if df is None or len(df) < (time_bars + 2):
        return None
    # Locate the entry bar = the first bar with ts >= cand.ts
    arr_ts = df["ts"].astype("int64").to_numpy()
    idx = int(np.searchsorted(arr_ts, ts, side="left"))
    if idx >= len(df) - 2:
        return None

    label = triple_barrier(
        highs=df["high"].astype(float).to_numpy(),
        lows=df["low"].astype(float).to_numpy(),
        closes=df["close"].astype(float).to_numpy(),
        entry_idx=idx,
        side=side,
        tp_pct=tp_pct,
        sl_pct=sl_pct,
        time_bars=time_bars,
    )
    if label == 0:
        return None  # drop time-bar firings
    y = 1 if label == 1 else 0
    return (y, None)  # exit_bars left None for now (labeler returns the label only)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--market", choices=["futures", "spot"], default="futures")
    parser.add_argument("--time-bars", type=int, default=None,
                        help=f"override default {DEFAULT_TIME_BARS}")
    parser.add_argument("--tf", default=None,
                        help=f"override OHLCV timeframe; defaults {DEFAULT_TF}")
    parser.add_argument("--since", default=None,
                        help="ISO date YYYY-MM-DD; only label candidates after this ts")
    parser.add_argument("--max-rows", type=int, default=None)
    parser.add_argument("--symbols", default=None)
    parser.add_argument("--print-every", type=int, default=200)
    args = parser.parse_args()

    time_bars = args.time_bars or DEFAULT_TIME_BARS[args.market]
    tf = args.tf or DEFAULT_TF[args.market]
    print(f"market={args.market} tf={tf} time_bars={time_bars}")

    wh = get_warehouse()
    ex = _binance_public()
    fetcher = _fetcher(ex)

    sql = (
        "SELECT id, ts, symbol, side, market_type, features_json "
        "FROM candidates "
        "WHERE features_json IS NOT NULL AND length(features_json) > 100"
    )
    params: list = []
    # Market-type column on candidates is reliable since the systematic_v3_1
    # path always sets it. Filter accordingly.
    sql += " AND market_type = ?"
    params.append(args.market)
    if args.since:
        dt = datetime.strptime(args.since, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        sql += " AND ts >= ?"
        params.append(dt.timestamp())
    if args.symbols:
        wl = [s.strip() for s in args.symbols.split(",") if s.strip()]
        ph = ",".join("?" for _ in wl)
        sql += f" AND symbol IN ({ph})"
        params.extend(wl)
    sql += " ORDER BY ts"
    if args.max_rows:
        sql += f" LIMIT {int(args.max_rows)}"

    rows = wh.query(sql, params)
    if not rows:
        print("no candidates match")
        return 0
    print(f"loaded {len(rows)} candidates; labeling...")

    written = 0
    skipped = 0
    n_pos = 0
    n_neg = 0
    t0 = time.time()
    for i, r in enumerate(rows):
        out = _label_one(
            cand=r, market_type=args.market,
            time_bars=time_bars, tf=tf, fetcher=fetcher,
        )
        if out is None:
            skipped += 1
            continue
        y, exit_bars = out
        wh.record_label(
            candidate_id=int(r["id"]),
            ts=float(r["ts"]),
            symbol=str(r["symbol"]),
            side=str(r["side"] or "buy"),
            market_type=args.market,
            y=int(y),
            exit_bars=exit_bars,
            label_horizon_bars=int(time_bars),
            sl_pct=None,
            tp_pct=None,
        )
        written += 1
        n_pos += int(y == 1)
        n_neg += int(y == 0)
        if (i + 1) % args.print_every == 0:
            elapsed = time.time() - t0
            print(f"  [{i+1}/{len(rows)}] written={written} skipped={skipped} "
                  f"WR={n_pos/max(1,n_pos+n_neg):.3f} rate={(i+1)/max(1e-3,elapsed):.1f}/s")

    print(f"done -- written={written} skipped={skipped} "
          f"WR={n_pos/max(1,n_pos+n_neg):.3f} elapsed={time.time()-t0:.0f}s")
    counts = wh.query(
        "SELECT y, COUNT(*) c FROM labels WHERE market_type=? GROUP BY y",
        (args.market,)
    )
    print(f"labels[{args.market}] now: {counts}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
