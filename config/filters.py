"""Trading gates, expectancy filter, hour gates, universe flow."""
import os

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

# 2026-07-27 — Universe Flow Loosen V1 (Approach 1 / hybrid bar).
# Mild temporary PAPER flow loosen for UniverseFilter only. Band regime +
# economic gate stay untouched. After 7 days run
# scripts/review_universe_flow_loosen.py → KEEP or REVERT.
# Spec: docs/superpowers/specs/2026-07-27-universe-flow-loosen-design.md
UNIVERSE_FLOW_LOOSEN_V1 = (
    os.getenv("UNIVERSE_FLOW_LOOSEN_V1", "false").lower() in {"1", "true", "yes"}
)
UNIVERSE_FLOW_LOOSEN = {
    "enabled": UNIVERSE_FLOW_LOOSEN_V1,
    "max_spread_pct": float(os.getenv("UNIVERSE_LOOSEN_MAX_SPREAD_PCT", "0.0075")),
    "min_depth_usd": float(os.getenv("UNIVERSE_LOOSEN_MIN_DEPTH_USD", "1200")),
    "min_range_of_change": float(
        os.getenv("UNIVERSE_LOOSEN_MIN_RANGE_OF_CHANGE", "0.015")
    ),
    "min_trend_efficiency": float(
        os.getenv("UNIVERSE_LOOSEN_MIN_TREND_EFFICIENCY", "0.12")
    ),
    "review_after_days": float(os.getenv("UNIVERSE_FLOW_LOOSEN_REVIEW_DAYS", "7")),
    "cohort_path": os.getenv(
        "UNIVERSE_FLOW_LOOSEN_COHORT_PATH", "data/universe_flow_loosen_cohort.json"
    ),
}

# 2026-05-24 — Kill switch for the 2026-05-22 throughput-raise stack.
# Default ON. Flip to false via env to revert: drops SCALP tier, restores
# the pre-UNBLOCK_ALL blacklist + hour gates, and disables the mcp_brain
# Claude-clamp / blend-fallthrough changes. See core/mcp_brain.py gates.
#
# Used when the reclassified ghost data (Commit 1) shows the SCALP cohort
# is genuinely catastrophic rather than an attribution artifact.
