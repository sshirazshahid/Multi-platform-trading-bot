"""Shadow probes and deep-breakout lane configuration."""
import os

from config.modes import _AGGRESSIVE_PAPER_RESEARCH

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
    # Absolute USDT move band (2026-07-30 owner deep-research): mid-priced
    # coins moving ~$5–$200 over 1h/24h/7d. 0 / blank = no band filter.
    "abs_move_usdt_min": float(os.getenv("BROAD_UNIVERSE_ABS_MOVE_USDT_MIN", "5")),
    "abs_move_usdt_max": float(os.getenv("BROAD_UNIVERSE_ABS_MOVE_USDT_MAX", "200")),
    "prefer_abs_usdt_rank": os.getenv(
        "BROAD_UNIVERSE_PREFER_ABS_USDT_RANK", "true"
    ).lower() == "true",
    "prefer_crypto_shortlist": os.getenv(
        "BROAD_UNIVERSE_PREFER_CRYPTO_SHORTLIST", "true"
    ).lower() == "true",
}

# ── Tier-geometry time-exit hold (2026-08-03, owner-directed STALE fix) ──────
# Post-AccBand tier geometry (TP 2.0-3.75% vs SL 0.9-1.5%) needs TIME to reach
# either barrier: the first 10 post-fix resolved trades show 4/10 STALE exits
# and 0 full take-profits — the Phase-14-era stale/age cutoffs (tuned when
# edge half-life was ~60min) harvest wide-target positions while still flat,
# exactly the failure measured and fixed for the ACCURACY band on 2026-07-10
# (72h hold precedent). While a position's planned R:R >= min_planned_rr and
# age < max_hold_hours, STALE / AGE_LIMIT / AGE_LOSS defer so first-touch
# SL/TP governs. Zombie protection: past the horizon, time exits fire again.
# NOTE (measurement honesty): activating this resets the post-fix verdict
# cohort epoch — geometry-v1 trades (pre-hold) must never be pooled with v2.
TIER_GEOMETRY_TIME_EXIT_HOLD = {
    "enabled": os.getenv("TIER_TIME_EXIT_HOLD", "true").lower() == "true",
    "max_hold_hours": float(os.getenv("TIER_TIME_EXIT_MAX_HOLD_H", "72")),
    "min_planned_rr": float(os.getenv("TIER_TIME_EXIT_MIN_RR", "1.0")),
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

# ── Bundle-MR shadow probes (owner-directed cloud bundle test, 2026-07-19) ───
# NOT a pipeline GO. The cloud evidence test (paper_bundle_test prereg +
# shortlist): cfg365 (MR-C 4h z-fade, CANDIDATE) FAILED gate G2 — OOS WR 70-71%
# sits ABOVE the frozen 63-67 band — and is a 1-of-432 sweep survivor:
# plausible-unconfirmed. cfg226 (MR-B 4h RSI-2, TRACKER) was in/near band but
# net NEGATIVE — kept to measure the band-vs-profit tension forward.
# ZfadeProbeAgent + Rsi2TrackerProbeAgent propose 4h mean-reversion entries on
# BTC/ETH/SOL/BNB/XRP bybit perps into the shadow lane (EMA200 trend-side
# gate, ATR brackets, 12-bar time-stop, notational 3% stake). Expectation:
# NO-PROMOTE; promotion only via the frozen gate on >=30 RESOLVED per arm plus
# owner sign-off. They place NO orders and only run inside the (already
# log-only) shadow lane, so they change ZERO live behaviour. Off restores the
# pre-probe shadow lane exactly.
BUNDLE_MR_PROBE = {
    "enabled": os.getenv("SHADOW_BUNDLE_MR_PROBE_ENABLED", "true").lower() == "true",
    # match the TsmomProbeAgent data venue (bybit USDT linear perps)
    "venue": os.getenv("SHADOW_BUNDLE_MR_PROBE_VENUE", "bybit"),
}

# ── Pullback-momentum shadow probe (owner-directed forward test, 2026-07-23) ─
# NOT a pipeline GO: textbook pullback-momentum sits inside the REFUTED
# trend/momentum families (0/40 OOS 2026-06-13). PullbackMomentumProbeAgent
# forward paper tests the owner's OWN stated rules (SMA50>SMA200 trend gate,
# close>SMA20, RSI14 cross above 55 entry; RSI14>70 / close<SMA20 / 1.5xATR
# intrabar stop / 42-bar time exits; long-only 4h bybit perps on the
# bundle-MR spec-derived universe; notational 1%-risk sizing) — the
# TsmomProbeAgent precedent. Expectation: NO-PROMOTE. It places NO orders and
# only runs inside the (already log-only) shadow lane, so it changes ZERO
# live behaviour. Off restores the pre-probe shadow lane exactly.
PULLBACK_MOMENTUM_PROBE = {
    "enabled": os.getenv("SHADOW_PULLBACK_PROBE_ENABLED", "true").lower() == "true",
    # match the bundle-MR probe data venue (bybit USDT linear perps)
    "venue": os.getenv("SHADOW_PULLBACK_PROBE_VENUE", "bybit"),
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

