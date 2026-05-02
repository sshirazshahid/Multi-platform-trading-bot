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
# MODEL GATE — calibrated LR+GBM ensemble blended with the MCP rule score
# Loads data/models/ensemble_{market}_latest.json on startup.
# When `enabled=False` or no latest pointer exists, the gate is bypassed
# and the rule-only path runs (existing behavior).
# `shadow_only=True` keeps p_win logged to the warehouse but does NOT block
# entries — used for soak windows where we want bit-for-bit baseline.
# ==============================================================
MODEL_GATE = {
    "enabled":           os.getenv("MODEL_GATE_ENABLED", "true").lower() == "true",
    # 2026-04-28: defaulted to TRUE per UNBLOCK_ALL directive — model
    # logged but didn't gate.
    # 2026-05-01 (stop-bleed plan): flipped to FALSE. The live ensemble
    # (AUC 0.76 / OOS WR 70.7% at p>=0.55) is now the entry authority.
    # Candidates with p_win_ensemble < threshold_futures (0.55) are
    # blocked at core/mcp_brain.py:~2509-2526. When no model bundle is
    # loaded (fresh install / artifact missing) the gate auto-bypasses
    # via the `mscore["model_version"] is None` check, so the bot still
    # falls through to the rule-only path. Operational consequence: with
    # the current model and current setups, the bot may open zero trades
    # for hours/days until either market regime shifts or the next weekly
    # retrain produces stronger predictions. Revert via env override:
    #   MODEL_GATE_SHADOW=true python main.py
    "shadow_only":       os.getenv("MODEL_GATE_SHADOW", "false").lower() == "true",
    "threshold_futures": float(os.getenv("MODEL_GATE_THRESHOLD_FUTURES", "0.55")),
    "threshold_spot":    float(os.getenv("MODEL_GATE_THRESHOLD_SPOT",    "0.58")),
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
    # 2026-04-27: tightened from 5% → 1.5% after 16h/9-loss bleed.
    # 2026-04-28 (L99): KEPT at 0.015. Daily-loss halt is the last
    # post-trade circuit breaker — at 99x leverage a single -1% move
    # is catastrophic; this halt limits damage to one bad day. Removal
    # would require explicit user authorization.
    # 2026-05-01: tightened 1.5% → 1.0% per capital-preservation pass.
    # At $791 balance: 1.0% = $7.91 daily loss limit. Triggers same-day
    # halt, recovers next UTC day. Goal: cap any one bad day's damage.
    "max_daily_loss_pct":   0.010,
    "default_stop_loss":    0.020,    # 2.0% fallback SL (ATR-based is primary)
    "default_take_profit":  0.060,    # 6.0% fallback TP (~3:1 R:R vs 2% SL)
    # 2026-04-27: leverage cut 3 → 2. Last 7d at 3x: 46 closed trades, 16 wins,
    # net −$3.19. Single APT outlier was −$3.28 (−9.5% margin = −3.17% price ×
    # 3x). At 2x the same price move is −6.34% margin = ~−$2.13 worst case;
    # cuts every loss magnitude by 33% while preserving the 2.5:1 R:R math.
    # 2026-04-28 (L99 ALL-IN): max leverage 2 → 99 per user directive.
    # Exchange-side caps will clamp (Binance ~75-125x by symbol; Bybit
    # similar). Liquidation risk: any 1% adverse move at 99x ≈ wipe the
    # margin. SL at 1.5% = -148% lev loss → exchange liquidates first.
    # Restore by reverting both to 2.
    # 2026-04-29: reverted L99 99→2 per user directive "I don't want 99
    # leverage". size_pct stays at 0.50 (per "go all-in" — full deployment).
    # At 2x × 0.50 size on $400 balance: $200 margin × 2 = $400 notional;
    # 1.5% SL = $6 loss = 1.5% of balance. Survivable; supports ~2-3
    # concurrent positions per exchange pocket.
    "futures_max_leverage": 2,
    "default_leverage":     2,
    "min_rr_ratio":         1.2,      # 1.2:1 — high-WR strategies don't need large R:R
    "trailing_stop":        True,
    # 2026-04-28 retune (Phase 11) — converge trailing on the empirical
    # mcp_take_profit distribution:
    #   trailing_stop:    n=25, mean +1.00% gross, +0.4-0.5% net (sub-cost-floor)
    #   mcp_take_profit:  n=13, mean +1.47% gross, +1.2-1.4% net  (clears costs)
    # The 47% performance gap is NOT Claude AI wisdom — it's the 0.5% net-PnL
    # filter at bot_engine.py:2535-2557. Raising activation 1.5%→2.0% delays
    # engagement until peaks are real; lock_fraction small-win tier 0.40→0.55
    # (in trailing_stop_manager._lock_fraction_default) tightens the lock so
    # exits land at +1.1% gross / +0.9% net at peak 2.0% — above the cost
    # floor and matching mcp_take_profit's empirical capture.
    "trailing_activation":  0.020,    # 2.0% — clear cost floor (was 1.5%)
    "trailing_distance":    0.010,    # 1.0% — wider trail past activation
    # 2026-04-28 (L99): KEPT at 0.12. Drawdown halt is the from-peak
    # circuit breaker; at 99x leverage it's the only thing standing
    # between a few bad trades and a wiped account. Removal would
    # require explicit user authorization.
    # 2026-05-01: 12% → 8% per capital-preservation pass. At $791 balance,
    # 8% = $63 max drawdown before halt. Combined with 1% daily loss limit,
    # bot has at most ~8 bad days before forced halt. Recovery requires
    # operator-cleared peak (or 4h auto-cooldown for consec_global halts).
    "max_drawdown_pct":     0.08,
    "position_sizing_mode": "tiered", # leverage tier drives sizing; kelly is a sanity check
    # 2026-04-29 (Phase 14) — age cutoffs tightened from 6h/4h/3h after
    # warehouse retrenchment showed the bot's edge expires past 60min:
    #   <10min:    -$22.26 / 0% WR  (the L99 disaster, post-revert mostly clean)
    #   10-30min:  +$1.28  / 38% WR / R:R 3:1  ← FAST WINNERS, the edge zone
    #   30-60min:  +$0.31  / 55% WR / R:R 1:1  ← slow winners, breakeven-ish
    #   1-2h:      -$1.89  / 40% WR / avg_win=avg_loss (no edge)
    #   >2h:       -$4.34  / 39% WR over 56 trades (slow bleed cluster)
    # The 1-2h and >2h buckets together account for -$6.23 / 94 trades.
    # Cutting losers earlier doesn't hurt winners (winners exit via
    # mcp_take_profit / trailing well before AGE_LOSS fires) but does
    # save the slow-bleed losers from sliding deeper.
    "max_position_age_hours": 4,      # was 6 — hard expiry tightened
    "max_stale_hours":       2.0,     # was 4.0 — stale (flat) exit at 2h
    "max_loss_age_hours":    1.5,     # was 3.0 — losing position cut at 1.5h
    "max_loss_age_pct":      0.3,     # was 0.5 — looser threshold complements earlier cutoff
    # 2026-04-27: hard cap on trade count per UTC day. The bot did 52 trades
    # on 2026-04-27 — overtrading on negative-EV strategies amplifies the
    # bleed regardless of per-trade SL discipline.
    # 2026-04-28 (UNBLOCK_ALL): user directive "Dont block any trades" —
    # raised from 20 to 200 so the daily cap effectively never binds.
    # Restore by reverting to 20 if you want the data-driven cap back.
    "max_trades_per_day":   200,
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
# 2026-04-27: every tier's leverage dropped 3 → 2 alongside RISK[
# "futures_max_leverage"]. The size_pct is left untouched: at 0.15 × 2 ×
# 0.015 = 0.45% balance risk per STANDARD trade (was 0.675% at 3x), still
# above the cost floor for any pocket ≥ $165 and well under the 1.0%
# MAX_LOSS_PER_TRADE_PCT cap below.
LEVERAGE_TIERS = {
    "STANDARD": {
        # 2026-04-29: reverted 99→2 per user directive. size_pct retained at
        # 0.50 for capital deployment ("go all-in with available USDT").
        "leverage":               2,
        "size_pct":               0.50,
        "sl_pct":                 0.015,
        "tp_pct":                 0.0375,   # 2.5:1 R:R
        # 2026-04-28 (UNBLOCK_ALL/A): 0.65 -> 0.0. STANDARD now accepts
        # any candidate the score gate passed. Restore by reverting to 0.65.
        # 2026-05-01 (stop-bleed plan): raised 0.0 -> 0.55 — recent
        # mcp_decisions.jsonl shows OPENs at confidence 0.44/0.51/0.52
        # with `algo_confidence: null` (Claude advisor timed out → bot
        # opening blind). 0.55 still permits the warmup band but blocks
        # the lowest-quality fallback signals. Functionally a no-op for
        # the algorithmic path (confidence = 0.66 + bonus×0.08 starts
        # ≥ 0.66) — gates Claude-AI-proposed entries which can return
        # arbitrary confidence values.
        "min_confidence":         0.55,
        "requires_whitelist":     False,
        # 2026-04-28 (UNBLOCK_ALL/A): hour gate already empty — flag retained
        # for higher tiers' use; doesn't matter for STANDARD when allowed=24h.
        "requires_allowed_hour":  True,
        "requires_peak_hour":     False,
        "requires_btc_aligned":   False,
    },
    "STRONG": {
        "leverage":               2,
        "size_pct":               0.50,
        "sl_pct":                 0.015,
        "tp_pct":                 0.0375,
        "min_confidence":         0.72,
        "requires_whitelist":     True,
        "requires_allowed_hour":  True,
        "requires_peak_hour":     False,
        "requires_btc_aligned":   True,
    },
    "CONVICTION": {
        "leverage":               2,
        "size_pct":               0.50,
        "sl_pct":                 0.015,
        "tp_pct":                 0.0375,
        "min_confidence":         0.80,
        "requires_whitelist":     True,
        "requires_allowed_hour":  True,
        "requires_peak_hour":     True,
        "requires_btc_aligned":   True,
    },
    "AGGRESSIVE": {
        "leverage":               2,
        "size_pct":               0.50,
        "sl_pct":                 0.015,
        "tp_pct":                 0.0375,
        "min_confidence":         0.85,
        "requires_whitelist":     True,
        "requires_allowed_hour":  True,
        "requires_peak_hour":     True,
        "requires_btc_aligned":   True,
    },
}

# 2026-04-29: raised in lockstep with the L99→2 + size_pct=0.50 sizing
# regime. At 50% size × 2x × 1.5% SL = 1.5% balance per normal SL hit. The
# old 1.0% / $4 thresholds were calibrated for 0.225%-0.45% balance/trade
# (15% size × 2-3x × 1.5%) and would fire the outlier flag on EVERY normal
# SL hit, halting the bot — which combined with a separate auto-resume
# bug in risk_manager.py would deadlock the bot indefinitely. New
# thresholds: 2.5× the new typical SL loss, so genuine outliers (slippage,
# SL placement bug, exchange glitch) still fire but normal trading doesn't.
MAX_LOSS_PER_TRADE_PCT = 0.025

# 2.5× normal $6 SL loss. At $400 balance × 50% × 2x × 1.5% = $6 expected;
# $15 catches anything 2.5x larger (e.g. 4% gap-down past a 1.5% SL).
MAX_LOSS_PER_TRADE_USD = 15.0

# Consecutive-loss throttle — dynamic leverage downgrade + pause
CONSEC_LOSS_DOWNGRADE_COUNT = 2   # 2 losses in a row → drop tier cap by 1
CONSEC_LOSS_DOWNGRADE_HOURS = 4   # downgrade lasts 4h
CONSEC_LOSS_PAUSE_COUNT     = 3   # 3 losses in a row → full pause
CONSEC_LOSS_PAUSE_HOURS     = 0.5 # pause lasts 30min (was 2h — too long, missed recoveries)

# Volatility-adaptive leverage cap — high-ATR symbols clamp to STANDARD
HIGH_ATR_PCT_THRESHOLD = 0.025    # ATR% > 2.5% → max leverage = STANDARD (2x)

# 2026-04-27: hard kill-switch on shorts. Last 7d futures: 9 sells averaging
# −$0.16/trade vs 37 buys averaging −$0.05 (3.4× worse), with the worst
# single-trade loss in the dataset coming from the short side. The
# auto_mutator's existing shorts_blocked_until window only fires on
# "counter-trend" wording in post-mortem mistakes, which is too narrow to
# catch the broader negative-EV pattern. AutoMutator.shorts_blocked() honors
# this flag; bot_engine._execute_open's existing gate at the side=='sell'
# check then refuses entries with no further wiring needed.
# 2026-04-28 (UNBLOCK_ALL): user directive "Dont block any trades" —
# re-enabled shorts. Restore the 7-day-evidence kill-switch by setting True
# (last evidence: 9 sells avg -$0.16/trade vs 37 buys avg -$0.05).
# 2026-05-01 (stop-bleed plan): re-enabled. Stronger 262-trade sample
# from data/positions.json shows shorts at 39.4% WR / -$51.48 net vs
# longs at 46.8% / -$3.48. The bleed is concentrated entirely on the
# short side. Reverts the UNBLOCK_ALL directive on shorts only — longs
# stay open. Restore by setting False.
SHORTS_DISABLED = True

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
    # 2026-04-28 (Phase 12.3): refreshed using claude_portfolio-only
    # warehouse data (NOT combined which is dominated by the deprecated
    # MultiTF/Supertrend trades). Comments reflect realised PnL inside
    # the strategy_family that's actually firing today.
    #
    # Tier hints (sized up via leverage-tier selector):
    "ATOM/USDT:USDT",   # ⭐ n=14, +$2.06, 43% WR, $0.147/trade
    "ARB/USDT:USDT",    # ⭐ n=18, +$1.20, 44% WR, $0.067/trade
    "DOGE/USDT:USDT",   # ⭐ n=10, +$4.83, 50% WR, $0.483/trade — TOP CONTRIBUTOR (added 2026-05-01)
    "ETH/USDT:USDT",    #   n=8,  +$0.45, 75% WR — high-WR, small absolute mean
    "MANA/USDT:USDT",   #   n=3,  +$0.37, 33% WR — thin sample, tentative
    "BTC/USDT:USDT",    #   macro anchor, always tradeable
    "AVAX/USDT:USDT",   #   n=4,  near-zero — neutral, kept as tier hint
    # Tentative thin-sample symbols (n<3 in claude_portfolio):
    "LUMIA/USDT:USDT",  # historical positive
    "ORDI/USDT:USDT",
    "DOT/USDT:USDT",    # n=6 in claude_portfolio, marginal -$0.038/trade
    "FET/USDT:USDT",
    "BCH/USDT:USDT",
    "GRASS/USDT:USDT",
    "QTUM/USDT:USDT",
    "ACT/USDT:USDT",
    "IOTA/USDT:USDT",
    "VET/USDT:USDT",
    # REMOVED 2026-04-28 — moved to BLACKLIST_HARD per claude_portfolio data:
    #   ALGO/USDT:USDT  n=7  -$0.93  29% WR  (was ⭐ on stale combined data)
    #   LINK/USDT:USDT  n=8  -$0.82  38% WR
    #   BNB/USDT:USDT   n=12 -$0.49  50% WR  — kept allowed but not whitelisted
}

# 2026-04-28 (Phase 12.3): symbols proven net-positive in claude_portfolio.
# Used by leverage-tier selector to auto-promote to STRONG/CONVICTION on
# matching setups. Sample-size guard: only promote when claude_portfolio
# has >= 8 trades AND mean PnL > $0.05/trade.
# 2026-05-01: DOGE added — outperforms both prior STAR symbols on every
# axis (claude_portfolio only):
#   ATOM:  n=14  sum=+$2.06   avg=+$0.147  WR=43%
#   ARB:   n=18  sum=+$1.20   avg=+$0.067  WR=44%
#   DOGE:  n=10  sum=+$4.83   avg=+$0.483  WR=50%   ← strongest cell
# Phase 12 missed DOGE because the analysis filter cut it; today's data
# (n>=8, mean>$0.05) puts it well above the sample-size guard.
STAR_SYMBOLS = {
    "ATOM/USDT:USDT",   # n=14, +$2.06 sum, +$0.147/trade, 43% WR
    "ARB/USDT:USDT",    # n=18, +$1.20 sum, +$0.067/trade, 44% WR
    "DOGE/USDT:USDT",   # n=10, +$4.83 sum, +$0.483/trade, 50% WR
}

# 2026-05-01 — SPOT-PROTECT-V1 (peak-drawdown spot strategy).
# Ref: docs/superpowers/specs/2026-05-01-spot-protect-v1-design.md
#
# Replaces SpotPortfolioManager.evaluate_holding's EMA/RSI logic with
# peak-drawdown stop rules. For every spot holding ≥ min_position_usd:
#   drawdown >= drawdown_full_pct  → SELL (full exit)
#   drawdown >= drawdown_half_pct  → SCALE_OUT (half exit, peak resets)
#   else                           → HOLD
#
# Rationale: at $1052 across 32 holdings the existing TA logic was
# fee-eaten whipsaw. Peak-based rules are mechanical, regime-agnostic,
# and asymmetric (cap downside, preserve upside).
#
# Rollback: enabled=False → existing TA path runs unchanged.
SPOT_STRATEGY = {
    "enabled":              True,
    "dust_cutoff_usd":      25.0,   # Component A — sell positions worth less
    "min_position_usd":     50.0,   # Component B — below this, no rules apply
    "drawdown_half_pct":    0.25,   # SCALE_OUT trigger (half-exit, peak resets)
    "drawdown_full_pct":    0.40,   # SELL trigger (full exit)
}

# 2026-05-01 — Expectancy Filter (Tier 1.2 from predictive-strategy stack).
# Per-trade abstain rule: if the candidate's symbol has insufficient recent
# realised expectancy (mean PnL after fees < min_expected_dollar), skip.
#
# Cell-filter (above) is binary — in or out. This adds magnitude: a symbol
# whose recent expectancy has flipped below the floor gets blocked even
# though it's in the allowed set. Self-correcting over time.
#
# Floor calibration (2026-05-01 ultrathink wire-check):
#   30d avg fee per trade: $0.027
#   30d STAR avg fee:     $0.029-$0.048
#   Original $0.30 floor (≈ 2× fee at $75 notional) was 17× too high
#   for current $4.40 notional — would block ALL 3 STAR symbols
#   (ATOM $0.147, ARB $0.061, DOGE $0.285) despite being net-positive.
# Fix: $0.05 = ~2× actual round-trip fee, just above breakeven.
#
# STAR exemption: STAR symbols pass at mean >= $0.0 (just must not be
# net-negative). The cell-filter has already validated them as proven-
# edge cells; the expectancy filter's role for them is only to catch
# regime-flip into outright loss, not to demand strong positive edge.
#
# `min_sample_size`: when fewer than N closed trades exist for the symbol
# in the lookback window, return None (allow through). We don't block on
# noise — only on evidence-backed negative cells.
EXPECTANCY_FILTER = {
    "enabled":              True,
    "min_expected_dollar":  0.05,   # non-STAR floor (~2× actual round-trip fee)
    "min_expected_star":    0.00,   # STAR floor: just must not be net-negative
    "lookback_days":        30,
    "min_sample_size":      5,
}

# 2026-05-01 — Entry-Staleness Exit (Tier 1.1 from predictive-strategy stack).
# On every position monitor cycle, re-check the entry's directional
# hypothesis. If the 4h EMA20/50 has flipped against the position with
# margin >= invalidation_gap_pct, close the position at market.
#
# Why 4h EMA? It's the SIDE-determining signal in mcp_brain._score_coin
# (line 1976: `side = "buy" if ema20_above_50_4h else "sell"`). If that
# flips, the entry rationale is structurally invalid.
#
# Why a margin? Avoids whipsaw on tight crosses that flip back. EMAs
# touching the cross-line by 0.05% isn't a regime change.
#
# `min_hold_minutes`: don't fire in the first 30 minutes — entries that
# fired right before a brief 4h cross deserve a chance to resolve.
ENTRY_STALENESS_EXIT = {
    "enabled":               True,
    "invalidation_gap_pct":  0.15,   # min EMA gap (in WRONG direction) to fire
    "min_hold_minutes":      30,     # grace period after entry
}

# 2026-05-01 — Cell-Filter Entry Gate
# Ref: docs/superpowers/specs/2026-05-01-cell-filter-entry-gate-design.md
#
# Hard entry gate: only fire on proven-edge cells.
#   STAR symbols (ATOM/ARB/DOGE): always allowed (star_overrides=True).
#   Non-STAR: allowed only when score_band_min <= mcp_score <= score_band_max.
# Other cells (score < band_min, score > band_max on non-STAR) are blocked.
#
# Anchored in claude_portfolio realised data (2026-05-01):
#   score 65-74:   n=18  +$2.41   +$0.134/trade  44% WR
#   score 75-84:   n=20  +$4.86   +$0.243/trade  40% WR  <- mid-band
#   score 85-100:  n=16  -$2.98   -$0.186/trade  31% WR  <- anti-EV
#
# The score-85 tier-cap (commit 86acef3) stays as defense-in-depth on
# STAR symbols at high score — they're allowed but tier-capped to STANDARD.
#
# Rollback: set enabled=False. No data migration needed.
# 2026-05-01 evening (UNBLOCK_ALL restoration): cell-filter disabled
# entirely. User reasoning: if MCP score + model gate + meta-filter
# already determined "profitable," layering symbol-universe blocking
# on top contradicts our own engine. The historical loss-tape that
# justified the cell-filter was generated under OLDER, looser gates
# (no model gate, no expectancy filter, no tighter caps) — so it
# doesn't predict losses under CURRENT config.
#
# Engine thinking layers (the "block if not profitable" sources):
#   - MCP scoring: 4 required + 6 bonus, score >= 65 to fire
#   - Model gate: LR+GBM ensemble p_win >= 0.55
#   - Meta-filter: spread/vol/depth percentile floors
#   - Expectancy filter: per-symbol mean PnL >= floor (self-correcting)
#
# Capital safety nets (the "stop if losing" rails):
#   - Daily loss limit: 1.0% of balance
#   - Drawdown halt: 8% from peak
#   - Per-position SL (exchange-side or soft monitor)
#   - Score-85 tier-cap (downsize, not block)
#
# Restore tightening by setting enabled=True + star_only=False (BAND
# tier active, score 70-84 only) or star_only=True (STAR-only mode).
CELL_FILTER = {
    "enabled":         False,   # was True with star_only=True
    "star_only":       True,    # ignored when enabled=False
    "score_band_min":  70.0,
    "score_band_max":  84.0,
    "star_overrides":  True,
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
#
# 2026-04-27 (Phase 10.2): expanded with three more symbols from the 30-day
# combined-warehouse attribution. THIS WAS A MISTAKE — see Phase 12.2 below.
#
# 2026-04-28 (Phase 12.2): RECTIFIED. The Phase 10.2 BLACKLIST was built on
# COMBINED-warehouse attribution that was dominated by the deprecated MultiTF
# / Supertrend strategies (since killed). When restricted to the actually-
# active claude_portfolio strategy_family, the same symbols look very
# different:
#
#                       combined 30d        claude_portfolio (n>=3, all-time)
#   ETH/USDT:USDT       -$3.45  67% WR      +$0.45  75% WR (n=8)   ← profitable!
#   AVAX/USDT:USDT      -$1.92  43% WR      -$0.02  50% WR (n=4)   ← near-zero
#   ADA/USDT:USDT       -$2.12  17% WR      no claude_portfolio data
#
# Phase 10.2 was penalising claude_portfolio for sins of strategies that
# don't fire anymore. ETH / AVAX / ADA are unblocked.
#
# Conversely, the claude_portfolio-only data exposes symbols that ARE
# net-negative in the active path but were not on the combined blacklist:
#   ALGO/USDT:USDT  (n=7)   -$0.93   29% WR
#   LINK/USDT:USDT  (n=8)   -$0.82   38% WR
#   AAVE/USDT:USDT  (n=3)   -$0.44   0%  WR  (small sample but 100% loss rate)
#   SOL/USDT:USDT   (n=4)   -$0.32   0%  WR  (already blocked; remains)
#
# Adding these per claude_portfolio-only evidence. ALGO is particularly
# important — it was on the WHITELIST as ⭐ (based on stale combined data
# from deprecated strategies) which would have sized it UP.
#
# Net BLACKLIST_HARD update: removed {ETH, AVAX, ADA}, added {ALGO, LINK,
# AAVE}, kept {SOL, XRP}. Re-evaluate after 50+ post-restart trades using
# `python scripts/diagnostic_report.py --since 2026-04-28`.
# 2026-04-28 (UNBLOCK_ALL): user directive "Dont block any trades" — emptied.
# Prior evidence-driven contents (recoverable via git):
#   SOL/USDT:USDT  n=12-13, 0-15% WR (Phase 10.2)
#   XRP/USDT:USDT  n=12-13, 0-15% WR (Phase 10.2)
#   ALGO/USDT:USDT n=7,  29% WR (Phase 12.2)
#   LINK/USDT:USDT n=8,  38% WR (Phase 12.2)
#   AAVE/USDT:USDT n=3,  0%  WR (Phase 12.2)
# AutoMutator's runtime blacklist (consec-loss based) still operates on top.
BLACKLIST_HARD: set = set()

# Hour gating (UTC)
#
# 2026-04-27 (Phase 10.3): tightened from set(range(24)) per COMBINED 30-day
# warehouse attribution.
# 2026-04-28 (Phase 12.2): Phase 10.3 was wrong. The combined-data hourly
# losses were dominated by deprecated MultiTF/Supertrend trades. When
# restricted to the active claude_portfolio strategy_family, three of
# Phase 10.3's BLOCKED hours were among the strongest WINNERS:
#
#   hour  combined-data    claude_portfolio (n>=3)
#   H00   -$20.04 (23%)    +$0.98  50% WR (n=10)  ← was BLOCKED
#   H17   -$26.08 (27%)    +$0.89  33% WR (n=6)   ← was BLOCKED
#   H19   -$7.83  (38%)    +$1.04  40% WR (n=5)   ← was BLOCKED
#
# Re-fitting on claude_portfolio-only data:
#
# STRONG WINNERS (sum > +$0.50 in claude_portfolio):
#   H03 +$1.02 (80%, n=5)  H19 +$1.04 (40%, n=5)
#   H00 +$0.98 (50%, n=10) H17 +$0.89 (33%, n=6)
#   H02 +$0.70 (33%, n=3)  H15 +$0.67 (40%, n=5)
#   H14 +$0.57 (33%, n=6)  H21 +$0.50 (67%, n=3)
#
# CATASTROPHIC LOSERS (sum << 0):
#   H22 -$3.49 (0%, n=5)   H05 -$1.31 (0%, n=6)
#   H18 -$0.54 (40%)       H20 -$0.53 (20%)
#   H01 -$0.49 (0%)        H12 -$0.45 (50%)
#
# THIN POSITIVE / MARGINAL (kept allowed as upside):
#   H07 (-$0.17, 67% WR)   H08 (-$0.26, 50% WR)
#   H16 (-$0.32, 60% WR)   H23 (-$0.11, 50% WR)
#
# THIN SAMPLE (n<3 in claude_portfolio — no evidence either way, default
# allowed so signal can develop): H06, H09, H10, H13.
#
# ALLOWED ∪ BLOCKED == set(range(24)) and ALLOWED ∩ BLOCKED == ∅
# (locked by tests/test_hour_gates.py).
#
# 2026-04-28 (UNBLOCK_ALL): user directive "Dont block any trades" — opened
# all 24 hours. PEAK_HOURS_UTC retained as leverage-tier hint (CONVICTION
# routing on the strongest hours); WARMUP_HOURS_UTC retained as half-size
# hint on thin-sample hours. Neither REJECTS entries — they only modulate
# sizing on entries that the (now-empty) gate accepts.
# Prior data-driven values (recoverable via git):
#   ALLOWED = {0,2,3,6,7,8,9,10,13,14,15,16,17,19,21,23}
#   BLOCKED = {1,4,5,11,12,18,20,22}
ALLOWED_HOURS_UTC = set(range(24))
PEAK_HOURS_UTC    = {0, 3, 17, 19}
WARMUP_HOURS_UTC  = {6, 7, 8, 9, 10, 13, 16, 23}
BLOCKED_HOURS_UTC = set()

# Side filter — shorts require BTC macro-bear confirmation
# 2026-04-12: Relaxed. BTC-bear gate blocked 90%+ of short signals
# even when the coin's own 4h+1h EMAs clearly pointed down. The scoring
# engine now requires per-coin 4h+1h EMA alignment — that IS the trend
# filter. Demanding BTC alignment on top was redundant.
SHORTS_REQUIRE_BTC_BEAR = False
BTC_TREND_TIMEFRAME     = "4h"
BTC_TREND_EMA_PERIOD    = 200

# Short-side filter (May 2026, evidence-based). Warehouse: 126 shorts net
# -$54 vs 210 longs net -$4. Filter blocks SELL when BTC is up-aligned on
# both 4h and 1h EMA20>EMA50. Idiosyncratic bearish news on the symbol
# overrides the block. Toggle off via env or by setting `enabled=False`.
SHORT_SIDE_FILTER = {
    "enabled": os.getenv("SHORT_SIDE_FILTER_ENABLED", "true").lower() == "true",
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
# SPOT-PROTECT-V2 (May 2026): model-driven early SCALE_OUT.
# When the promoted spot ensemble outputs p_win_ensemble < p_win_floor
# AND the holding is already past drawdown_pct from peak, take half off.
# Complements the deterministic SPOT-PROTECT-V1 25%/40% peak-DD rules
# with model-aware risk reduction. No-op when no spot model is promoted.
SPOT_PROTECT_V2 = {
    "enabled":      True,
    "drawdown_pct": 0.15,
    "p_win_floor":  0.40,
}

SPOT_PORTFOLIO = {
    "enabled":                  True,
    # 2026-05-01 (under-supervision readiness pass): flip to False so
    # SPOT-PROTECT-V1 (peak-DD half/full exits at -25%/-40%) actually
    # places SELL orders. Was True since 2026-04-14 learning-first pivot.
    # User explicitly authorized autonomous defensive sells. The
    # threshold is deep (-25% from peak) so triggers are rare; bot
    # only sells what's already deeply drawn down — not hair-trigger.
    # Hedge_via_futures STAYS off (no perp overlay on spot DD).
    # Sell_on_structure_break STAYS off (we only act on peak-DD).
    "recommendation_only":      False,
    "scan_interval_min":        30,
    "cost_basis_method":        "average",
    "scale_out_threshold_pct":  0.20,
    "sell_on_structure_break":  False,   # disabled — only peak-DD triggers exits
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
