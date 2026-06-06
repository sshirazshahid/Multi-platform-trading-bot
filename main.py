"""
main.py — Entry point for the trading bot.

Usage:
    python main.py              # Start the live/dry-run bot
    python main.py --status     # Show current open positions & stats
"""

import sys
import time
import argparse
from loguru import logger
from utils.logger import setup_logger
from config import DRY_RUN


def parse_args():
    parser = argparse.ArgumentParser(description="Crypto Trading Bot")
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
                f"{p.entry_price:.4f}" if p.entry_price else "N/A",
                f"{p.stop_loss:.4f}" if p.stop_loss else "N/A",
                f"{p.take_profit:.4f}" if p.take_profit else "N/A",
                f"{p.duration_minutes:.0f}",
            )
        console.print(pos_tbl)
    else:
        console.print("[yellow]No open positions.[/yellow]")


def _classify_exit(exc: BaseException) -> str:
    """Classify a watchdog-caught exit as 'clean' (intentional → stop) or 'crash' (→ restart).

    bot_engine.run() signals a fatal main-loop crash with sys.exit(1) (a non-zero SystemExit). That
    must be treated as a CRASH so the watchdog restarts — NOT as a clean exit. Only a zero/None-code
    SystemExit or a KeyboardInterrupt is an intentional, do-not-restart shutdown.
    """
    if isinstance(exc, KeyboardInterrupt):
        return "clean"
    if isinstance(exc, SystemExit):
        code = exc.code
        return "clean" if (code is None or code == 0) else "crash"
    return "crash"  # any other Exception is a crash


def run_with_watchdog():
    """Watchdog wrapper — restarts the bot on crashes for 24/7 operation."""
    from core.bot_engine import BotEngine

    max_restarts = 10
    restart_window = 3600  # 1 hour
    restart_times = []

    while True:
        try:
            logger.info("[Watchdog] Initialising trading bot...")
            engine = BotEngine()
            engine.run()
            # If run() returns normally, exit cleanly
            break
        except (KeyboardInterrupt, SystemExit, Exception) as e:
            # A non-zero SystemExit is bot_engine.run()'s fatal-crash signal (sys.exit(1)) — restart
            # it; do NOT mistake it for a clean exit (the bug that left the bot permanently down).
            if _classify_exit(e) == "clean":
                logger.info("[Watchdog] Clean exit requested")
                break  # Intentional exit
            # loguru ignores the stdlib `exc_info` kwarg — use .opt(exception=True)
            # so the full traceback lands in the log. 2026-04-20: previously
            # `'list' object has no attribute 'get'` crashes were invisible.
            logger.opt(exception=True).critical(f"[Watchdog] Bot crashed: {e}")

            # Check restart rate — append BEFORE guard so current crash is counted
            now = time.time()
            restart_times.append(now)
            restart_times = [t for t in restart_times if now - t < restart_window]
            if len(restart_times) > max_restarts:
                logger.critical(
                    f"[Watchdog] Too many restarts ({max_restarts} in "
                    f"{restart_window}s) — giving up")
                # Send critical alert
                try:
                    from utils import TelegramNotifier
                    TelegramNotifier().error(
                        f"BOT PERMANENTLY STOPPED: {max_restarts} crashes in "
                        f"{restart_window // 60} minutes.\n"
                        f"Last error: {e}")
                except Exception:
                    pass
                break
            cooldown = min(300, 30 * len(restart_times))  # 30s, 60s, 90s, ..., max 300s
            logger.info(
                f"[Watchdog] Restarting in {cooldown}s "
                f"(restart #{len(restart_times)})")
            time.sleep(cooldown)


def main():
    setup_logger()
    args = parse_args()

    if args.status:
        print_status()
        sys.exit(0)

    run_with_watchdog()


if __name__ == "__main__":
    main()
