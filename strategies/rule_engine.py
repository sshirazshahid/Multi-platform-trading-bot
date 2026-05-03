"""
strategies/rule_engine.py — Feature 1: Strategy Customization + Rule-Based Engine

Allows building a custom trading strategy by defining rules in config.py
using a simple dictionary format — no Python coding required.

Example config (add to config.py):
  CUSTOM_RULES = {
    "name": "My Custom Strategy",
    "timeframe": "1h",
    "lookback": 100,
    "entry_long": [
      {"indicator": "rsi",      "op": "<",  "value": 35},
      {"indicator": "ema_fast", "op": ">",  "indicator2": "ema_slow"},
      {"indicator": "volume",   "op": ">",  "indicator2": "vol_ma"},
    ],
    "entry_short": [
      {"indicator": "rsi",      "op": ">",  "value": 65},
      {"indicator": "ema_fast", "op": "<",  "indicator2": "ema_slow"},
    ],
    "exit_long":  [{"indicator": "rsi", "op": ">", "value": 60}],
    "exit_short": [{"indicator": "rsi", "op": "<", "value": 40}],
    "indicators": {
      "rsi":      {"type": "rsi",  "period": 14},
      "ema_fast": {"type": "ema",  "period": 9},
      "ema_slow": {"type": "ema",  "period": 21},
      "vol_ma":   {"type": "sma",  "source": "volume", "period": 20},
    },
  }

Supported indicators: rsi, ema, sma, bb_upper, bb_lower, bb_mid,
                       atr, macd, macd_signal, volume
Supported ops:        > < >= <= == !=
"""

import numpy as np
import pandas as pd
from loguru import logger

from core.order_manager import OrderManager
from core.risk_manager import RiskManager
from exchanges.base import BaseExchange
from strategies.base_strategy import BaseStrategy

# ── Pure-pandas indicator library ────────────────────────────────────

def _ema(s, p):  return s.ewm(span=p, adjust=False).mean()
def _sma(s, p):  return s.rolling(p).mean()

def _rsi(s, p=14):
    d = s.diff()
    g = d.clip(lower=0).ewm(com=p-1, adjust=False).mean()
    l = (-d.clip(upper=0)).ewm(com=p-1, adjust=False).mean()
    return 100 - 100 / (1 + g / l.replace(0, np.nan))

def _macd(s, fast=12, slow=26, sig=9):
    line   = _ema(s, fast) - _ema(s, slow)
    signal = _ema(line, sig)
    return line, signal

def _bbands(s, p=20, std=2.0):
    mid   = s.rolling(p).mean()
    sigma = s.rolling(p).std()
    return mid - std * sigma, mid, mid + std * sigma

def _atr(h, l, c, p=14):
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()],
                   axis=1).max(axis=1)
    return tr.ewm(com=p-1, adjust=False).mean()


def build_indicators(df: pd.DataFrame, indicator_defs: dict) -> dict:
    """
    Compute all indicators defined in the config and return as a dict
    of {name: pd.Series}.
    """
    computed = {
        "open":   df["open"],
        "high":   df["high"],
        "low":    df["low"],
        "close":  df["close"],
        "volume": df["volume"],
    }
    for name, defn in indicator_defs.items():
        t      = defn.get("type", "").lower()
        p      = defn.get("period", 14)
        source = defn.get("source", "close")
        src    = df.get(source, df["close"])
        try:
            if t == "ema":
                computed[name] = _ema(src, p)
            elif t == "sma":
                computed[name] = _sma(src, p)
            elif t == "rsi":
                computed[name] = _rsi(src, p)
            elif t == "atr":
                computed[name] = _atr(df["high"], df["low"], df["close"], p)
            elif t == "bb_upper":
                _, _, upper = _bbands(src, p, defn.get("std", 2.0))
                computed[name] = upper
            elif t == "bb_lower":
                lower, _, _ = _bbands(src, p, defn.get("std", 2.0))
                computed[name] = lower
            elif t == "bb_mid":
                _, mid, _ = _bbands(src, p, defn.get("std", 2.0))
                computed[name] = mid
            elif t == "macd":
                line, _ = _macd(src, defn.get("fast", 12),
                                  defn.get("slow", 26), defn.get("sig", 9))
                computed[name] = line
            elif t == "macd_signal":
                _, sig = _macd(src, defn.get("fast", 12),
                                defn.get("slow", 26), defn.get("sig", 9))
                computed[name] = sig
        except Exception as e:
            logger.warning(f"[RuleEngine] Failed to compute {name}: {e}")
    return computed


def evaluate_rules(rules: list, computed: dict, row_index: int = -1) -> bool:
    """
    Evaluate a list of rule dicts against computed indicator values.
    All rules must be True (AND logic).
    """
    OPS = {
        ">":  lambda a, b: a > b,
        "<":  lambda a, b: a < b,
        ">=": lambda a, b: a >= b,
        "<=": lambda a, b: a <= b,
        "==": lambda a, b: abs(a - b) < 1e-9,
        "!=": lambda a, b: abs(a - b) >= 1e-9,
    }
    for rule in rules:
        ind  = rule.get("indicator")
        op   = rule.get("op", ">")
        ind2 = rule.get("indicator2")
        val  = rule.get("value")

        if ind not in computed:
            logger.debug(f"[RuleEngine] Indicator '{ind}' not found")
            return False

        a_series = computed[ind]
        a = float(a_series.iloc[row_index]) if hasattr(a_series, "iloc") else float(a_series)

        if ind2 is not None:
            if ind2 not in computed:
                logger.debug(f"[RuleEngine] Indicator2 '{ind2}' not found")
                return False
            b_series = computed[ind2]
            b = float(b_series.iloc[row_index]) if hasattr(b_series, "iloc") else float(b_series)
        elif val is not None:
            b = float(val)
        else:
            return False

        fn = OPS.get(op)
        if fn is None or not fn(a, b):
            return False

    return True


class RuleBasedStrategy(BaseStrategy):
    """
    A configurable strategy driven entirely by config.py CUSTOM_RULES.
    No code changes needed to customise entry/exit conditions.
    """

    def __init__(self, order_manager: OrderManager,
                 risk_manager: RiskManager,
                 market_type: str = "spot"):
        super().__init__(order_manager, risk_manager, "CustomRules", market_type)
        try:
            from config import CUSTOM_RULES
            self.cfg  = CUSTOM_RULES
            self.name = self.cfg.get("name", "CustomRules")
        except (ImportError, AttributeError):
            logger.warning(
                "[RuleEngine] CUSTOM_RULES not defined in config.py. "
                "Add a CUSTOM_RULES dict to config.py to use this strategy."
            )
            self.cfg = None

    def generate_signal(self, df: pd.DataFrame):
        if not self.cfg:
            return None

        ind_defs = self.cfg.get("indicators", {})
        computed = build_indicators(df, ind_defs)

        entry_long  = self.cfg.get("entry_long",  [])
        entry_short = self.cfg.get("entry_short", [])

        if entry_long and evaluate_rules(entry_long, computed):
            return "buy"
        if entry_short and evaluate_rules(entry_short, computed):
            return "sell"
        return None

    def run(self, exchange: BaseExchange, symbol: str):
        if not self.cfg:
            return

        logger.debug(f"[{self.name}] {symbol}")
        self.order_manager.check_sl_tp(exchange, self.market_type)

        # Check exit rules for open positions
        positions = self.order_manager.tracker.get_open(
            exchange=exchange.name, symbol=symbol
        )
        if positions:
            tf      = self.cfg.get("timeframe", "1h")
            lb      = self.cfg.get("lookback", 100)
            df      = self.get_dataframe(exchange, symbol, tf, lb)
            if df is not None:
                ind_defs   = self.cfg.get("indicators", {})
                computed   = build_indicators(df, ind_defs)
                exit_long  = self.cfg.get("exit_long",  [])
                exit_short = self.cfg.get("exit_short", [])
                for pos in positions:
                    # Guard: empty rule list → evaluate_rules returns True (vacuous truth)
                    # which would close ALL positions immediately. Only evaluate if rules exist.
                    if pos.side == "buy" and exit_long and evaluate_rules(exit_long, computed):
                        logger.info(f"[{self.name}] EXIT LONG rule triggered for {symbol}")
                        ticker = exchange.fetch_ticker(symbol, self.market_type)
                        price  = ticker.get("last") or ticker.get("close")
                        if price:
                            self.order_manager.close_position(
                                exchange, pos, "rule_exit", price
                            )
                    elif pos.side == "sell" and exit_short and evaluate_rules(exit_short, computed):
                        logger.info(f"[{self.name}] EXIT SHORT rule triggered for {symbol}")
                        ticker = exchange.fetch_ticker(symbol, self.market_type)
                        price  = ticker.get("last") or ticker.get("close")
                        if price:
                            self.order_manager.close_position(
                                exchange, pos, "rule_exit", price
                            )
            return   # don't open new position if one is already open

        # Entry
        tf  = self.cfg.get("timeframe", "1h")
        lb  = self.cfg.get("lookback", 100)
        df  = self.get_dataframe(exchange, symbol, tf, lb)
        if df is None:
            return

        signal = self.generate_signal(df)
        if not signal:
            return

        current_price = float(df["close"].iloc[-1])
        self.log_signal(symbol, signal, current_price)

        balance = self.get_usdt_balance(exchange)
        if balance < 10:
            return

        from config import RISK
        leverage = RISK["default_leverage"] if self.market_type == "futures" else 1
        size     = self.risk.calculate_position_size(balance, current_price, leverage)
        sl, tp   = self.risk.get_sl_tp(current_price, signal)

        self.order_manager.open_position(
            exchange=exchange, symbol=symbol, side=signal,
            market_type=self.market_type, strategy=self.name,
            size=size, sl=sl, tp=tp, leverage=leverage,
        )
