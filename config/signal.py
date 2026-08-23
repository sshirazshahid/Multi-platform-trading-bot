"""Signal source, machine strategy, TSMOM, execution freshness gates."""
import os

from config.modes import MODE_PROFILE
from config.risk import LEVERAGE_TIERS

SCALP_TIER_ENABLED = os.getenv("SCALP_TIER_ENABLED", "false").lower() == "true"

# ── Signal source (De-Emotion 2026-08-04: no LLM on the decision path) ──────
#   "tsmom"   - long-only TSMOM on validated majors (DEFAULT).
#   "machine" - deterministic detector ensemble.
#   "s3"      - multi-horizon momentum ensemble.
#   "mcp" / "mcp_det" - deterministic MCPStrategyScorer (LLM-free).
#   "none"    - directional entries OFF; carry runner only.
# ⚠ Do NOT flip the default to mcp_det without an owner decision.
SIGNAL_SOURCE = os.getenv("SIGNAL_SOURCE", "tsmom").lower()
if SIGNAL_SOURCE not in ("mcp", "mcp_det", "tsmom", "machine", "s3", "none"):
    raise ValueError(
        f"SIGNAL_SOURCE must be 'mcp', 'mcp_det', 'tsmom', 'machine', 's3', or 'none', "
        f"got {SIGNAL_SOURCE!r}"
    )

# F1 carry execution intent (PAPER). Both values book pessimistic taker/taker
# fills today. ``maker_first`` is retained as an experiment label, but receives
# no maker fee/midpoint credit until an event-driven queue/fill simulator proves
# a resting spot order filled before its hedge timeout.
F1_EXECUTION_MODE = os.getenv("F1_EXECUTION_MODE", "taker").lower()
if F1_EXECUTION_MODE not in ("taker", "maker_first"):
    raise ValueError(
        f"F1_EXECUTION_MODE must be 'taker' or 'maker_first', got {F1_EXECUTION_MODE!r}"
    )

# S3 ensemble horizons (days). Comma-separated; longest gates min-history.
S3_LOOKBACKS_DAYS = tuple(
    int(x) for x in os.getenv("S3_LOOKBACKS_DAYS", "28,14,7").split(",") if x.strip()
) or (28, 14, 7)

# Machine detector scores are vote weights, not MCP confidence. Earlier paper
# collection used 0.25 and produced too many weak after-cost trades; use the
# live-equivalent floor by default and rely on explicit env overrides for probes.
_MACHINE_MIN_SCORE_DEFAULT = "0.34"

MACHINE_STRATEGY = {
    "enabled": os.getenv("MACHINE_STRATEGY_ENABLED", "true").lower() == "true",
    "runtime_mode": os.getenv("MACHINE_STRATEGY_RUNTIME_MODE", "paper_shadow"),
    "market_type": os.getenv("MACHINE_STRATEGY_MARKET_TYPE", "futures").lower(),
    "exchange_preference": [
        s.strip().lower()
        for s in os.getenv("MACHINE_STRATEGY_EXCHANGES", "binance,bybit,bitget").split(",")
        if s.strip()
    ],
    "universe": [
        s.strip().upper()
        for s in os.getenv(
            "MACHINE_STRATEGY_UNIVERSE",
            "BTC,ETH,SOL,BNB,XRP,ADA,AVAX,LINK,DOGE",
        ).split(",")
        if s.strip()
    ],
    "timeframe": os.getenv("MACHINE_STRATEGY_TIMEFRAME", "15m"),
    "context_timeframes": [
        s.strip()
        for s in os.getenv("MACHINE_STRATEGY_CONTEXT_TIMEFRAMES", "1h,4h").split(",")
        if s.strip()
    ],
    "lookback_candles": int(os.getenv("MACHINE_STRATEGY_LOOKBACK", "220")),
    "max_actions_per_cycle": int(os.getenv("MACHINE_STRATEGY_MAX_ACTIONS", "3")),
    "min_score": float(os.getenv("MACHINE_STRATEGY_MIN_SCORE", _MACHINE_MIN_SCORE_DEFAULT)),
    "execution_confidence_floor": float(
        os.getenv("MACHINE_STRATEGY_EXECUTION_CONFIDENCE_FLOOR", "0.50")
    ),
    "rr": float(os.getenv("MACHINE_STRATEGY_RR", "1.8")),
    "atr_period": int(os.getenv("MACHINE_STRATEGY_ATR_PERIOD", "14")),
    "atr_stop_mult": float(os.getenv("MACHINE_STRATEGY_ATR_STOP_MULT", "1.6")),
    "min_stop_pct": float(os.getenv("MACHINE_STRATEGY_MIN_STOP_PCT", "0.0035")),
    "max_stop_pct": float(os.getenv("MACHINE_STRATEGY_MAX_STOP_PCT", "0.025")),
    "scalp_time_stop_bars": int(os.getenv("MACHINE_STRATEGY_TIME_STOP_BARS", "12")),
    "allow_short_spot": os.getenv("MACHINE_STRATEGY_ALLOW_SHORT_SPOT", "false").lower() == "true",
    "default_leverage": int(os.getenv("MACHINE_STRATEGY_LEVERAGE", "1")),
    "size_pct": float(os.getenv("MACHINE_STRATEGY_SIZE_PCT", "3.0")),
    "min_quote_volume": float(os.getenv("MACHINE_STRATEGY_MIN_QUOTE_VOLUME", "10000000")),
    "max_spread_bps": float(os.getenv("MACHINE_STRATEGY_MAX_SPREAD_BPS", "12")),
    "confirmation_detectors": [
        s.strip()
        for s in os.getenv("MACHINE_STRATEGY_CONFIRMATION_DETECTORS", "ema_rsi").split(",")
        if s.strip()
    ],
    "unconfirmed_min_score": float(os.getenv("MACHINE_STRATEGY_UNCONFIRMED_MIN_SCORE", "0.40")),
    "weights": {
        "valuation": float(os.getenv("MACHINE_WEIGHT_VALUATION", "0.14")),
        "harmonic": float(os.getenv("MACHINE_WEIGHT_HARMONIC", "0.14")),
        "stochastic": float(os.getenv("MACHINE_WEIGHT_STOCHASTIC", "0.10")),
        "ict": float(os.getenv("MACHINE_WEIGHT_ICT", "0.16")),
        "asian_range": float(os.getenv("MACHINE_WEIGHT_ASIAN_RANGE", "0.08")),
        "dow_swing": float(os.getenv("MACHINE_WEIGHT_DOW_SWING", "0.10")),
        "bb_squeeze": float(os.getenv("MACHINE_WEIGHT_BB_SQUEEZE", "0.12")),
        "ema_rsi": float(os.getenv("MACHINE_WEIGHT_EMA_RSI", "0.16")),
    },
    "detector_params": {
        "ema_rsi": {
            "fast_ema": int(os.getenv("MACHINE_EMA_RSI_FAST_EMA", "9")),
            "slow_ema": int(os.getenv("MACHINE_EMA_RSI_SLOW_EMA", "21")),
            "trend_ema": int(os.getenv("MACHINE_EMA_RSI_TREND_EMA", "50")),
            "rsi_period": int(os.getenv("MACHINE_EMA_RSI_RSI_PERIOD", "14")),
            "rsi_signal_ema": int(os.getenv("MACHINE_EMA_RSI_RSI_SIGNAL_EMA", "5")),
            "trend_slope_bars": int(os.getenv("MACHINE_EMA_RSI_TREND_SLOPE_BARS", "4")),
            "min_trend_slope": float(os.getenv("MACHINE_EMA_RSI_MIN_TREND_SLOPE", "0.00005")),
            "min_ema_spread_pct": float(
                os.getenv("MACHINE_EMA_RSI_MIN_EMA_SPREAD_PCT", "0.0002")
            ),
            "max_ema_spread_pct": float(
                os.getenv("MACHINE_EMA_RSI_MAX_EMA_SPREAD_PCT", "0.035")
            ),
            "long_rsi_floor": float(os.getenv("MACHINE_EMA_RSI_LONG_RSI_FLOOR", "45")),
            "long_rsi_ceiling": float(os.getenv("MACHINE_EMA_RSI_LONG_RSI_CEILING", "70")),
            "short_rsi_floor": float(os.getenv("MACHINE_EMA_RSI_SHORT_RSI_FLOOR", "30")),
            "short_rsi_ceiling": float(os.getenv("MACHINE_EMA_RSI_SHORT_RSI_CEILING", "55")),
            "hold_bars": int(os.getenv("MACHINE_EMA_RSI_HOLD_BARS", "3")),
        },
    },
}

SELF_HEALING = {
    "enabled": os.getenv("SELF_HEALING_ENABLED", "true").lower() == "true",
    "repair_enabled": os.getenv("SELF_HEALING_REPAIR_ENABLED", "true").lower() == "true",
    "restart_main": os.getenv("SELF_HEALING_RESTART_MAIN", "false").lower() == "true",
    "retrain_enabled": os.getenv("SELF_HEALING_RETRAIN_ENABLED", "true").lower() == "true",
    "adapt_enabled": os.getenv("SELF_HEALING_ADAPT_ENABLED", "true").lower() == "true",
    "dry_run": os.getenv("SELF_HEALING_DRY_RUN", "false").lower() == "true",
    "min_interval_sec": int(os.getenv("SELF_HEALING_MIN_INTERVAL_SEC", str(15 * 60))),
    "repair_cooldown_sec": int(os.getenv("SELF_HEALING_REPAIR_COOLDOWN_SEC", str(10 * 60))),
    "retrain_cooldown_sec": int(os.getenv("SELF_HEALING_RETRAIN_COOLDOWN_SEC", str(12 * 60 * 60))),
    "adapt_cooldown_sec": int(os.getenv("SELF_HEALING_ADAPT_COOLDOWN_SEC", str(4 * 60 * 60))),
    "replay_timeframe": os.getenv("SELF_HEALING_REPLAY_TIMEFRAME", "15m"),
    "replay_max_files": int(os.getenv("SELF_HEALING_REPLAY_MAX_FILES", "8")),
    "replay_max_bars": int(os.getenv("SELF_HEALING_REPLAY_MAX_BARS", "1500")),
    "min_trades": int(os.getenv("SELF_HEALING_MIN_TRADES", "100")),
    "min_profit_factor": float(os.getenv("SELF_HEALING_MIN_PROFIT_FACTOR", "1.05")),
    "min_avg_ret": float(os.getenv("SELF_HEALING_MIN_AVG_RET", "0.0")),
    "min_positive_folds": int(os.getenv("SELF_HEALING_MIN_POSITIVE_FOLDS", "2")),
    "adaptive_ttl_hours": int(os.getenv("SELF_HEALING_ADAPTIVE_TTL_HOURS", "24")),
}

# Phase 2b: a tsmom long is held to its momentum-flip exit, so the scalp early
# exits (TP/partial/trailing/BE/age/staleness/3%-hard-loss) are suppressed for
# it — but a WIDE disaster stop remains. The entry stop is ~8% (the signal's
# sl_pct); this hard-max-loss backstop sits just BEYOND it so the 8% stop / wick
# / exchange-SL fire first and this only catches a gap-through catastrophe.
TSMOM_HARD_MAX_LOSS_PCT = float(os.getenv("TSMOM_HARD_MAX_LOSS_PCT", "9.0"))

# "Loosen the triggers" knob (owner decision 2026-06-18). The long-only momentum
# lookback in DAYS. Validated default is 28d (medium-term TSMOM). A SHORTER window
# recognises a fresh uptrend sooner, so the bot trades a recovering tape instead of
# waiting for the whole 28d window to clear — at the cost of more whipsaw (the exact
# trade-off the 2026-06-15 validation flagged). Fully reversible: set back to 28.
# This moves only WHEN an uptrend is recognised; long-only and the disaster stop are
# unchanged (it still never buys a falling window and never shorts).
TSMOM_LOOKBACK_DAYS = int(os.getenv("TSMOM_LOOKBACK_DAYS", "28"))
if TSMOM_LOOKBACK_DAYS < 2:
    raise ValueError(f"TSMOM_LOOKBACK_DAYS must be >= 2, got {TSMOM_LOOKBACK_DAYS}")

# CLAUDE.md §2 (R2): hard portfolio-exposure circuit breaker. The constitution caps
# total open exposure at 12% of capital, but nothing enforced it (only a position
# COUNT limit existed). bot_engine._execute_open rejects a new entry that would push
# total open GROSS NOTIONAL over this % of equity. Set 0 to disable. Reversible knob.
# Note: gross notional includes leverage, so at futures_max_leverage=2 a single ~5%
# margin trade is ~10% notional — 12% allows ~1-2 leveraged positions by design.
MAX_PORTFOLIO_EXPOSURE_PCT = float(os.getenv(
    "MAX_PORTFOLIO_EXPOSURE_PCT", str(MODE_PROFILE.gross_exposure_pct * 100.0)
))
MAX_AGGREGATE_OPEN_RISK_PCT = float(os.getenv(
    "MAX_AGGREGATE_OPEN_RISK_PCT", str(MODE_PROFILE.aggregate_open_risk_pct)
))
STRESSED_EXIT_COST_FRAC = float(os.getenv("STRESSED_EXIT_COST_FRAC", "0.002"))

# Phase C fill-time freshness gate (core/feed_health): reject a market/no-price
# fill when the ticker carries a DEFINITELY-stale timestamp (older than this many
# seconds). A ticker with NO timestamp (mocked tests, thin venues) is ALLOWED —
# the gate is fail-open on missing data. Generous default so live fresh tickers
# never trip it; runtime behavior is unchanged. Set 0 to disable the gate.
FILL_FRESHNESS_MAX_AGE_SEC = float(os.getenv("FILL_FRESHNESS_MAX_AGE_SEC", "300"))

# Final venue-pinned pre-submission book gate. This is deliberately much
# tighter than the legacy ticker freshness threshold: a marketable entry is
# denied unless the selected venue supplies enough current depth for the exact
# quantized quantity that will be submitted.
EXECUTION_BOOK_MAX_AGE_SEC = float(os.getenv("EXECUTION_BOOK_MAX_AGE_SEC", "5"))
MAX_ENTRY_SLIPPAGE_BPS = float(os.getenv("MAX_ENTRY_SLIPPAGE_BPS", "30"))
EXECUTION_BOOK_DEPTH_LEVELS = int(os.getenv("EXECUTION_BOOK_DEPTH_LEVELS", "50"))
if EXECUTION_BOOK_MAX_AGE_SEC <= 0:
    raise ValueError("EXECUTION_BOOK_MAX_AGE_SEC must be greater than zero")
if MAX_ENTRY_SLIPPAGE_BPS < 0:
    raise ValueError("MAX_ENTRY_SLIPPAGE_BPS must not be negative")
if not 1 <= EXECUTION_BOOK_DEPTH_LEVELS <= 500:
    raise ValueError("EXECUTION_BOOK_DEPTH_LEVELS must be between 1 and 500")

if not SCALP_TIER_ENABLED:
    LEVERAGE_TIERS.pop("SCALP", None)
    # 2026-06-20 (owner "remove any blacklist and blocks", PAPER): disabling the
    # SCALP tier no longer re-introduces a symbol blacklist or blocked hours.
    # Previously this branch silently re-populated BLACKLIST_HARD
    # ({APT,SOL,XRP,ETH,DOGE,BTC}) and BLOCKED_HOURS_UTC ({0,9,19,21,23}) — a
    # latent trap that contradicted the unblock directive. BLACKLIST_HARD now
    # stays empty and all 24h stay open regardless of the SCALP-tier toggle.

# Side filter — shorts require BTC macro-bear confirmation
# 2026-04-12: Relaxed. BTC-bear gate blocked 90%+ of short signals
# even when the coin's own 4h+1h EMAs clearly pointed down. The scoring
# engine now requires per-coin 4h+1h EMA alignment — that IS the trend
# filter. Demanding BTC alignment on top was redundant.
SHORTS_REQUIRE_BTC_BEAR = False
BTC_TREND_TIMEFRAME = "4h"
BTC_TREND_EMA_PERIOD = 200

# Short-side filter (May 2026, evidence-based). Warehouse: 126 shorts net
# -$54 vs 210 longs net -$4. Filter blocks SELL when BTC is up-aligned on
# both 4h and 1h EMA20>EMA50. Idiosyncratic bearish news on the symbol
# overrides the block. Toggle off via env or by setting `enabled=False`.
#
# 2026-05-27 (454-trade hardening): shorts are 2.5x worse than longs.
# Raised thresholds: min_mcp_score=75, min_confidence=0.70.
SHORT_SIDE_FILTER = {
    # 2026-05-27 (UNBLOCK directive): if MCP Brain scored it, don't second-guess.
    "enabled": os.getenv("SHORT_SIDE_FILTER_ENABLED", "false").lower() == "true",
    "min_mcp_score": float(os.getenv("SHORT_MIN_MCP_SCORE", "75")),
    "min_confidence": float(os.getenv("SHORT_MIN_CONFIDENCE", "0.70")),
}

# Market-wide BTC volatility circuit-breaker for NEW entries (2026-06-04, owner
# directive: "wait during the violent one-line-down move, react when worth the
# risk"). Pauses NEW entries (any side) while BTC 1h ATR% spikes >= vol_spike_mult
# x its OWN trailing-median 1h ATR% (adaptive, not a fixed % tuned to this week),
# then auto-resumes after clear_minutes of calm. Direction-AGNOSTIC by design — no
# "short the dump" bias (that is market beta, not edge; see core/btc_vol_pause.py).
# Fail-OPEN on missing BTC data / warmup. NEW ENTRIES ONLY (never exits/SL).
# Lowers cost + variance + trade count; does NOT add alpha.
BTC_VOL_PAUSE = {
    "enabled": os.getenv("BTC_VOL_PAUSE_ENABLED", "true").lower() == "true",
    "timeframe": "1h",
    "vol_spike_mult": float(os.getenv("BTC_VOL_SPIKE_MULT", "2.0")),
    "hysteresis_mult": float(os.getenv("BTC_VOL_HYSTERESIS_MULT", "1.5")),
    "clear_minutes": float(os.getenv("BTC_VOL_CLEAR_MIN", "30")),
    # Ceiling on how long ONE spike may keep vetoing entries. Without it the
    # cooldown re-arms on every evaluation while ATR sits in [clear, spike)
    # and never expires -- measured 2026-08-23 holding entries 10.3h on a
    # 30-minute cooldown. Does NOT loosen either threshold: a fresh spike
    # still pauses instantly, and calm still resumes early.
    "max_pause_minutes": float(os.getenv("BTC_VOL_MAX_PAUSE_MIN", "240")),
    "min_samples": int(os.getenv("BTC_VOL_MIN_SAMPLES", "24")),  # ~1d warmup (hourly)
    "append_min_interval_sec": 3600,
    "buffer_max": 1000,
}

# 2026-05-27 (454-trade hardening): min hold time gate.
# Trades closed <15 min = 43% of total, 31% WR, -$41.25. Only profitable
# bucket is 15m-1h (64% WR, +$4.41). Guard in position monitor.
MIN_HOLD_CLOSE_MINUTES = int(os.getenv("MIN_HOLD_CLOSE_MINUTES", "15"))
MIN_HOLD_STRONG_CONF = float(os.getenv("MIN_HOLD_STRONG_CONF", "0.80"))
