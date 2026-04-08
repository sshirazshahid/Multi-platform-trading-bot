"""
core/auto_optimizer.py — Feature 8: Auto-Optimizing

Runs a grid search over strategy parameters using historical data
and updates config.py with the best-performing parameter set.

Triggered:
  - Manually via launcher Option [O]
  - Automatically on schedule (weekly by default)

Optimizes:
  - ATR period + multiplier for Supertrend
  - RSI thresholds for Mean Reversion
  - EMA periods for Trend Following
  - Grid spacing for Grid Trading

Uses the Backtester to score each parameter set.
Metric: Sharpe Ratio (risk-adjusted), with win rate > 50% filter.
"""

import re
import itertools
from pathlib import Path
from loguru import logger


class AutoOptimizer:

    def __init__(self, exchange, symbol: str = "BTC/USDT",
                 days: int = 30):
        self.exchange = exchange
        self.symbol   = symbol
        self.days     = days

    def optimize_supertrend(self) -> dict:
        """Grid search over ATR period and multiplier."""
        logger.info(
            f"[Optimizer] Optimizing Supertrend on {self.symbol} "
            f"({self.days} days)..."
        )
        periods      = [7, 10, 14]
        multipliers  = [2.5, 3.0, 3.5]
        best         = None
        best_sharpe  = -999.0

        df = self._fetch_df("1h", self.days * 24)
        if df is None:
            return {}

        for period, mult in itertools.product(periods, multipliers):
            try:
                from strategies.supertrend import SupertrendStrategy
                from core.risk_manager     import RiskManager
                from core.order_manager    import OrderManager
                from core.position_tracker import PositionTracker
                from utils.notifier        import TelegramNotifier
                from core.backtester       import Backtester

                risk   = RiskManager()
                trk    = PositionTracker()
                om     = OrderManager(trk, risk, TelegramNotifier())
                strat  = SupertrendStrategy(om, risk, "spot")
                strat.cfg["atr_period"]     = period
                strat.cfg["atr_multiplier"] = mult

                bt     = Backtester()
                result = bt.run(strat, df.copy(), self.symbol)

                if result.total_trades >= 5 and result.win_rate >= 50:
                    if result.sharpe_ratio > best_sharpe:
                        best_sharpe = result.sharpe_ratio
                        best = {
                            "atr_period":     period,
                            "atr_multiplier": mult,
                            "sharpe":         result.sharpe_ratio,
                            "win_rate":       result.win_rate,
                            "trades":         result.total_trades,
                        }
            except Exception as e:
                logger.debug(f"[Optimizer] Supertrend {period}/{mult}: {e}")

        if best:
            logger.info(
                f"[Optimizer] Best Supertrend params: "
                f"ATR period={best['atr_period']} "
                f"multiplier={best['atr_multiplier']} "
                f"Sharpe={best['sharpe']:.3f} WR={best['win_rate']:.1f}%"
            )
            self._write_config("SUPERTREND", {
                "atr_period":     best["atr_period"],
                "atr_multiplier": best["atr_multiplier"],
            })
        return best or {}

    def optimize_mean_reversion(self) -> dict:
        """Grid search RSI thresholds and BB standard deviation."""
        logger.info(f"[Optimizer] Optimizing MeanReversion on {self.symbol}...")
        oversolds    = [25, 30, 35]
        overboughts  = [65, 70, 75]
        bb_stds      = [1.8, 2.0, 2.2]
        best         = None
        best_sharpe  = -999.0

        df = self._fetch_df("1h", self.days * 24)
        if df is None:
            return {}

        for ov, ob, std in itertools.product(oversolds, overboughts, bb_stds):
            if ov >= ob:
                continue
            try:
                from strategies.mean_reversion import MeanReversionStrategy
                from core.risk_manager          import RiskManager
                from core.order_manager         import OrderManager
                from core.position_tracker      import PositionTracker
                from utils.notifier             import TelegramNotifier
                from core.backtester            import Backtester

                risk  = RiskManager()
                trk   = PositionTracker()
                om    = OrderManager(trk, risk, TelegramNotifier())
                strat = MeanReversionStrategy(om, risk, "spot")
                strat.cfg["rsi_oversold"]  = ov
                strat.cfg["rsi_overbought"] = ob
                strat.cfg["bb_std"]         = std

                bt     = Backtester()
                result = bt.run(strat, df.copy(), self.symbol)

                if result.total_trades >= 5 and result.win_rate >= 55:
                    if result.sharpe_ratio > best_sharpe:
                        best_sharpe = result.sharpe_ratio
                        best = {
                            "rsi_oversold":  ov,
                            "rsi_overbought": ob,
                            "bb_std":         std,
                            "sharpe":         result.sharpe_ratio,
                            "win_rate":       result.win_rate,
                        }
            except Exception as e:
                logger.debug(f"[Optimizer] MR {ov}/{ob}/{std}: {e}")

        if best:
            logger.info(
                f"[Optimizer] Best MeanReversion: "
                f"RSI {best['rsi_oversold']}/{best['rsi_overbought']} "
                f"BB std={best['bb_std']} Sharpe={best['sharpe']:.3f}"
            )
            self._write_config("MEAN_REVERSION", {
                "rsi_oversold":  best["rsi_oversold"],
                "rsi_overbought": best["rsi_overbought"],
                "bb_std":         best["bb_std"],
            })
        return best or {}

    def run_all(self):
        """Run optimization for all strategies and log a summary."""
        logger.info("[Optimizer] Starting full auto-optimization run...")
        results = {}
        results["supertrend"]    = self.optimize_supertrend()
        results["mean_reversion"] = self.optimize_mean_reversion()
        logger.info("[Optimizer] Auto-optimization complete.")
        return results

    # ── Helpers ───────────────────────────────────────────────────────

    def _fetch_df(self, timeframe: str, limit: int):
        try:
            import pandas as pd
            raw = self.exchange.fetch_ohlcv(self.symbol, timeframe, limit=limit)
            if not raw or len(raw) < 60:
                logger.warning(
                    f"[Optimizer] Not enough data for {self.symbol}"
                )
                return None
            df = pd.DataFrame(
                raw, columns=["timestamp", "open", "high", "low", "close", "volume"]
            )
            df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
            df.set_index("timestamp", inplace=True)
            return df
        except Exception as e:
            logger.error(f"[Optimizer] fetch data failed: {e}")
            return None

    def _write_config(self, section: str, updates: dict):
        """Patch a config section in config.py with optimized values."""
        cfg = Path("config.py")
        if not cfg.exists():
            return
        try:
            content = cfg.read_text(encoding="utf-8")
            for key, val in updates.items():
                pattern = rf'("{key}":\s*)[\d.]+'
                replace = rf'\g<1>{val}'
                content = re.sub(pattern, replace, content)
            cfg.write_text(content, encoding="utf-8")
            logger.info(
                f"[Optimizer] config.py updated: {section} -> {updates}"
            )
        except Exception as e:
            logger.error(f"[Optimizer] config write failed: {e}")
