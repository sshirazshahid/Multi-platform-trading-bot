"""
multi_profile_main.py — Multi-Profile Learning Entry Point

Runs all three risk profiles (Conservative, Moderate, Aggressive) simultaneously
on all connected exchanges. This is the recommended way to run the bot.

Usage:
    python multi_profile_main.py           # start the bot
    python multi_profile_main.py --report  # print the last comparison report
"""

import sys
from pathlib import Path

# Ensure the project root is on sys.path when run directly
sys.path.insert(0, str(Path(__file__).parent))


def main():
    if "--report" in sys.argv:
        _print_report()
        return

    from core.multi_profile_runner import MultiProfileRunner
    runner = MultiProfileRunner()
    runner.run()


def _print_report():
    import json
    path = Path("data/profiles/comparison.json")
    if not path.exists():
        print("\n  No comparison report yet. Run the bot first.\n")
        return

    data = json.loads(path.read_text(encoding="utf-8"))
    updated = data.get("updated_at", "?")[:19]
    leader  = data.get("leader", "?")
    recommend = data.get("recommendation", "")

    print(f"\n  Multi-Profile Report  ({updated})")
    print("  " + "─" * 60)
    print(f"  {'Profile':<16} {'Balance':>9} {'Return':>8} {'WR':>6} {'PnL':>10} {'DD':>6}")
    print("  " + "─" * 60)

    for name in data.get("ranked", []):
        d   = data.get("profiles", {}).get(name, {})
        bal = d.get("balance", 0)
        ret = d.get("return_pct", 0)
        wr  = d.get("win_rate", 0)
        pnl = d.get("net_pnl", 0)
        dd  = d.get("max_drawdown", 0)
        tag = " ← LEADER" if name == leader else ""
        hlt = " [HALTED]" if d.get("is_halted") else ""
        print(f"  {name.upper():<16} ${bal:>8.2f} {ret:>+7.2f}% "
              f"{wr:>5.1f}% {pnl:>+9.4f} {dd:>5.1f}%{tag}{hlt}")

    print()
    if recommend:
        print(f"  {recommend}")
    print()


if __name__ == "__main__":
    main()
