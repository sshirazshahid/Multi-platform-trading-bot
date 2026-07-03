"""Single-shot per-venue funding harvester for the F1 carry gate (PAPER).

Copies the repo's harvester pattern (one pass per invocation, scheduled
externally via Windows Task Scheduler, ~hourly): for each venue x coin, read
the live funding frame from MarketDataLedger and append it to the rolling
history in ``data/funding_carry/`` (one row per funding period — see
core/funding_history). This history activates the F1 entry gate's
``avg_funding_7d`` regime check, which was a documented None pass-through.

Read-only against exchanges (public funding endpoints); writes only local CSVs.

Schedule:
    schtasks /Create /TN TradingBot-FundingCarryHarvest /TR
      "cmd /c cd /d D:\\Downloads\\Trading_Bot && venv\\Scripts\\python.exe
       scripts\\harvest_funding_carry.py" /SC HOURLY
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.funding_history import record  # noqa: E402

VENUES = ("binance", "bybit", "bitget")
COINS = ("BTC", "ETH")


def main() -> None:
    from core.market_data_ledger import MarketDataLedger
    from exchanges.binance_client import BinanceClient
    from exchanges.bitget_client import BitgetClient
    from exchanges.bybit_client import BybitClient

    factory = {"binance": BinanceClient, "bybit": BybitClient, "bitget": BitgetClient}
    summary: dict[str, int] = {"recorded": 0, "skipped": 0, "errors": 0}
    for venue in VENUES:
        try:
            ledger = MarketDataLedger(clients={venue: factory[venue]()})
            frames = ledger.funding(list(COINS))
        except Exception as e:  # noqa: BLE001 - one venue down must not kill the pass
            print(f"[funding_carry] {venue}: ledger error: {e}")
            summary["errors"] += len(COINS)
            continue
        for coin in COINS:
            frame = dict((frames.get(coin) or {}).get(venue) or {})
            try:
                wrote = record(venue, coin, frame)
            except Exception as e:  # noqa: BLE001 - keep harvesting the rest
                print(f"[funding_carry] {venue}/{coin}: record error: {e}")
                summary["errors"] += 1
                continue
            summary["recorded" if wrote else "skipped"] += 1
    print(f"[funding_carry] pass: {json.dumps(summary)}")


if __name__ == "__main__":
    main()
