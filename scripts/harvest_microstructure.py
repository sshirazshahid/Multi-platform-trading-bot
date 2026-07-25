"""Harvest one microstructure_features row per spec-universe symbol (LOG-ONLY).

Runs 5 minutes after each 4h bar close (Windows schtask
``TradingBot_MicrostructureHarvest`` at 00:05/04:05/08:05/12:05/16:05/20:05)
and writes ONE row per symbol into data/warehouse.sqlite
``microstructure_features``, keyed on the just-CLOSED 4h bar's open epoch so
rows join to probe signal_bar_ts directly. See core/microstructure_store.py
for the honest end-of-bar-window semantics (REST fetch_trades reaches ~the
last 1000 trades only; window_trades_n discloses the sample size).

Universe: resolved EXACTLY like the bundle-MR probes —
core.agents.bundle_mr_probe_agent.resolve_universe(venue='bybit') on the
active PAPER-futures spec artifacts (fail-closed to the frozen 5-major
basket). Data: keyless ccxt bybit REST (fetch_order_book limit=50 -> top-20
features; fetch_trades). Fail-soft per symbol: an error or empty payload
skips that symbol and is counted; exit 0 unless ALL symbols fail.

This script imports NO order/risk/live path and can never place an order.
Paths are resolved from the repo root (never the cwd) because scheduled
tasks may start in System32 — the resolve_shadow_outcomes.py lesson.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from core.microstructure_store import (  # noqa: E402
    compute_book_features,
    compute_trade_features,
    ensure_schema,
    insert_row,
)

BAR_S = 4 * 3600
DEFAULT_DB = REPO_ROOT / "data" / "warehouse.sqlite"
SPEC_DIR = REPO_ROOT / "data" / "strategy_specs"
VENUE = "bybit"
SOURCE = "ccxt.bybit.rest"


def closed_bar_open_ts(now: float) -> int:
    """Open epoch of the most recently CLOSED 4h bar as of ``now``."""
    return (int(now) // BAR_S - 1) * BAR_S


def harvest(db_path: Path, *, now: float | None = None) -> tuple[int, list[str]]:
    """Fetch + persist one row per symbol. Returns (rows_inserted, failures)."""
    import ccxt  # local import: keyless REST client only

    from core.agents.bundle_mr_probe_agent import resolve_universe

    symbols, skipped = resolve_universe(venue=VENUE, spec_dir=SPEC_DIR)
    if skipped:
        print(f"[harvest] universe bases skipped by resolver: {', '.join(skipped)}")
    ex = ccxt.bybit({"enableRateLimit": True})
    ex.load_markets()

    now = time.time() if now is None else now
    bar_ts = closed_bar_open_ts(now)
    # WAL is already set on the warehouse file; timeout=30 rides out the
    # bot's cross-process writers (same posture as resolve_shadow_outcomes).
    conn = sqlite3.connect(str(db_path), timeout=30)
    try:
        ensure_schema(conn)
        inserted = 0
        failures: list[str] = []
        for sym in symbols:
            try:
                book = compute_book_features(ex.fetch_order_book(sym, limit=50))
                tf = compute_trade_features(ex.fetch_trades(sym, limit=1000))
            except Exception as exc:  # fail-soft per symbol, never abort the run
                failures.append(f"{sym}: {exc}")
                continue
            if book is None and tf is None:
                failures.append(f"{sym}: empty book AND empty trades")
                continue
            row = {
                "symbol": sym,
                "venue": VENUE,
                "bar_ts": bar_ts,
                "asof_ts": float(time.time()),
                "source": SOURCE,
                **(tf or {}),
                **(book or {}),
            }
            insert_row(conn, row)
            inserted += 1
    finally:
        conn.close()
    return inserted, failures


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--db", type=Path, default=DEFAULT_DB, help="warehouse sqlite path")
    args = ap.parse_args(argv)

    inserted, failures = harvest(args.db)
    print(f"[harvest] rows inserted: {inserted}; failures: {len(failures)}")
    for f in failures:
        print(f"[harvest]   skipped {f}")
    if inserted == 0 and failures:
        print("[harvest] ALL symbols failed — exiting nonzero")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
