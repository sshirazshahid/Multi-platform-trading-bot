"""Risk management tiers, leverage, and short disable flag."""
import os

from config.modes import MODE_PROFILE

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
