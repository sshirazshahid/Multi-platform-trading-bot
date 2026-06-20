"""SPOT-PROTECT-V1 Component A: dust consolidation (one-shot operator script).

Background
==========
Spec: docs/superpowers/specs/2026-05-01-spot-protect-v1-design.md

`data/spot_portfolio.json` shows ~32 spot holdings across 3 exchanges
totaling ~$1,052. ~17 of those positions are < $30 each — "dust" that
is fee-eaten if traded (round-trip 0.2% on $20 = $0.04, eats any 10%
gain) and pollutes aggregate stats.

This script sells every spot position with USD value <
`SPOT_STRATEGY.dust_cutoff_usd` (default $25) to USDT on the same
exchange. Marks consolidated rows in `data/spot_dust_consolidated.json`
so a re-run doesn't sell positions that drift back under the threshold
(which would chase any small position that just cooled off — exact
opposite of what we want).

Safety
======
1. Refuses to run when `heartbeat.json` is < 120s old (live bot may
   write balances/positions during the script's window). Override with
   `--force`.
2. Dry-run by default — print the plan, don't execute. `--commit`
   actually places sell orders.
3. Idempotent: a second `--commit` run skips already-marked positions.
4. Records every sell in the marker file with timestamp, price, qty,
   and proceeds for audit.
5. Stablecoins (USDT/USDC/BUSD/USD) never get touched.

Usage
=====
    python scripts/consolidate_spot_dust.py            # dry-run
    python scripts/consolidate_spot_dust.py --commit   # apply
    python scripts/consolidate_spot_dust.py --commit --force   # bypass HB
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HB_PATH = ROOT / "data" / "heartbeat.json"
PORTFOLIO_PATH = ROOT / "data" / "spot_portfolio.json"
MARKER_PATH = ROOT / "data" / "spot_dust_consolidated.json"

STABLES = {"USDT", "USD", "USDC", "BUSD"}


def heartbeat_age_seconds() -> float | None:
    if not HB_PATH.exists():
        return None
    try:
        hb = json.loads(HB_PATH.read_text(encoding="utf-8"))
        ts = hb.get("timestamp", "")
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        return (datetime.now(timezone.utc) - dt).total_seconds()
    except Exception:
        return None


def load_marker() -> dict:
    """Returns {(exchange, coin): row} for already-consolidated positions."""
    if not MARKER_PATH.exists():
        return {}
    try:
        data = json.loads(MARKER_PATH.read_text(encoding="utf-8"))
        out = {}
        for row in data.get("consolidated", []):
            key = (row.get("exchange"), row.get("coin"))
            out[key] = row
        return out
    except Exception:
        return {}


def save_marker(rows: list[dict]) -> None:
    """Append `rows` to the marker file (preserves prior history)."""
    existing: list[dict] = []
    if MARKER_PATH.exists():
        try:
            data = json.loads(MARKER_PATH.read_text(encoding="utf-8"))
            existing = data.get("consolidated", []) or []
        except Exception:
            pass
    existing.extend(rows)
    MARKER_PATH.parent.mkdir(parents=True, exist_ok=True)
    MARKER_PATH.write_text(
        json.dumps({"consolidated": existing}, indent=2),
        encoding="utf-8",
    )


def dust_candidates(portfolio: dict, marker: dict, cutoff: float) -> list[dict]:
    """Walk the portfolio file, return rows worth < cutoff USD that
    aren't already in the marker."""
    candidates: list[dict] = []
    for ex_name, holdings in portfolio.items():
        if not isinstance(holdings, dict):
            continue
        for coin, info in holdings.items():
            if not isinstance(info, dict):
                continue
            if coin in STABLES:
                continue
            balance = float(info.get("balance", 0) or 0)
            price = float(info.get("current_price", 0) or 0)
            value = balance * price
            if value <= 0:
                continue
            if value >= cutoff:
                continue
            if (ex_name, coin) in marker:
                continue
            candidates.append(
                {
                    "exchange": ex_name,
                    "coin": coin,
                    "balance": balance,
                    "price": price,
                    "value_usdt": value,
                    "symbol": f"{coin}/USDT",
                }
            )
    return candidates


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument(
        "--commit", action="store_true", help="Actually place sell orders. Default is dry-run."
    )
    ap.add_argument("--force", action="store_true", help="Bypass heartbeat freshness guard.")
    ap.add_argument(
        "--cutoff", type=float, default=None, help="Override SPOT_STRATEGY.dust_cutoff_usd (USD)."
    )
    args = ap.parse_args()

    if not PORTFOLIO_PATH.exists():
        print(f"spot_portfolio.json not found: {PORTFOLIO_PATH}", file=sys.stderr)
        return 1

    # Heartbeat guard
    age = heartbeat_age_seconds()
    if args.commit and not args.force and age is not None and age < 120:
        print(
            f"REFUSED: heartbeat is {age:.0f}s old — bot is running and may "
            f"alter balances mid-script. Stop the bot, or pass --force.",
            file=sys.stderr,
        )
        return 2

    # Resolve cutoff
    cutoff = args.cutoff
    if cutoff is None:
        try:
            sys.path.insert(0, str(ROOT))
            from config import SPOT_STRATEGY as _SS

            cutoff = float(_SS.get("dust_cutoff_usd", 25.0))
        except Exception:
            cutoff = 25.0

    portfolio = json.loads(PORTFOLIO_PATH.read_text(encoding="utf-8"))
    marker = load_marker()
    cands = dust_candidates(portfolio, marker, cutoff)

    if not cands:
        already = len(marker)
        print(
            f"No dust to consolidate. (cutoff=${cutoff:.2f}, {already} positions already in marker)"
        )
        return 0

    print(f"Dust cutoff: ${cutoff:.2f}")
    print(f"Already-consolidated marker rows: {len(marker)}")
    print(f"New dust candidates ({len(cands)}):")
    print(
        "  {:<10}  {:<8}  {:>14}  {:>10}  {:>10}".format(
            "exchange", "coin", "balance", "price", "value_usd"
        )
    )
    total_value = 0.0
    for c in cands:
        print(
            "  {:<10}  {:<8}  {:>14.6f}  {:>10.4f}  {:>10.2f}".format(
                c["exchange"], c["coin"], c["balance"], c["price"], c["value_usdt"]
            )
        )
        total_value += c["value_usdt"]
    print(f"Total value to consolidate: ${total_value:.2f}")

    if not args.commit:
        print()
        print("DRY RUN - no orders placed. Re-run with --commit to apply.")
        return 0

    # Real-mode: place market sells
    print()
    print("Placing market sells...")
    try:
        sys.path.insert(0, str(ROOT))
        from config import OPERATING_MODE
        from exchanges.binance_client import BinanceClient
        from exchanges.bitget_client import BitgetClient
        from exchanges.bybit_client import BybitClient
    except Exception as e:
        print(f"Could not import exchange clients: {e}", file=sys.stderr)
        return 1

    if OPERATING_MODE != "CONTROLLED_LIVE":
        print(
            f"REFUSED: OPERATING_MODE={OPERATING_MODE} — refuses to "
            f"sell unless mode is CONTROLLED_LIVE."
        )
        return 3

    clients: dict = {}
    try:
        clients["binance"] = BinanceClient()
        clients["bybit"] = BybitClient()
        clients["bitget"] = BitgetClient()
    except Exception as e:
        print(f"Exchange client init failed: {e}", file=sys.stderr)
        return 1

    completed: list[dict] = []
    failed: list[dict] = []
    for c in cands:
        ex = clients.get(c["exchange"].lower())
        if ex is None or not getattr(ex, "_connected", False):
            failed.append({**c, "error": "exchange not connected"})
            continue
        try:
            order = ex.create_order(
                symbol=c["symbol"],
                order_type="market",
                side="sell",
                amount=c["balance"],
                market_type="spot",
            )
            now_ts = time.time()
            row = {
                "ts": now_ts,
                "ts_iso": datetime.fromtimestamp(now_ts, tz=timezone.utc).isoformat(),
                "exchange": c["exchange"],
                "coin": c["coin"],
                "symbol": c["symbol"],
                "balance_sold": c["balance"],
                "price_at_decision": c["price"],
                "expected_proceeds_usd": c["value_usdt"],
                "order_id": (order.get("id") if isinstance(order, dict) else None),
            }
            completed.append(row)
            print(
                f"  SOLD: {c['exchange']:<8} {c['coin']:<8} "
                f"qty={c['balance']:.6g} ~${c['value_usdt']:.2f}"
            )
        except Exception as e:
            failed.append({**c, "error": str(e)[:200]})
            print(f"  FAIL: {c['exchange']} {c['coin']}: {e}", file=sys.stderr)

    if completed:
        save_marker(completed)
        print()
        print(f"Marker updated: {len(completed)} new rows in {MARKER_PATH}")

    print()
    print(f"Summary: {len(completed)} sold, {len(failed)} failed.")
    return 0 if not failed else 4


if __name__ == "__main__":
    sys.exit(main())
