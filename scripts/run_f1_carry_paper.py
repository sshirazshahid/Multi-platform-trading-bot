"""Single-shot F1 delta-neutral carry PAPER runner (Rev 5 Phase 3).

Copies the harvest_derivs pattern: one pass per invocation, scheduled
externally (e.g. Windows Task Scheduler, ~15 min). Each run loads
``data/carry_positions.json``, reads a live BTC/ETH Binance snapshot (spot
BBO/depth, perp BBO/mark, Phase-1 funding frame incl interval +
next_funding_ts, fees via cost_model), evaluates the full Rev-5 entry gate,
manages/settles open PAPER positions, and saves state atomically.

PAPER/SIM ONLY: no exchange order call exists on this path and there is no
directional fallback. Read-only w.r.t. the live bot.

    python scripts/run_f1_carry_paper.py            # one runner pass
    python scripts/run_f1_carry_paper.py --report   # emit the markdown report
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.carry_runner import (  # noqa: E402
    DEFAULT_STATE_PATH,
    CarryRunner,
    write_report,
)

STATE_PATH = ROOT / DEFAULT_STATE_PATH
VENUE = "binance"
SYMBOLS = ("BTC/USDT", "ETH/USDT")
# Delta-neutral 1x spot-hedged short perp: liquidation sits far from mark.
# Conservative fixed buffer used for the paper gate until a per-position
# liquidation model is wired (fill_reality.liquidation_price needs leverage).
PAPER_LIQ_BUFFER_X = 10.0
PAPER_NOTIONAL_HINT = 500.0  # depth_ratio denominator (paper clip size)


def _bbo_depth(book: dict) -> tuple[float, float, float] | None:
    """(bid, ask, top5_notional) from a ccxt order book; None if unusable."""
    bids, asks = book.get("bids") or [], book.get("asks") or []
    if not bids or not asks:
        return None
    top5 = sum(p * q for p, q in bids[:5]) + sum(p * q for p, q in asks[:5])
    return float(bids[0][0]), float(asks[0][0]), float(top5)


def build_live_snapshot_provider():
    """Ledger + exchange-backed snapshot provider (imports live deps lazily)."""
    from core.cost_model import round_trip_cost
    from core.market_data_ledger import MarketDataLedger
    from exchanges.binance_client import BinanceClient

    client = BinanceClient()
    ledger = MarketDataLedger(clients={VENUE: client})
    rt_cost = round_trip_cost(VENUE, market_type="futures")

    def provider(symbol: str) -> dict | None:
        coin = symbol.split("/")[0]
        now = time.time()
        try:
            spot = _bbo_depth(client.exchange.fetch_order_book(symbol, limit=5))
            perp = _bbo_depth(
                client.exchange.fetch_order_book(f"{symbol}:USDT", limit=5)
            )
            mi = ledger.mark_index(symbol).get(VENUE) or {}
            funding = dict((ledger.funding([coin]).get(coin) or {}).get(VENUE) or {})
        except Exception:  # noqa: BLE001 - fail-open: runner logs no_snapshot
            return None
        if spot is None or perp is None or not funding:
            return None
        return {
            "spot_bid": spot[0],
            "spot_ask": spot[1],
            "perp_bid": perp[0],
            "perp_ask": perp[1],
            "perp_mark": mi.get("mark") or (perp[0] + perp[1]) / 2.0,
            "depth_ratio": min(spot[2], perp[2]) / PAPER_NOTIONAL_HINT,
            "liq_buffer_x": PAPER_LIQ_BUFFER_X,
            "both_legs_fillable": True,
            "avg_funding_7d": None,  # optional gate input (pass-through)
            "round_trip_cost_frac": rt_cost,
            "funding": funding,
            "ts": now,
        }

    return provider


def run_report_only(
    *, state_path: Path | str = STATE_PATH, out_dir: Path | str = ROOT / "reports"
) -> Path:
    """Emit reports/f1_carry_report_<YYYYMMDD>.md from the persisted state."""
    p = Path(state_path)
    state = (
        json.loads(p.read_text(encoding="utf-8"))
        if p.exists()
        else {"positions": {}, "blocks": {}, "cycles": []}
    )
    return write_report(state, out_dir=out_dir)


def main() -> None:
    if "--report" in sys.argv[1:]:
        out = run_report_only()
        print(f"[f1_carry_paper] report -> {out}")
        return
    from core.warehouse import Warehouse

    runner = CarryRunner(
        state_path=STATE_PATH,
        snapshot_provider=build_live_snapshot_provider(),
        symbols=SYMBOLS,
        venue=VENUE,
        gate_log_path=ROOT / "data" / "carry_gate_log.jsonl",
        warehouse=Warehouse(),
        registry_path=ROOT / "data" / "strategy_specs",
    )
    summary = runner.run_once()
    print(f"[f1_carry_paper] PAPER pass: {json.dumps(summary)}")


if __name__ == "__main__":
    main()
