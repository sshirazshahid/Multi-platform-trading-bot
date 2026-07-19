"""
config.py — Central configuration. All settings in one place.
"""

import os

from dotenv import load_dotenv

from core.entry_policy import (
    AGGRESSIVE_RESEARCH_PAPER_PROFILE,
    STANDARD_PAPER_PROFILE,
    mode_profile_for,
    normalize_paper_profile,
    parse_allowlist,
)

load_dotenv()

# ==============================================================
# EXCHANGE CREDENTIALS
# ==============================================================
BINANCE_API_KEY = os.getenv("BINANCE_API_KEY", "")
BINANCE_SECRET_KEY = os.getenv("BINANCE_SECRET_KEY", "")
BINANCE_TESTNET = os.getenv("BINANCE_TESTNET", "false").lower() == "true"


BYBIT_API_KEY = os.getenv("BYBIT_API_KEY", "")
BYBIT_SECRET_KEY = os.getenv("BYBIT_SECRET_KEY", "")

BITGET_API_KEY = os.getenv("BITGET_API_KEY", "")
BITGET_SECRET_KEY = os.getenv("BITGET_SECRET_KEY", "")
BITGET_PASSPHRASE = os.getenv("BITGET_PASSPHRASE", "")

# ==============================================================
# NOTIFICATIONS
# ==============================================================
GMAIL_SENDER = os.getenv("GMAIL_SENDER", "")
GMAIL_APP_PASSWORD = os.getenv("GMAIL_APP_PASSWORD", "")
GMAIL_RECIPIENT = os.getenv("GMAIL_RECIPIENT", "")
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
    raise ValueError(f"OPERATING_MODE must be one of {_VALID_MODES}, got {OPERATING_MODE!r}")

PAPER_TRADING_PROFILE = normalize_paper_profile(
    os.getenv("PAPER_TRADING_PROFILE", STANDARD_PAPER_PROFILE)
)
if OPERATING_MODE != "PAPER" and PAPER_TRADING_PROFILE != STANDARD_PAPER_PROFILE:
    raise ValueError(
        f"PAPER_TRADING_PROFILE={PAPER_TRADING_PROFILE} requires OPERATING_MODE=PAPER"
    )
PAPER_PROFILE_STARTED_AT = os.getenv("PAPER_PROFILE_STARTED_AT", "").strip()
_AGGRESSIVE_PAPER_RESEARCH = (
    OPERATING_MODE == "PAPER"
    and PAPER_TRADING_PROFILE == AGGRESSIVE_RESEARCH_PAPER_PROFILE
)

# Legacy DRY_RUN is now derived from the mode. Any existing code path that
# branches on DRY_RUN gets paper execution unless we are explicitly CONTROLLED_LIVE.
DRY_RUN = OPERATING_MODE != "CONTROLLED_LIVE"

# Extra latch for live mode — env var must also be flipped explicitly.
CONTROLLED_LIVE_ENABLED = os.getenv("CONTROLLED_LIVE_ENABLED", "false").lower() == "true"

# New-exposure authorization is independent from process mode. PAPER can run
# feeds, shadow decisions, reconciliation, and exits while entries stay denied.
_VALID_ENTRY_POLICIES = {
    "PROTECT_ONLY", "SHADOW_ONLY", "APPROVED_PAPER", "CONTROLLED_LIVE",
}
ENTRY_POLICY = os.getenv("ENTRY_POLICY", "SHADOW_ONLY").strip().upper()
if ENTRY_POLICY not in _VALID_ENTRY_POLICIES:
    raise ValueError(
        f"ENTRY_POLICY must be one of {_VALID_ENTRY_POLICIES}, got {ENTRY_POLICY!r}"
    )
APPROVED_PAPER_STRATEGIES = parse_allowlist(os.getenv("APPROVED_PAPER_STRATEGIES", ""))
APPROVED_LIVE_STRATEGIES = parse_allowlist(os.getenv("APPROVED_LIVE_STRATEGIES", ""))
LIVE_ENTRY_APPROVAL_PATH = os.getenv(
    "LIVE_ENTRY_APPROVAL_PATH", "data/live_entry_approval.json"
)
MODE_PROFILE = mode_profile_for(OPERATING_MODE, PAPER_TRADING_PROFILE)

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
    "spot_taker": 0.001,
    "spot_maker": 0.001,
    "futures_taker": 0.0005,
    "futures_maker": 0.0002,
    # Binance explicit keys (arb engine looks up "{exchange}_spot_taker")
    "binance_spot_taker": 0.001,
    "binance_futures_taker": 0.0005,
    # Bybit
    "bybit_spot_taker": 0.001,
    "bybit_spot_maker": 0.001,
    "bybit_futures_taker": 0.0006,
    "bybit_futures_maker": 0.0001,
    # Bitget
    "bitget_spot_taker": 0.001,
    "bitget_spot_maker": 0.001,
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
    "enabled": True,  # Master switch for the slippage model.
    "pct_open": 0.0005,  # 5 bps on open — cross spread + walk book
    "pct_close": 0.0005,  # 5 bps on close
    "pct_stop_loss": 0.0010,  # 10 bps on stop-out — SL fills worse in fast moves
    "wick_sl_tp": True,  # Use last 1m candle high/low for SL/TP trigger
    "funding": True,  # Charge funding every 8h on paper futures
    "prefer_book": True,  # Prefer ticker bid/ask over ticker last when available
}

# ==============================================================
# TRADING MODE
# ==============================================================
TRADING_MODE = os.getenv("TRADING_MODE", "usdt_only")
PORTFOLIO_MIN_VALUE_USD = float(os.getenv("PORTFOLIO_MIN_VALUE_USD", "2.0"))
PORTFOLIO_RESCAN_MINUTES = int(os.getenv("PORTFOLIO_RESCAN_MINUTES", "60"))

# ==============================================================
# CLAUDE PORTFOLIO — Claude Code is the SOLE decision authority
# ==============================================================
CLAUDE_PORTFOLIO = {
    "enabled": True,
    "scan_interval_min": 1,  # 1 min — was 5 (2026-05-20). User directive:
    # "don't miss a single second to miss a profitable
    # trade". Pairs with parallel _fetch_exchange_indicators
    # so 60s cycle stays under wall-time budget. Engine sees
    # 60 chances/hour vs prior 12. ENTRY_COOLDOWN dropped
    # to 50s in mcp_brain.py to track.
    "position_monitor_sec": 30,  # Position monitor every 30s (consumed by bot_engine)
    "max_actions_per_cycle": 12,  # PAPER aggressive (2026-05-31): was 4 — allow opens across all 3 venues per cycle. Revert to 4 before live.
    "model": "sonnet",  # Claude model for portfolio analysis
    "max_prompt_chars": 6000,  # Cap prompt size
    "max_per_exchange": 20,  # PAPER 2026-05-30: was 2 — allow ~10-20 positions/exchange for data-gathering. Revert to 2 before live.
    "news_interval_min": 30,  # News scan interval in minutes (bot_engine)
    "learn_interval_min": 60,  # Learning engine interval in minutes (bot_engine)
}

# ==============================================================
# PER-PAIR SL/TP OVERRIDES (Phase 15, 2026-05-03)
# ==============================================================
# Empirically-calibrated SL/TP per symbol from 30-day realized warehouse
# data. Overrides the default ATR×1.5 SL + 2:1 R:R for symbols where the
# realized R:R distribution clearly favors a different ratio.
#
# Methodology: per-symbol futures stats (n>=8 trades, 30d):
#   ARB:  WR 44%, R:R 2.23 → push TP to 3.5x SL (proven follow-through)
#   ORDI: WR 50%, R:R 3.67 → push TP to 4.5x SL (fastest pair, 8min holds)
#   ATOM: WR 42%, R:R 1.76 → tighten TP to 2.5x SL to fit Phase 15 (75min)
#   ETH:  WR 75%, R:R 1.69 → tighten TP to 1.5x SL to capture in 75min
#   DOT:  WR 29%, R:R 2.51 → keep wider R:R, low WR
#   DOGE: WR 62%, R:R 0.82 → tighten TP to fix R:R asymmetry
#
# Floor: SL minimum 1.5% per RISK clamp (zone-tightening invariant).
# Symbols not in this map fall through to default ATR-based + STAR
# extender + S/D zone tightening.
#
# To disable per-pair calibration entirely, set PAIR_OVERRIDES = {}.
PAIR_OVERRIDES = {
    # Two schemas supported (per Phase 15.3, 2026-05-03):
    #   FLAT  : {"sl_pct": X, "tp_pct": Y}          — applies symmetrically
    #   SIDED : {"long":  {"sl_pct": X, "tp_pct": Y},
    #            "short": {"sl_pct": X, "tp_pct": Y}}
    # Memory feedback_short_side_filter / 2026-04-30 attribution: 126 shorts
    # net -$54 vs 210 longs net -$4 — shorts harder to capture at this fee
    # tier. Per-side schema lets us tighten short TPs without touching longs.
    #
    # Proven winners — push R:R higher
    "ARB/USDT:USDT": {"sl_pct": 1.5, "tp_pct": 3.5},  # R:R 2.33, ARB is STAR
    "ORDI/USDT:USDT": {"sl_pct": 1.5, "tp_pct": 4.5},  # R:R 3.00, fastest pair
    "ATOM/USDT:USDT": {"sl_pct": 1.5, "tp_pct": 2.5},  # R:R 1.67, ATOM is STAR
    "DOT/USDT:USDT": {"sl_pct": 1.5, "tp_pct": 3.0},  # R:R 2.00
    # High-WR pairs — tighten TP to reach within Phase 15 horizon
    "ETH/USDT:USDT": {"sl_pct": 1.5, "tp_pct": 2.2},  # 75% WR, fast capture
    "FET/USDT:USDT": {"sl_pct": 1.5, "tp_pct": 2.2},  # 57% WR
    "ALGO/USDT:USDT": {"sl_pct": 1.5, "tp_pct": 2.0},  # 56% WR
    # Asymmetric R:R + per-side tuning: shorts tighter than longs
    "BNB/USDT:USDT": {
        "long": {"sl_pct": 1.5, "tp_pct": 2.0},
        "short": {"sl_pct": 1.5, "tp_pct": 1.6},  # tighter R:R for the harder side
    },
    "DOGE/USDT:USDT": {
        "long": {"sl_pct": 1.5, "tp_pct": 2.0},
        "short": {"sl_pct": 1.5, "tp_pct": 1.6},
    },
    # Note: SOL/XRP/AAVE/AVAX/LINK left to fall through to ATR defaults.
}

# ==============================================================
# MAKER-ONLY EXECUTION (Phase 15, 2026-05-03)
# ==============================================================
# Bybit futures maker fee = 0.01% (vs 0.06% taker). Round-trip drops from
# 0.12% to 0.02% — making 5m strategies that were marginal at taker fees
# (60% fee burden vs typical move) viable (10% fee burden).
#
# Trade-off: postOnly limit orders can be REJECTED if they would cross
# the spread, and may NOT fill on fast-moving setups. When enabled, the
# bot skips the trade after `max_wait_sec` instead of falling back to
# market — preserving fee savings at the cost of missed entries.
#
# Default OFF for safety. Flip to True (or set MAKER_ONLY_ENABLED=true)
# to take the fee saving once convinced the missed-fill cost is less
# than the fee saving on the trades that DO fill.
MAKER_ONLY = {
    # Default ON as of 2026-05-29, per owner directive, for a LIVE maker-fee
    # soak test. Safely integrated 2026-05-29 (tests/test_maker_only_integration.py):
    # the executor reports a partial fill as 'partial_maker' (never a silent skip),
    # and order_manager._interpret_execution_result opens NO position on
    # skip/uncertain (no phantom — old code keyed only on order["id"]) and sizes a
    # partial to the actual fill (SL/TP cover the real position).
    # HONEST CAVEAT (unchanged): maker entries are adversely selected (filled on
    # losers, skipped on the bounce), so maker-only does NOT improve EV — the
    # scalp-edge study found it negative across 3yr folds. The soak measures the
    # realized maker-fill RATE and FEE saving, NOT profitability (known-negative).
    # Also: paper bypasses the executor, so judge ONLY on LIVE fills. Revert with
    # MAKER_ONLY_ENABLED=false.
    "enabled": os.getenv("MAKER_ONLY_ENABLED", "true").lower() == "true",
    "max_wait_sec": int(os.getenv("MAKER_ONLY_MAX_WAIT_SEC", "120")),
}

# ==============================================================
# SL/TP TRIGGER PRICE BASIS (C3, tpbot retrofit 2026-07-08)
# ==============================================================
# When True, exchange-side SL/TP conditionals trigger on MARK price instead
# of the venue's default LAST price (Binance workingType=MARK_PRICE, Bybit
# triggerBy/slTriggerBy/tpTriggerBy=MarkPrice, Bitget triggerType=mark_price).
# Mark-price triggering ignores single rogue last-price prints (no
# wick-stopouts from one bad trade) and stays synced with the venue's
# liquidation engine. NOTE: ccxt 4.5.54 already defaults Bitget tpsl orders
# to mark_price — this flag makes the basis explicit on ALL venues.
# ⚠ WR-relevant semantics change (flagged per the WR-floor rule): fewer
# last-price wick stop-outs is expected neutral-to-positive, but the trigger
# feed changes in CONTROLLED_LIVE. Revert with SLTP_TRIGGER_MARK_PRICE=false.
# PAPER is unaffected (sim triggers on 1m last-price candles either way —
# see the honest divergence note in CLAUDE.md gotchas).
SLTP_TRIGGER_MARK_PRICE = (
    os.getenv("SLTP_TRIGGER_MARK_PRICE", "true").lower() == "true")

# ==============================================================
# SL-FAIL EMERGENCY CLOSE (Codex order_router port, 2026-07-12)
# ==============================================================
# Charter §2 Stop-Loss Guardian alignment: when exchange-side SL placement
# GENUINELY fails after the full retry ladder (BaseExchange.create_order
# retries incl. Bybit-110072 clientOrderId regen / Binance -4120 routing),
# the position is closed immediately through the NORMAL close path
# (close_position → tracker.close → on_close hooks, reason=
# 'sl_placement_failed') and the alert then reports "closed fail-safe".
# Default TRUE. The flag exists ONLY as an operator escape hatch: false
# restores the old behavior (_sl_failed=True + EMERGENCY alert + local
# polled SL + per-minute exchange-SL reconcile — position stays open
# WITHOUT exchange-side protection). Identical in PAPER and
# CONTROLLED_LIVE (close_position sim-closes in paper).
SL_FAIL_EMERGENCY_CLOSE_ENABLED = (
    os.getenv("SL_FAIL_EMERGENCY_CLOSE_ENABLED", "true").lower() == "true")

# OHLCV integrity validation (Codex port, 2026-07-12): every
# BaseExchange.fetch_ohlcv result is checked for monotonic timestamps,
# finite values, OHLC range sanity and staleness (exchanges/base.py::
# validate_ohlcv). A defective series is replaced by [] (the existing
# no-data return) so callers skip the symbol for one cycle — fail-closed
# for the data, fail-open for the bot. Kill flag for emergencies only.
OHLCV_VALIDATION_ENABLED = (
    os.getenv("OHLCV_VALIDATION_ENABLED", "true").lower() == "true")

# C8 (tpbot retrofit 2026-07-08): venue clock-drift alert threshold (ms).
# The 60s health cycle samples an NTP-style offset per venue; sustained
# drift beyond this warns in-engine and edge-alerts via health_watchdog.
# Exchanges reject signed requests when drift approaches recvWindow — the
# repo's deployment notes call Windows drift "the #1 silent killer".
CLOCK_DRIFT_ALERT_MS = int(os.getenv("CLOCK_DRIFT_ALERT_MS", "500"))

# ==============================================================
# SCALP MODE — 15-60 minute VWAP-centric entries (2026-05-27)
# ==============================================================
# Evidence: 454-trade dataset shows 15-60m holds = 63.8% WR (+$4.41)
# vs 0-15m = 32.3% WR (-$41.10) and >60m = 35-38% WR.
# VWAP + Long = 61.9% WR — strongest signal in the dataset.
# B1 MACD (25% WR) and B3 15m timing (35% WR) are anti-predictive.
SCALP_MODE = {
    "enabled": os.getenv("SCALP_MODE_ENABLED", "true").lower() == "true",
    "entry_threshold": int(os.getenv("SCALP_ENTRY_THRESHOLD", "65")),
    # 2026-05-28: tightened SL/TP for $1-2/trade target at $130+ notional.
    # Old 1.0/1.8 produced $0.62/$1.12 per trade at $62 notional — too small.
    # New 0.8/1.3 at $136 notional (35% × 3x × $130 pocket):
    #   TP hit: $136 × 1.3% = $1.77   SL hit: $136 × 0.8% = $1.09
    #   R:R = 1.625:1, clears min_rr_ratio 1.2:1
    "sl_pct": float(os.getenv("SCALP_SL_PCT", "0.8")),
    "tp_pct": float(os.getenv("SCALP_TP_PCT", "1.3")),
    # 2026-05-28: raised for 15m-1h candle entries; 60min/45min was killing winners
    "time_wall_min": int(os.getenv("SCALP_TIME_WALL_MIN", "180")),
    "stale_close_min": int(os.getenv("SCALP_STALE_MIN", "120")),
    "stale_min_profit": float(os.getenv("SCALP_STALE_PROFIT", "0.3")),
    "trailing_enabled": os.getenv("SCALP_TRAILING", "false").lower() == "true",
    "longs_only": os.getenv("SCALP_LONGS_ONLY", "true").lower() == "true",
    "min_atr_pct": float(os.getenv("SCALP_MIN_ATR", "0.8")),
    "max_spread_bps": float(os.getenv("SCALP_MAX_SPREAD_BPS", "10")),
    "vwap_distance_max_pct": float(os.getenv("SCALP_VWAP_MAX", "0.3")),
    "peak_hours": {22, 23, 0},
    "partial_tp": {
        "enabled": True,
        "fraction": 0.5,
        "at_pct": 1.0,
        "move_sl_to_be": True,
    },
}

# ==============================================================
# MODEL GATE — calibrated LR+GBM ensemble blended with the MCP rule score
# Loads data/models/ensemble_{market}_latest.json on startup.
# When `enabled=False` or no latest pointer exists, the gate is bypassed
# and the rule-only path runs (existing behavior).
# `shadow_only=True` keeps p_win logged to the warehouse but does NOT block
# entries — used for soak windows where we want bit-for-bit baseline.
# ==============================================================
# ==============================================================
# SHADOW MODE (Phase A multi-agent build, 2026-05-02)
# ==============================================================
# When enabled, a parallel multi-agent runner (TrendAgent + ScalpAgent +
# MeanReversionAgent) computes proposed trades on the same tick stream
# as the live bot, but writes to warehouse.shadow_decisions instead of
# placing orders. Live path is untouched. Auto-disables on kill criteria.
# Default TRUE per user directive 2026-05-02 (Option 4 multi-agent build).
SHADOW_MODE = {
    "enabled": os.getenv("SHADOW_MODE_ENABLED", "true").lower() == "true",
    "tick_interval_s": int(os.getenv(
        "SHADOW_TICK_INTERVAL_S", "60" if _AGGRESSIVE_PAPER_RESEARCH else "300"
    )),
    "alt_notional": float(os.getenv("SHADOW_ALT_NOTIONAL", "200.0")),
    "kill_fee_burn_x": float(os.getenv("SHADOW_KILL_FEE_BURN_X", "2.0")),
    "kill_wr_floor": float(os.getenv("SHADOW_KILL_WR_FLOOR", "0.30")),
    "kill_min_decisions": int(os.getenv("SHADOW_KILL_MIN_DECISIONS", "100")),
    "kill_max_halts_7d": int(os.getenv("SHADOW_KILL_MAX_HALTS_7D", "3")),
    # 2026-07-06 mover universe: the shadow ensemble watches the day's biggest
    # |24h %| movers among the liquidity-screened futures pairs (owner ask:
    # continuously test scalping the daily movers) instead of the first-listed
    # pairs. Log-only lane; SHADOW_MOVER_UNIVERSE=false restores legacy.
    "mover_universe": os.getenv("SHADOW_MOVER_UNIVERSE", "true").lower() == "true",
    "mover_cap": int(os.getenv(
        "SHADOW_MOVER_CAP", "30" if _AGGRESSIVE_PAPER_RESEARCH else "10"
    )),
    "mover_refresh_s": int(os.getenv(
        "SHADOW_MOVER_REFRESH_S", "300" if _AGGRESSIVE_PAPER_RESEARCH else "900"
    )),
    "mover_min_qv_usd": float(os.getenv("SHADOW_MOVER_MIN_QV_USD", "5000000")),
}

# Broad USDT-perpetual monitor (READ-ONLY / SHADOW-ONLY). One batched ticker
# request per active venue feeds a persistent point-in-time store; a small,
# direction-balanced 1h/24h/7d shortlist is then handed to the existing shadow
# agents for deeper analysis. It never modifies TRADING_PAIRS/current_pairs or
# receives an OrderManager, so enabling it cannot widen executable exposure.
BROAD_UNIVERSE_MONITOR = {
    "enabled": os.getenv("BROAD_UNIVERSE_MONITOR_ENABLED", "true").lower() == "true",
    "db_path": os.getenv(
        "BROAD_UNIVERSE_MONITOR_DB_PATH", "data/universe_monitor.sqlite"
    ),
    "min_quote_volume_usdt": float(os.getenv(
        "BROAD_UNIVERSE_MIN_QUOTE_VOLUME_USDT", "5000000"
    )),
    "max_ticker_age_s": float(os.getenv("BROAD_UNIVERSE_MAX_TICKER_AGE_S", "180")),
    "reference_tolerance_s": float(os.getenv(
        "BROAD_UNIVERSE_REFERENCE_TOLERANCE_S", "1800"
    )),
    "retention_days": float(os.getenv("BROAD_UNIVERSE_RETENTION_DAYS", "8")),
    "per_direction_per_horizon": int(os.getenv(
        "BROAD_UNIVERSE_PER_DIRECTION", "10" if _AGGRESSIVE_PAPER_RESEARCH else "3"
    )),
    # Final deep-analysis budget is also clamped by SHADOW_MODE.mover_cap.
    "shortlist_cap": int(os.getenv(
        "BROAD_UNIVERSE_SHORTLIST_CAP", "36" if _AGGRESSIVE_PAPER_RESEARCH else "18"
    )),
    "max_contracts_per_venue": int(os.getenv(
        "BROAD_UNIVERSE_MAX_CONTRACTS_PER_VENUE", "5000"
    )),
}

# ── Listing-short shadow probe (pipeline rev3 CONFIRMED_GO, 2026-07-09) ──────
# LOG-ONLY forward soak of the capital-scaled post-listing perp short. Detects
# new Binance USDT-M perp listings, proposes 3%-notional shorts (7d + 30d,
# 4-concurrent cap) into the shadow lane, and logs the per-bar MTM path,
# concurrent account-MTM drawdown, day-1 execution realism, and a discriminating
# score — the binding conditions from 03_rev3_audit_findings.md. It places NO
# orders and only runs inside the (already log-only) shadow lane, so it changes
# ZERO live behaviour. Off restores the pre-probe shadow lane exactly.
LISTING_SHORT_PROBE = {
    "enabled": os.getenv("SHADOW_LISTING_PROBE_ENABLED", "true").lower() == "true",
    "venue": os.getenv("SHADOW_LISTING_PROBE_VENUE", "binance"),
}

# ── Unlock-short shadow probe (pipeline 08b CONFIRMED-GO, 2026-07-11) ────────
# LOG-ONLY forward soak of the capital-scaled pre-unlock cliff short
# (_workspace/strategy_pipeline/09_audit_candidate2_final.md). Scans the
# data/unlock_calendar/ snapshot for >=10%-of-mcap cliff unlocks, proposes
# 3%-notional UNLEVERED shorts at T-28d (W1) and T-14d (W2) exiting at unlock
# T (4-concurrent cap per arm) into the shadow lane, and logs the per-bar MTM
# path, the charter-§2 8%-SL-Guardian counterfactual, realized funding, and
# the frozen discriminating score — binding conditions 1-5 of the audit. It
# places NO orders and only runs inside the (already log-only) shadow lane, so
# it changes ZERO live behaviour. Off restores the pre-probe shadow lane
# exactly. NOTE: the probe only sees events present in data/unlock_calendar/ —
# keep it refreshed via scripts/backfill_unlock_calendar.py --forward-days.
UNLOCK_SHORT_PROBE = {
    "enabled": os.getenv("SHADOW_UNLOCK_PROBE_ENABLED", "true").lower() == "true",
    "calendar_dir": os.getenv("SHADOW_UNLOCK_CALENDAR_DIR", "data/unlock_calendar"),
    # screen venue-preference order (binance 5 bps taker, then bybit/bitget 6)
    "venue_order": ("binance", "bybit", "bitget"),
}

# ── TSMOM-20d shadow probe (owner-directed regime-watch, 2026-07-11) ─────────
# NOT a pipeline GO: time-series momentum is a REFUTED family (refuted-families
# ledger — long-only TSMOM 2026-06-15, textbook trend 0/40 OOS 2026-06-13) and
# the external Codex backtest behind this probe did NOT meet the reopen bar
# (~1.8-month single-regime OOS, ~90-run sweep winner, prior period -17.4%).
# The owner explicitly directed a LOG-ONLY forward paper test — the Codex
# report's own recommendation was "regime-watch: monitor in paper mode, do NOT
# automate" (bot_weight 0.0). TsmomProbeAgent proposes 20d-momentum entries on
# BTC/ETH/SOL perps in two arms (1h + 4h; 2xATR stop, 2R target, 7d max hold,
# notational 1%-risk sizing) into the shadow lane. Expectation: NO-PROMOTE.
# It places NO orders and only runs inside the (already log-only) shadow lane,
# so it changes ZERO live behaviour. Off restores the pre-probe lane exactly.
TSMOM_PROBE = {
    "enabled": os.getenv("SHADOW_TSMOM_PROBE_ENABLED", "true").lower() == "true",
    # the Codex backtest data source was Bybit USDT linear perps
    "venue": os.getenv("SHADOW_TSMOM_PROBE_VENUE", "bybit"),
}

# ── Breakout-60d shadow probe (owner-directed, Codex deep-run winner, 2026-07-11)
# NOT a pipeline GO: textbook trend/breakout is a REFUTED family (ledger: 0/40
# OOS 2026-06-13; donchian scored F in Codex's own first sweep). The Codex deep
# run (5-6yr x 10 markets, survives 2x costs, 9/9 parameter cells stable) is
# the family's strongest external evidence yet BUT was selected ON burned
# holdout across 20 candidates, and Codex's OWN Monte Carlo fails our frozen
# capital-preservation gates (P>0 91.5% < 0.95; maxDD p95 42.5% > 0.25).
# Codex's own creation gate requires forward paper trading — this probe is
# that, per owner directive. ~30-35% WR by design (3:1 R:R): conflicts with
# the owner's >=65% WR-floor preference; could never join the accuracy-band
# lane without a separate owner decision. Expectation: NO-PROMOTE. LOG-ONLY:
# no orders, shadow lane only; off restores the pre-probe lane exactly.
BREAKOUT_PROBE = {
    "enabled": os.getenv("SHADOW_BREAKOUT_PROBE_ENABLED", "true").lower() == "true",
    # the Codex deep run's data source was Bybit USDT linear perps
    "venue": os.getenv("SHADOW_BREAKOUT_PROBE_VENUE", "bybit"),
}

# ── Deep-breakout ACTIVE PAPER lane (owner: "start trading aggressively", 2026-07-11)
# ACTIVE PAPER orders (sim fills via sim_execution) for the Codex deep_breakout
# strategy — unlike the log-only BREAKOUT_PROBE above, which stays untouched and
# keeps collecting frozen-gate evidence in parallel. PAPER-ONLY by structural
# gate: core/deep_breakout_lane.py refuses to construct/tick unless DRY_RUN
# (going live = explicit owner decision + code change; Codex forward gate).
# ~33% WR BY DESIGN (3:1 R:R trend system) — every order is cohort-tagged
# strategy_family='deep_breakout' and the accuracy-band goal reporting
# (report_goal_progress + dashboard THIS-BOOT) excludes the family.
# "Aggressive" = every valid signal at the researched size, never above it.
DEEP_BREAKOUT_LANE = {
    "enabled": os.getenv("DEEP_BREAKOUT_LANE_ENABLED", "false").lower() == "true",
    # Codex cross-exchange confidence: binance 78.8, bybit 57.4 — binance-first.
    # bitget EXCLUDED: 44.7 on only ~100 days of usable 4h history.
    "venues": ("binance", "bybit"),
    # the 10 Codex-validated majors (== core.agents.breakout_probe_agent.SYMBOLS)
    "symbols": (
        "BTC/USDT:USDT", "ETH/USDT:USDT", "SOL/USDT:USDT", "XRP/USDT:USDT",
        "BNB/USDT:USDT", "ADA/USDT:USDT", "DOGE/USDT:USDT", "LINK/USDT:USDT",
        "AVAX/USDT:USDT", "DOT/USDT:USDT",
    ),
    "risk_pct": 1.0,               # researched: 1% equity risk per trade
    "max_notional_multiple": 2.0,  # researched: notional <= 2x equity (< 2.5x charter)
    "max_concurrent": 4,           # lane position cap
    "lane_exposure_cap_pct": 6.0,  # lane gross notional <= 6% equity (half the §2 12% cap)
    "hard_max_loss_pct": 8.0,      # charter §2 Stop-Loss-Guardian backstop (order_manager)
    "tick_sec": 300,               # lane tick cadence; no-ops off the 4h boundary
}

# ── ACCURACY TARGET MODE (owner goal 2026-07-10: 60-65% WR futures band) ─────
# Inverts the exit geometry at BOTH TP authorities in mcp_brain: TP distance
# becomes tp_frac_of_sl x SL distance (default 0.5 -> theoretical hit rate
# SL/(SL+TP) ~ 67%, realized ~60-65% after costs/slippage), instead of the
# default 2:1 R:R whose no-edge realized WR is ~30%.
# HONESTY: this meets the ACCURACY target by construction — it does NOT create
# profit edge; expectancy stays ~ -costs on a no-edge signal. PAPER research
# posture. The CONTROLLED_LIVE promotion gate is untouched and still requires
# after-cost expectancy. SL authority (ATR/DistFit floors) is NOT modified —
# only the TP leg compresses; min_tp_pct keeps TP above round-trip costs.
ACCURACY_TARGET_MODE = {
    # Retained only so historical replay/tests can decode old positions. The
    # strategy program rejects hit-rate optimization as an entry/exit policy;
    # runtime cannot reactivate it through an environment toggle.
    "enabled": False,
    "requested_enabled": os.getenv("ACCURACY_TARGET_MODE", "false").lower() == "true",
    "tp_frac_of_sl": float(os.getenv("ACCURACY_TP_FRAC_OF_SL", "0.5")),
    # Per-side frac overrides (2026-07-10 geometry sweep, 8,878 entries +
    # adversarial audit): at frac 0.50 longs realized 67.0% WR vs shorts
    # 56.2% — a single global frac cannot put both sides mid-band; shorts
    # need a smaller frac. The sweep's side-splits put buy~0.45 / sell~0.35
    # inside the 63-67% owner band. 0 / unset -> None -> fall back to the
    # global tp_frac_of_sl above. min_tp_pct floor applies to both sides.
    "tp_frac_buy": float(os.getenv("ACCURACY_TP_FRAC_BUY", "0")) or None,
    "tp_frac_sell": float(os.getenv("ACCURACY_TP_FRAC_SELL", "0")) or None,
    "min_tp_pct": float(os.getenv("ACCURACY_MIN_TP_PCT", "0.5")),
    # Band time-exit horizon (2026-07-10 leak fix): the band's 63-67% WR was
    # audited on pure first-touch SL/TP with a 72h horizon, but the Phase-14
    # era STALE/AGE_LIMIT cutoffs (~60min-4h) were closing band trades BEFORE
    # the tight TP could be touched (warehouse 2026-07-10: take_profit 10/10
    # wins vs STALE n=5 + AGE_LIMIT n=1 ALL losses). While a band position is
    # younger than this horizon, time-based closes are suppressed and
    # first-touch SL/TP governs; past it the existing time exits apply
    # (zombie-position protection). See order_manager._accuracy_band_hold_active.
    "max_hold_hours": float(os.getenv("ACCURACY_MAX_HOLD_HOURS", "72")),
}

# ── MCP ENTRY MIN SCORE (2026-07-19 max-flow band engine, owner-approved) ────
# Overrides the mcp_brain algorithmic-fallback entry-score floors (standard
# path 66, scalp path SCALP_MODE.entry_threshold default 65) when set. The
# layers_ok gates (6+/10 standard, 4+ scalp) are NOT affected. Unset/blank ->
# None -> exactly today's 66/65 behavior. PAPER research knob (max-flow
# firehose, threshold 50 per owner amendment); HONESTY: a lower floor admits
# lower-conviction entries — expectancy expected <= historical (~ -0.24R).
_MCP_ENTRY_MIN_SCORE_RAW = os.getenv("MCP_ENTRY_MIN_SCORE", "").strip()
MCP_ENTRY_MIN_SCORE = float(_MCP_ENTRY_MIN_SCORE_RAW) if _MCP_ENTRY_MIN_SCORE_RAW else None

# ── BAND REGIME FILTER (2026-07-12) — band-lane-ONLY toxic-regime veto ───────
# Pre-registered screen _workspace/strategy_pipeline/13_band_conditional_screen
# .json (14,555 resolved band outcomes, 12d span, Bonferroni m=16; band
# baseline WR 65.7%) found two conditionally-WORST regimes for the accuracy
# band:
#   * 4h ADX > 30 at entry          -> band WR 59.0% (n=5,352, halves 59.0/59.0)
#   * BTC 1h ATR / 30d median < 0.7 -> band WR 55.6% (n=3,203)
# When enabled, _execute_open's accuracy-band carve-out VETOES new band-lane
# entries in either regime (reject_reason band_regime_filter:*). Applies ONLY
# where the band geometry is applied — never mcp_brain scoring, the
# deep_breakout lane, or shadow probes. Fail-OPEN on missing ADX/ratio.
# Thresholds are the screen's pre-registered bucket edges, hardcoded at the
# check site (not tunable knobs — retuning means a NEW pre-registered screen).
# HONESTY: WR-band protection + bleed reduction (removes the worst buckets),
# NOT edge — every screen bucket kept a NEGATIVE after-cost expectancy;
# PnL-positive still requires the evidence lanes.
BAND_REGIME_FILTER_ENABLED = os.getenv("BAND_REGIME_FILTER_ENABLED", "false").lower() == "true"

# ── MAKER-FIRST PAPER ENTRIES (2026-07-10) ───────────────────────────────────
# Fees were 25.4% of the last-500-trade loss ($39.63 of -$156.28) and the
# 2026-07-10 pre-fix cohort was gross-POSITIVE before fees; on the no-edge
# directional lane, cost engineering is the only honest expectancy lever
# (blueprinted 2026-06-11 "honest paper maker-fill model").
# When enabled, PAPER futures entries on the mcp/algorithmic lane place a
# VIRTUAL post-only limit at the touch (bid for buys / ask for sells) instead
# of an immediate taker fill. HONEST FILL RULE: the limit fills as maker only
# when the market trades strictly THROUGH the price (never on a touch).
# After timeout_sec unfilled -> taker fallback at the CURRENT price; if the
# market ran >0.3% beyond the signal price the entry is ABANDONED (no chase).
# SL/TP recompute off the actual fill so the ACCURACY band geometry is exact.
# Scope: PAPER futures entries only (exits, carry runner, live smart_executor
# untouched). Default OFF -> byte-identical to today.
MAKER_FIRST_PAPER = {
    "enabled": os.getenv("MAKER_FIRST_PAPER_ENABLED", "false").lower() == "true",
    "timeout_sec": int(os.getenv("MAKER_FIRST_PAPER_TIMEOUT_SEC", "45")),
    "max_chase": int(os.getenv("MAKER_FIRST_PAPER_MAX_CHASE", "1")),
}

# ── MIN-NOTIONAL FLOOR (owner UNBLOCK directive 2026-07-10) ──────────────────
# "The trading bot should perform ANY trades ... which it analyzes to be
# profitable." The last de-facto edge-opinion block: the stacked EV size
# multipliers (Phase 17 rolling-50, Phase 18 calibrator, Phase 27 per-symbol
# EV) shrink size below the exchange lot minimum and _execute_open dust-skips
# (+30min cooldown). When enabled, bot_engine floors such entries back UP to
# the exchange minimum — ONLY when the pre-multiplier base sizing could afford
# it (undoing opinion-downsizing, never overriding balance/margin reality) AND
# the floored size passes the same loss-clamp + §2 exposure-cap rails as any
# other size. Default OFF (public repo); the owner opts in via .env.
MIN_NOTIONAL_FLOOR = {
    "enabled": os.getenv("MIN_NOTIONAL_FLOOR_ENABLED", "false").lower() == "true",
}

MODEL_GATE = {
    "enabled": os.getenv("MODEL_GATE_ENABLED", "true").lower() == "true",
    # 2026-04-28: defaulted to TRUE per UNBLOCK_ALL directive — model
    # logged but didn't gate.
    # 2026-05-01 (stop-bleed plan): flipped to FALSE. The live ensemble
    # (reported AUC 0.76 / OOS WR 70.7% at p>=0.55) is now the entry authority.
    # ⚠ HISTORY (2026-05-30 audit): an earlier bundle promoted with PBO=1.0 and
    # deflated_sharpe≈0.0008 only because the train_models gates were loosened to
    # min_dsr=0.0 / max_pbo=1.0 — an UNPROVEN gate, not edge, consistent with the
    # project-wide NO_EDGE record.
    # RESOLVED (2026-06-20): the honest thresholds are now enforced in
    # core/promotion_gate.py (MIN_DSR=0.10, MAX_PBO=0.5, MIN_OOS_WR=0.55,
    # MIN_AUC=0.60); a model with that overfit profile is now REJECTED (see
    # tests/test_promotion_gate_honest.py). No model bundle ships in data/models/,
    # so the gate auto-bypasses to the rule-only path until an honest model is
    # trained. Re-running the leak-check (scripts/leak_check_embargo.py, embargo
    # >= 96-bar label horizon) requires warehouse training data. To force
    # logged-only behaviour regardless, set MODEL_GATE_SHADOW=true.
    # Candidates with p_win_ensemble < threshold_futures (0.55) are
    # blocked at core/mcp_brain.py:~2509-2526. When no model bundle is
    # loaded (fresh install / artifact missing) the gate auto-bypasses
    # via the `mscore["model_version"] is None` check, so the bot still
    # falls through to the rule-only path. Operational consequence: with
    # the current model and current setups, the bot may open zero trades
    # for hours/days until either market regime shifts or the next weekly
    # retrain produces stronger predictions. Revert via env override:
    #   MODEL_GATE_SHADOW=true python main.py
    "shadow_only": os.getenv("MODEL_GATE_SHADOW", "false").lower() == "true",
    "threshold_futures": float(os.getenv("MODEL_GATE_THRESHOLD_FUTURES", "0.55")),
    "threshold_spot": float(os.getenv("MODEL_GATE_THRESHOLD_SPOT", "0.58")),
}

# Final economic admission rule for the canonical MCP_DIRECTIONAL_PAPER
# futures lane. This is intentionally separate from MODEL_GATE's historical
# score threshold: the execution boundary requires a real promoted model and
# prices each candidate using its FINAL SL/TP plus conservative round-trip
# friction. The margin is a predeclared three percentage points above the
# candidate-specific after-cost breakeven probability; it is not a target-WR
# geometry knob and does not rewrite exits or labels. Fee/slippage stresses
# follow the backtest robustness policy (1.5x fee tier, 2x slippage).
MCP_DIRECTIONAL_ECONOMIC_GATE = {
    "probability_margin": float(os.getenv(
        "MCP_DIRECTIONAL_ECONOMIC_PROBABILITY_MARGIN", "0.03"
    )),
    "fee_stress_multiplier": float(os.getenv(
        "MCP_DIRECTIONAL_ECONOMIC_FEE_STRESS_MULT", "1.5"
    )),
    "slippage_stress_multiplier": float(os.getenv(
        "MCP_DIRECTIONAL_ECONOMIC_SLIPPAGE_STRESS_MULT", "2.0"
    )),
}
if not 0.0 <= MCP_DIRECTIONAL_ECONOMIC_GATE["probability_margin"] <= 1.0:
    raise ValueError(
        "MCP_DIRECTIONAL_ECONOMIC_PROBABILITY_MARGIN must be between 0 and 1"
    )
if MCP_DIRECTIONAL_ECONOMIC_GATE["fee_stress_multiplier"] < 1.0:
    raise ValueError("MCP_DIRECTIONAL_ECONOMIC_FEE_STRESS_MULT must be >= 1")
if MCP_DIRECTIONAL_ECONOMIC_GATE["slippage_stress_multiplier"] < 1.0:
    raise ValueError("MCP_DIRECTIONAL_ECONOMIC_SLIPPAGE_STRESS_MULT must be >= 1")

# ==============================================================
# SMART SCANNER (legacy — kept for reference, not used by Claude Portfolio mode)
# ==============================================================
SCANNER = {
    "min_confidence": 0.45,
    "scan_interval_min": 15,
    "min_adx_trend": 22,
    "futures_adx_threshold": 28,
    "max_atr_pct": 0.08,
    "timeframes": ["1d", "4h", "1h", "15m", "1m"],
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
    "BTC/USDT",
    "ETH/USDT",
    "BNB/USDT",
    "SOL/USDT",
    "XRP/USDT",
    "DOGE/USDT",
    "ADA/USDT",
    "AVAX/USDT",
    "LINK/USDT",
    "DOT/USDT",
    "UNI/USDT",
    "LTC/USDT",
    "BCH/USDT",
    "NEAR/USDT",
    "APT/USDT",
    "FIL/USDT",
    "ARB/USDT",
    "OP/USDT",
    "ATOM/USDT",
    "SUI/USDT",
    "SEI/USDT",
    "INJ/USDT",
    "FET/USDT",
    "RENDER/USDT",
    "TIA/USDT",
    "ALGO/USDT",
    "IOTA/USDT",
    "VET/USDT",
    "PEPE/USDT",
    "WIF/USDT",
    # 2026-05-31: added XLM — liquid top-coin listed on all 3 venues (owner-directed).
    "XLM/USDT",
]
_TOP_FUTURES = [s.replace("/USDT", "/USDT:USDT") for s in _TOP_SPOT]
# 2026-05-31: PRUNED commodity perps (XAU/XAG/CL) from the traded universe (owner-directed).
# They are not listed uniformly across Binance/Bybit/Bitget, so every scan cycle logged
# "[Bybit] XAU/USDT not available — skipped" / "CL/USDT not available" noise for zero benefit
# (no validated commodity edge; this is a crypto bot). The COMMODITIES metadata block below is
# left intact (dormant) so commodities can be re-added cleanly later if ever desired.

TRADING_PAIRS = {
    "binance": {"spot": list(_TOP_SPOT), "futures": list(_TOP_FUTURES)},
    "bybit": {"spot": list(_TOP_SPOT), "futures": list(_TOP_FUTURES)},
    "bitget": {"spot": list(_TOP_SPOT), "futures": list(_TOP_FUTURES)},
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
        "name": "Gold",
        "emoji": "🥇",
        "atr_mult": 1.5,  # wider SL for Gold (less volatile intraday)
        "min_adx": 18,  # Gold trends strongly — lower ADX threshold
        "corr_asset": "USD",  # inversely correlated with USD strength
    },
    "XAG": {
        "name": "Silver",
        "emoji": "🥈",
        "atr_mult": 1.8,  # Silver more volatile than Gold
        "min_adx": 20,
        "corr_asset": "XAU",  # follows Gold with amplification
    },
    "WTI": {
        "name": "Oil (WTI)",
        "emoji": "🛢️",
        "atr_mult": 2.0,  # Oil can be very volatile
        "min_adx": 22,
        "corr_asset": "USD",  # oil priced in USD
    },
    "CL": {  # alternative symbol for Oil on some exchanges
        "name": "Oil (WTI)",
        "emoji": "🛢️",
        "atr_mult": 2.0,
        "min_adx": 22,
        "corr_asset": "USD",
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
# ANALYSIS-ONLY INSTRUMENTS (2026-06-02; entry block LIFTED 2026-06-11)
# Commodity + equity perpetuals that ARE live + liquid on Binance/Bybit/Bitget
# (ccxt `XAU/USDT:USDT`, raw `XAUUSDT`). Originally hard-blocked from entries
# (~5 months of history, no screened edge).
#
# 2026-06-11 (owner UNBLOCK directive #3: "unblock all symbols. add new symbols
# which are listed on all connected exchanges"): the hard entry block is now
# OPT-IN — default OFF; re-arm with ANALYSIS_ONLY_ENFORCED=true in .env.
# These perps are listed on all 3 connected venues and are discovered in
# TRADING_MODE=all, so with the block off they flow into the tradeable
# universe like any crypto perp (MCP score / meta-filter / risk gates apply).
# ⚠ They still have NO screened edge (2026-06-02 probe: noise-like).
# The bases set is RETAINED as the perp-only instrument registry —
# mcp_brain fetch routing and _collect_all_coins depend on it; do not empty it.
# ==============================================================
ANALYSIS_ONLY_ENFORCED = os.getenv("ANALYSIS_ONLY_ENFORCED", "false").lower() == "true"
ANALYSIS_ONLY_BASES = {
    # commodities (gold, silver, WTI, Brent, copper)
    "XAU",
    "XAG",
    "CL",
    "BZ",
    "COPPER",
    "WTI",
    # equity perps
    "TSLA",
    "NVDA",
    "AMZN",
    "AAPL",
    "GOOGL",
    "META",
    "MSFT",
    "MSTR",
    "COIN",
}


def is_analysis_only(symbol: str) -> bool:
    """True if the symbol is entry-blocked as an analysis-only instrument.

    Always False while ANALYSIS_ONLY_ENFORCED is off (2026-06-11 owner unblock —
    see the section header above). When enforced, matches the ccxt perp form
    (`XAU/USDT:USDT`), the spot-name form (`XAU/USDT`), AND the raw exchange id
    (`XAUUSDT`) by EXACT base — so crypto-native tokens that merely share a
    prefix (e.g. XAUT = Tether Gold -> `XAUTUSDT`) are NOT caught.

    A few analysis-only bases are short/generic (CL, BZ, META, COIN). The match is
    deliberately fail-SAFE: if a real crypto perp ever shared one of these exact
    tickers it would be over-blocked (never traded), never under-blocked. This is the
    single safety choke in bot_engine._execute_open, so erring toward over-block is
    correct. The raw-id branch closes the only unsafe direction — a future caller
    passing a slash-less id silently failing OPEN through the choke.
    """
    if not ANALYSIS_ONLY_ENFORCED:
        return False
    s = symbol.upper()
    base = s.split("/")[0].split(":")[0]
    if base in ANALYSIS_ONLY_BASES:
        return True
    if "/" not in s:  # raw concatenated id (e.g. 'XAUUSDT'); match base+quote EXACTLY
        for b in ANALYSIS_ONLY_BASES:
            for q in ("USDT", "USDC", "USD"):
                if s == b + q or s == f"{b}{q}:{q}":
                    return True
    return False


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
    # 2026-05-04 (Phase 19, Ruflo audit): caution-symbol/caution-strategy
    # binary BLOCK gate disabled by default per UNBLOCK_ALL directive.
    # The gate at bot_engine.py:1992 was hard-blocking entries with conf<0.90
    # on any symbol whose 30d WR < 35%. With Phase 16 adaptive sizing
    # (sizes down by rolling EV) + Phase 18 calibrator (pulls confidence
    # toward historical WR) already handling these cases organically,
    # the binary block is a redundant double-veto contradicting the
    # "engine should decide" directive. Flip True to restore.
    "caution_symbol_block_enabled": False,
    "caution_strategy_block_enabled": False,
    # 2026-05-04 (Phase 22): regime VOLATILE soft-multiplier instead of
    # hard block. Phase 16's hard block was rejecting 10+ proposals/hour
    # on BTC/ETH/LINK at 0.6-1.5% ATR (95th-pctl-relative classification).
    # Now: counter-trend keeps hard block; volatile gets ×0.4 size
    # multiplier (default — tunable). Trade through volatile regime at
    # 40% size; capture momentum upside, cap drawdown vs full-size.
    # Flip regime_volatile_block_enabled=True to restore Phase 16 behavior.
    "regime_volatile_block_enabled": False,
    "regime_volatile_size_mult": 0.4,
    # 2026-04-24: raised from 0.01 → 0.05 on explicit user direction after the
    # 386-trade postmortem (WR 41.7%, avg_win $0.12 vs avg_loss $0.18, negative
    # expectancy because per-trade costs dominate at $0.10-0.30 gross). At $377
    # balance + 1% sizing, average trade notional was $11 at 3x — below the
    # structural cost floor. 5% × 3x = ~$56 notional makes individual P&L
    # moves meaningfully larger than fees+spread+slippage. Supersedes the
    # 2026-04-16 signed-checklist value; user accepted the trade-off.
    "max_position_pct": 0.05,
    "max_open_positions": MODE_PROFILE.max_open_positions,
    # 2026-04-27: tightened from 5% → 1.5% after 16h/9-loss bleed.
    # 2026-04-28 (L99): KEPT at 0.015. Daily-loss halt is the last
    # post-trade circuit breaker — at 99x leverage a single -1% move
    # is catastrophic; this halt limits damage to one bad day. Removal
    # would require explicit user authorization.
    # 2026-05-01: tightened 1.5% → 1.0% per capital-preservation pass.
    # At $791 balance: 1.0% = $7.91 daily loss limit. Triggers same-day
    # halt, recovers next UTC day. Goal: cap any one bad day's damage.
    "max_daily_loss_pct": 0.010,
    "default_stop_loss": 0.020,  # 2.0% fallback SL (ATR-based is primary)
    "default_take_profit": 0.060,  # 6.0% fallback TP (~3:1 R:R vs 2% SL)
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
    "futures_max_leverage": MODE_PROFILE.max_leverage,
    "default_leverage": min(1.0, MODE_PROFILE.max_leverage),
    "min_rr_ratio": 1.2,  # 1.2:1 — high-WR strategies don't need large R:R
    "trailing_stop": True,
    # 2026-04-28 retune (Phase 11) — converged trailing on mcp_take_profit
    # distribution; activation 1.5→2.0%, lock_fraction tier 0.40→0.55.
    # 2026-05-03 (Phase 15) — combined with the 4h→1.25h AGE_LIMIT cut,
    # trailing must engage faster or AGE_LIMIT fires before trailing locks.
    # Activation 2.0%→1.2% and lock_fraction (small-win tier) 0.55→0.65
    # to capture the 30-60min profitable cell before its 75min expiry.
    "trailing_activation": 0.012,  # 1.2% — engage earlier under tighter age cap
    "trailing_distance": 0.008,  # 0.8% — tighter trail to lock faster
    # ── NEAR-TARGET EXIT (B5+B6, audit 2026-06-21) — ONE default-OFF flag ──
    # When True (PAPER A/B only): (B5) mcp_take_profit may fire only at a
    # NEAR-PLANNED-TP level — net >= near_target_frac * planned_TP_dist, with the
    # leverage units reconciled (net_pnl_pct is LEVERAGED; tp_dist is price%) —
    # instead of the bare +0.5%/+1.5% floor that caps every winner; (B6) the
    # configured/planned TP becomes the FIRST profit authority checked in
    # check_sl_tp, and the trailing age-out activation is floored above the
    # round-trip cost. Default OFF => behavior byte-identical to today. This is a
    # WR-affecting EV-shape change: validate in PAPER before enabling. Toggle via
    # NEAR_TARGET_EXIT_ENABLED=true. The ceiling stays break-even (entry NO_EDGE).
    "near_target_exit_enabled": os.getenv("NEAR_TARGET_EXIT_ENABLED", "false").lower() == "true",
    "near_target_frac": float(os.getenv("NEAR_TARGET_FRAC", "0.8")),
    # 2026-04-28 (L99): KEPT at 0.12. Drawdown halt is the from-peak
    # circuit breaker; at 99x leverage it's the only thing standing
    # between a few bad trades and a wiped account. Removal would
    # require explicit user authorization.
    # 2026-05-01: 12% → 8% per capital-preservation pass. At $791 balance,
    # 8% = $63 max drawdown before halt. Combined with 1% daily loss limit,
    # bot has at most ~8 bad days before forced halt. Recovery requires
    # operator-cleared peak (or 4h auto-cooldown for consec_global halts).
    "max_drawdown_pct": 0.08,
    "position_sizing_mode": "tiered",  # leverage tier drives sizing; kelly is a sanity check
    # 2026-04-29 (Phase 14) — age cutoffs tightened from 6h/4h/3h.
    # 2026-05-03 (Phase 15) — hold-time analysis on 186 trades.
    # 2026-05-28 — RELAXED for 15m-1h candle trading. The 1.0h stale kill
    # was prematurely closing profitable positions (+0.28% XRP, +0.05% SOL)
    # before TP could hit. 15m-1h entries typically need 1-3h to reach a
    # 2-3% TP target. Raised limits to give winners room to run:
    #   - max_position_age: 4h (was 1.25h) — hard ceiling
    #   - max_stale: 3h (was 1h) — flat-only, profitable trades exempt
    #   - max_loss_age: 1.5h (was 0.75h) — losers still cut, but not at 45m
    "max_position_age_hours": 4.0,  # hard ceiling — force-close losing positions
    "max_stale_hours": 3.0,  # flat positions (-0.3% to 0%) cut; profitable exempt
    "max_loss_age_hours": 1.5,  # losing positions cut at 90min
    "max_loss_age_pct": 0.3,  # threshold for what counts as "losing" (unchanged)
    # 2026-04-27: hard cap on trade count per UTC day. The bot did 52 trades
    # on 2026-04-27 — overtrading on negative-EV strategies amplifies the
    # bleed regardless of per-trade SL discipline.
    # 2026-04-28 (UNBLOCK_ALL): user directive "Dont block any trades" —
    # raised from 20 to 200 so the daily cap effectively never binds.
    # Restore by reverting to 20 if you want the data-driven cap back.
    "max_trades_per_day": 200,
}

RISK_PER_TRADE_RANGE = (
    MODE_PROFILE.risk_per_trade_pct,
    MODE_PROFILE.risk_per_trade_pct,
)

# 2026-06-11 — per-trade risk-budget (vol-target) sizing.
# Margin is capped so loss-at-SL <= per_trade_risk_pct of the market-type
# balance. CEILING-ONLY vs the multiplier chain: it can only SHRINK a
# trade, never grow one, so every existing de-risk multiplier keeps full
# authority. With the ATR-based SL (1.5x ATR1h clamped 1.5-3.5%) this
# equalizes loss-at-SL across volatility regimes (notional ~ 1/ATR) —
# previously per-trade risk varied 2.3x purely with SL width. At PAPER
# size_pct=0.06/3x it binds only when sl% > ~2.78% (the wide-vol tail);
# at a live size_pct=0.50 posture it would become the de facto sizer
# (~0.5% risk per trade) — surface that before any live flip.
VOL_TARGET_SIZING = {
    "enabled": True,
    "per_trade_risk_pct": MODE_PROFILE.risk_per_trade_pct,
}

# 2026-06-11 — PORTFOLIO ES SOFT-CAP (core/portfolio_risk.py).
# Parametric 97.5% Expected Shortfall of the open book (EWMA RiskMetrics
# covariance, 1h returns, signed USD legs so longs/shorts net). SOFT size
# taper on new entries only — NEVER blocks (UNBLOCK stance); floor 0.25.
# Fail-OPEN on any data gap (factor 1.0). budget_pct=0.005: the ES_97.5,4h
# budget = 0.5% of equity = half the max_daily_loss halt (1.0%) — the
# ex-ante cap engages before the ex-post circuit breaker. A 2% budget was
# measured to NEVER bind at paper scale; 0.5% binds at ~$2-3k of
# same-direction correlated gross notional (measured EWMA pairwise rho
# 0.85-0.94 on majors, 1h vols 0.6-1.2%).
ES_RISK = {
    "enabled": True,
    "q": 0.975,  # ES confidence (0.95 | 0.975 | 0.99)
    "lambda": 0.94,  # RiskMetrics EWMA decay (per 1h bar)
    "horizon_hours": 4.0,  # ~typical holding period (age cutoffs 4h/2h/1.5h)
    "budget_pct": 0.005,  # ES budget as fraction of total equity
    "floor": 0.25,  # min taper factor — soft cap, never 0
    "bars": 240,  # 10d of 1h; >99.99% EWMA mass at lambda=0.94
    "min_bars": 60,  # min aligned rows per leg else drop leg
    "cache_ttl_sec": 1800,  # per-base closes cache
}

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
# 2026-06-06 (audit + owner directive): the MCP score is anti-predictive (r=-0.285; score>=85 =
# WORST cohort), yet confidence rose with score and unlocked higher leverage tiers (phase51 mapped
# score 85+ -> AGGRESSIVE 10x). That sizes UP on the known-worst trades. With this False, confidence
# can NO LONGER escalate leverage above STANDARD (3x) — pure loss-variance reduction on a NO_EDGE
# book (does NOT create edge). Set True to restore phase51 aggressive-on-high-score sizing.
CONFIDENCE_LEVERAGE_ESCALATION = False

LEVERAGE_TIERS = {
    "STANDARD": {
        # 2026-05-12 (phase51): 2→3x per user directive. Covers all allowed-
        # hour signals at conf>=55%. The 3x era (90 trades) was the only
        # historically profitable leverage era (+$0.28 net vs -$5.07 at 2x).
        "leverage": 3,
        "size_pct": 0.06,  # PAPER: 0.06 (raised from 0.03 — 0.03 fell under $5 min after de-risk multipliers). Revert to 0.50 before live.
        "sl_pct": 0.015,
        "tp_pct": 0.0375,  # 2.5:1 R:R
        "min_confidence": 0.50,  # PAPER aggressive (2026-05-31): was 0.55 — lowered to the 0.50 worst-band boundary (NOT below; see test_math_tune). Revert to 0.55 before live.
        "requires_whitelist": False,
        "requires_allowed_hour": True,
        "requires_peak_hour": False,
        "requires_btc_aligned": False,
    },
    "STRONG": {
        # 2026-05-12 (phase51): 2→4x. Whitelist + BTC-aligned any allowed hour.
        "leverage": 4,
        "size_pct": 0.06,  # PAPER: 0.06 (was 0.03). Revert to 0.50 before live.
        "sl_pct": 0.015,
        "tp_pct": 0.0375,
        "min_confidence": 0.72,
        "requires_whitelist": True,
        "requires_allowed_hour": True,
        "requires_peak_hour": False,
        "requires_btc_aligned": True,
    },
    "CONVICTION": {
        # 2026-05-12 (phase51): 2→5x per user directive. Whitelist + peak hour
        # + BTC aligned. Tightest standard conditions — highest-conviction setups.
        "leverage": 5,
        "size_pct": 0.06,  # PAPER: 0.06 (was 0.03). Revert to 0.50 before live.
        "sl_pct": 0.015,
        "tp_pct": 0.0375,
        "min_confidence": 0.80,
        "requires_whitelist": True,
        "requires_allowed_hour": True,
        "requires_peak_hour": True,
        "requires_btc_aligned": True,
    },
    "AGGRESSIVE": {
        # 2026-05-12 (phase51): 2→10x per user directive. Fires only at conf
        # >=85% during peak hours on whitelist symbols with BTC aligned.
        # Anti-EV score cap removed in bot_engine (was from bug-era data).
        "leverage": 10,
        "size_pct": 0.06,  # PAPER: 0.06 (was 0.03). Revert to 0.50 before live.
        "sl_pct": 0.015,
        "tp_pct": 0.0375,
        "min_confidence": 0.85,
        "requires_whitelist": True,
        "requires_allowed_hour": True,
        "requires_peak_hour": True,
        "requires_btc_aligned": True,
    },
    # 2026-05-22 — SCALP tier added per user directive: "Higher trades,
    # small TPs. Even 1-2 USDT or 1-2% gain per trade (FUTURES)". This is
    # the LOW-CONVICTION FALLBACK: when no higher tier qualifies (typical
    # in chop where blended confidence sits at 0.30-0.50), SCALP catches
    # the signal at small size + tight TP so the bot stays active without
    # taking high-conviction-tier risk.
    #
    # Sizing math @ $400 equity:
    #   10% × 2x × 1.5% SL = 0.30% balance/loss = ~$1.20 per SL hit
    #   10% × 2x × 1.8% TP = 0.36% balance/win  = ~$1.44 per TP hit
    # Lands directly in the user's "1-2 USDT per trade" bracket.
    # R:R = 1.2 → clears the global min_rr_ratio gate after fees.
    # Reaches that WR comfortably based on prior 0.55-conf cohort (60% WR).
    "SCALP": {
        # 2026-05-28 tune — size_pct 0.16→0.35, SL/TP matched to SCALP_MODE.
        # Evidence: 123 recent trades, 0 TP hits. Old notional $62 too small
        # for $1-2/trade target. New sizing @ $130 pocket:
        #   35% × 3x × 0.8% SL = 0.84% balance/loss = ~$1.09 per SL hit
        #   35% × 3x × 1.3% TP = 1.37% balance/win  = ~$1.77 per TP hit
        #   R:R = 1.625:1. At 55% WR: EV = +$0.48/trade.
        "leverage": 3,
        "size_pct": 0.06,  # PAPER: 0.06 (was 0.03/0.35). Revert to 0.35 before live.
        "sl_pct": 0.008,  # 0.8% price (matches SCALP_MODE)
        "tp_pct": 0.013,  # 1.3% price = 1.625:1 R:R
        "min_confidence": 0.50,  # FLOOR — do NOT lower: the 0.40-0.50 conf band is the anti-monotonic worst-WR cohort (~23-27%), guarded by test_math_tune_2026_05_24. Aggression comes from the prompt + cadence knobs, not from admitting the worst band.
        "requires_whitelist": False,
        "requires_allowed_hour": True,
        "requires_peak_hour": False,
        "requires_btc_aligned": False,
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
CONSEC_LOSS_DOWNGRADE_COUNT = 2  # 2 losses in a row → drop tier cap by 1
CONSEC_LOSS_DOWNGRADE_HOURS = 4  # downgrade lasts 4h
# Phase 49 (2026-05-10): PAUSE disabled. The L-streak counts ghost_sync /
# ghost_reconciled / sl_placement_failed closes which are EXCHANGE-side
# events (network outage forced closes), not bot decisions. Penalizing
# the bot for those by halting entries for 30min is wrong — the user
# explicitly wants "trade with confidence 24x7" and "focus on recovering
# losses, not halting". Tier DOWNGRADE (above) still applies as a softer
# risk-management measure: bot keeps trading but at smaller size during
# losing streaks. To re-enable pause, set hours back to 0.5.
CONSEC_LOSS_PAUSE_COUNT = 999  # effectively disabled
CONSEC_LOSS_PAUSE_HOURS = 0  # pause disabled

# Volatility-adaptive leverage cap — high-ATR symbols clamp to STANDARD
HIGH_ATR_PCT_THRESHOLD = 0.025  # ATR% > 2.5% → max leverage = STANDARD (2x)

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
# 2026-06-20 (owner "remove any blacklist and blocks", PAPER): shorts re-enabled.
# This flag was already inert — auto_mutator.shorts_blocked() hard-returns False —
# so flipping it to False just keeps the unblock durable if that is ever restored.
SHORTS_DISABLED = False

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
    "ATOM/USDT:USDT",  # ⭐ n=14, +$2.06, 43% WR, $0.147/trade
    "ARB/USDT:USDT",  # ⭐ n=18, +$1.20, 44% WR, $0.067/trade
    "DOGE/USDT:USDT",  # ⭐ n=10, +$4.83, 50% WR, $0.483/trade — TOP CONTRIBUTOR (added 2026-05-01)
    "ETH/USDT:USDT",  #   n=8,  +$0.45, 75% WR — high-WR, small absolute mean
    "MANA/USDT:USDT",  #   n=3,  +$0.37, 33% WR — thin sample, tentative
    "BTC/USDT:USDT",  #   macro anchor, always tradeable
    "AVAX/USDT:USDT",  #   n=4,  near-zero — neutral, kept as tier hint
    # Tentative thin-sample symbols (n<3 in claude_portfolio):
    "LUMIA/USDT:USDT",  # historical positive
    "ORDI/USDT:USDT",
    "DOT/USDT:USDT",  # n=6 in claude_portfolio, marginal -$0.038/trade
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
    "ATOM/USDT:USDT",  # n=14, +$1.86 sum, 43% WR (all-time)
    "ARB/USDT:USDT",  # n=35, +$1.15 sum, 49% WR (all-time)
    # DOGE removed Phase 39 (2026-05-09): was n=10/+$4.83 at Phase 12.2;
    # now n=18/-$3.71 (56% WR but asymmetric — losses swamp wins).
    # Moved to BLACKLIST_HARD.
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
    "enabled": True,
    "dust_cutoff_usd": 25.0,  # Component A — sell positions worth less
    "min_position_usd": 50.0,  # Component B — below this, no rules apply
    "drawdown_half_pct": 0.25,  # SCALE_OUT trigger (half-exit, peak resets)
    "drawdown_full_pct": 0.40,  # SELL trigger (full exit)
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
    # 2026-05-04 (Phase 20, after live monitor showed 0/N entries firing):
    # Disabled per UNBLOCK_ALL directive ("Don't block any trades. Engine
    # should decide.") — same rationale as Phase 19's caution-symbol
    # disable. Phase 16 adaptive sizing (rolling-50 EV) and Phase 18
    # calibrator (soft size multiplier) already handle low-EV setups
    # ORGANICALLY: a symbol with negative recent expectancy gets sized
    # down by both layers. The hard $0.05 floor was a binary veto on
    # top of that — a triple-gate that locked out symbols whose recent
    # mean had drifted slightly negative, with no path to recovery
    # (chicken-and-egg: 0 trades → mean never moves).
    #
    # Live evidence (08:34 UTC): conf=0.81 BTC entry blocked because
    # recent_mean=-$0.467 < $0.05 floor. Even at floor=$-0.30, BTC
    # would still fail — effective state is "permanently locked out
    # until manually whitelisted." That's not "engine should decide."
    #
    # The floor is also lowered to a "catastrophic-only" guard: -$0.50
    # mean PnL across 5+ trades is genuinely catastrophic and should
    # be vetoed even if the user re-enables. Below this, the bot is
    # losing > 1× its average notional per trade — Phase 16 sizing
    # alone can't compensate.
    #
    # 2026-05-05 (Phase 27): re-enabled as a GRADUATED multiplier, not a
    # binary block. _ev_per_symbol_multiplier in bot_engine.py reads this
    # config and returns:
    #   mean >= 0:               ×1.0 (full size)
    #   -0.20 <= mean < 0:       ×0.75
    #   -0.50 <= mean < -0.20:   ×0.50
    #   mean < -0.50 (n>=5):     ×0.0  HARD BLOCK (catastrophic only)
    # Whitelist still bypasses entirely. Per user directive:
    #   "Test before it trades. No bias. Just data. Self-correcting."
    "enabled": True,
    "min_expected_dollar": -0.50,  # catastrophic-only floor
    "min_expected_star": -0.50,
    "lookback_days": 30,
    "min_sample_size": 5,
    # A2 (audit 2026-06-21): when True, the per-symbol expectancy gate measures
    # WHOLE-TRADE PnL (runner realized_pnl + partial_realized_pnl) instead of the
    # runner leg alone (which under-counts partial-taken winners). ⚠ Default-OFF
    # and OWNER-GATED: folding partials in makes expectancy less negative -> the
    # gate ALLOWS MORE trades, which on a NO_EDGE bot means more negative-EV
    # trades. Do NOT enable without a measured entry edge. OFF => byte-identical.
    "whole_trade": False,
    # Operator-whitelisted symbols bypass the floor entirely. Use sparingly —
    # a symbol on this list trades regardless of recent expectancy. Per-trade
    # SL, MODEL_GATE, and Spec §12 streak halt still apply.
    # 2026-05-05 (Phase 33): UNBLOCK_ALL_STRICT — operator whitelist
    # emptied per user directive: "Remove any blocks, blacklist. Trade
    # ANY PAIR whether SPOT/FUTURE if the bot analyzes its going to be
    # profitable." DOGE no longer special — Phase 27 graduated EV
    # evaluates it on the same data terms as everything else.
    # historical reference (kept commented for audit trail):
    #   "DOGE/USDT:USDT", "DOGE/USDT" used to live here pre-Phase 33
    "whitelist": set(),
}

# 2026-05-05 (Phase 33): UNBLOCK_ALL_STRICT — gate-toggle flags.
# User directive: "Remove any blocks, blacklist. Trade ANY PAIR
# whether SPOT/FUTURE if the bot analyzes its going to be profitable."
#
# These flags disable PROTECTIVE GATES that aren't per-trade analysis.
# Phase 23 (calibrator hard-refuse <40%) and Phase 27 (graduated EV)
# stay ON because those ARE the analysis. ShortGate, Spec §12 per-pair
# pauses are protective rules layered ON TOP of analysis — disabled.
#
# Spec §12 GLOBAL halt (5 consecutive losses → 4h cooldown) is a
# catastrophic safety rail and stays ON regardless. Phase 29 post-SL
# cooldown stays ON — 2026-06-11 (owner-approved): 180 min per
# (symbol, side) after a stop_loss, re-armed as a HARD BLOCK (the
# 2026-05-27 advisory-only mode blocked nothing; see risk_manager).
SHORT_GATE_ENABLED = False  # rolling 30-trade SELL WR < 45% pause
SPEC12_SYMBOL_PAUSE_ENABLED = False  # per-symbol pause after 2 consec losses
SPEC12_FAMILY_PAUSE_ENABLED = False  # per-family pause after 3 consec losses

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
    "enabled": True,
    "invalidation_gap_pct": 0.15,  # min EMA gap (in WRONG direction) to fire
    "min_hold_minutes": 30,  # grace period after entry
    # 2026-06-11 gap-flip semantics: fire ONLY when the 4h gap actually
    # FLIPPED after entry. Born-invalid positions (gap already >= threshold
    # against the side AT THE ENTRY BAR) are exempt forever — SL/TP/trailing
    # manage them. Jun-11 evidence: all 85 entry_invalidated closes since
    # Jun 4 were longs killed at exactly 30min with gaps -1.6%..-4.8% that
    # were already invalid at entry (a 4h gap can't move 2% in 30min) —
    # the old rule structurally forced an all-short book in a 4h-bear
    # regime. False restores the pre-2026-06-11 fire-on-current-gap rule.
    "require_flip_after_entry": True,
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
    "enabled": False,  # was True with star_only=True
    "star_only": True,  # ignored when enabled=False
    "score_band_min": 70.0,
    "score_band_max": 84.0,
    "star_overrides": True,
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
# Phase 39 (2026-05-09): re-enabled from 421-trade all-time analysis.
# These symbols WERE net-negative across all strategies combined:
#   APT/USDT:USDT   n=4   -$12.97  25% WR  (single -$7.35 outlier)
#   SOL/USDT:USDT   n=18  -$11.02  22% WR
#   XRP/USDT:USDT   n=11   -$7.38   9% WR
#   ETH/USDT:USDT   n=15   -$6.47  47% WR  (asymmetric R:R, winners small)
#   DOGE/USDT:USDT  n=18   -$3.71  56% WR  (wins small, losses large)
#   BTC/USDT:USDT   n=12   -$2.07  33% WR
#
# 2026-05-21 (UNBLOCK_ALL): User directive — "Clear all blacklist and blocked
# coins". Cleared per user, conflicts with Phase 39 evidence. AutoMutator's
# runtime blacklist (consec-loss based) still operates on top, as do
# per-trade gates (Phase 23 calibrator hard-refuse <40%, Phase 27 graduated
# EV per cell, Phase 29 post-SL cooldown). Spec §12 GLOBAL halt + drawdown
# halt + daily loss circuit breaker also remain.
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
# Phase 39 (2026-05-09): re-fitted on 421 all-time real trades.
#
# CATASTROPHIC losers (block — heavily net-negative, ≥$9.91 loss):
#   H22 -$35.81  H09 -$23.59  H05 -$19.12  H00 -$15.69
#   H23 -$12.05  H19 -$10.33  H21  -$9.91
#
# STRONG winners (keep — net positive, ≥+$1.17):
#   H08 +$5.27 70%WR  H01 +$5.17  H20 +$6.30 65%WR  H18 +$1.71 72%WR
#   H16 +$2.25 90%WR  H05 +$2.40 78%WR  H04 +$1.92  H12 +$1.65
#   H10 +$5.60  H19 ... wait H19 negative all-time: blocked.
#
# MARGINAL / NEUTRAL (allow — low sample or near-zero):
#   H02 H03 H06 H07 H11 H13 H14 H15 H17
#
# ALLOWED ∪ BLOCKED == set(range(24)) and ALLOWED ∩ BLOCKED == ∅
# Phase 44 (2026-05-10): re-fitted on REAL-trade filter (Phase 43 dashboard
# audit revealed unfiltered data was contaminated by MANUAL/RECONCILE imports).
# Changes vs Phase 39:
#   H05: BLOCKED → ALLOWED (filtered: +$2.40 / 78% WR — was unfiltered -$19.12)
#   H22: BLOCKED → ALLOWED (filtered: -$0.35 / 62% WR — was unfiltered -$35.81)
# Real catastrophic losers (kept blocked): H00, H09, H19, H21, H23
# 2026-05-21 (UNBLOCK_ALL): User directive — "Clear all blacklist and
# blocked coins". All 24 hours allowed; PEAK/WARMUP retained as sizing
# hints only (not entry gates). Reversible via config edit.
#
# 2026-05-27 (454-trade hardening): H17 (30 trades, 27% WR, -$34.94) and
# H00 (35 trades, 26% WR, -$20.76) are catastrophic — noted for sizing.
# 2026-05-27 (UNBLOCK directive): owner says don't block correctly-analyzed
# trades. Hours cleared; WEEKDAY_CONFIDENCE_MULT handles day-level risk.
BLOCKED_HOURS_UTC = set()
ALLOWED_HOURS_UTC = set(range(24)) - BLOCKED_HOURS_UTC
PEAK_HOURS_UTC = {1, 5, 8, 10, 16, 18, 20}  # sizing hint: CONVICTION tier
WARMUP_HOURS_UTC = {2, 3, 6, 7, 11, 13, 14, 15, 22}  # sizing hint: half-size (H17 removed)

# 2026-06-11 (owner: "Trade only in those hours where its profitable"):
# dynamic profit-only hour gate — entries allowed ONLY during UTC hours
# whose recent warehouse history (60d, current mode, whole-trade PnL,
# n>=8) is net-positive, per data/hour_gate_evidence.json `profitable`
# (refreshed weekly by scripts/refresh_hour_gates.py). Fail-open on
# missing/stale(>14d)/empty evidence — an empty list is indistinguishable
# from insufficient data, and a silently-halted bot is worse than an
# ungated one. At ship time the profitable set was {2, 19, 20} (+18.81)
# vs -236 across the other 21 hours. Honest caveat: hour-of-day patterns
# did NOT survive IS/OOS validation (2026-06-02, 0 survivors) — the
# robust effect of this gate is fewer trades / less bleed, not profit.
# Supersedes the 2026-05-27 "hours cleared" decision above for entries;
# static sets stay open (this gate is evidence-file-driven, not static).
# Disable: HOUR_GATE_PROFIT_ONLY=false in .env.
HOUR_GATE_PROFIT_ONLY = os.getenv("HOUR_GATE_PROFIT_ONLY", "true").lower() == "true"

# ── Kronos-inspired temporal awareness (2026-05-27) ────────────
# Day-of-week confidence multiplier. 459-trade data (all-time):
#   Mon 19% WR -$39, Tue 36% WR -$67 → catastrophic
#   Thu 45% WR +$4.28, Sun 51% WR -$6 → best
# Inspired by Kronos foundation model's calendar embeddings (hour/weekday/month).
# Multiplier applied to MCP Brain confidence AFTER scoring. 1.0 = neutral.
# strftime %w: 0=Sun 1=Mon 2=Tue 3=Wed 4=Thu 5=Fri 6=Sat
WEEKDAY_CONFIDENCE_MULT = {
    # 2026-05-27 (UNBLOCK directive): soft sizing hints only, floor at 0.85
    # so weekday never tanks confidence enough to block a valid trade.
    0: 1.00,  # Sun: 51% WR, near-breakeven — neutral
    1: 0.85,  # Mon: 19% WR, -$39 — mild penalty (was 0.50, too aggressive)
    2: 0.85,  # Tue: 36% WR, -$67 — mild penalty (was 0.65)
    3: 0.90,  # Wed: 28% WR, +$0.86 — slight penalty
    4: 1.10,  # Thu: 45% WR, +$4.28 — slight boost (best day)
    5: 1.00,  # Fri: 46% WR, -$2.92 — neutral
    6: 1.00,  # Sat: 43% WR, +$0.59 — neutral
}

# 2026-05-25 — Range-stability / chop filter (no-edge-forensics bundle).
# When True, UniverseFilter rejects dead-range and severe-chop coins at the
# universe stage using daily-candle range-of-change + Kaufman efficiency
# ratio. The bot is a trend engine that bleeds in chop; this is a dynamic
# quality gate (NOT a symbol/hour blacklist — UNBLOCK_ALL-safe). Disable
# here in one line if it over-filters and trade frequency drops too far.
RANGE_STABILITY_FILTER_ENABLED = (
    os.getenv("RANGE_STABILITY_FILTER_ENABLED", "true").lower() != "false"
)

# Chop-gate floor: Kaufman Efficiency Ratio threshold for the range-stability
# filter (core/pair_discovery.py). Symbols whose 10d ER < this are rejected as
# "severe chop". Owner tuning 2026-06-18: lowered 0.20 -> 0.10 via .env to admit
# moderately-choppy recovering majors under the loosened 10d TSMOM lookback.
# Reversible (set back to 0.20). UniverseFilter reads this to override its default.
MIN_TREND_EFFICIENCY = float(os.getenv("MIN_TREND_EFFICIENCY", "0.20"))
if not 0.0 <= MIN_TREND_EFFICIENCY <= 1.0:
    raise ValueError(f"MIN_TREND_EFFICIENCY must be in [0,1], got {MIN_TREND_EFFICIENCY}")

# 2026-05-24 — Kill switch for the 2026-05-22 throughput-raise stack.
# Default ON. Flip to false via env to revert: drops SCALP tier, restores
# the pre-UNBLOCK_ALL blacklist + hour gates, and disables the mcp_brain
# Claude-clamp / blend-fallthrough changes. See core/mcp_brain.py gates.
#
# Used when the reclassified ghost data (Commit 1) shows the SCALP cohort
# is genuinely catastrophic rather than an attribution artifact.
SCALP_TIER_ENABLED = os.getenv("SCALP_TIER_ENABLED", "true").lower() != "false"

# ── Claude-dependence control (owner 2026-06-07: "make the bot less dependent on ClaudeCode") ──
# The live decision path is Claude-PRIMARY (every cycle calls Opus 4.8 via CLI). Measured this
# session: ~5,058 Claude calls/6.5d, ~17% (portfolio path ~27%) time out at 120s; and Claude is
# NO_EDGE just like the algo (corr(mcp_score,realized_pnl) ~ -0.008). So leaning less on Claude cuts
# Max-plan usage + timeout latency without sacrificing edge. Gates BOTH the portfolio-entry and
# position-monitor Claude calls; the existing algorithmic path decides whenever Claude is skipped.
#   "primary"   - Claude every cycle, algo fallback (original behaviour)
#   "throttled" - Claude every CLAUDE_THROTTLE_N-th cycle, algo otherwise  (DEFAULT = less dependent)
#   "off"       - pure algorithmic, no Claude calls at all
# ⚠ Routing more entries to the algo's score>=66 gate MAY lower WR (mid-score buckets ran worse this
# session) — this is cost/robustness, NOT edge. Revert with CLAUDE_PORTFOLIO_MODE=primary; measure
# in PAPER (accept only if cost↓ AND trade-count↓ WITHOUT realized-R↓).
CLAUDE_PORTFOLIO_MODE = os.getenv("CLAUDE_PORTFOLIO_MODE", "throttled").lower()
CLAUDE_THROTTLE_N = max(1, int(os.getenv("CLAUDE_THROTTLE_N", "3")))

# ── Signal source (2026-06-15) ──────────────────────────────────────────────
#   "mcp"     - multi-factor scoring v3.1 / Claude-portfolio path (DEFAULT, unchanged prod)
#   "tsmom"   - long-only time-series-momentum on validated majors (core/tsmom_signal.py).
#             Capital-preservation variant: long while the daily trend is up, else flat;
#             NEVER shorts. Validated 2026-06-15 (reports/tsmom_validation_2026-06-15.md) as a
#             drawdown-halver, NOT a profit engine. PAPER-only research path.
#   "machine" - deterministic detector ensemble (core/machine_signal.py) built from causal
#             OHLCV-only modules: valuation, harmonics, stochastic, ICT variation, Asian
#             range, Dow swing, Bollinger squeeze, and EMA/RSI chart-state detection.
#             PAPER/shadow default until the after-cost OOS gate proves an edge.
# ⚠ tsmom changes ENTRY selection only; bot_engine's scalp EXIT stack (ATR stop / trailing /
#   mcp_take_profit / entry_invalidated) still fires and will clip trends — gate those before
#   trusting PAPER results. Flag is fully reversible: SIGNAL_SOURCE=mcp restores prod.
#   "s3"      - multi-horizon (28/14/7d) long-only momentum ENSEMBLE on the same
#             validated majors (core/tsmom_signal.py:S3EnsembleSignal). Phase-1
#             vertical-slice strategy; tsmom-family exit policy (hold-to-flip).
#             PAPER/sim research path.
#   "mcp_det" - deterministic MCPStrategyScorer (core/mcp_strategy_scorer.py): the
#             rebuilt algorithmic layer that reads approved StrategySpec(s) + market
#             context and emits the entry action-dict with NO Claude/LLM value blended
#             into the score/confidence (LLM demoted to a research-only warehouse note).
#   "none"    - directional entries OFF (Rev 5 carry-first posture, 2026-07-02).
#             analyze_portfolio always returns [] so the bot opens NOTHING itself;
#             the carry runner (scripts/run_f1_carry_paper.py) is the only opener.
#             Engine stays alive: feeds, deterministic SL/TP monitoring, and the
#             spot manager keep managing anything already open.
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
    "min_samples": int(os.getenv("BTC_VOL_MIN_SAMPLES", "24")),  # ~1d warmup (hourly)
    "append_min_interval_sec": 3600,
    "buffer_max": 1000,
}

# 2026-05-27 (454-trade hardening): min hold time gate.
# Trades closed <15 min = 43% of total, 31% WR, -$41.25. Only profitable
# bucket is 15m-1h (64% WR, +$4.41). Guard in position monitor.
MIN_HOLD_CLOSE_MINUTES = int(os.getenv("MIN_HOLD_CLOSE_MINUTES", "15"))
MIN_HOLD_STRONG_CONF = float(os.getenv("MIN_HOLD_STRONG_CONF", "0.80"))

# ==============================================================
# BLACKLISTING
# ==============================================================
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

# ==============================================================
# NEWS MONITORING (24/7 Enhanced Scanner)
# ==============================================================
NEWS = {
    "scan_interval_min": 30,  # Default scan interval (minutes)
    "fast_scan_interval_min": 10,  # When volatility is high (F&G < 20 or > 80)
    "max_headlines": 50,  # Max headlines to keep per scan
    "breaking_news_alert": True,  # Log breaking news at WARNING level
    "sentiment_history_days": 7,  # Days of per-coin sentiment history to keep
}

# ==============================================================
# STRATEGY ENABLE FLAGS — only Trend Pullback + Range Mean Reversion active
# ==============================================================
ENABLE_DCA = False
ENABLE_REBALANCE = False

# ==============================================================
# PARTIAL TAKE PROFIT
# ==============================================================
# 2026-05-19 sweet-spot retune. Captures more wins in the empirically
# proven 30-60min hold-time cell (67% WR, +$10.05 / 33 trades / 30d).
# Lowered first_take_at_pct from 0.5 to 0.35 (fires earlier) and raised
# first_take_size from 0.5 to 0.6 (books larger chunk early), so more
# positions book a small win before deteriorating into the
# 120-240min bleed band (-$23.63 / 30d).
# Rollback: revert both values to 0.5, restart bot. Sub-1-minute reversal.
# Spec: docs/superpowers/specs/2026-05-19-sweet-spot-partial-tp-retune-design.md
#
# 2026-05-25 — first_take_size 0.6 → 0.3. The 5-agent forensic swarm
# (memory: no-edge-forensics-2026-05-25) traced the realized R:R of 0.63
# (vs configured 1.2) to THIS line: booking 60% of every winner at
# first_take_at_pct (= +0.63% on a 1.8% TP = 0.42 R) then breakevening
# the rest mechanically caps blended R at ~0.63 under any realistic
# continuation. avg_win was $0.41 vs avg_loss $0.79 (win/loss ratio
# 0.526). Lowering the early book to 30% lets 70% of each winner ride to
# the full TP, lifting avg_win toward ~$0.65 (expectancy -$0.24 → -$0.13
# per trade). first_take_at_pct stays at 0.35 so the early-win capture in
# the 30-60min cell is preserved, just smaller. Rollback: set back to 0.6.
PARTIAL_TP = {
    "enabled": True,
    "first_take_at_pct": 0.35,
    "first_take_size": 0.3,
    "move_sl_to_breakeven": True,
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
    "enabled": True,
    "drawdown_pct": 0.15,
    "p_win_floor": 0.40,
}

# Rev 5 Phase 5 — Spot S1 defensive allocation (risk control, PAPER-only).
# Opt-in: default OFF so runtime behavior is unchanged until the owner flips it.
# When enabled, SpotPortfolioManager.run_s1_cycle emits REBALANCE
# recommendations to data/spot_recommendations.jsonl — never orders.
SPOT_S1 = {
    "enabled": os.getenv("SPOT_S1_ENABLED", "false").lower() == "true",
}

SPOT_PORTFOLIO = {
    "enabled": True,
    # 2026-05-01 (under-supervision readiness pass): flip to False so
    # SPOT-PROTECT-V1 (peak-DD half/full exits at -25%/-40%) actually
    # places SELL orders. Was True since 2026-04-14 learning-first pivot.
    # User explicitly authorized autonomous defensive sells. The
    # threshold is deep (-25% from peak) so triggers are rare; bot
    # only sells what's already deeply drawn down — not hair-trigger.
    # Hedge_via_futures STAYS off (no perp overlay on spot DD).
    # Sell_on_structure_break STAYS off (we only act on peak-DD).
    "recommendation_only": False,
    "scan_interval_min": 30,
    "cost_basis_method": "average",
    "scale_out_threshold_pct": 0.20,
    "sell_on_structure_break": False,  # disabled — only peak-DD triggers exits
    "hedge_via_futures": False,  # disabled — no derivatives overlay
    "hedge_drawdown_pct": 0.10,
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
    # Learning-first / safety-first: keep the evaluator enabled so it can
    # write transfer opportunities for review, but never call wallet-transfer
    # APIs unless CONTROLLED_LIVE has been explicitly armed and this flag is
    # deliberately changed.
    "enabled": True,
    "recommendation_only": True,  # recommendations only; no real transfers
    "cycle_interval_min": 15,
    "accumulation_threshold_usd": 10.0,
    "spot_targets": {"BTC": 0.50, "ETH": 0.50},
    "hedge_drawdown_pct": 0.10,
    "max_transfer_pct": 0.20,
    "min_transfer_usdt": 5.0,
    "rebalance_threshold_pct": 0.60,
}

# ==============================================================
# STRATEGY GATE — auto-disable underperformers
# ==============================================================
STRATEGY_GATE = {
    "min_win_rate": 0.50,  # disable below 50% WR
    "min_sample_size": 15,  # need 15+ trades before judging
    "fee_alert_ratio": 0.20,  # alert if fees > 20% of gross profit
    "auto_disable_fee_heavy": True,
}

# ==============================================================
# SCALING CONDITIONS — scale risk only after proving edge
# ==============================================================
SCALING = {
    "min_live_trades": 200,
    "min_win_rate": 0.60,
    "max_drawdown_for_scale": 0.10,
    "scale_factor": 1.5,
}

# 2026-05-19 Patch #0 — Ghost-class reroute instrumentation (log-only).
# Wraps the ghost-emit path with a counter that records what Patch #1 WOULD
# have done. No behavior change. Read by scripts/ghost_reroute_report.py
# over a 72h window; results gate whether Patch #1 ships.
GHOST_REROUTE_INSTRUMENT: bool = True

# 2026-05-19 Patch #3 — mcp_take_profit path amplification.
# When a soft-close (STALE/AGE_LIMIT/AGE_LOSS) would fire, first check
# how close the position is to triggering mcp_take_profit. If proximity
# >= threshold, defer the soft-close by MCP_TP_GRACE_SEC and let TP
# fire if it's going to. After grace expires the original soft-close
# fires with `_post_grace` suffix in the exit_reason.
# SL paths (stop_loss / sl_failed / sl_placement_failed) are NEVER gated.
# Rollback: flip MCP_TP_AMPLIFY_ENABLED to False, restart. <1 min.
MCP_TP_AMPLIFY_ENABLED: bool = True
MCP_TP_PROXIMITY_THRESHOLD: float = 0.7
MCP_TP_GRACE_SEC: int = 1800

# 2026-05-19 — User directive: "Don't halt or pause when losing trades."
# HALT_MECHANISMS dict removed 2026-05-27. All nine loss-driven halt/pause
# mechanisms were permanently disabled (all False). Gate checks in
# risk_manager and auto_mutator have been removed to match.
# Per-position SL/TP still placed on every entry. Exchange-side liquidation
# still applies (not bot's control).

# ==============================================================
# DAILY-LOSS CIRCUIT BREAKER (2026-05-28) — opt-in, SOFT, auto-resetting
# ==============================================================
# The user-requested replacement for the removed global halts. This is NOT a
# halt: when today's realized loss exceeds `max_loss_pct` of start-of-day
# balance, RiskManager.can_trade() refuses only NEW entries for the rest of the
# UTC day, then AUTO-RESETS at the day rollover (in lockstep with the existing
# trade-count cap). It does NOT switch OPERATING_MODE, does NOT write
# review_required.json, does NOT stop the process, and does NOT touch existing
# positions (their fail-closed per-trade SLs still protect them). Fails OPEN
# (does not block) if start-of-day balance is unknown.
# Rationale: under the confirmed no-edge regime (research/scalp_edge_finding_
# 2026_05_28.md) the bot bleeds ~$0.25/trade with no portfolio-level floor;
# this caps a single bad day without re-imposing the halt the user removed.
# Default ON at 2% (looser than the old 1% halt — fires on a bad day, not on
# routine bleed). Disable with DAILY_LOSS_BREAKER_ENABLED=false; tune with
# DAILY_LOSS_BREAKER_PCT.
DAILY_LOSS_BREAKER = {
    "enabled": os.getenv("DAILY_LOSS_BREAKER_ENABLED", "true").lower() == "true",
    "max_loss_pct": float(os.getenv("DAILY_LOSS_BREAKER_PCT", "0.02")),
}

# ── PER-POSITION CLOSE/SL LOCK (B7-P2, audit 2026-06-21) — default OFF ──
# When True, OrderManager serializes close_position + _replace_exchange_sl on the
# SAME position id via a per-id RLock (with a 5s timeout backstop + is_position_open
# idempotency). Prevents the 10s SL/TP loop, the 30s MCP monitor, and the portfolio
# cycle from double-closing / cancel-racing one position. LIVE-only value: in PAPER
# close_position and both _replace_exchange_sl helpers issue NO exchange order, so
# the lock is dormant by construction. Default OFF: this is unproven concurrency
# code in the safety-critical exit core; flip ON for a PAPER soak before any live
# flip. RLock is mandatory (fail-closed re-entry) — see the proof in
# reports/ / tasks/todo.md B7-P2.
PER_POSITION_LOCK_ENABLED = os.getenv("PER_POSITION_LOCK_ENABLED", "true").lower() == "true"

# PAPER futures margin model. Venue/instrument metadata wins when it exposes a
# maintenance rate; this conservative fallback is used only when metadata is
# unavailable. It affects simulation and never changes venue leverage/margin.
PAPER_MAINTENANCE_MARGIN_RATE = float(
    os.getenv("PAPER_MAINTENANCE_MARGIN_RATE", "0.01")
)

# ── PORTFOLIO DISCRETIONARY-CLOSE GUARD (A4, audit 2026-06-21) — default OFF ──
# The portfolio-cycle Claude CLOSE -> _execute_close has NO gate beyond OBSERVATION
# mode (no pnl / proximity / source filter) — the un-suppressed twin of the 30s
# monitor CLOSE that was disabled 2026-04-24 after a 1W/17L record. When True, a
# Claude-sourced (source=="claude") CLOSE of a NON-disaster position is refused so
# SL/TP/trailing own the exit; a genuine catastrophic loss still passes (escape
# hatch). The path is LATENT (the OPEN-centric portfolio prompt rarely solicits a
# profitable CLOSE), so default OFF => behavior unchanged. The separate
# is_tsmom_position guard in _execute_close is ALWAYS-ON (tsmom owns its exit).
PORTFOLIO_DISCRETIONARY_CLOSE_GUARD_ENABLED = (
    os.getenv("PORTFOLIO_DISCRETIONARY_CLOSE_GUARD_ENABLED", "false").lower() == "true")

# ==============================================================
# 2026-05-20 GHOST + NOISE CLEANUP + SMALL-TP CAPTURE
# Per-area kill switches for the five-area improvement set.
# Spec: docs/superpowers/specs/2026-05-20-ghost-and-noise-cleanup-design.md
# ==============================================================

# Area 1 — Ghost path accuracy
GHOST_LEDGER_WINDOW_H = 24  # was 6; widen to catch lagged ledger writes
GHOST_PENDING_REQUEUE = True  # two-pass reconcile: ghost_sync upgrades on next sync

# Area 4 — Age-aware SL→breakeven tightening
# 2026-05-24 — Raised min profit threshold from 0.0001 (essentially any
# positive tick) to 0.005 (0.5% profit). Per realized-R diagnostic, the
# original threshold was clipping winners at micro-profit and getting
# wick-knocked at breakeven before reaching configured TP (1.8%).
# Raising the floor lets each winner build a real cushion before its SL
# gets ratcheted to entry.
AGE_AWARE_SL_ENABLED = True
AGE_AWARE_SL_MIN_AGE_MIN = 60  # fire at age >= 60 min
# Profit band [low, high). Was [0.0001, 0.02]; now [0.005, 0.02].
AGE_AWARE_SL_MIN_PNL_FRAC = 0.005  # >= 0.5% (was 0.0001 — too aggressive)
AGE_AWARE_SL_MAX_PNL_FRAC = 0.02  # < 2% (trailing stop owns above)

# Area 5 — Deterministic small-TP capture
# 2026-05-24 — DISABLED per realized-R diagnostic. Memory
# project_trailing_clips_winners_2026_04_21 confirmed: when this fires
# at 1.0% profit, it clips winners at 56% of the SCALP-tier configured
# TP (1.8%). 50 trailing-wins @ $0.57 vs 4 full-TPs @ $2.84 in the
# pre-fix sample — 5× R loss. With this off, trades reach configured
# TP via the normal order_manager.check_sl_tp path, restoring the
# 1.2:1 R:R the SCALP tier was designed for.
AUTO_SMALL_TP_ENABLED = False
AUTO_SMALL_TP_MIN_AGE_MIN = 30  # fire at age >= 30 min (vestigial when disabled)
AUTO_SMALL_TP_MIN_PNL_FRAC = 0.01  # >= +1.0%
AUTO_SMALL_TP_MAX_PNL_FRAC = 0.02  # < +2% (trailing stop owns above)

# ==============================================================
# DATA FEEDS — External enrichment for MCP Brain scoring
# 2026-05-27: New data sources to address anti-predictive score problem.
#
# Architecture: core/data_coordinator.py orchestrates all feeds on
# independent TTLs. Each feed is fail-open (API down = neutral signal).
# mcp_brain.py calls coordinator.get_market_context(coin) once per
# scoring cycle. The coordinator runs feeds in background threads.
#
# Kill switch: set any *_enabled key to False to disable a feed.
# All weights/thresholds are tunable without code changes.
# ==============================================================
DATA_FEEDS = {
    # ── Master switches ────────────────────────────────────────────
    "funding_enabled": True,
    "oi_enabled": True,
    "orderbook_enabled": True,
    "news_enabled": True,
    "smart_money_enabled": True,
    # ── Cache TTLs (seconds) ───────────────────────────────────────
    # Each feed refreshes independently at its own cadence.
    # Staleness threshold = TTL * staleness_multiplier.
    "funding_ttl": 300,  # 5 min (FR settles 3x/day but predicted rate drifts)
    "oi_ttl": 180,  # 3 min (OI updates every ~10s on Binance)
    "orderbook_ttl": 60,  # 1 min (most volatile source)
    "news_ttl": 600,  # 10 min (news doesn't move faster than this)
    "smart_money_ttl": 900,  # 15 min (on-chain aggregate, slow-moving)
    "staleness_multiplier": 2.0,  # >2x TTL = data marked stale, use neutral defaults
    "max_workers": 5,  # ThreadPoolExecutor workers for parallel refresh
    # ── Scoring weights (MCP Brain bonus points) ───────────────────
    # B11: Funding rate alignment bonus
    #   FR z-score aligns with proposed side (extreme neg FR + long = bonus)
    # ⚠ DISABLED 2026-05-30: SCREENED at owner's request (run_edge_sweep.py, 31
    #   syms x 210 8h-bars through 2026-05-30) -> NO_EDGE, weakly ANTI-predictive.
    #   B11's exact construction is the per-symbol funding z-fade (B_ts_zfade):
    #   Sharpe -1.29; cross-sectional fade A_H1 IR +0.26/-0.04 (vs the 0.50 bar);
    #   time-series funding IC mean -0.055, t=-3.70 (significantly negative).
    #   A +7 bonus on an anti-predictive signal was inflating live entry scores.
    #   Mechanism kept; re-enable only if a funding signal clears IR>=0.50.
    "b11_funding_enabled": False,
    "b11_funding_points": 7,  # points awarded when FR aligns
    "b11_fr_zscore_threshold": 1.5,  # |z| must exceed this to qualify
    # B12: OI-price divergence confirmation bonus
    #   "continuation" signal when entering WITH the trend
    # ⚠ DISABLED 2026-05-30: OI-divergence (H5 = sign(dPrice)*dOI) was finally
    #   screened on 28 syms x 100 daily bars (scripts/run_oi_edge_screen.py) and
    #   FAILED stage-1 by a wide margin (IR -0.06 @1d, -0.07 @3d vs the 0.50 bar;
    #   DSR~0). NO_EDGE. Removing the +8 stops noise from inflating entry scores.
    "b12_oi_enabled": False,
    "b12_oi_points": 8,  # points for continuation signal
    "b12_oi_conviction_min": 0.3,  # minimum conviction to award
    # B13: Smart money alignment bonus
    #   Coin in top-20 smart money inflow AND proposed side = buy
    # ⚠ DISABLED 2026-05-30 (owner directive): UNSCREENABLE with available data —
    #   smart_money_feed POSTs Binance Web3 for a CURRENT top-30 inflow ranking
    #   (BSC chain, 24h window): a live snapshot with NO historical time-series,
    #   so it cannot be validated against forward returns. Owner chose to disable
    #   the unproven +5 bonus rather than let it gate entries ungated. Mechanism
    #   kept; to revisit, log the daily ranking forward ~60-90d then screen.
    "b13_smart_money_enabled": False,
    "b13_smart_money_points": 5,  # points when smart money confirms
    # ── VETO gates (block entry, not just reduce score) ────────────
    # V1: OI exhaustion veto — price up + OI down with high conviction
    #   Blocks LONG entries when the move is short-covering, not new money
    # ⚠ DISABLED 2026-05-30: rests on the SAME OI-divergence signal B12 uses,
    #   which screened NO_EDGE (see B12). A veto firing on a falsified signal is
    #   noise, not protection — and blocking longs on noise contradicts the
    #   owner's UNBLOCK_ALL stance. Cost/risk vetoes V2/V3 stay ON. (Re-enabling
    #   trades fewer/more is a turnover knob, not an edge — owner's call.)
    "v1_oi_exhaustion_veto": False,
    "v1_oi_exhaustion_conviction_min": 0.5,
    # V2: News veto — negative-impact news on specific coin in last 2h
    "v2_news_veto": True,
    # V3: Slippage veto — estimated slippage > threshold
    "v3_slippage_veto": True,
    "v3_slippage_max_bps": 30.0,  # max 30 bps slippage
    # V4: Social FOMO veto — extreme hype + positive sentiment = crowded
    #   Reduces position size by multiplier rather than hard block
    "v4_fomo_size_reduction": True,
    "v4_fomo_size_multiplier": 0.5,  # half size on FOMO signal
    # ── B6 orderbook-imbalance bonus ───────────────────────────────
    # ⚠ DISABLED 2026-05-30 (owner directive): the B6 bonus (+7) rewards live
    #   L2 orderbook imbalance + funding direction. It is UNSCREENABLE — L2
    #   depth is a point-in-time snapshot (CoinDesk history paywalled/403, ccxt
    #   snapshot-only), so it can't be validated against forward returns; and
    #   its funding leg screened NO_EDGE/anti-predictive (B11). Top-of-book
    #   imbalance has no documented edge at 15-60min+ holds and is spoofable.
    #   `b6_orderbook_enabled` is the MASTER gate (off => no B6 bonus at all,
    #   enhanced OR legacy). The V3 slippage veto uses the same feed and stays
    #   ON. To revisit, log imbalance forward then screen.
    "b6_orderbook_enabled": False,
    # When B6 is on, `enhanced_b6_enabled` picks the enhanced orderbook feed
    # (imbalance momentum, depth ratio) over the basic Binance depth fetch.
    "enhanced_b6_enabled": True,
    "enhanced_b6_points": 7,  # was 5 in legacy B6
    # ── Short-side filter integration ──────────────────────────────
    # Short side uses STRICTER thresholds on data feed signals
    "short_side_stricter_feeds": True,
    "short_fr_zscore_threshold": 1.0,  # lower bar = more shorts filtered
}

# Convenience: per-feed env-var overrides for operational toggling
# without editing config.py. Set FEED_FUNDING_ENABLED=false etc.
for _feed_key in ("funding", "oi", "orderbook", "news", "smart_money"):
    _env_key = f"FEED_{_feed_key.upper()}_ENABLED"
    _env_val = os.getenv(_env_key, "").strip().lower()
    if _env_val == "false":
        DATA_FEEDS[f"{_feed_key}_enabled"] = False
    elif _env_val == "true":
        DATA_FEEDS[f"{_feed_key}_enabled"] = True
