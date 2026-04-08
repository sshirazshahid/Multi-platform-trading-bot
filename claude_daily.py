"""
claude_daily.py — Entry point for Claude AI-powered daily trading analysis.

Usage:
    python claude_daily.py           # Run analysis and execute paper trades
    python claude_daily.py --report  # Print summary of today's Claude trades
    python claude_daily.py --daemon  # Run once per day at 08:00 UTC indefinitely
"""

import sys
import argparse
from loguru import logger
from utils.logger import setup_logger


def print_report():
    """Print a summary of all Claude AI trades to the console."""
    import json
    from pathlib import Path
    from datetime import datetime

    trades_file = Path("data/claude_trades.json")
    report_file = Path("data/claude_analysis/latest_report.html")

    if not trades_file.exists():
        print("\n  No Claude trades yet. Run: python claude_daily.py\n")
        return

    trades = json.loads(trades_file.read_text(encoding="utf-8"))
    if not trades:
        print("\n  No Claude trades recorded yet.\n")
        return

    open_t   = [t for t in trades if t.get("status") == "open"]
    closed_t = [t for t in trades if t.get("status") != "open"]

    print()
    print("  =" * 34)
    print("  CLAUDE AI TRADES SUMMARY")
    print("  =" * 34)
    print("  Total trades  : {}".format(len(trades)))
    print("  Open          : {}".format(len(open_t)))
    print("  Closed        : {}".format(len(closed_t)))
    print()
    print("  {:<12} {:<10} {:<8} {:<10} {:<10} {:<10} {:<6}".format(
        "Symbol", "Side", "Type", "Entry", "SL", "TP", "Conf"
    ))
    print("  " + "-" * 68)
    for t in sorted(trades, key=lambda x: x.get("timestamp", ""), reverse=True)[:15]:
        ts  = t.get("timestamp", "")[:16]
        sym = t.get("symbol", "?")
        sd  = t.get("side", "?").upper()
        mt  = t.get("market_type", "spot")
        ep  = t.get("entry_price", 0)
        sl  = t.get("stop_loss", 0)
        tp  = t.get("take_profit", 0)
        cf  = t.get("confidence", 0)
        dr  = "[DRY]" if t.get("dry_run", True) else "[LIVE]"
        print("  {:<12} {:<10} {:<8} {:>10.4f} {:>10.4f} {:>10.4f} {:>5.0%} {}".format(
            sym, sd, mt, ep, sl, tp, cf, dr
        ))

    if report_file.exists():
        print()
        print("  Full HTML report: {}".format(report_file.resolve()))
    print()


def run_daemon():
    """Run Claude analysis once per day at 08:00 UTC."""
    import schedule
    import time
    from core.claude_trader import ClaudeTrader
    from config import DRY_RUN

    logger.info("[ClaudeDaily] Daemon mode — will run daily at 08:00 UTC")

    def daily_job():
        logger.info("[ClaudeDaily] Running scheduled daily analysis...")
        trader = ClaudeTrader(dry_run=DRY_RUN)
        trader.run()

    schedule.every().day.at("08:00").do(daily_job)

    # Also run immediately on first start
    daily_job()

    logger.info("[ClaudeDaily] Waiting for next scheduled run (08:00 UTC)...")
    try:
        while True:
            schedule.run_pending()
            time.sleep(30)
    except KeyboardInterrupt:
        logger.info("[ClaudeDaily] Daemon stopped.")


def main():
    setup_logger()
    parser = argparse.ArgumentParser(
        description="Claude AI Daily Trading Analysis"
    )
    parser.add_argument(
        "--report", action="store_true",
        help="Print trade summary and exit"
    )
    parser.add_argument(
        "--daemon", action="store_true",
        help="Run once per day at 08:00 UTC indefinitely"
    )
    args = parser.parse_args()

    if args.report:
        print_report()
        sys.exit(0)

    if args.daemon:
        run_daemon()
        sys.exit(0)

    # Single run
    from config import DRY_RUN
    from core.claude_trader import ClaudeTrader

    logger.info("[ClaudeDaily] Starting single analysis run...")
    trader = ClaudeTrader(dry_run=DRY_RUN)
    result = trader.run()

    if result:
        decisions = result.get("decisions", [])
        executed  = result.get("executed",  [])
        print()
        print("  Claude Analysis Complete")
        print("  Decisions  : {}".format(len(decisions)))
        print("  Executed   : {}".format(len(executed)))
        if executed:
            print()
            print("  Trades placed:")
            for t in executed:
                print("    {} {} {} @ {:.4f} (conf {:.0%})".format(
                    t.get("side","?").upper(),
                    t.get("symbol","?"),
                    t.get("market_type","spot"),
                    t.get("entry_price", 0),
                    t.get("confidence", 0),
                ))
        from pathlib import Path
        rpt = Path("data/claude_analysis/latest_report.html")
        if rpt.exists():
            print()
            print("  Full report: {}".format(rpt.resolve()))
        print()
    else:
        print("\n  Analysis failed. Check logs for details.\n")


if __name__ == "__main__":
    main()
