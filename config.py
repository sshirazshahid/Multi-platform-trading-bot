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
# ------------------------------------------------------------------
# OPERATING MODE — learning-first rebuild (2026-04-14 pivot)
# ------------------------------------------------------------------
# OBSERVATION     — collect balances/positions/candidates, place NO orders
#                   (not even paper). Used for warehouse population & feature
#                   learning when the bot is in diagnostic mode.
# PAPER           — simulate all fills via core/sim_execution.py. The default
#                   during rebuild. Risk engine fully active; no real capital
#                   moves.
# CONTROLLED_LIVE — real orders. Disabled unless ALL of:
#                     * OPERATING_MODE=CONTROLLED_LIVE
#                     * env var CONTROLLED_LIVE_ENABLED=true
#                     * docs/CONTROLLED_LIVE_CHECKLIST.md is signed
#                   Spec requires owner sign-off before any live capital.
# ------------------------------------------------------------------
_VALID_MODES = {"OBSERVATION", "PAPER", "CONTROLLED_LIVE"}
OPERATING_MODE = os.getenv("OPERATING_MODE", "PAPER").upper()
if OPERATING_MODE not in _VALID_MODES:
    raise ValueError(
        f"OPERATING_MODE must be one of {_VALID_MODES}, got {OPERATING_MODE!r}"
    )

# Legacy DRY_RUN is now derived from the mode. Any existing code path that
# branches on DRY_RUN gets paper execution unless we are explicitly CONTROLLED_LIVE.
DRY_RUN = OPERATING_MODE != "CONTROLLED_LIVE"

# Extra latch for live mode — env var must also be flipped explicitly.
CONTROLLED_LIVE_ENABLED = os.getenv("CONTROLLED_LIVE_ENABLED", "false").lower() == "true"

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
# SIM-LIVE REALISM  (2026-04-11)
# ==============================================================
# After 2 weeks of DRY_RUN profits turned into LIVE losses, we diagnosed
# three execution-path gaps:
#
#   1. Paper fills used `ticker.last` (a midpoint-ish price). Real market
#      orders cross the spread and slip through the book.
#   2. Paper SL/TP compared `ticker.last` at poll time — an intrabar wick
#      that dipped below SL and recovered was invisible to paper, so paper
#      kept losers that LIVE would have stopped out (survivorship bias).
#   3. Paper futures never paid funding.
#
# These knobs make DRY_RUN's execution match LIVE closely enough that a
# paper P&L curve should predict a live P&L curve within ~1 sigma.
SLIPPAGE = {
    "enabled":        True,     # Master switch for the slippage model.
    "pct_open":       0.0005,   # 5 bps on open — cross spread + walk book
    "pct_close":      0.0005,   # 5 bps on close
    "pct_stop_loss":  0.0010,   # 10 bps on stop-out — SL fills worse in fast moves
    "wick_sl_tp":     True,     # Use last 1m candle high/low for SL/TP trigger
    "funding":        True,     # Charge funding every 8h on paper futures
    "prefer_book":    True,     # Prefer ticker bid/ask over ticker last when available
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
    "scan_interval_min":    5,        # 5 min — was 15; faster reaction in peak hours.
                                      # At 15 min the bot was only getting 4 chances/hour
                                      # which is too slow when signals are time-sensitive.
    "position_monitor_sec": 30,       # Position monitor every 30s (consumed by bot_engine)
    "max_actions_per_cycle": 4,       # Max 4 OPEN/CLOSE actions per cycle
    "model":                "sonnet", # Claude model for portfolio analysis
    "max_prompt_chars":     6000,     # Cap prompt size
    "max_per_exchange":     2,        # Max 2 positions per exchange — focus capital
    "news_interval_min":    30,       # News scan interval in minutes (bot_engine)
    "learn_interval_min":   60,       # Learning engine interval in minutes (bot_engine)
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

# NOTE: The authoritative BLOCKED_HOURS_UTC definition lives further down in
# the "TRADING GATES" section (evidence-based from knowledge_model.json).
# The previous "24/7 mode" empty-set definition was removed on 2026-04-11
# because the tiered mechanism uses hour gating as a primary filter.

# ==============================================================
# TRADING PAIRS
#
# CRYPTO pairs — spot + futures on all 4 exchanges
# COMMODITY pairs — futures only (traded as USDT-margined perpetuals)
#
#   Gold  (XAU/USDT) — available on Binance, Bybit, Bitget futures
#   Silver (XAG/USDT) — available on Binance, Bybit, Bitget futures
#   Oil (WTI/USDT)   — available on Bitget futures as WTIUSDT; Bybit as WTIUSDT
#
# NOTE: Commodities trade as USDT-margined futures perpetuals on crypto
#       exchanges. They are NOT physically settled. The strategy selector
#       treats them identically to crypto futures (trend + multi-TF).
#       Symbol format: "{BASE}/USDT:USDT" (ccxt unified perpetual format)
# ==============================================================

# 2026-04-15: Expanded from BTC/ETH-only to top 30 liquid coins across all
# 3 exchanges. MCP Brain's scoring engine + meta-filter + risk engine provide
# the quality gate — narrow universe was starving the bot of opportunities.
_TOP_SPOT = [
    "BTC/USDT", "ETH/USDT", "BNB/USDT", "SOL/USDT", "XRP/USDT",
    "DOGE/USDT", "ADA/USDT", "AVAX/USDT", "LINK/USDT", "DOT/USDT",
    "UNI/USDT", "LTC/USDT", "BCH/USDT", "NEAR/USDT", "APT/USDT",
    "FIL/USDT", "ARB/USDT", "OP/USDT", "ATOM/USDT", "SUI/USDT",
    "SEI/USDT", "INJ/USDT", "FET/USDT", "RENDER/USDT", "TIA/USDT",
    "ALGO/USDT", "IOTA/USDT", "VET/USDT", "PEPE/USDT", "WIF/USDT",
]
_TOP_FUTURES = [s.replace("/USDT", "/USDT:USDT") for s in _TOP_SPOT]

TRADING_PAIRS = {
    "binance": {"spot": list(_TOP_SPOT), "futures": list(_TOP_FUTURES)},
    "bybit":   {"spot": list(_TOP_SPOT), "futures": list(_TOP_FUTURES)},
    "bitget":  {"spot": list(_TOP_SPOT), "futures": list(_TOP_FUTURES)},
}

UNIVERSE_WHITELIST = set(_TOP_SPOT) | set(_TOP_FUTURES)


MEME_COINS = {"DOGE", "SHIB", "PEPE", "WIF", "BONK", "FLOKI", "TURBO", "LOOM"}

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
# RISK MANAGEMENT — HIGH-WR TIERED MECHANISM (2026-04-11 rewrite)
#
# Rationale (from data/knowledge_model.json + data/kelly_stats.json + post_mortem):
#   147 live trades → 41.5% WR, PF 0.42, −$57 net. 80% of losses traced to
#   5x leverage × 3.5% SL × wide alt shorts in BTC uptrend. Trailing stop is
#   the ONLY profitable exit (100% WR, +$26). Hours 12-20 UTC WR ~65%,
#   hours 0-11/22 UTC WR ~20%. Whitelist ALGO/LUMIA/BNB/BCH etc. at 80%+ WR.
#
# Strategy: fewer, tighter, better-filtered trades; leverage earned by setup
# quality up to 20x; hard dollar-loss clamp so 20x is survivable.
# Leverage is always strategy/backtest-driven — higher tiers require stronger
# signal confluence (confidence ≥ 85% for 20x vs 65% for 5x).
# ==============================================================
RISK = {
    # 2026-04-24: raised from 0.01 → 0.05 on explicit user direction after the
    # 386-trade postmortem (WR 41.7%, avg_win $0.12 vs avg_loss $0.18, negative
    # expectancy because per-trade costs dominate at $0.10-0.30 gross). At $377
    # balance + 1% sizing, average trade notional was $11 at 3x — below the
    # structural cost floor. 5% × 3x = ~$56 notional makes individual P&L
    # moves meaningfully larger than fees+spread+slippage. Supersedes the
    # 2026-04-16 signed-checklist value; user accepted the trade-off.
    "max_position_pct":     0.05,
    "max_open_positions":   8,        # 2 per exchange — focus capital, reduce correlation risk
    "max_daily_loss_pct":   0.05,     # 5% daily loss halt
    "default_stop_loss":    0.020,    # 2.0% fallback SL (ATR-based is primary)
    "default_take_profit":  0.060,    # 6.0% fallback TP (~3:1 R:R vs 2% SL)
    "futures_max_leverage": 3,        # 3x hard ceiling per signed checklist
    "default_leverage":     3,        # 3x baseline
    "min_rr_ratio":         1.2,      # 1.2:1 — high-WR strategies don't need large R:R
    "trailing_stop":        True,
    # 2026-04-24 trailing retune after 50W/0L trailing @ $0.09 avg clipped 4 full
    # TPs @ $2.84 avg. Activation doubled and lock fraction lowered at low peaks
    # (see trailing_stop_manager._lock_fraction) so winners have room to run.
    "trailing_activation":  0.015,    # 1.5% — give winners room before BE lock
    "trailing_distance":    0.010,    # 1.0% — wider trail past activation
    "max_drawdown_pct":     0.12,     # 12% max DD before halt
    "position_sizing_mode": "tiered", # leverage tier drives sizing; kelly is a sanity check
    "max_position_age_hours": 6,      # 6h hard expiry
    "max_stale_hours":       4.0,     # 4h stale exit (was 1.5h — conflicted with 2h min hold)
}

RISK_PER_TRADE_RANGE = (0.0025, 0.005)  # 0.25%-0.5% risk per trade

# ==============================================================
# LEVERAGE TIER SYSTEM (2026-04-17 — size raised to 5% to clear exchange min notionals)
# NOTE: signed CONTROLLED_LIVE checklist (Apr 16) pinned 1% sizing — NOW OUT OF SYNC.
# Current: 3x leverage, 5% size, 1.5% SL → 0.225% balance risk per trade.
# Tiers still exist so the quality-based gating (whitelist, peak hour, BTC-aligned,
# min_confidence) keeps filtering entries — but leverage/size are uniform.
# Invariant: 0.05 × 3 × 0.015 = 0.225% of balance per trade (still under MAX_LOSS 0.5%).
# ==============================================================
#
# 2026-04-24: size_pct lifted so individual trade P&L clears the cost floor.
# Exchange pockets are fragmented ($10-130 per pocket), so 5% of a $10 pocket
# was producing $0.50 notional and step-rejection spam. Sizing below clears
# the $50 min-notional floor at 3x for any pocket ≥ $110 (typical post-rebal).
LEVERAGE_TIERS = {
    "STANDARD": {
        "leverage":               3,
        "size_pct":               0.15,     # 15% (was 5%) — lift above cost floor
        "sl_pct":                 0.015,
        "tp_pct":                 0.0375,   # 2.5:1 R:R
        "min_confidence":         0.65,
        "requires_whitelist":     False,
        "requires_allowed_hour":  True,
        "requires_peak_hour":     False,
        "requires_btc_aligned":   False,
    },
    "STRONG": {
        "leverage":               3,
        "size_pct":               0.10,     # 10% (was 3%)
        "sl_pct":                 0.015,
        "tp_pct":                 0.0375,
        "min_confidence":         0.72,
        "requires_whitelist":     True,
        "requires_allowed_hour":  True,
        "requires_peak_hour":     False,
        "requires_btc_aligned":   True,
    },
    "CONVICTION": {
        "leverage":               3,
        "size_pct":               0.10,
        "sl_pct":                 0.015,
        "tp_pct":                 0.0375,
        "min_confidence":         0.80,
        "requires_whitelist":     True,
        "requires_allowed_hour":  True,
        "requires_peak_hour":     True,
        "requires_btc_aligned":   True,
    },
    "AGGRESSIVE": {
        "leverage":               3,
        "size_pct":               0.10,
        "sl_pct":                 0.015,
        "tp_pct":                 0.0375,
        "min_confidence":         0.85,
        "requires_whitelist":     True,
        "requires_allowed_hour":  True,
        "requires_peak_hour":     True,
        "requires_btc_aligned":   True,
    },
}

# 2026-04-24: clamp loosened with size_pct increase. 0.15 × 3 × 0.015 = 0.675%
# balance risk worst-case; 1.0% clamp leaves headroom for strategy-level SL
# overrides up to 2.2%.
MAX_LOSS_PER_TRADE_PCT = 0.010

# 2026-04-24: raised from $2 → $4 to allow 15% × 3x × 1.5% sizing on $377
# balance (~$2.55 risk/trade). $4 cap still prevents oversize on spikes.
MAX_LOSS_PER_TRADE_USD = 4.0

# Consecutive-loss throttle — dynamic leverage downgrade + pause
CONSEC_LOSS_DOWNGRADE_COUNT = 2   # 2 losses in a row → drop tier cap by 1
CONSEC_LOSS_DOWNGRADE_HOURS = 4   # downgrade lasts 4h
CONSEC_LOSS_PAUSE_COUNT     = 3   # 3 losses in a row → full pause
CONSEC_LOSS_PAUSE_HOURS     = 0.5 # pause lasts 30min (was 2h — too long, missed recoveries)

# Volatility-adaptive leverage cap — high-ATR symbols clamp to STANDARD
HIGH_ATR_PCT_THRESHOLD = 0.025    # ATR% > 2.5% → max leverage = STANDARD (2x)

# ==============================================================
# TRADING GATES — evidence-based whitelist / blacklist / hours
#
# Whitelist (always eligible, earns STRONG/CONVICTION tiers):
#   live data 5+ trades, WR ≥ 50%, positive avg PnL preferred
# Blacklist (hard-blocked at entry):
#   live data 3+ trades, WR ≤ 30%, negative avg PnL
# Allowed hours (UTC):
#   hours where live WR ≥ 45% and volume ≥ 3 trades
# ==============================================================
WHITELIST_SYMBOLS = {
    "ALGO/USDT:USDT",   # 14 trades, 85.7% WR, +$0.12/trade ⭐
    "LUMIA/USDT:USDT",  # 6 trades, 100% WR, +$0.33/trade ⭐
    "AVAX/USDT:USDT",   # 10 trades, 60% WR
    "BNB/USDT:USDT",    # 8 trades, 50% WR, +$0.29/trade
    "BCH/USDT:USDT",    # 3 trades, 66.7% WR
    "ORDI/USDT:USDT",   # 8 trades, 50% WR
    "DOT/USDT:USDT",    # 7 trades, 42.9% WR, +$0.08/trade (positive)
    "LINK/USDT:USDT",   # 7 trades, 42.9% WR
    "GRASS/USDT:USDT",  # 3 trades, 66.7% WR, +$0.84/trade
    "QTUM/USDT:USDT",   # 1 trade, 100% WR, +$3.69 (tentative)
    "ACT/USDT:USDT",    # 1 trade, 100% WR, +$1.59 (tentative)
    "IOTA/USDT:USDT",   # 1 trade, 100% WR, +$0.60 (tentative)
    "FET/USDT:USDT",    # 1 trade, 100% WR, +$0.21 (tentative)
    "VET/USDT:USDT",    # 1 trade, 100% WR (tentative)
    "BTC/USDT:USDT",    # macro anchor, always tradeable
    "ETH/USDT:USDT",    # 6 trades, 50% WR — core asset; STRONG tier only
}

# 2026-04-13: Cleared. All prior losses were under the old broken engine
# (ANTI-LOSS gate widening SL, fuzzy scoring, no max-loss cap, wrong sizing).
# The new systematic engine (EMA cross + RSI + hard SL + 1.5% risk) is a
# completely different system. AutoMutator will re-blacklist any symbol that
# accumulates >=4 losses at >=70% loss rate under the NEW engine.
#
# 2026-04-16 (post-audit): SOL and XRP accumulated 16.7% and 10% WR across
# 10-20 trades driving -$14 and -$7 under the v3.x engine (NOT the old broken
# one). AutoMutator's 4-loss / 70%-rate rule requires >=4 LOSSES in the
# lookback and those symbols weren't tripping it due to post_mortem tag gaps.
# Hard-block them explicitly until the meta-filter has enough data to re-rate.
#
# 2026-04-17: cleared at user request.
# 2026-04-26: Re-added per Phase 1 attribution diagnostic. Bootstrap-CI95 on
# alpha:
#   SOL/USDT:USDT (n=13)  mean alpha -$0.227, NEGATIVE_EDGE-trending
#   XRP/USDT:USDT (n=12)  mean alpha -$0.140, NEGATIVE_EDGE
# Both are eating ~$0.20+/trade without statistically detectable upside under
# the current signal mix. Blocking until Phase 3 (fitted model) can re-rate.
BLACKLIST_HARD: set = {"SOL/USDT:USDT", "XRP/USDT:USDT"}

# Hour gating (UTC) — from knowledge_model.hour_scores over 147 trades
ALLOWED_HOURS_UTC = set(range(24))  # 2026-04-17: opened to all hours at user request (was 12-20 UTC core cluster)
WARMUP_HOURS_UTC  = {8, 9, 21, 23}                         # half-size entries permitted
# H14 demoted 2026-04-17: previously 92.9%/14 but recent slice shows 25%/4 (or 0%/1
# in latest model) — sample collapsed. Keep in ALLOWED, no CONVICTION routing until
# it re-establishes an edge.
PEAK_HOURS_UTC    = set()
BLOCKED_HOURS_UTC = {1, 5, 6, 10, 22}  # Evidence: H1:0%(6), H5:17.6%(17), H6:0%(6), H10:14.3%(7), H22:30.8%(13) — added 2026-04-17

# Side filter — shorts require BTC macro-bear confirmation
# 2026-04-12: Relaxed. BTC-bear gate blocked 90%+ of short signals
# even when the coin's own 4h+1h EMAs clearly pointed down. The scoring
# engine now requires per-coin 4h+1h EMA alignment — that IS the trend
# filter. Demanding BTC alignment on top was redundant.
SHORTS_REQUIRE_BTC_BEAR = False
BTC_TREND_TIMEFRAME     = "4h"
BTC_TREND_EMA_PERIOD    = 200

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
    "volume_ma": 20, "min_volume_mult": 1.2,   # tightened from 0.7 — whipsaws in low-vol
    "atr_sl_mult": 2.0, "atr_tp_mult": 4.0,
    "lookback_candles": 120, "min_atr_pct": 0.002, "max_atr_pct": 0.08,
}

MEAN_REVERSION = {
    "timeframe": "1h",
    "bb_period": 20, "bb_std": 2.0,
    "rsi_period": 14, "rsi_oversold": 30, "rsi_overbought": 70,
    "rsi_exit_long": 55, "rsi_exit_short": 45,
    "bb_squeeze_min": 0.008,
    "volume_ma": 20, "min_volume_mult": 0.8,
    "sl_bb_mult": 0.5, "tp_midline": False,
    "tp_range_pct": 0.6,
    "lookback_candles": 100,
    "trend_filter": True, "trend_ema_period": 200,
    "range_lookback": 20,
    "max_range_pct": 0.06,
    "max_hold_candles": 4,
    "require_sweep": True,
}

MULTI_TF = {
    "htf_timeframe": "1h", "mtf_timeframe": "15m", "ltf_timeframe": "5m",
    "trend_ema": 200, "structure_fast": 9, "structure_slow": 21,
    "entry_fast": 5, "entry_slow": 13,
    "rsi_period": 14, "rsi_oversold": 35, "rsi_overbought": 65,
    "adx_period": 14, "adx_min": 22,
    "atr_period": 14, "atr_sl_mult": 1.5,
    "target_rr_min": 1.2, "target_rr_max": 1.6,
    "max_trades_per_day": 3, "lookback_candles": 250,
    "vwap_pullback_pct": 0.002,
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
    "min_spread_pct": 0.00005, "max_spread_pct": 0.003,
    "stop_loss": 0.006, "take_profit": 0.015,
    "lookback_candles": 50,
}

# ==============================================================
# NEWS MONITORING (24/7 Enhanced Scanner)
# ==============================================================
NEWS = {
    "scan_interval_min":       30,    # Default scan interval (minutes)
    "fast_scan_interval_min":  10,    # When volatility is high (F&G < 20 or > 80)
    "max_headlines":           50,    # Max headlines to keep per scan
    "breaking_news_alert":     True,  # Log breaking news at WARNING level
    "sentiment_history_days":  7,     # Days of per-coin sentiment history to keep
}

# ==============================================================
# STRATEGY ENABLE FLAGS — only Trend Pullback + Range Mean Reversion active
# ==============================================================
ENABLE_DCA       = False
ENABLE_REBALANCE = False

# ==============================================================
# PARTIAL TAKE PROFIT
# ==============================================================
PARTIAL_TP = {
    "enabled":                True,
    "first_take_at_pct":      0.5,    # close partial at 50% of TP distance
    "first_take_size":        0.5,    # close 50% of position
    "move_sl_to_breakeven":   True,   # move SL to entry after partial
}

# ==============================================================
# SPOT PORTFOLIO MANAGEMENT
# 2026-04-14 learning-first pivot: recommendation-only. SpotManager still
# evaluates holdings each cycle but writes HOLD/TRIM/EXIT/ROTATE_TO_USDT
# suggestions to data/spot_recommendations.jsonl instead of executing them.
# Spec §13: "No autonomous futures funding from spot during the rebuild
# phase." Hedging is disabled until futures proves positive expectancy.
# ==============================================================
SPOT_PORTFOLIO = {
    "enabled":                  True,
    "recommendation_only":      True,    # NEW — never executes orders
    "scan_interval_min":        30,
    "cost_basis_method":        "average",
    "scale_out_threshold_pct":  0.20,
    "sell_on_structure_break":  False,   # downgraded to recommendation-only
    "hedge_via_futures":        False,   # disabled — no derivatives overlay
    "hedge_drawdown_pct":       0.10,
}

# ==============================================================
# CAPITAL ALLOCATION (Spot ↔ Futures)
# 2026-04-14 learning-first pivot: disabled. CapitalAllocator still runs
# to *evaluate* transfer opportunities and writes them to
# data/allocation_recommendations.jsonl, but never calls exchange.transfer().
# Re-enable only after paper trading proves positive expectancy AND owner
# signs docs/CONTROLLED_LIVE_CHECKLIST.md.
# ==============================================================
CAPITAL_ALLOCATION = {
    "enabled":                    False,  # hard-off for rebuild phase
    "recommendation_only":        True,   # emits to jsonl, never executes
    "cycle_interval_min":         15,
    "accumulation_threshold_usd": 10.0,
    "spot_targets":               {"BTC": 0.50, "ETH": 0.50},
    "hedge_drawdown_pct":         0.10,
    "max_transfer_pct":           0.20,
    "min_transfer_usdt":          5.0,
    "rebalance_threshold_pct":    0.60,
}

# ==============================================================
# STRATEGY GATE — auto-disable underperformers
# ==============================================================
STRATEGY_GATE = {
    "min_win_rate":          0.50,    # disable below 50% WR
    "min_sample_size":       15,      # need 15+ trades before judging
    "fee_alert_ratio":       0.20,    # alert if fees > 20% of gross profit
    "auto_disable_fee_heavy": True,
}

# ==============================================================
# SCALING CONDITIONS — scale risk only after proving edge
# ==============================================================
SCALING = {
    "min_live_trades":        200,
    "min_win_rate":           0.60,
    "max_drawdown_for_scale": 0.10,
    "scale_factor":           1.5,
}
