"""Single-shot F1 delta-neutral carry PAPER runner (Rev 5 Phase 3, W2 two-venue).

Copies the harvest_derivs pattern: one pass per invocation, scheduled
externally (e.g. Windows Task Scheduler, ~15 min). Each run loops the PAPER
venues (binance, bybit — opted in via the build_f1_spec Rev-5 latch) over ONE
shared ``data/carry_positions.json`` state file, reads a live BTC/ETH snapshot
per venue (spot BBO/depth, perp BBO/mark, Phase-1 funding frame incl interval +
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
HEARTBEAT_PATH = ROOT / "data" / "carry_heartbeat.json"
VENUES = ("binance", "bybit")
SYMBOLS = ("BTC/USDT", "ETH/USDT")
# EXIT-SIDE FALLBACK ONLY: entry gating and exit management now compute a
# per-position margin buffer via fill_reality.perp_short_margin_buffer_x;
# this constant is used only when that computation is impossible mid-hold
# (the runner logs {"liq_buffer_fallback": true} when it happens).
PAPER_LIQ_BUFFER_X = 10.0
PAPER_NOTIONAL_HINT = 500.0  # depth_ratio denominator (paper clip size)


def _bbo_depth(book: dict) -> tuple[float, float, float] | None:
    """(bid, ask, top5_notional) from a ccxt order book; None if unusable."""
    bids, asks = book.get("bids") or [], book.get("asks") or []
    if not bids or not asks:
        return None
    top5 = sum(p * q for p, q in bids[:5]) + sum(p * q for p, q in asks[:5])
    return float(bids[0][0]), float(asks[0][0]), float(top5)


def build_live_snapshot_provider(venue: str):
    """Ledger + exchange-backed snapshot provider for ``venue`` (lazy imports).

    Uses the WRAPPED ``client.fetch_order_book(symbol, limit, market_type=...)``
    — Bybit requires the wrapper's _defaultType_lock; never call
    ``client.exchange.fetch_order_book`` directly.
    """
    from core.cost_model import round_trip_cost
    from core.market_data_ledger import MarketDataLedger
    from exchanges.binance_client import BinanceClient
    from exchanges.bybit_client import BybitClient

    factory = {"binance": BinanceClient, "bybit": BybitClient}
    client = factory[venue]()
    # Single-venue ledger: this provider only reads its own venue, and a
    # multi-client ledger would double the funding API calls per pass.
    ledger = MarketDataLedger(clients={venue: client})
    rt_cost = round_trip_cost(venue, market_type="futures")

    def provider(symbol: str) -> dict | None:
        coin = symbol.split("/")[0]
        now = time.time()
        try:
            spot = _bbo_depth(
                client.fetch_order_book(symbol, limit=5, market_type="spot"))
            perp = _bbo_depth(
                client.fetch_order_book(f"{symbol}:USDT", limit=5,
                                        market_type="futures"))
            mi = ledger.mark_index(symbol).get(venue) or {}
            funding = dict((ledger.funding([coin]).get(coin) or {}).get(venue) or {})
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
    from research.funding_carry_lab import build_f1_spec

    # Rev-5 documented opt-in latch: raises loudly at startup if this venue
    # set is not explicitly allowed (bybit is outside the F1 default universe).
    build_f1_spec(symbols=SYMBOLS, venues=VENUES, allow_extended_universe=True)
    warehouse = Warehouse()
    summaries = []
    for venue in VENUES:  # sequential; ONE shared state + heartbeat file
        runner = CarryRunner(
            state_path=STATE_PATH,
            snapshot_provider=build_live_snapshot_provider(venue),
            symbols=SYMBOLS,
            venue=venue,
            gate_log_path=ROOT / "data" / "carry_gate_log.jsonl",
            warehouse=warehouse,
            registry_path=ROOT / "data" / "strategy_specs",
            heartbeat_path=HEARTBEAT_PATH,
        )
        summaries.append(runner.run_once())
    print(f"[f1_carry_paper] PAPER pass: {json.dumps(summaries)}")


if __name__ == "__main__":
    main()
