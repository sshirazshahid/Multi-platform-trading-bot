"""
main.py — Entry point for the trading bot.

Usage:
    python main.py              # Start the live/dry-run bot
    python main.py --status     # Show current open positions & stats
"""

import sys
import argparse
from loguru import logger
from utils.logger import setup_logger
from config import DRY_RUN


def parse_args():
    parser = argparse.ArgumentParser(description="Binance + MEXC Trading Bot")
    parser.add_argument(
        "--status", action="store_true",
        help="Print current bot status and open positions, then exit"
    )
    return parser.parse_args()


def print_status():
    """Quick status dump without starting the full engine."""
    from core.position_tracker import PositionTracker
    from rich.console import Console
    from rich.table   import Table
    from rich         import box

    console  = Console()
    tracker  = PositionTracker()
    summary  = tracker.summary()
    open_pos = tracker.get_open()

    tbl = Table(title="Bot Status", box=box.ROUNDED)
    tbl.add_column("Metric",  style="cyan")
    tbl.add_column("Value",   style="green")
    tbl.add_row("Mode",          "DRY RUN" if DRY_RUN else "LIVE")
    tbl.add_row("Total Trades",  str(summary["total_trades"]))
    tbl.add_row("Win Rate",      f"{summary['win_rate']:.1f}%")
    tbl.add_row("Total PnL",     f"{summary['total_pnl']:+.4f} USDT")
    tbl.add_row("Open Positions",str(summary["open_positions"]))
    console.print(tbl)

    if open_pos:
        pos_tbl = Table(title="Open Positions", box=box.SIMPLE)
        pos_tbl.add_column("Exchange")
        pos_tbl.add_column("Symbol")
        pos_tbl.add_column("Side")
        pos_tbl.add_column("Type")
        pos_tbl.add_column("Strategy")
        pos_tbl.add_column("Entry")
        pos_tbl.add_column("SL")
        pos_tbl.add_column("TP")
        pos_tbl.add_column("Age (min)")
        for p in open_pos:
            pos_tbl.add_row(
                p.exchange, p.symbol, p.side.upper(), p.market_type,
                p.strategy,
                f"{p.entry_price:.4f}",
                f"{p.stop_loss:.4f}",
                f"{p.take_profit:.4f}",
                f"{p.duration_minutes:.0f}",
            )
        console.print(pos_tbl)
    else:
        console.print("[yellow]No open positions.[/yellow]")


def main():
    setup_logger()
    args = parse_args()

    if args.status:
        print_status()
        sys.exit(0)

    from core.bot_engine import BotEngine

    logger.info("Initialising trading bot...")
    bot = BotEngine()
    bot.run()


if __name__ == "__main__":
    main()
