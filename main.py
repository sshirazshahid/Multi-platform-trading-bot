"""
main.py — Single-Profile Bot Entry Point

Runs a single bot instance using the risk parameters from config.py.
For running all three profiles simultaneously, use multi_profile_main.py instead.

Usage:
    python main.py           # start the bot
    python main.py --status  # show open positions and summary
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))


def main():
    if "--status" in sys.argv:
        _print_status()
        return

    from core.bot_engine import BotEngine
    engine = BotEngine()
    engine.run()


def _print_status():
    import json

    path = Path("data/positions.json")
    if not path.exists():
        print("\n  No position data yet. Run the bot first.\n")
        return

    data  = json.loads(path.read_text(encoding="utf-8"))
    open_ = data.get("open",   [])
    closed = data.get("closed", [])

    print(f"\n  Open positions: {len(open_)}")
    for pos in open_:
        print(f"    {pos.get('exchange','?')} {pos.get('side','?').upper()} "
              f"{pos.get('symbol','?')} @ {pos.get('entry_price', 0):.6g}")

    wins  = sum(1 for p in closed if (p.get("pnl") or 0) > 0)
    total = len(closed)
    pnl   = sum(p.get("pnl") or 0 for p in closed)
    wr    = (wins / total * 100) if total else 0

    print(f"\n  Closed trades : {total}")
    print(f"  Win rate      : {wr:.1f}%")
    print(f"  Net P&L       : {pnl:+.4f} USDT")
    print()


if __name__ == "__main__":
    main()
