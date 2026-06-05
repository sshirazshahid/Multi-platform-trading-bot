"""Backfill beta-adjusted alpha-vs-BTC labels onto closed trades (idempotent).

Why: realized PnL conflates entry alpha with BTC market beta. A net-short book that
profits during a BTC dump looks like "edge" but is beta — the documented 2026-06-04
failure mode. This labels every closed trade with alpha = trade_return -
side*beta*btc_return (in $), so the learning substrate can separate the two.

Two-phase by design: trades close with the label NULL (no network on the close path);
this script (and the learning cycle) fill it later from cached BTC OHLCV.

Run:
    python scripts/backfill_btc_alpha.py            # label all unlabeled closed trades
    python scripts/backfill_btc_alpha.py --dry-run  # compute + print, no DB writes
    python scripts/backfill_btc_alpha.py --fetch     # fetch missing BTC 15m via ccxt
"""
from __future__ import annotations

import argparse
import statistics
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv()

from core.btc_alpha import label_trades  # noqa: E402
from core.feature_store import load_ohlcv_window  # noqa: E402
from core.warehouse import get_warehouse  # noqa: E402


def _ccxt_fetcher():
    """Public BTC/USDT perp 15m fetcher (no auth) for cache misses."""
    import ccxt

    ex = ccxt.binance({"options": {"defaultType": "future"}, "enableRateLimit": True})

    def fetch(symbol, timeframe, since_ms, limit):
        return ex.fetch_ohlcv(symbol, timeframe, since=since_ms, limit=limit)

    return fetch


def _d(ts: float) -> str:
    return datetime.fromtimestamp(float(ts), tz=timezone.utc).strftime("%Y-%m-%d %H:%M")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=1_000_000)
    ap.add_argument("--beta", type=float, default=1.0)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--fetch", action="store_true",
                    help="fetch missing BTC 15m bars via ccxt (public, no auth)")
    args = ap.parse_args()
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    wh = get_warehouse()
    rows = wh.query(
        """SELECT id, ts_entry, ts_exit, entry_px, exit_px, size, side,
                  realized_pnl, partial_realized_pnl
           FROM trades
           WHERE status='CLOSED' AND alpha_vs_btc IS NULL
             AND ts_entry IS NOT NULL AND ts_exit IS NOT NULL
             AND entry_px IS NOT NULL AND exit_px IS NOT NULL
           ORDER BY ts_entry LIMIT ?""",
        (args.limit,),
    )
    print(f"=== BTC-alpha backfill ===\nunlabeled closed trades: {len(rows)}")
    if not rows:
        print("nothing to label — all closed trades already have alpha_vs_btc.")
        return

    min_ts = min(float(r["ts_entry"]) for r in rows) - 900
    max_ts = max(float(r["ts_exit"]) for r in rows) + 900
    print(f"trade window: {_d(min_ts)} -> {_d(max_ts)} UTC")

    fetcher = _ccxt_fetcher() if args.fetch else None
    btc = load_ohlcv_window("BTC/USDT", "15m", int(min_ts), int(max_ts), fetcher=fetcher)
    n_bars = 0 if btc is None else len(btc)
    print(f"BTC 15m bars available in window: {n_bars}")
    if n_bars == 0:
        print("[abort] BTC 15m cache is empty for this window. Re-run with --fetch, "
              "or run scripts/backfill_universe_ohlcv.py first.")
        return
    cov0, cov1 = float(btc["ts"].iloc[0]), float(btc["ts"].iloc[-1])
    print(f"BTC coverage: {_d(cov0)} -> {_d(cov1)} UTC")

    labeled = label_trades(rows, btc, beta=args.beta)
    skipped = len(rows) - len(labeled)
    alphas = [a for (_id, a, _br, _b) in labeled]
    if alphas:
        pos = sum(1 for a in alphas if a > 0)
        print(
            f"labelable: {len(labeled)}  skipped(no BTC cover): {skipped}\n"
            f"alpha_vs_btc: sum=${sum(alphas):.2f}  mean=${statistics.mean(alphas):+.4f}  "
            f"median=${statistics.median(alphas):+.4f}  pos={pos}/{len(alphas)} "
            f"({100 * pos / len(alphas):.0f}%)"
        )
        # Honest beta context: raw realized PnL over the SAME labelable trades.
        raw = [
            (float(r["realized_pnl"] or 0.0) + float(r["partial_realized_pnl"] or 0.0))
            for r in rows if r["id"] in {lid for (lid, *_x) in labeled}
        ]
        print(f"raw PnL (same trades): sum=${sum(raw):.2f}  => beta share ≈ "
              f"${sum(raw) - sum(alphas):+.2f}")
    else:
        print("no trades labelable (BTC window did not cover any trade).")

    if args.dry_run:
        print("[dry-run] no DB writes.")
        return
    updates = [(a, br, b, tid) for (tid, a, br, b) in labeled]
    n = wh.update_btc_alpha(updates)
    print(f"wrote {n} alpha labels to trades.alpha_vs_btc.")


if __name__ == "__main__":
    main()
