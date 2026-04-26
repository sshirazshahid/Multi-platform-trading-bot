"""
backtest.py — Backtest any strategy on any connected exchange.

Usage:
    python backtest.py --strategy supertrend --symbol BTC/USDT --days 60
    python backtest.py --strategy multitf --exchange bybit --days 30
    python backtest.py --strategy supertrend --all-exchanges --days 60
    python backtest.py --strategy supertrend --years 5 --all-exchanges
    python backtest.py --strategy arbitrage --days 30     (arb spread analysis)

Exchanges: binance | bybit | bitget

Extended history: Use --years for multi-year backtests. Fetches data in
paginated chunks via ccxt's `since` parameter. Max ~9 years (Binance 2017).
"""

import argparse
import sys
import time as _time
from loguru import logger
from utils.logger import setup_logger

STRATEGY_CHOICES = [
    "supertrend", "meanreversion", "multitf",
    "trend", "scalping", "grid", "arbitrage",
]
EXCHANGE_CHOICES = ["binance", "bybit", "bitget"]


def parse_args():
    p = argparse.ArgumentParser(description="Strategy Backtester — All Exchanges")
    p.add_argument("--exchange",      default="binance",
                   choices=EXCHANGE_CHOICES,
                   help="Exchange to backtest on (default: binance)")
    p.add_argument("--all-exchanges", action="store_true",
                   help="Run backtest on ALL connected exchanges")
    p.add_argument("--symbol",    default="BTC/USDT")
    p.add_argument("--strategy",  default="supertrend",  choices=STRATEGY_CHOICES)
    p.add_argument("--timeframe", default=None,          help="Override candle timeframe")
    p.add_argument("--days",      default=60,  type=int,
                   help="Days of history (default 60, max ~3300)")
    p.add_argument("--years",     default=None, type=float,
                   help="Years of history (overrides --days, max ~9)")
    p.add_argument("--sl",        default=None, type=float, help="Override stop loss pct")
    p.add_argument("--tp",        default=None, type=float, help="Override take profit pct")
    p.add_argument("--balance",   default=1000.0, type=float, help="Starting USDT balance")
    a = p.parse_args()
    if a.years is not None:
        a.days = int(a.years * 365)
    return a


def _tf_minutes(tf: str) -> int:
    unit = tf[-1]
    n    = int(tf[:-1])
    return {"m": n, "h": n * 60, "d": n * 1440}.get(unit, 60)


def _fetch_ohlcv_extended(exchange, symbol: str, timeframe: str,
                          days: int, market_type: str = "spot") -> list:
    """Paginated OHLCV fetch for extended history (multi-year backtests).
    Fetches in chunks of 1000 candles, advancing `since` each iteration."""
    tf_ms = _tf_minutes(timeframe) * 60 * 1000
    candles_needed = days * 24 * 60 // _tf_minutes(timeframe)
    chunk_size = 1000

    if candles_needed <= chunk_size:
        return exchange.fetch_ohlcv(symbol, timeframe,
                                    limit=candles_needed, market_type=market_type)

    now_ms = int(_time.time() * 1000)
    since_ms = now_ms - (days * 24 * 60 * 60 * 1000)
    all_candles = []
    fetched = 0

    while fetched < candles_needed:
        batch_limit = min(chunk_size, candles_needed - fetched)
        try:
            params = {}
            if market_type == "futures" and hasattr(exchange, '_futures_params'):
                params = exchange._futures_params()
            raw = exchange.exchange.fetch_ohlcv(
                symbol, timeframe, since=since_ms,
                limit=batch_limit, params=params)
        except Exception as e:
            logger.warning(f"[Backtest] Fetch chunk at {since_ms}: {e}")
            break

        if not raw:
            break

        all_candles.extend(raw)
        fetched += len(raw)
        since_ms = int(raw[-1][0]) + tf_ms

        if len(raw) < batch_limit:
            break

        logger.debug(f"[Backtest] Fetched {fetched}/{candles_needed} candles...")

    seen = set()
    deduped = []
    for c in all_candles:
        if c[0] not in seen:
            seen.add(c[0])
            deduped.append(c)
    return sorted(deduped, key=lambda x: x[0])


def _build_exchange(name: str):
    from exchanges import BinanceClient, BybitClient, BitgetClient
    clients = {
        "binance": BinanceClient,
        "bybit":   BybitClient,
        "bitget":  BitgetClient,
    }
    cls = clients.get(name.lower())
    if not cls:
        logger.error("Unknown exchange: {}".format(name))
        return None
    ex = cls()
    if not getattr(ex, "_connected", False):
        logger.warning("[Backtest] {} — not connected (check API keys in .env)".format(
            name.upper()))
        return None
    return ex


def _build_strategy(name: str, order_mgr, risk):
    """Build strategy object by name."""
    import strategies
    kwargs = dict(order_manager=order_mgr, risk_manager=risk)
    tf_map = {
        "supertrend":    "1h",
        "meanreversion": "1h",
        "multitf":       "15m",
        "trend":         "15m",
        "scalping":      "1m",
        "grid":          "1h",
    }
    strategy_map = {
        "supertrend":    lambda: strategies.SupertrendStrategy(**kwargs),
        "meanreversion": lambda: strategies.MeanReversionStrategy(**kwargs),
        "multitf":       lambda: strategies.MultiTFStrategy(**kwargs, market_type="futures"),
        "trend":         lambda: strategies.TrendFollowingStrategy(**kwargs),
        "scalping":      lambda: getattr(strategies, "ScalpingStrategy")(**kwargs),
        "grid":          lambda: strategies.GridTradingStrategy(**kwargs),
    }
    factory = strategy_map.get(name)
    if not factory:
        logger.error("Unknown strategy: {}".format(name))
        return None, "1h"
    return factory(), tf_map.get(name, "1h")


def run_backtest_on_exchange(exchange, ex_name: str, args) -> bool:
    """Run a single backtest for one exchange. Returns True on success."""
    import pandas as pd
    from core.risk_manager     import RiskManager
    from core.position_tracker import PositionTracker
    from core.order_manager    import OrderManager
    from utils.notifier        import TelegramNotifier
    from core.backtester       import Backtester
    from config                import RISK

    risk      = RiskManager()
    tracker   = PositionTracker()
    notifier  = TelegramNotifier()
    order_mgr = OrderManager(tracker, risk, notifier)

    strategy, default_tf = _build_strategy(args.strategy, order_mgr, risk)
    if strategy is None:
        return False

    timeframe = args.timeframe or default_tf
    candles_needed = args.days * 24 * 60 // _tf_minutes(timeframe)

    print()
    print("  " + "=" * 58)
    print("  Backtest: {} on {} - {}".format(
        args.strategy.upper(), ex_name.upper(), args.symbol))
    print("  " + "=" * 58)

    years_str = f" ({args.days/365:.1f} years)" if args.days > 365 else ""
    logger.info("[Backtest] Fetching {} × {} candles for {} on {} ({} days{})...".format(
        candles_needed, timeframe, args.symbol, ex_name, args.days, years_str))

    raw = _fetch_ohlcv_extended(exchange, args.symbol, timeframe, args.days)
    if not raw:
        logger.error("[Backtest] No data returned from {} — check symbol and API keys".format(
            ex_name))
        return False

    if len(raw) < candles_needed:
        logger.warning(
            "[Backtest] Requested {} candles but exchange returned only {} "
            "(coverage may be shorter than {} days)".format(
                candles_needed, len(raw), args.days))

    raw = [r for r in raw if isinstance(r, (list, tuple)) and len(r) >= 6]
    if not raw:
        logger.error("[Backtest] No valid OHLCV data returned")
        return False

    df = pd.DataFrame(raw, columns=["timestamp","open","high","low","close","volume"])
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
    df.set_index("timestamp", inplace=True)
    logger.info("[Backtest] Loaded {} candles | {} → {}".format(
        len(df), df.index[0], df.index[-1]))

    sl = args.sl if args.sl is not None else RISK["default_stop_loss"]
    tp = args.tp if args.tp is not None else RISK["default_take_profit"]

    bt     = Backtester(sl_pct=sl, tp_pct=tp, initial_balance=args.balance)
    result = bt.run(strategy, df, args.symbol)

    # Print recommendation
    print()
    if result.total_trades == 0:
        print("  No trades generated. Try a longer range or different symbol.")
    elif result.win_rate >= 55 and result.sharpe_ratio >= 1.0:
        print("  Strategy looks PROMISING on {}.".format(ex_name.upper()))
        print("  Consider testing with DRY_RUN=true on this exchange.")
    elif result.win_rate >= 50:
        print("  Strategy is MARGINAL on {}. Test more data.".format(ex_name.upper()))
    else:
        print("  Strategy underperformed on {}. Adjust parameters.".format(ex_name.upper()))

    return True


def run_arb_backtest(args):
    """
    Arbitrage backtest: analyse historical price feeds from multiple exchanges
    and compute how many spread opportunities existed and their profitability.
    """
    from exchanges import BinanceClient, BybitClient, BitgetClient
    import pandas as pd
    from config import FEE

    print()
    print("  " + "=" * 58)
    print("  Arbitrage Spread Backtest - {} days - {}".format(
        args.days, args.symbol))
    print("  " + "=" * 58)

    all_clients = {
        "binance": BinanceClient(),
        "bybit":   BybitClient(),
        "bitget":  BitgetClient(),
    }
    active = {n: ex for n, ex in all_clients.items()
              if getattr(ex, "_connected", False)}

    if len(active) < 2:
        print("\n  Need at least 2 connected exchanges to run arb backtest.")
        print("  Connected: {}".format(list(active.keys())))
        return

    print("\n  Connected exchanges: {}".format(list(active.keys())))
    print("  Fetching 1h OHLCV data from each...\n")

    candle_limit = min(args.days * 24, 1000)
    all_closes: dict[str, pd.Series] = {}

    for ex_name, ex in active.items():
        raw = ex.fetch_ohlcv(args.symbol, "1h", limit=candle_limit)
        if raw:
            df = pd.DataFrame(raw, columns=["ts","open","high","low","close","vol"])
            df["ts"] = pd.to_datetime(df["ts"], unit="ms")
            df.set_index("ts", inplace=True)
            all_closes[ex_name] = df["close"]
            print("  {} — {} candles".format(ex_name, len(df)))
        else:
            print("  {} — no data".format(ex_name))

    if len(all_closes) < 2:
        print("\n  Not enough data across exchanges for comparison.")
        return

    # Align on common timestamps
    combined = pd.DataFrame(all_closes).dropna()
    if combined.empty:
        print("\n  No overlapping timestamps found.")
        return

    ex_names = list(combined.columns)
    print("\n  {} overlapping candles across: {}\n".format(len(combined), ex_names))

    # Scan all pairs
    MIN_SPREAD = 0.004
    from config import FEE as fees_cfg

    total_opps = 0
    total_profit = 0.0
    pair_results = []

    for i, ea in enumerate(ex_names):
        for eb in ex_names[i+1:]:
            spreads = abs(combined[ea] - combined[eb]) / combined[[ea,eb]].min(axis=1)
            fee_a   = fees_cfg.get("{}_spot_taker".format(ea), 0.001)
            fee_b   = fees_cfg.get("{}_spot_taker".format(eb), 0.001)
            net     = spreads - fee_a - fee_b
            opps    = (net > MIN_SPREAD).sum()
            avg_net = net[net > MIN_SPREAD].mean() if opps > 0 else 0
            est_pnl = avg_net * args.balance * 0.02 * opps  # 2% position
            total_opps    += opps
            total_profit  += est_pnl
            pair_results.append((ea, eb, opps, avg_net, est_pnl))

            print("  {:<8} vs {:<8} | Opportunities: {:>4} | "
                  "Avg Net: {:.3f}% | Est PnL: {:.2f} USDT".format(
                      ea.upper(), eb.upper(), opps,
                      avg_net * 100, est_pnl))

    print()
    print("  TOTAL OPPORTUNITIES : {}".format(total_opps))
    print("  EST TOTAL PnL       : {:.2f} USDT (on ${:.0f} capital, 2% per trade)".format(
        total_profit, args.balance))
    print("  PERIOD              : {} days  ({} candles at 1h)".format(
        args.days, len(combined)))
    print()
    if total_opps > 50:
        print("  VERDICT: Active arb opportunities exist — arb engine ACTIVE.")
    elif total_opps > 10:
        print("  VERDICT: Occasional spread — arb runs but selectivity important.")
    else:
        print("  VERDICT: Few spread opportunities in this period.")


def main():
    setup_logger()
    args = parse_args()

    # Arbitrage is handled differently
    if args.strategy == "arbitrage":
        run_arb_backtest(args)
        return

    # Determine which exchanges to test
    if args.all_exchanges:
        target_exchanges = EXCHANGE_CHOICES
    else:
        target_exchanges = [args.exchange]

    success_count = 0
    for ex_name in target_exchanges:
        exchange = _build_exchange(ex_name)
        if exchange is None:
            continue
        ok = run_backtest_on_exchange(exchange, ex_name, args)
        if ok:
            success_count += 1
        print()

    if args.all_exchanges:
        print("  Completed backtests on {}/{} exchanges.".format(
            success_count, len(target_exchanges)))

    if success_count == 0:
        print("  No backtests completed. Check exchange connections.")
        sys.exit(1)


if __name__ == "__main__":
    main()
