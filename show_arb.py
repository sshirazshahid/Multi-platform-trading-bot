"""
show_arb.py — Print the last arbitrage scan results.
"""

import json
from pathlib import Path


def main():
    path = Path("data/arbitrage/opportunities.json")
    if not path.exists():
        print("\n  No arbitrage data yet. Run the bot (Option M) first.\n")
        return

    data = json.loads(path.read_text(encoding="utf-8"))
    scan = data.get("scan_count", 0)
    opps = data.get("opportunities", [])
    pnl  = data.get("total_pnl", 0)

    print(f"\n  Arbitrage — Scan #{scan}  |  Total P&L: {pnl:+.4f} USDT")
    print("  " + "─" * 64)

    if not opps:
        print("  No opportunities in last scan.\n")
        return

    print(f"  {'Symbol':<14} {'Buy':^10} {'Sell':^10} {'Net Spread':>10} "
          f"{'Conf':>6} {'Z':>5}")
    print("  " + "─" * 64)

    for o in opps[:10]:
        ns   = o.get("net_spread", 0) * 100
        conf = o.get("confidence", 0)
        z    = o.get("zscore", 0)
        print(f"  {o.get('symbol','?'):<14} "
              f"{o.get('buy_ex','?')[:9]:<10} "
              f"{o.get('sell_ex','?')[:9]:<10} "
              f"{ns:>9.3f}%  {conf:>5.0%}  {z:>4.1f}")
    print()


if __name__ == "__main__":
    main()
