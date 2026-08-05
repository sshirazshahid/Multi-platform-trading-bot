"""Legacy strategy parameters (backtest / reference)."""
import os

BLACKLIST = {
    "consecutive_sl_limit": 3,
    "auto_expiry_hours": 24,
    "volatility_spike_pct": 0.15,
    "manual_list": [],
}

# ==============================================================
# AUTO-OPTIMIZATION
# ==============================================================
AUTO_OPTIMIZE = {
    "enabled": True,  # ENABLED — auto-tune every Sunday 2am UTC
    "symbol": "BTC/USDT",
    "days": 30,
}

# ==============================================================
# DCA
# ==============================================================
DCA = {
    "interval_hours": 3,  # Every 3h (was 4 — more frequent accumulation)
    "amount_usdt": 6.0,  # $6 per buy — meets Binance NOTIONAL filter ($5 min)
    "dip_buy_pct": 0.03,  # Buy extra at 3% dip (was 5% — catch more dips)
    "dip_multiplier": 2.0,  # 2x on dips
    "max_daily_buys": 8,  # Up to 8/day (was 6)
    "take_profit_pct": 0.03,  # Take profit at 3% — lock in gains, reuse capital faster
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
    "interval_hours": 24,
    "threshold_pct": 0.05,
    "min_trade_usdt": 10.0,
}

# ==============================================================
# CUSTOM RULES
# ==============================================================
CUSTOM_RULES = {
    "name": "My Custom Strategy",
    "timeframe": "1h",
    "lookback": 100,
    "indicators": {
        "rsi": {"type": "rsi", "period": 14},
        "ema_fast": {"type": "ema", "period": 9},
        "ema_slow": {"type": "ema", "period": 21},
    },
    "entry_long": [
        {"indicator": "rsi", "op": "<", "value": 35},
        {"indicator": "ema_fast", "op": ">", "indicator2": "ema_slow"},
    ],
    "entry_short": [
        {"indicator": "rsi", "op": ">", "value": 65},
        {"indicator": "ema_fast", "op": "<", "indicator2": "ema_slow"},
    ],
    "exit_long": [{"indicator": "rsi", "op": ">", "value": 60}],
    "exit_short": [{"indicator": "rsi", "op": "<", "value": 40}],
}

# ==============================================================
# STRATEGY PARAMETERS (legacy — kept for DCA/rebalance and reference)
# Claude Portfolio mode does NOT use these for entry/exit decisions.
# ==============================================================
SUPERTREND = {
    "timeframe": "1h",
    "htf_timeframe": "4h",
    "atr_period": 10,
    "atr_multiplier": 3.0,
    "rsi_period": 14,
    "rsi_min": 30,
    "rsi_max": 70,
    "volume_ma": 20,
    "min_volume_mult": 1.2,  # tightened from 0.7 — whipsaws in low-vol
    "atr_sl_mult": 2.0,
    "atr_tp_mult": 4.0,
    "lookback_candles": 120,
    "min_atr_pct": 0.002,
    "max_atr_pct": 0.08,
}

MEAN_REVERSION = {
    "timeframe": "1h",
    "bb_period": 20,
    "bb_std": 2.0,
    "rsi_period": 14,
    "rsi_oversold": 30,
    "rsi_overbought": 70,
    "rsi_exit_long": 55,
    "rsi_exit_short": 45,
    "bb_squeeze_min": 0.008,
    "volume_ma": 20,
    "min_volume_mult": 0.8,
    "sl_bb_mult": 0.5,
    "tp_midline": False,
    "tp_range_pct": 0.6,
    "lookback_candles": 100,
    "trend_filter": True,
    "trend_ema_period": 200,
    "range_lookback": 20,
    "max_range_pct": 0.06,
    "max_hold_candles": 4,
    "require_sweep": True,
}

MULTI_TF = {
    "htf_timeframe": "1h",
    "mtf_timeframe": "15m",
    "ltf_timeframe": "5m",
    "trend_ema": 200,
    "structure_fast": 9,
    "structure_slow": 21,
    "entry_fast": 5,
    "entry_slow": 13,
    "rsi_period": 14,
    "rsi_oversold": 35,
    "rsi_overbought": 65,
    "adx_period": 14,
    "adx_min": 22,
    "atr_period": 14,
    "atr_sl_mult": 1.5,
    "target_rr_min": 1.2,
    "target_rr_max": 1.6,
    "max_trades_per_day": 3,
    "lookback_candles": 250,
    "vwap_pullback_pct": 0.002,
}

# ==============================================================
# BREAKOUT STRATEGY PROFILES (research / backtest only — NOT wired
# into the live Claude-Portfolio pipeline). All thresholds live here.
# ==============================================================
ASIAN_RANGE_BREAKOUT = {
    "timeframe": "1h",
    "lookback_candles": 250,
    # Asian session window (UTC hours, inclusive) used to build the range.
    "session_start_hour": 0,
    "session_end_hour": 6,
    # Entry buffer above/below the session range to avoid false pokes.
    "breakout_buffer_pct": 0.0015,  # 0.15%
    "atr_period": 14,
    "atr_sl_mult": 1.5,  # SL distance = ATR * mult
    "rr": 2.0,  # reward:risk
    "min_range_pct": 0.003,  # ignore degenerate flat sessions
    "max_range_pct": 0.05,  # ignore blown-out sessions
}

DOW_SWING = {
    "timeframe": "4h",
    "lookback_candles": 250,
    # Confirmed fractal pivots define swing structure.
    "swing_left": 2,
    "swing_right": 2,
    "breakout_buffer_pct": 0.0010,  # 0.10% beyond the prior swing
    "atr_period": 14,
    "atr_sl_mult": 1.5,
    "rr": 2.0,
    "trend_filter": True,
    "trend_ema_period": 200,
}

BB_SQUEEZE = {
    "timeframe": "1h",
    "lookback_candles": 200,
    "bb_period": 20,
    "bb_std": 2.0,
    "kc_period": 20,
    "kc_mult": 1.5,
    "breakout_buffer_pct": 0.0010,  # 0.10% beyond band on release
    "atr_period": 14,
    "atr_sl_mult": 1.5,
    "rr": 2.0,
    "min_squeeze_bars": 6,  # require coiling before the release
}

SCALP_PROFILE = {
    "timeframe": "15m",
    "lookback_candles": 200,
    "swing_left": 2,
    "swing_right": 2,
    "breakout_buffer_pct": 0.0008,  # 0.08%
    "atr_period": 14,
    "atr_sl_mult": 1.2,  # tighter stop for scalps
    "rr": 1.5,
    "max_hold_candles": 8,
}

# Pairs the breakout research profiles screen against.
BREAKOUT_PAIRS = [
    "BTC/USDT",
    "ETH/USDT",
    "SOL/USDT",
    "BNB/USDT",
    "XRP/USDT",
]

GRID_TRADING = {
    "timeframe": "1h",
    "grid_levels": 8,
    "grid_spacing": 0.006,
    "order_size_pct": 0.015,
    "upper_offset": 0.04,
    "lower_offset": 0.04,
    "rebalance_after": 12,
    "volatility_check": True,
    "atr_pause_pct": 0.03,
}

TREND_FOLLOWING = {
    "timeframe": "15m",
    "fast_ema": 9,
    "slow_ema": 21,
    "trend_ema": 50,
    "rsi_period": 14,
    "rsi_oversold": 30,
    "rsi_overbought": 70,
    "macd_fast": 12,
    "macd_slow": 26,
    "macd_signal": 9,
    "atr_period": 14,
    "atr_sl_mult": 2.5,
    "atr_tp_mult": 6.5,
    "volume_ma": 20,
    "min_volume_mult": 1.0,
    "lookback_candles": 100,
}

SCALPING = {
    "timeframe": "1m",
    "fast_ema": 5,
    "slow_ema": 13,
    "rsi_period": 7,
    "rsi_oversold": 30,
    "rsi_overbought": 70,
    "bb_period": 20,
    "bb_std": 2.0,
    "volume_spike_mult": 1.5,
    "orderbook_depth": 10,
    "imbalance_ratio": 1.3,
    "min_spread_pct": 0.00005,
    "max_spread_pct": 0.003,
    "stop_loss": 0.006,
    "take_profit": 0.015,
    "lookback_candles": 50,
}
