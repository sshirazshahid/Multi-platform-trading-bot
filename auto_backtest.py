"""
auto_backtest.py — Run ALL backtest modes automatically across all pairs/exchanges.

Runs 6 strategy backtests on all connected exchanges and saves results.
Usage: python auto_backtest.py [--days 30]
"""

import sys
import json
import time
from pathlib import Path
from datetime import datetime
from loguru import logger

# Strategies and their default timeframes
STRATEGIES = {
    "supertrend":     {"timeframe": "1h",  "market": "futures"},
    "multitf":        {"timeframe": "1h",  "market": "futures"},
    "trend":          {"timeframe": "15m", "market": "futures"},
    "meanreversion":  {"timeframe": "1h",  "market": "spot"},
    "scalping":       {"timeframe": "1m",  "market": "spot"},
    "grid":           {"timeframe": "1h",  "market": "spot"},
}

# Top pairs to backtest (most liquid)
TOP_PAIRS = [
    "BTC/USDT", "ETH/USDT", "SOL/USDT", "XRP/USDT",
    "BNB/USDT", "ADA/USDT", "DOGE/USDT", "AVAX/USDT",
]

RESULTS_DIR = Path("data/backtest_results")


def run_single_backtest(exchange, symbol, strategy_name, days=30, balance=1000.0):
    """Run a single backtest and return results dict."""
    import pandas as pd
    from core.backtester import Backtester
    from config import RISK

    cfg = STRATEGIES.get(strategy_name, {"timeframe": "1h", "market": "futures"})
    timeframe = cfg["timeframe"]
    market_type = cfg["market"]
    leverage = RISK.get("default_leverage", 5) if market_type == "futures" else 1

    try:
        # Fetch OHLCV data
        _tf_mins = {"1m": 1, "5m": 5, "15m": 15, "1h": 60, "4h": 240, "1d": 1440}
        limit = days * 24 * 60 // _tf_mins.get(timeframe, 60)
        raw = exchange.fetch_ohlcv(symbol, timeframe, limit=min(limit, 1000),
                                    market_type=market_type)
        if not raw or len(raw) < 50:
            return {"symbol": symbol, "strategy": strategy_name,
                    "exchange": exchange.name, "error": "insufficient data"}
        df = pd.DataFrame(raw, columns=["timestamp","open","high","low","close","volume"])
        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
        df.set_index("timestamp", inplace=True)

        # Build strategy object
        from core.order_manager import OrderManager
        from core.risk_manager import RiskManager
        from core.strategy_selector import _make_strategy
        risk = RiskManager()
        om = OrderManager(tracker=None, risk=risk, exchanges={}, dry_run=True)
        strategy = _make_strategy(strategy_name, om, risk, market_type)
        if not strategy:
            return {"symbol": symbol, "strategy": strategy_name,
                    "exchange": exchange.name, "error": "unknown strategy"}

        bt = Backtester(
            sl_pct=RISK.get("default_stop_loss", 0.04),
            tp_pct=RISK.get("default_take_profit", 0.10),
            initial_balance=balance, leverage=leverage,
        )
        result = bt.run(strategy, df, symbol)
        return {
            "symbol": symbol,
            "strategy": strategy_name,
            "exchange": exchange.name,
            "timeframe": timeframe,
            "market_type": market_type,
            "days": days,
            "total_trades": result.total_trades,
            "win_rate": result.win_rate,
            "total_pnl": result.total_pnl,
            "sharpe": result.sharpe,
        }
    except Exception as e:
        return {
            "symbol": symbol,
            "strategy": strategy_name,
            "exchange": exchange.name,
            "error": str(e)[:200],
        }


def run_all_backtests(days=30):
    """Run all strategies on all pairs on all connected exchanges."""
    from exchanges import BinanceClient, BybitClient, BitgetClient

    logger.info("=" * 60)
    logger.info("  AUTO-BACKTEST — All Strategies × All Pairs × All Exchanges")
    logger.info(f"  Period: {days} days | Balance: $1000 USDT")
    logger.info("=" * 60)

    # Connect exchanges
    exchanges = {}
    for name, cls in [("binance", BinanceClient),
                      ("bybit", BybitClient), ("bitget", BitgetClient)]:
        try:
            ex = cls()
            if getattr(ex, "_connected", False):
                exchanges[name] = ex
                logger.info(f"  [OK] {name.upper()} connected")
            else:
                logger.info(f"  [--] {name.upper()} not connected")
        except Exception:
            pass
    
    if not exchanges:
        logger.error("No exchanges connected!")
        return
    
    all_results = []
    total = len(STRATEGIES) * len(TOP_PAIRS) * len(exchanges)
    done = 0
    
    for strategy_name in STRATEGIES:
        for symbol in TOP_PAIRS:
            for ex_name, exchange in exchanges.items():
                done += 1
                logger.info(f"[{done}/{total}] {strategy_name} | {symbol} | {ex_name}")
                
                try:
                    result = run_single_backtest(exchange, symbol, strategy_name, days)
                    all_results.append(result)
                    
                    if "error" not in result:
                        wr = result.get("win_rate", 0)
                        pnl = result.get("total_pnl", 0)
                        trades = result.get("total_trades", 0)
                        wrc = "\033[92m" if wr >= 60 else ("\033[93m" if wr >= 50 else "\033[91m")
                        logger.info(
                            f"  {wrc}WR={wr:.1f}%\033[0m  PnL={pnl:+.2f}  trades={trades}")
                    else:
                        logger.debug(f"  Error: {result['error'][:80]}")
                    
                    time.sleep(0.5)  # Rate limiting
                except Exception as e:
                    logger.debug(f"  Failed: {e}")
    
    # Save results
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    results_file = RESULTS_DIR / f"backtest_{timestamp}.json"
    results_file.write_text(
        json.dumps(all_results, indent=2, default=str), encoding="utf-8")
    
    # Generate summary
    _print_summary(all_results)
    
    # Save summary
    summary_file = RESULTS_DIR / f"summary_{timestamp}.txt"
    summary_file.write_text(_build_summary_text(all_results), encoding="utf-8")
    
    logger.info(f"\nResults saved to: {results_file}")
    logger.info(f"Summary saved to: {summary_file}")
    

def _print_summary(results):
    """Print ranked summary of all backtests."""
    valid = [r for r in results if "error" not in r and r.get("total_trades", 0) > 0]
    
    if not valid:
        logger.info("No valid backtest results!")
        return
    
    # Rank by win rate × PnL
    valid.sort(key=lambda r: (r.get("win_rate", 0), r.get("total_pnl", 0)), reverse=True)
    
    logger.info("\n" + "=" * 80)
    logger.info("  BACKTEST RESULTS RANKED BY WIN RATE")
    logger.info("=" * 80)
    logger.info(f"  {'Strategy':<16} {'Symbol':<12} {'Exchange':<10} {'WR%':>6} "
                f"{'PnL':>10} {'Trades':>7} {'Best':>8} {'Worst':>8}")
    logger.info("  " + "-" * 75)
    
    for r in valid[:20]:
        wr = r.get("win_rate", 0)
        pnl = r.get("total_pnl", 0)
        trades = r.get("total_trades", 0)
        best = r.get("best_trade", 0)
        worst = r.get("worst_trade", 0)
        wrc = "\033[92m" if wr >= 60 else ("\033[93m" if wr >= 50 else "\033[91m")
        pc = "\033[92m" if pnl >= 0 else "\033[91m"
        
        logger.info(
            f"  {r['strategy']:<16} {r['symbol']:<12} {r['exchange']:<10} "
            f"{wrc}{wr:>5.1f}%\033[0m {pc}{pnl:>+9.2f}\033[0m "
            f"{trades:>7} {best:>+7.2f} {worst:>+7.2f}")
    
    # Best strategy per symbol
    logger.info("\n  BEST STRATEGY PER SYMBOL:")
    by_symbol = {}
    for r in valid:
        sym = r["symbol"]
        if sym not in by_symbol or r.get("win_rate", 0) > by_symbol[sym].get("win_rate", 0):
            by_symbol[sym] = r
    
    for sym, r in sorted(by_symbol.items()):
        logger.info(f"    {sym:<12} → {r['strategy']} on {r['exchange']} "
                    f"(WR={r.get('win_rate',0):.0f}% PnL={r.get('total_pnl',0):+.2f})")


def _build_summary_text(results):
    """Build plain text summary."""
    lines = [
        f"BACKTEST SUMMARY — {datetime.now().isoformat()}",
        f"Total runs: {len(results)}",
        f"Successful: {sum(1 for r in results if 'error' not in r)}",
        "",
        f"{'Strategy':<16} {'Symbol':<12} {'Exchange':<10} {'WR%':>6} "
        f"{'PnL':>10} {'Trades':>7}",
        "-" * 70,
    ]
    
    valid = [r for r in results if "error" not in r and r.get("total_trades", 0) > 0]
    valid.sort(key=lambda r: r.get("win_rate", 0), reverse=True)
    
    for r in valid:
        lines.append(
            f"{r['strategy']:<16} {r['symbol']:<12} {r['exchange']:<10} "
            f"{r.get('win_rate',0):>5.1f}% {r.get('total_pnl',0):>+9.2f} "
            f"{r.get('total_trades',0):>7}")
    
    return "\n".join(lines)


if __name__ == "__main__":
    import argparse
    from utils.logger import setup_logger
    setup_logger()
    
    parser = argparse.ArgumentParser(description="Auto-Backtest All Strategies")
    parser.add_argument("--days", type=int, default=30, help="Backtest period in days")
    args = parser.parse_args()
    
    run_all_backtests(args.days)
