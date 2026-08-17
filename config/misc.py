"""Ghost reroute instrumentation, daily loss breaker, age-aware SL."""
import os

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
# ==============================================================
# EXTERNAL-POSITION ACTIONS (2026-08-18) — opt-in, default OFF
# ==============================================================
# An "external" position (source == "exchange") is one the position monitor
# found on the venue but never opened: the owner's MANUAL futures position or
# spot holding. Phase 39 (2026-05-09) already suppressed EXT CLOSE after it
# accumulated -$20.09 over 44 trades at 30% WR — but EXT TAKE_PROFIT stayed
# live. In CONTROLLED_LIVE that market-closes a manual futures position at
# >=1% PnL / >=70% confidence, or market-SELLS the owner's actual coins at
# >=80% confidence, driven by a score this repo measures as non-predictive
# (mcp_score corr ~= -0.008).
#
# It could never be validated first: DRY_RUN no-ops the whole path, so PAPER
# accrues no evidence either way, and the CONTROLLED_LIVE gate validates
# configuration, not behaviour. Selling the owner's own coins is their call to
# make explicitly, so this defaults OFF rather than being inherited by
# omission. Enable with EXTERNAL_POSITION_ACTIONS_ENABLED=true.
EXTERNAL_POSITION_ACTIONS_ENABLED = (
    os.getenv("EXTERNAL_POSITION_ACTIONS_ENABLED", "false").lower() == "true"
)

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
