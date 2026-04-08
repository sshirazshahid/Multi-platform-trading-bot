"""
core/backtester.py — Walk-forward backtester with 70/30 train/test split.

TRAIN / TEST SPLIT:
  70% of candles  -> training window  (strategy is "fitted" on this data)
  30% of candles  -> test window      (out-of-sample evaluation)

  This mirrors the standard ML practice of never evaluating a strategy on
  the same data it was developed on.  The printed result shows BOTH periods
  so you can compare in-sample vs out-of-sample performance.

  Example with 200 candles:
    Train: candles 0-139  (first 70%)
    Test:  candles 140-199 (last 30%)

  Why this matters:
    A strategy that looks great on all historical data might be curve-fitted.
    If test performance is significantly worse than train performance, the
    strategy is likely overfit and should not be traded live.
"""

import pandas as pd
import numpy as np
from dataclasses import dataclass, field
from loguru import logger


TRAIN_RATIO = 0.70   # 70% train, 30% test


@dataclass
class SplitResult:
    """Result for one window (train or test)."""
    window:        str    # "train" or "test"
    candles:       int
    total_trades:  int
    wins:          int
    losses:        int
    win_rate:      float
    total_pnl:     float
    max_drawdown:  float
    sharpe_ratio:  float
    avg_trade_pct: float
    best_trade:    float
    worst_trade:   float


@dataclass
class BacktestResult:
    """Combined result with train/test split + overall summary."""
    symbol:        str
    strategy:      str
    timeframe:     str
    # Overall (combined) -- kept for backward compatibility
    total_trades:  int
    wins:          int
    losses:        int
    win_rate:      float
    total_pnl:     float
    max_drawdown:  float
    sharpe_ratio:  float
    avg_trade_pct: float
    best_trade:    float
    worst_trade:   float
    # Split details
    train: SplitResult = field(default=None)
    test:  SplitResult = field(default=None)
    overfit_warning: bool = False
    split_note: str = ""
    # Live-gating fields
    approved: bool = False
    reject_reason: str = ""
    profit_factor: float = 0.0


class Backtester:

    def __init__(self, sl_pct: float = 0.040, tp_pct: float = 0.100,
                 fee_pct: float = 0.0007, initial_balance: float = 1000.0):
        self.sl_pct          = sl_pct   # 4% SL matches futures config
        self.tp_pct          = tp_pct   # 10% TP matches futures config
        self.fee_pct         = fee_pct
        self.initial_balance = initial_balance

    # ------------------------------------------------------------------ #
    # Public entry point                                                   #
    # ------------------------------------------------------------------ #

    def run(self, strategy, df: pd.DataFrame, symbol: str) -> BacktestResult:
        """
        Run backtest with 70/30 train/test split.
        Prints a detailed comparison of in-sample vs out-of-sample results.
        """
        n          = len(df)
        split_idx  = int(n * TRAIN_RATIO)

        # Minimum candles needed for a meaningful split
        if n < 100:
            logger.warning(
                "[Backtest] Only {} candles available. "
                "Recommend 200+ for reliable train/test split.".format(n))

        df_train = df.iloc[:split_idx].copy()
        df_test  = df.iloc[split_idx:].copy()

        logger.info(
            "[Backtest] {} | {} | split=70/30 | "
            "train={} candles | test={} candles".format(
                symbol, strategy.name, len(df_train), len(df_test)))

        # Run both windows
        train_trades = self._run_window(strategy, df_train, "train")
        test_trades  = self._run_window(strategy, df_test,  "test")
        all_trades   = train_trades + test_trades

        # Build split summaries
        train_result = self._summarize("train", df_train, train_trades)
        test_result  = self._summarize("test",  df_test,  test_trades)
        overall      = self._summarize("overall", df, all_trades)

        # Overfit detection
        overfit = False
        overfit_note = ""
        if (train_result.total_trades >= 5 and test_result.total_trades >= 3):
            wr_drop = train_result.win_rate - test_result.win_rate
            pnl_drop = train_result.total_pnl - test_result.total_pnl
            if wr_drop > 20:
                overfit = True
                overfit_note = (
                    "OVERFIT WARNING: Win rate dropped {:.0f}pp from train ({:.1f}%) "
                    "to test ({:.1f}%). Strategy may be curve-fitted.".format(
                        wr_drop, train_result.win_rate, test_result.win_rate))
            elif wr_drop > 10:
                overfit_note = (
                    "MILD DEGRADATION: WR dropped {:.0f}pp train→test. "
                    "Monitor carefully in live trading.".format(wr_drop))
            else:
                overfit_note = (
                    "ROBUST: WR delta train→test = {:.0f}pp. "
                    "Strategy generalises well out-of-sample.".format(wr_drop))

        # Profit factor (total wins / total losses)
        all_wins = sum(t["pnl"] for t in all_trades if t["pnl"] > 0)
        all_loss = abs(sum(t["pnl"] for t in all_trades if t["pnl"] <= 0)) or 0.001
        pf = round(all_wins / all_loss, 3)

        result = BacktestResult(
            symbol=symbol,
            strategy=strategy.name,
            timeframe="?",
            total_trades=overall.total_trades,
            wins=overall.wins,
            losses=overall.losses,
            win_rate=overall.win_rate,
            total_pnl=overall.total_pnl,
            max_drawdown=overall.max_drawdown,
            sharpe_ratio=overall.sharpe_ratio,
            avg_trade_pct=overall.avg_trade_pct,
            best_trade=overall.best_trade,
            worst_trade=overall.worst_trade,
            train=train_result,
            test=test_result,
            overfit_warning=overfit,
            split_note=overfit_note,
            profit_factor=pf,
        )

        # ── Live-gate: approve or reject ──
        result.approved, result.reject_reason = self._check_approval(result)

        self._print_result(result)
        return result

    # ------------------------------------------------------------------ #
    # Run one window                                                       #
    # ------------------------------------------------------------------ #

    def _run_window(self, strategy, df: pd.DataFrame,
                     window_name: str) -> list:
        """Execute strategy signals on a DataFrame window. Returns trade list."""
        trades      = []
        balance     = self.initial_balance
        in_trade    = False
        entry_price = 0.0
        entry_side  = ""
        sl = tp     = 0.0
        warmup      = 50     # candles needed to warm up indicators

        for i in range(warmup, len(df)):
            window = df.iloc[:i].copy()
            candle = df.iloc[i]
            high, low, close = float(candle["high"]), float(candle["low"]), float(candle["close"])

            if in_trade:
                pnl_pct = 0.0
                reason  = None
                if entry_side == "buy":
                    if low <= sl:
                        pnl_pct, reason = -(self.sl_pct + 2 * self.fee_pct), "stop_loss"
                    elif high >= tp:
                        pnl_pct, reason = self.tp_pct - 2 * self.fee_pct, "take_profit"
                else:
                    if high >= sl:
                        pnl_pct, reason = -(self.sl_pct + 2 * self.fee_pct), "stop_loss"
                    elif low <= tp:
                        pnl_pct, reason = self.tp_pct - 2 * self.fee_pct, "take_profit"

                if reason:
                    trade_pnl = balance * 0.05 * pnl_pct
                    balance  += trade_pnl
                    trades.append({
                        "window":   window_name,
                        "side":     entry_side,
                        "entry":    entry_price,
                        "exit":     close,
                        "pnl_pct":  pnl_pct,
                        "pnl":      trade_pnl,
                        "reason":   reason,
                    })
                    in_trade = False

            else:
                try:
                    result = strategy.generate_signal(window)
                    signal = result[0] if isinstance(result, tuple) else result
                except Exception:
                    signal = None

                if signal in ("buy", "sell"):
                    in_trade    = True
                    entry_price = close
                    entry_side  = signal
                    if signal == "buy":
                        sl = close * (1 - self.sl_pct)
                        tp = close * (1 + self.tp_pct)
                    else:
                        sl = close * (1 + self.sl_pct)
                        tp = close * (1 - self.tp_pct)

        return trades

    # ------------------------------------------------------------------ #
    # Summarise a list of trades into a SplitResult                       #
    # ------------------------------------------------------------------ #

    def _summarize(self, window: str, df: pd.DataFrame,
                    trades: list) -> SplitResult:
        total = len(trades)
        if total == 0:
            return SplitResult(
                window=window, candles=len(df),
                total_trades=0, wins=0, losses=0, win_rate=0.0,
                total_pnl=0.0, max_drawdown=0.0, sharpe_ratio=0.0,
                avg_trade_pct=0.0, best_trade=0.0, worst_trade=0.0,
            )

        wins   = [t for t in trades if t["pnl"] > 0]
        losses = [t for t in trades if t["pnl"] <= 0]

        # Equity curve for drawdown
        balance     = self.initial_balance
        peak        = balance
        max_dd      = 0.0
        for t in trades:
            balance += t["pnl"]
            peak     = max(peak, balance)
            dd       = (peak - balance) / max(peak, 1e-8) * 100
            max_dd   = max(max_dd, dd)

        total_pnl   = sum(t["pnl"]     for t in trades)
        pnls        = [t["pnl_pct"]    for t in trades]
        avg_pct     = sum(pnls) / total * 100
        best        = max(pnls)  * 100
        worst       = min(pnls)  * 100

        mean_r = np.mean(pnls)
        std_r  = np.std(pnls)
        sharpe = (mean_r / std_r * (252 ** 0.5)) if std_r > 0 else 0.0

        return SplitResult(
            window=window,
            candles=len(df),
            total_trades=total,
            wins=len(wins),
            losses=len(losses),
            win_rate=round(len(wins) / total * 100, 2),
            total_pnl=round(total_pnl, 4),
            max_drawdown=round(max_dd, 2),
            sharpe_ratio=round(sharpe, 3),
            avg_trade_pct=round(avg_pct, 3),
            best_trade=round(best, 3),
            worst_trade=round(worst, 3),
        )

    # ------------------------------------------------------------------ #
    # Formatted output                                                     #
    # ------------------------------------------------------------------ #

    def _print_result(self, r: BacktestResult):
        SEP = "=" * 62

        logger.info(SEP)
        logger.info("  BACKTEST RESULT  --  {}  |  {}".format(r.symbol, r.strategy))
        logger.info("  Train/Test Split: 70% train / 30% test")
        logger.info(SEP)

        def _row(label, train_val, test_val, overall_val):
            logger.info("  {:<22} {:>12}  {:>10}  {:>10}".format(
                label, str(train_val), str(test_val), str(overall_val)))

        logger.info("  {:<22} {:>12}  {:>10}  {:>10}".format(
            "", "TRAIN (70%)", "TEST (30%)", "OVERALL"))
        logger.info("  " + "-" * 58)

        tr = r.train
        te = r.test

        _row("Candles",       tr.candles,         te.candles,         tr.candles + te.candles)
        _row("Total Trades",  tr.total_trades,    te.total_trades,    r.total_trades)
        _row("Wins / Losses", "{}/{}".format(tr.wins, tr.losses),
                               "{}/{}".format(te.wins, te.losses),
                               "{}/{}".format(r.wins,  r.losses))
        _row("Win Rate",
             "{:.1f}%".format(tr.win_rate),
             "{:.1f}%".format(te.win_rate),
             "{:.1f}%".format(r.win_rate))
        _row("Total PnL",
             "{:+.4f}".format(tr.total_pnl),
             "{:+.4f}".format(te.total_pnl),
             "{:+.4f}".format(r.total_pnl))
        _row("Max Drawdown",
             "{:.2f}%".format(tr.max_drawdown),
             "{:.2f}%".format(te.max_drawdown),
             "{:.2f}%".format(r.max_drawdown))
        _row("Sharpe Ratio",
             "{:.3f}".format(tr.sharpe_ratio),
             "{:.3f}".format(te.sharpe_ratio),
             "{:.3f}".format(r.sharpe_ratio))
        _row("Avg Trade",
             "{:+.3f}%".format(tr.avg_trade_pct),
             "{:+.3f}%".format(te.avg_trade_pct),
             "{:+.3f}%".format(r.avg_trade_pct))
        _row("Best Trade",
             "{:+.3f}%".format(tr.best_trade),
             "{:+.3f}%".format(te.best_trade),
             "{:+.3f}%".format(r.best_trade))
        _row("Worst Trade",
             "{:+.3f}%".format(tr.worst_trade),
             "{:+.3f}%".format(te.worst_trade),
             "{:+.3f}%".format(r.worst_trade))

        logger.info(SEP)
        if r.split_note:
            prefix = "  ⚠ " if r.overfit_warning else "  ✓ "
            logger.info("{}{}".format(prefix, r.split_note))
        if r.approved:
            logger.info("  APPROVED for live trading (PF={:.2f})".format(r.profit_factor))
        else:
            logger.info("  REJECTED: {}".format(r.reject_reason))
        logger.info(SEP)

    # ------------------------------------------------------------------ #
    # Live-gate approval                                                    #
    # ------------------------------------------------------------------ #

    MIN_TRADES    = 5     # Need at least 5 trades in test window
    MIN_WIN_RATE  = 35.0  # Minimum 35% WR on TEST (out-of-sample)
    MIN_PF        = 1.0   # Profit factor >= 1.0 (at least break-even)
    MAX_DD        = 30.0  # Max drawdown < 30%

    def _check_approval(self, r: BacktestResult) -> tuple:
        """Check if backtest passes minimum criteria for live trading.
        Uses TEST window (out-of-sample) when available — never approve on train alone."""
        test = r.test
        if test and test.total_trades >= 3:
            # Evaluate on out-of-sample data
            if test.win_rate < self.MIN_WIN_RATE:
                return False, "test WR {:.0f}% < {:.0f}%".format(test.win_rate, self.MIN_WIN_RATE)
            if test.total_pnl < 0:
                return False, "test PnL negative ({:+.4f})".format(test.total_pnl)
        if r.total_trades < self.MIN_TRADES:
            return False, "too few trades ({})".format(r.total_trades)
        if r.win_rate < self.MIN_WIN_RATE:
            return False, "WR {:.0f}% < {:.0f}%".format(r.win_rate, self.MIN_WIN_RATE)
        if r.profit_factor < self.MIN_PF:
            return False, "PF {:.2f} < {:.2f}".format(r.profit_factor, self.MIN_PF)
        if r.max_drawdown > self.MAX_DD:
            return False, "DD {:.1f}% > {:.1f}%".format(r.max_drawdown, self.MAX_DD)
        if r.overfit_warning:
            return False, "overfit detected (WR drops train→test)"
        return True, "passed"

    # ------------------------------------------------------------------ #
    # Convenience: validate a strategy+symbol before going live            #
    # ------------------------------------------------------------------ #

    def validate_before_live(self, strategy, exchange, symbol: str,
                             timeframe: str = "1h", limit: int = 500,
                             market_type: str = "futures") -> BacktestResult:
        """Fetch OHLCV from exchange, run full backtest, return result with approved flag.

        Usage in bot_engine:
            result = backtester.validate_before_live(strategy, exchange, symbol)
            if not result.approved:
                logger.info("Backtest rejected: %s", result.reject_reason)
                continue
        """
        try:
            raw = exchange.fetch_ohlcv(symbol, timeframe, limit=limit,
                                       market_type=market_type)
            if not raw or len(raw) < 100:
                r = BacktestResult(
                    symbol=symbol, strategy=getattr(strategy, "name", "?"),
                    timeframe=timeframe, total_trades=0, wins=0, losses=0,
                    win_rate=0, total_pnl=0, max_drawdown=0, sharpe_ratio=0,
                    avg_trade_pct=0, best_trade=0, worst_trade=0,
                    reject_reason="insufficient data ({} candles)".format(
                        len(raw) if raw else 0))
                return r
            df = pd.DataFrame(
                raw, columns=["timestamp", "open", "high", "low", "close", "volume"])
            df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
            df.set_index("timestamp", inplace=True)
            return self.run(strategy, df, symbol)
        except Exception as e:
            logger.debug("[Backtest] validate_before_live failed: {}".format(e))
            r = BacktestResult(
                symbol=symbol, strategy=getattr(strategy, "name", "?"),
                timeframe=timeframe, total_trades=0, wins=0, losses=0,
                win_rate=0, total_pnl=0, max_drawdown=0, sharpe_ratio=0,
                avg_trade_pct=0, best_trade=0, worst_trade=0,
                reject_reason="error: {}".format(str(e)[:100]))
            return r
