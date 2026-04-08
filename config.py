"""
config.py — Central configuration. All settings in one place.
"""

import os
from dotenv import load_dotenv

load_dotenv()

# ==============================================================
# EXCHANGE CREDENTIALS
# ==============================================================
BINANCE_API_KEY    = os.getenv("BINANCE_API_KEY", "")
BINANCE_SECRET_KEY = os.getenv("BINANCE_SECRET_KEY", "")
BINANCE_TESTNET    = os.getenv("BINANCE_TESTNET", "false").lower() == "true"

MEXC_API_KEY    = os.getenv("MEXC_API_KEY", "")
MEXC_SECRET_KEY = os.getenv("MEXC_SECRET_KEY", "")
MEXC_TESTNET    = os.getenv("MEXC_TESTNET", "false").lower() == "true"

BYBIT_API_KEY    = os.getenv("BYBIT_API_KEY", "")
BYBIT_SECRET_KEY = os.getenv("BYBIT_SECRET_KEY", "")

BITGET_API_KEY    = os.getenv("BITGET_API_KEY", "")
BITGET_SECRET_KEY = os.getenv("BITGET_SECRET_KEY", "")
BITGET_PASSPHRASE = os.getenv("BITGET_PASSPHRASE", "")

# ==============================================================
# NOTIFICATIONS
# ==============================================================
GMAIL_SENDER         = os.getenv("GMAIL_SENDER", "")
GMAIL_APP_PASSWORD   = os.getenv("GMAIL_APP_PASSWORD", "")
GMAIL_RECIPIENT      = os.getenv("GMAIL_RECIPIENT", "")
EMAIL_SUBJECT_PREFIX = os.getenv("EMAIL_SUBJECT_PREFIX", "[TradingBot]")

# ==============================================================
# GENERAL
# ==============================================================
DRY_RUN   = os.getenv("DRY_RUN",   "true").lower() == "true"
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

# ==============================================================
# DRY RUN PAPER WALLET
# $100 per exchange per profile — or replicate from live wallet
# ==============================================================
DRY_RUN_BALANCE = float(os.getenv("DRY_RUN_BALANCE", "100.0"))

# ==============================================================
# EXCHANGE FEE STRUCTURE
# ==============================================================
FEE = {
    # Binance (also generic fallback)
    "spot_taker":           0.001,
    "spot_maker":           0.001,
    "futures_taker":        0.0005,
    "futures_maker":        0.0002,
    # Binance explicit keys (arb engine looks up "{exchange}_spot_taker")
    "binance_spot_taker":   0.001,
    "binance_futures_taker": 0.0005,
    # MEXC
    "mexc_spot_taker":      0.001,
    "mexc_futures_taker":   0.0005,
    # Bybit
    "bybit_spot_taker":     0.001,
    "bybit_spot_maker":     0.001,
    "bybit_futures_taker":  0.0006,
    "bybit_futures_maker":  0.0001,
    # Bitget
    "bitget_spot_taker":    0.001,
    "bitget_spot_maker":    0.001,
    "bitget_futures_taker": 0.0006,
    "bitget_futures_maker": 0.0002,
}

# ==============================================================
# TRADING MODE
# ==============================================================
TRADING_MODE             = os.getenv("TRADING_MODE",             "usdt_only")
PORTFOLIO_MIN_VALUE_USD  = float(os.getenv("PORTFOLIO_MIN_VALUE_USD", "2.0"))
PORTFOLIO_RESCAN_MINUTES = int(os.getenv("PORTFOLIO_RESCAN_MINUTES",   "60"))

# ==============================================================
# CLAUDE PORTFOLIO — Claude Code is the SOLE decision authority
# ==============================================================
CLAUDE_PORTFOLIO = {
    "enabled":              True,
    "scan_interval_min":    15,       # Portfolio analysis every 15 min
    "position_monitor_sec": 120,      # Position monitor every 2 min
    "max_actions_per_cycle": 4,       # Max 4 OPEN/CLOSE actions per cycle
    "model":                "sonnet", # Claude model for portfolio analysis
    "max_prompt_chars":     6000,     # Cap prompt size
}

# ==============================================================
# SMART SCANNER (legacy — kept for reference, not used by Claude Portfolio mode)
# ==============================================================
SCANNER = {
    "min_confidence":        0.45,
    "scan_interval_min":     15,
    "min_adx_trend":         22,
    "futures_adx_threshold": 28,
    "max_atr_pct":           0.08,
    "timeframes":            ["1d", "4h", "1h", "15m", "1m"],
}

# ==============================================================
# ACTIVE STRATEGIES (legacy — Claude Portfolio mode makes its own decisions)
# ==============================================================
# ACTIVE_STRATEGIES = [
#     "multitf_futures",
#     "supertrend_futures",
#     "funding_arb_futures",
#     "mean_reversion_spot",
# ]
# ALL_MODE_STRATEGIES = [...]

# 24/7 MODE — Claude decides when to trade (no hour blocking)
BLOCKED_HOURS_UTC = set()   # Claude has access to time data and decides itself

# ==============================================================
# TRADING PAIRS
#
# CRYPTO pairs — spot + futures on all 4 exchanges
# COMMODITY pairs — futures only (traded as USDT-margined perpetuals)
#
#   Gold  (XAU/USDT) — available on Binance, MEXC, Bybit, Bitget futures
#   Silver (XAG/USDT) — available on Binance, MEXC, Bybit, Bitget futures
#   Oil (WTI/USDT)   — available on Bitget futures as WTIUSDT; Bybit as WTIUSDT
#
# NOTE: Commodities trade as USDT-margined futures perpetuals on crypto
#       exchanges. They are NOT physically settled. The strategy selector
#       treats them identically to crypto futures (trend + multi-TF).
#       Symbol format: "{BASE}/USDT:USDT" (ccxt unified perpetual format)
# ==============================================================

TRADING_PAIRS = {
    "binance": {
        "spot": [
            "BTC/USDT", "ETH/USDT", "BNB/USDT", "SOL/USDT",
            "XRP/USDT", "ADA/USDT", "DOGE/USDT", "AVAX/USDT",
            # Binance-unique
            "DOT/USDT", "LINK/USDT", "UNI/USDT", "ATOM/USDT",
            "FIL/USDT", "AAVE/USDT", "LTC/USDT", "BCH/USDT",
        ],
        "futures": [
            "BTC/USDT:USDT", "ETH/USDT:USDT", "BNB/USDT:USDT",
            "SOL/USDT:USDT", "XRP/USDT:USDT", "ADA/USDT:USDT",
            "DOGE/USDT:USDT", "AVAX/USDT:USDT",
            # Binance-unique futures
            "DOT/USDT:USDT", "LINK/USDT:USDT", "UNI/USDT:USDT",
            "ATOM/USDT:USDT", "FIL/USDT:USDT", "AAVE/USDT:USDT",
            "LTC/USDT:USDT", "BCH/USDT:USDT",
            # Commodities (XAG removed — 0% WR, -$2.35 avg PnL confirmed)
            "XAU/USDT:USDT",
        ],
    },
    "mexc": {
        "spot": [
            "BTC/USDT", "ETH/USDT", "SOL/USDT", "XRP/USDT",
            "ADA/USDT", "DOGE/USDT", "AVAX/USDT",
            # MEXC-unique spot (MEXC has many small-cap gems)
            "KASPA/USDT", "CFX/USDT", "AGIX/USDT",
            "TURBO/USDT", "LOOM/USDT",
        ],
        "futures": [],   # MEXC futures API geo-blocked from Pakistan (403)
    },
    "bybit": {
        "spot": [
            "BTC/USDT", "ETH/USDT", "SOL/USDT", "XRP/USDT", "DOGE/USDT",
            # Bybit-unique (not on Binance/Bitget)
            "SUI/USDT", "OP/USDT", "ARB/USDT", "NEAR/USDT", "APT/USDT",
            "FET/USDT", "RNDR/USDT", "INJ/USDT", "TIA/USDT", "SEI/USDT",
        ],
        "futures": [
            "BTC/USDT:USDT", "ETH/USDT:USDT", "SOL/USDT:USDT",
            "XRP/USDT:USDT", "DOGE/USDT:USDT",
            # Bybit-unique futures
            "SUI/USDT:USDT", "OP/USDT:USDT", "ARB/USDT:USDT",
            "NEAR/USDT:USDT", "APT/USDT:USDT", "FET/USDT:USDT",
            "RNDR/USDT:USDT", "INJ/USDT:USDT", "TIA/USDT:USDT", "SEI/USDT:USDT",
            # Commodities (XAG removed — 0% WR)
            "XAU/USDT:USDT",
        ],
    },
    "bitget": {
        "spot": [
            "BTC/USDT", "ETH/USDT", "SOL/USDT", "XRP/USDT",
            # Bitget-unique (not on Bybit unique list)
            "PEPE/USDT", "WIF/USDT", "BONK/USDT", "FLOKI/USDT",
            "ORDI/USDT", "STX/USDT", "IMX/USDT", "MANTA/USDT",
        ],
        "futures": [
            "BTC/USDT:USDT", "ETH/USDT:USDT", "SOL/USDT:USDT", "XRP/USDT:USDT",
            # Bitget-unique futures
            "PEPE/USDT:USDT", "WIF/USDT:USDT", "BONK/USDT:USDT",
            "FLOKI/USDT:USDT", "ORDI/USDT:USDT", "STX/USDT:USDT",
            "IMX/USDT:USDT", "MANTA/USDT:USDT",
            # Commodities (XAG removed — 0% WR)
            "XAU/USDT:USDT",
        ],
    },
}

# ==============================================================
# COMMODITY METADATA
# Used by the strategy selector to apply commodity-appropriate
# parameters (slower EMAs, wider ATR, different ADX thresholds)
# ==============================================================
COMMODITIES = {
    # symbol_base → metadata
    "XAU": {
        "name":         "Gold",
        "emoji":        "🥇",
        "atr_mult":     1.5,    # wider SL for Gold (less volatile intraday)
        "min_adx":      18,     # Gold trends strongly — lower ADX threshold
        "corr_asset":   "USD",  # inversely correlated with USD strength
    },
    "XAG": {
        "name":         "Silver",
        "emoji":        "🥈",
        "atr_mult":     1.8,    # Silver more volatile than Gold
        "min_adx":      20,
        "corr_asset":   "XAU",  # follows Gold with amplification
    },
    "WTI": {
        "name":         "Oil (WTI)",
        "emoji":        "🛢️",
        "atr_mult":     2.0,    # Oil can be very volatile
        "min_adx":      22,
        "corr_asset":   "USD",  # oil priced in USD
    },
    "CL": {                     # alternative symbol for Oil on some exchanges
        "name":         "Oil (WTI)",
        "emoji":        "🛢️",
        "atr_mult":     2.0,
        "min_adx":      22,
        "corr_asset":   "USD",
    },
}

def is_commodity(symbol: str) -> bool:
    """Return True if the symbol base is a commodity (Gold/Silver/Oil)."""
    base = symbol.split("/")[0].upper()
    return base in COMMODITIES

def get_commodity_meta(symbol: str) -> dict:
    """Return commodity metadata for a symbol, or {} if not a commodity."""
    base = symbol.split("/")[0].upper()
    return COMMODITIES.get(base, {})

# ==============================================================
# RISK MANAGEMENT (single-bot default)
# ==============================================================
RISK = {
    # AGGRESSIVE MODE: MCP Brain has full autonomy — utilize all available capital
    "max_position_pct":     0.05,     # 5% max notional per position
    "max_open_positions":   8,        # 8 concurrent positions (4 exchanges × 2 each)
    "max_daily_loss_pct":   0.05,     # 5% daily loss halt
    "default_stop_loss":    0.035,    # 3.5% SL fallback — wide enough to survive noise wicks
    "default_take_profit":  0.10,     # 10% TP fallback — let winners run (~1:2.8 R:R)
    "futures_max_leverage": 5,        # 5x max leverage (controlled risk)
    "default_leverage":     5,        # 5x default futures leverage
    "min_rr_ratio":         1.5,      # 1.5:1 minimum R:R
    "trailing_stop":        True,
    "trailing_activation":  0.018,    # Activate trailing at 1.8% (was 0.8% — within normal noise, exited winners too early)
    "trailing_distance":    0.012,    # Trail 1.2% behind peak (was 0.6% — any tiny pullback triggered exit)
    "max_drawdown_pct":     0.25,     # 25% max DD before halt
    "position_sizing_mode": "kelly",  # Kelly criterion sizing — adapts to edge
}

# ==============================================================
# BLACKLISTING
# ==============================================================
BLACKLIST = {
    "consecutive_sl_limit": 3,
    "auto_expiry_hours":    24,
    "volatility_spike_pct": 0.15,
    "manual_list":          [],
}

# ==============================================================
# AUTO-OPTIMIZATION
# ==============================================================
AUTO_OPTIMIZE = {
    "enabled": True,       # ENABLED — auto-tune every Sunday 2am UTC
    "symbol":  "BTC/USDT",
    "days":    30,
}

# ==============================================================
# DCA
# ==============================================================
DCA = {
    "interval_hours":   3,        # Every 3h (was 4 — more frequent accumulation)
    "amount_usdt":      6.0,      # $6 per buy — meets Binance NOTIONAL filter ($5 min)
    "dip_buy_pct":      0.03,     # Buy extra at 3% dip (was 5% — catch more dips)
    "dip_multiplier":   2.0,      # 2x on dips
    "max_daily_buys":   8,        # Up to 8/day (was 6)
    "take_profit_pct":  0.03,     # Take profit at 3% — lock in gains, reuse capital faster
}

# ==============================================================
# REBALANCING
# ==============================================================
REBALANCING = {
    "targets": {
        "BTC": 0.40,
        "ETH": 0.30,
        "BNB": 0.20,
    },
    "interval_hours":  24,
    "threshold_pct":   0.05,
    "min_trade_usdt":  10.0,
}

# ==============================================================
# CUSTOM RULES
# ==============================================================
CUSTOM_RULES = {
    "name":       "My Custom Strategy",
    "timeframe":  "1h",
    "lookback":   100,
    "indicators": {
        "rsi":      {"type": "rsi", "period": 14},
        "ema_fast": {"type": "ema", "period": 9},
        "ema_slow": {"type": "ema", "period": 21},
    },
    "entry_long":  [
        {"indicator": "rsi",      "op": "<",  "value": 35},
        {"indicator": "ema_fast", "op": ">",  "indicator2": "ema_slow"},
    ],
    "entry_short": [
        {"indicator": "rsi",      "op": ">",  "value": 65},
        {"indicator": "ema_fast", "op": "<",  "indicator2": "ema_slow"},
    ],
    "exit_long":  [{"indicator": "rsi", "op": ">", "value": 60}],
    "exit_short": [{"indicator": "rsi", "op": "<", "value": 40}],
}

# ==============================================================
# STRATEGY PARAMETERS (legacy — kept for DCA/rebalance and reference)
# Claude Portfolio mode does NOT use these for entry/exit decisions.
# ==============================================================
SUPERTREND = {
    "timeframe": "1h", "htf_timeframe": "4h",
    "atr_period": 10, "atr_multiplier": 3.0,
    "rsi_period": 14, "rsi_min": 30, "rsi_max": 70,
    "volume_ma": 20, "min_volume_mult": 0.7,
    "atr_sl_mult": 2.0, "atr_tp_mult": 4.0,
    "lookback_candles": 120, "min_atr_pct": 0.002, "max_atr_pct": 0.08,
}

MEAN_REVERSION = {
    "timeframe": "1h",
    "bb_period": 20, "bb_std": 2.0,
    "rsi_period": 14, "rsi_oversold": 35, "rsi_overbought": 65,
    "rsi_exit_long": 55, "rsi_exit_short": 45,
    "bb_squeeze_min": 0.008,
    "volume_ma": 20, "min_volume_mult": 0.8,
    "sl_bb_mult": 0.5, "tp_midline": True,
    "lookback_candles": 100,
    "trend_filter": True, "trend_ema_period": 200,
}

MULTI_TF = {
    "htf_timeframe": "4h", "mtf_timeframe": "1h", "ltf_timeframe": "15m",
    "trend_ema": 200, "structure_fast": 9, "structure_slow": 21,
    "entry_fast": 5, "entry_slow": 13,
    "rsi_period": 14, "rsi_oversold": 30, "rsi_overbought": 70,
    "adx_period": 14, "adx_min": 20,
    "atr_period": 14, "atr_sl_mult": 2.0, "atr_tp_mult": 4.5,
    "max_trades_per_day": 5, "lookback_candles": 250,
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
    "rsi_period": 14, "rsi_oversold": 30, "rsi_overbought": 70,
    "macd_fast": 12, "macd_slow": 26, "macd_signal": 9,
    "atr_period": 14, "atr_sl_mult": 2.5, "atr_tp_mult": 6.5,
    "volume_ma": 20, "min_volume_mult": 1.0, "lookback_candles": 100,
}

SCALPING = {
    "timeframe": "1m",
    "fast_ema": 5, "slow_ema": 13,
    "rsi_period": 7, "rsi_oversold": 30, "rsi_overbought": 70,
    "bb_period": 20, "bb_std": 2.0,
    "volume_spike_mult": 1.5,
    "orderbook_depth": 10, "imbalance_ratio": 1.3,
    "min_spread_pct": 0.001, "max_spread_pct": 0.003,
    "stop_loss": 0.006, "take_profit": 0.015,
    "lookback_candles": 50,
}
