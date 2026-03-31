"""
config.py — Central configuration.
All settings live here. Override via environment variables in .env
"""

import os
from dotenv import load_dotenv

load_dotenv()

# ══════════════════════════════════════════════════════════════════════
# EXCHANGE CREDENTIALS  (set in .env — never hardcode here)
# ══════════════════════════════════════════════════════════════════════
BINANCE_API_KEY    = os.getenv("BINANCE_API_KEY",    "")
BINANCE_SECRET_KEY = os.getenv("BINANCE_SECRET_KEY", "")
BINANCE_TESTNET    = os.getenv("BINANCE_TESTNET", "false").lower() == "true"

MEXC_API_KEY    = os.getenv("MEXC_API_KEY",    "")
MEXC_SECRET_KEY = os.getenv("MEXC_SECRET_KEY", "")

BYBIT_API_KEY    = os.getenv("BYBIT_API_KEY",    "")
BYBIT_SECRET_KEY = os.getenv("BYBIT_SECRET_KEY", "")

BITGET_API_KEY    = os.getenv("BITGET_API_KEY",    "")
BITGET_SECRET_KEY = os.getenv("BITGET_SECRET_KEY", "")
BITGET_PASSPHRASE = os.getenv("BITGET_PASSPHRASE", "")

# ══════════════════════════════════════════════════════════════════════
# NOTIFICATIONS
# ══════════════════════════════════════════════════════════════════════
GMAIL_SENDER         = os.getenv("GMAIL_SENDER",         "")
GMAIL_APP_PASSWORD   = os.getenv("GMAIL_APP_PASSWORD",   "")
GMAIL_RECIPIENT      = os.getenv("GMAIL_RECIPIENT",      "")
EMAIL_SUBJECT_PREFIX = os.getenv("EMAIL_SUBJECT_PREFIX", "[TradingBot]")

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")

# ══════════════════════════════════════════════════════════════════════
# GENERAL
# ══════════════════════════════════════════════════════════════════════
DRY_RUN   = os.getenv("DRY_RUN",   "true").lower() == "true"
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

DRY_RUN_BALANCE = float(os.getenv("DRY_RUN_BALANCE", "100.0"))

# ══════════════════════════════════════════════════════════════════════
# EXCHANGE FEE STRUCTURE
# ══════════════════════════════════════════════════════════════════════
FEE = {
    "spot_taker":           0.001,
    "spot_maker":           0.001,
    "futures_taker":        0.0005,
    "futures_maker":        0.0002,
    "mexc_spot_taker":      0.001,
    "mexc_futures_taker":   0.0005,
    "bybit_spot_taker":     0.001,
    "bybit_futures_taker":  0.0006,
    "bybit_futures_maker":  0.0001,
    "bitget_spot_taker":    0.001,
    "bitget_futures_taker": 0.0006,
    "bitget_futures_maker": 0.0002,
}

# ══════════════════════════════════════════════════════════════════════
# TRADING MODE
# ══════════════════════════════════════════════════════════════════════
TRADING_MODE             = os.getenv("TRADING_MODE",             "usdt_only")
PORTFOLIO_MIN_VALUE_USD  = float(os.getenv("PORTFOLIO_MIN_VALUE_USD", "2.0"))
PORTFOLIO_RESCAN_MINUTES = int(os.getenv("PORTFOLIO_RESCAN_MINUTES",   "60"))

# ══════════════════════════════════════════════════════════════════════
# TRADING PAIRS
# Crypto pairs — spot + futures on all 4 exchanges.
# Commodity pairs — futures only (USDT-margined perpetuals).
#   Gold  (XAU/USDT:USDT) — Binance, MEXC, Bybit, Bitget
#   Silver (XAG/USDT:USDT) — Binance, MEXC, Bybit, Bitget
# ══════════════════════════════════════════════════════════════════════
TRADING_PAIRS = {
    "binance": {
        "spot": [
            "BTC/USDT", "ETH/USDT", "BNB/USDT", "SOL/USDT",
            "XRP/USDT", "ADA/USDT", "DOGE/USDT", "AVAX/USDT",
        ],
        "futures": [
            "BTC/USDT:USDT", "ETH/USDT:USDT", "BNB/USDT:USDT", "SOL/USDT:USDT",
            "XRP/USDT:USDT", "ADA/USDT:USDT", "DOGE/USDT:USDT", "AVAX/USDT:USDT",
            "XAU/USDT:USDT",   # Gold
            "XAG/USDT:USDT",   # Silver
        ],
    },
    "mexc": {
        "spot": [
            "BTC/USDT", "ETH/USDT", "SOL/USDT", "XRP/USDT",
            "ADA/USDT", "DOGE/USDT", "AVAX/USDT",
        ],
        "futures": [],  # MEXC futures API geo-blocked from some regions
    },
    "bybit": {
        "spot": [
            "BTC/USDT", "ETH/USDT", "SOL/USDT", "XRP/USDT", "DOGE/USDT",
        ],
        "futures": [
            "BTC/USDT:USDT", "ETH/USDT:USDT", "SOL/USDT:USDT",
            "XRP/USDT:USDT", "DOGE/USDT:USDT",
            "XAU/USDT:USDT",   # Gold
            "XAG/USDT:USDT",   # Silver
        ],
    },
    "bitget": {
        "spot": [
            "BTC/USDT", "ETH/USDT", "SOL/USDT", "XRP/USDT",
        ],
        "futures": [
            "BTC/USDT:USDT", "ETH/USDT:USDT", "SOL/USDT:USDT", "XRP/USDT:USDT",
            "XAU/USDT:USDT",   # Gold
            "XAG/USDT:USDT",   # Silver
        ],
    },
}

# ══════════════════════════════════════════════════════════════════════
# COMMODITY METADATA
# ══════════════════════════════════════════════════════════════════════
COMMODITIES = {
    "XAU": {
        "name":       "Gold",
        "emoji":      "🥇",
        "atr_mult":   1.5,
        "min_adx":    18,
        "corr_asset": "USD",
    },
    "XAG": {
        "name":       "Silver",
        "emoji":      "🥈",
        "atr_mult":   1.8,
        "min_adx":    20,
        "corr_asset": "XAU",
    },
    "WTI": {
        "name":       "Oil (WTI)",
        "emoji":      "🛢️",
        "atr_mult":   2.0,
        "min_adx":    22,
        "corr_asset": "USD",
    },
}


def is_commodity(symbol: str) -> bool:
    return symbol.split("/")[0].upper() in COMMODITIES


def get_commodity_meta(symbol: str) -> dict:
    return COMMODITIES.get(symbol.split("/")[0].upper(), {})


# ══════════════════════════════════════════════════════════════════════
# RISK MANAGEMENT  (single-profile defaults)
# ══════════════════════════════════════════════════════════════════════
RISK = {
    "max_position_pct":     0.05,
    "max_open_positions":   20,
    "max_daily_loss_pct":   0.05,
    "default_stop_loss":    0.025,
    "default_take_profit":  0.065,
    "futures_max_leverage": 5,
    "default_leverage":     5,
    "min_rr_ratio":         2.0,
    "trailing_stop":        True,
    "trailing_activation":  0.015,
    "trailing_distance":    0.008,
    "max_drawdown_pct":     0.15,
    "position_sizing_mode": "fixed",
}

# ══════════════════════════════════════════════════════════════════════
# BLACKLISTING
# ══════════════════════════════════════════════════════════════════════
BLACKLIST = {
    "consecutive_sl_limit": 3,
    "auto_expiry_hours":    24,
    "volatility_spike_pct": 0.15,
    "manual_list":          [],
}

# ══════════════════════════════════════════════════════════════════════
# SCANNER
# ══════════════════════════════════════════════════════════════════════
SCANNER = {
    "min_confidence":        0.55,
    "scan_interval_min":     15,
    "min_adx_trend":         22,
    "futures_adx_threshold": 28,
    "max_atr_pct":           0.08,
    "timeframes":            ["1d", "4h", "1h", "15m", "1m"],
}

# ══════════════════════════════════════════════════════════════════════
# ACTIVE STRATEGIES
# ══════════════════════════════════════════════════════════════════════
ACTIVE_STRATEGIES = [
    "supertrend_spot",
    "supertrend_futures",
    "mean_reversion_spot",
    "multitf_futures",
    "grid_spot",
    "trend_spot",
    "trend_futures",
    "dca_spot",
]

# ══════════════════════════════════════════════════════════════════════
# STRATEGY PARAMETERS
# ══════════════════════════════════════════════════════════════════════
SUPERTREND = {
    "timeframe": "1h", "htf_timeframe": "4h",
    "atr_period": 10, "atr_multiplier": 3.0,
    "rsi_period": 14, "rsi_min": 35, "rsi_max": 65,
    "volume_ma": 20, "min_volume_mult": 0.8,
    "atr_sl_mult": 1.8, "atr_tp_mult": 4.5,
    "lookback_candles": 120, "min_atr_pct": 0.002, "max_atr_pct": 0.06,
}

MEAN_REVERSION = {
    "timeframe": "1h",
    "bb_period": 20, "bb_std": 2.0,
    "rsi_period": 14, "rsi_oversold": 30, "rsi_overbought": 70,
    "rsi_exit_long": 55, "rsi_exit_short": 45,
    "bb_squeeze_min": 0.02,
    "volume_ma": 20, "min_volume_mult": 0.8,
    "sl_bb_mult": 0.5, "tp_midline": True,
    "lookback_candles": 100,
    "trend_filter": True, "trend_ema_period": 200,
}

MULTI_TF = {
    "htf_timeframe": "4h", "mtf_timeframe": "1h", "ltf_timeframe": "15m",
    "trend_ema": 200, "structure_fast": 9, "structure_slow": 21,
    "entry_fast": 5, "entry_slow": 13,
    "rsi_period": 14, "rsi_oversold": 35, "rsi_overbought": 65,
    "adx_period": 14, "adx_min": 22,
    "atr_period": 14, "atr_sl_mult": 1.8, "atr_tp_mult": 4.5,
    "max_trades_per_day": 3, "lookback_candles": 250,
}

GRID_TRADING = {
    "timeframe": "1h",
    "grid_levels": 8, "grid_spacing": 0.006,
    "order_size_pct": 0.015,
    "upper_offset": 0.04, "lower_offset": 0.04,
    "rebalance_after": 12,
    "volatility_check": True, "atr_pause_pct": 0.03,
}

TREND_FOLLOWING = {
    "timeframe": "15m",
    "fast_ema": 9, "slow_ema": 21, "trend_ema": 50,
    "rsi_period": 14, "rsi_oversold": 35, "rsi_overbought": 65,
    "macd_fast": 12, "macd_slow": 26, "macd_signal": 9,
    "atr_period": 14, "atr_sl_mult": 2.0, "atr_tp_mult": 5.0,
    "volume_ma": 20, "min_volume_mult": 1.2, "lookback_candles": 100,
}

SCALPING = {
    "timeframe": "1m",
    "fast_ema": 5, "slow_ema": 13,
    "rsi_period": 7, "rsi_oversold": 30, "rsi_overbought": 70,
    "bb_period": 20, "bb_std": 2.0,
    "volume_spike_mult": 1.5,
    "orderbook_depth": 10, "imbalance_ratio": 1.3,
    "min_spread_pct": 0.001, "max_spread_pct": 0.003,
    "stop_loss": 0.005, "take_profit": 0.008,
    "lookback_candles": 50,
}

DCA = {
    "interval_hours":  4,
    "amount_usdt":     5.0,
    "dip_buy_pct":     0.05,
    "dip_multiplier":  2.0,
    "max_daily_buys":  6,
    "take_profit_pct": 0.20,
}

AUTO_OPTIMIZE = {
    "enabled": False,
    "symbol":  "BTC/USDT",
    "days":    30,
}
