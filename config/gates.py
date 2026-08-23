"""Accuracy band, MCP gates, model gate, economic entry gate."""
import os

from config.modes import (
    CONTROLLED_LIVE_ENABLED,
    DRY_RUN,
    OPERATING_MODE,
    PAPER_TRADING_PROFILE,
)


def _adx_demo_cohort_enabled(
    requested: bool,
    operating_mode: str,
    dry_run: bool,
    controlled_live_enabled: bool,
) -> bool:
    """Fail-CLOSED arming guard for the ADX demo cohort (falsification lane).

    2026-08-21, owner-authorised verbally and recorded in
    ``_workspace/strategy_pipeline/89_owner_decision_directional_kill_date.md``
    Section 5, after a four-voice council and a design review that returned
    NO-GO twice.

    WHAT THIS ARMS: lifting the ``regime_toxic_trend`` veto (4h ADX > 30) in
    ``core/scoring/entry_score.py``, which currently refuses 64.6% of all
    candidate evaluations. It lifts THAT VETO ONLY. Every other gate -- risk,
    sizing, liquidation buffer, kill switch, spread, staleness, DRY_RUN --
    still applies in full.

    THE ENV TOGGLE ALONE CAN NEVER ARM IT. All four conditions must hold:
    requested AND OPERATING_MODE == PAPER AND DRY_RUN AND NOT
    CONTROLLED_LIVE_ENABLED. A mode flip disarms it with no code change. This
    matters because a risk veto is being bypassed: a toggle that could do that
    by itself would be a way to disable a safety control by editing one line
    of .env.

    HONESTY (binding, and the owner accepted all of it before authorising):
      * This lane is expected to LOSE MONEY FASTER. That is its PURPOSE. The
        comment above the veto it lifts already records that even the
        SURVIVING 20-30 ADX band is significantly negative (mean -0.1551 per
        trade, 95% CI [-0.2232, -0.0871]); screen 13_band_conditional measured
        all three ADX buckets unprofitable after cost. This lane opens the
        worse band. It restores FLOW, never EDGE.
      * Its sample size is UNKNOWABLE in advance -- lifting the veto only
        reaches the scorer; a candidate must still clear score >= 66 AND
        layers_ok >= 6, and no score has ever been computed for an ADX > 30
        symbol. So it settles NOTHING formally.
      * Its results can NEVER be promotion evidence, may never be pooled into
        a screen, and no amount of its P&L -- good or bad -- authorises real
        capital. A profitable week was agreed IN ADVANCE to mean nothing.
      * Isolation is BEST-EFFORT, not structural, and the owner accepted that
        residual explicitly: demo P&L still lands in the paper-equity figure
        (core/virtual_wallet.py) and the daily count/PnL/WR notification
        (core/order_mgmt/closing.py), because those are keyed only by exchange
        and by close_reason respectively.
    """
    return (
        bool(requested)
        and str(operating_mode).upper() == "PAPER"
        and bool(dry_run)
        and not bool(controlled_live_enabled)
    )


_ADX_DEMO_REQUESTED = (
    os.getenv("ADX_DEMO_COHORT_ENABLED", "false").lower() == "true"
)
ADX_DEMO_COHORT = {
    "enabled": _adx_demo_cohort_enabled(
        _ADX_DEMO_REQUESTED, OPERATING_MODE, DRY_RUN, CONTROLLED_LIVE_ENABLED
    ),
    # Kept separate so an operator can see a toggle was SET but correctly
    # REFUSED (e.g. someone flips it while CONTROLLED_LIVE is armed).
    "requested_enabled": _ADX_DEMO_REQUESTED,
    # The marker written onto a bypassed candidate so its rows stay
    # identifiable downstream. Isolation is best-effort; the MARK is not
    # optional -- without it the lane silently pools with evidence.
    "cohort_tag": "adx_demo_ungated_v1",
}

def _accuracy_geometry_enabled(
    requested: bool, operating_mode: str, paper_profile: str
) -> bool:
    """Profile-gated reactivation guard for the accuracy-band geometry.

    History: the strategy program rejected hit-rate optimization as an
    entry/exit policy and a prior session hardcoded ``enabled: False`` so a
    stale env toggle could not reactivate it. On 2026-07-19 the owner
    explicitly approved reversing that disable for the MAX_FLOW_BAND paper
    cohort (docs/superpowers/specs/2026-07-19-max-flow-band-engine-design.md).
    The guard is therefore GATED, not deleted: ``requested_enabled`` is
    honored ONLY under OPERATING_MODE=PAPER + PAPER_TRADING_PROFILE=
    MAX_FLOW_BAND. Every other profile/mode keeps the disable, and
    CONTROLLED_LIVE can never see the geometry (stronger than before).
    HONESTY (binding): the WR band is delivered by geometry, not edge."""
    return (
        bool(requested)
        and operating_mode == "PAPER"
        and paper_profile == "MAX_FLOW_BAND"
    )


_ACC_GEOMETRY_REQUESTED = os.getenv("ACCURACY_TARGET_MODE", "false").lower() == "true"
ACCURACY_TARGET_MODE = {
    # Profile-gated (2026-07-19): see _accuracy_geometry_enabled docstring —
    # the env toggle alone still cannot reactivate the hit-rate geometry;
    # the owner-approved MAX_FLOW_BAND PAPER profile must also be selected.
    "enabled": _accuracy_geometry_enabled(
        _ACC_GEOMETRY_REQUESTED, OPERATING_MODE, PAPER_TRADING_PROFILE
    ),
    "requested_enabled": _ACC_GEOMETRY_REQUESTED,
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
    # Soft clearance under min_tp_pct (2026-07-29): must clear stressed
    # round-trip (~31.5bps paper_fallback default) so AccBand brackets are
    # not systematically refused by economic_gate_stressed_breakeven.
    # Override via ACCURACY_MIN_TP_COST_PCT.
    "min_tp_cost_pct": float(os.getenv("ACCURACY_MIN_TP_COST_PCT", "0.35")),
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
#
# F3 (2026-07-20 deep audit): both research knobs below were plain env reads
# with NO mode/profile gate, while the sibling geometry knob (T3,
# _accuracy_geometry_enabled above) IS gated PAPER+MAX_FLOW_BAND. A stale env
# entry could therefore lower the floor / disable the SL cooldown in ANY
# mode. Both knobs now deviate from the legacy defaults ONLY under
# OPERATING_MODE=PAPER + PAPER_TRADING_PROFILE=MAX_FLOW_BAND.
def _max_flow_research_knob_enabled(operating_mode: str, paper_profile: str) -> bool:
    """PAPER + MAX_FLOW_BAND gate for the max-flow research knobs (mirrors
    the T3 _accuracy_geometry_enabled pattern)."""
    return operating_mode == "PAPER" and paper_profile == "MAX_FLOW_BAND"


def _profile_gated_entry_floor(raw: str, operating_mode: str, paper_profile: str):
    """Entry-floor override: float(raw) only under the gated profile, else
    None (legacy 66/65 floors)."""
    raw = (raw or "").strip()
    if not raw or not _max_flow_research_knob_enabled(operating_mode, paper_profile):
        return None
    return float(raw)


def _profile_gated_sl_cooldown(raw: str, operating_mode: str, paper_profile: str) -> bool:
    """SL-cooldown flag: the env value is honored only under the gated
    profile, else the legacy default (enabled) is mandatory."""
    if _max_flow_research_knob_enabled(operating_mode, paper_profile):
        return (raw or "true").strip().lower() == "true"
    return True


MCP_ENTRY_MIN_SCORE = _profile_gated_entry_floor(
    os.getenv("MCP_ENTRY_MIN_SCORE", ""), OPERATING_MODE, PAPER_TRADING_PROFILE
)

# ── SL COOLDOWN FLAG (2026-07-19 max-flow band engine, owner-approved) ───────
# Default TRUE = Phase 29 post-SL CooldownPeriod + StoplossGuard unchanged.
# false -> risk_manager.is_sl_cooldown_active returns
# (False, "sl_cooldown_disabled_by_profile") WITHOUT touching the SL ledger
# (note_sl_hit keeps recording), so flipping back to true restores full
# protection from persisted state. PAPER research knob for entry throughput.
# Profile-gated since 2026-07-20 (F3): see _max_flow_research_knob_enabled.
SL_COOLDOWN_ENABLED = _profile_gated_sl_cooldown(
    os.getenv("SL_COOLDOWN_ENABLED", "true"), OPERATING_MODE, PAPER_TRADING_PROFILE
)

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

# ── SMART-MONEY HARD ENTRY GATE (2026-07-24, owner Approach 1) ───────────────
# Escalates Binance Web3 smart-money inflow ranks from MCP bonus B13 into a
# HARD directional entry filter. Honored ONLY under PAPER + MAX_FLOW_BAND
# (same profile gate as AccBand / max-flow knobs). CONTROLLED_LIVE never sees
# this gate as enabled.
# Rules when enabled + feed fresh:
#   buy  → require smart_money_inflow (top-N accumulation list)
#   sell → reject if smart_money_inflow (do not short SM accumulation)
# Stale/missing feed: fail-OPEN by default (aggressive PAPER continues).
# HONESTY: unscreened snapshot; does not create after-cost edge / profit band.
def _smart_money_entry_gate_enabled(
    requested: bool, operating_mode: str, paper_profile: str
) -> bool:
    return bool(requested) and _max_flow_research_knob_enabled(
        operating_mode, paper_profile
    )


_SMART_MONEY_ENTRY_REQUESTED = (
    os.getenv("SMART_MONEY_ENTRY_GATE_ENABLED", "false").lower() == "true"
)
SMART_MONEY_ENTRY_GATE = {
    "enabled": _smart_money_entry_gate_enabled(
        _SMART_MONEY_ENTRY_REQUESTED, OPERATING_MODE, PAPER_TRADING_PROFILE
    ),
    "requested_enabled": _SMART_MONEY_ENTRY_REQUESTED,
    "fail_open_stale": os.getenv(
        "SMART_MONEY_ENTRY_FAIL_OPEN_STALE", "true"
    ).lower()
    == "true",
}


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
#
# MODE (2026-07-21): "strict" (default) = today's fail-closed behavior — a
# promoted model probability is REQUIRED, and none has ever legitimately
# passed promotion, so strict blocks 100% of MCP_DIRECTIONAL_PAPER entries
# (reason=economic_gate_model_missing). "paper_fallback" — honored ONLY under
# OPERATING_MODE=PAPER + PAPER_TRADING_PROFILE=MAX_FLOW_BAND (same F3 gate as
# the entry-floor/SL-cooldown knobs) — skips the model-probability term when
# no promoted model exists and admits iff the bracket's TP clears the SAME
# stressed round-trip costs (geometric breakeven WR < 1.0). It never fakes a
# model; the model path resumes automatically upon a legitimate promotion.
def _profile_gated_economic_gate_mode(
    raw: str, operating_mode: str, paper_profile: str
) -> str:
    """Resolve MCP_DIRECTIONAL_ECONOMIC_GATE_MODE with the F3 profile gate."""
    mode = (raw or "").strip().lower() or "strict"
    if mode not in {"strict", "paper_fallback"}:
        raise ValueError(
            "MCP_DIRECTIONAL_ECONOMIC_GATE_MODE must be 'strict' or "
            f"'paper_fallback', got {raw!r}"
        )
    if mode == "paper_fallback" and not _max_flow_research_knob_enabled(
        operating_mode, paper_profile
    ):
        return "strict"
    return mode


MCP_DIRECTIONAL_ECONOMIC_GATE = {
    "mode": _profile_gated_economic_gate_mode(
        os.getenv("MCP_DIRECTIONAL_ECONOMIC_GATE_MODE", "strict"),
        OPERATING_MODE,
        PAPER_TRADING_PROFILE,
    ),
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


def _profile_gated_accband_research_max_opens(
    raw: str, operating_mode: str, paper_profile: str
):
    """Daily MCP directional PAPER open cap under MAX_FLOW_BAND only.

    Tuition / research budget — not an edge claim. Returns None when the
    profile gate is off (cap disabled). Default 12 opens/UTC-day.
    """
    if not _max_flow_research_knob_enabled(operating_mode, paper_profile):
        return None
    text = (raw or "").strip() or "12"
    try:
        return max(0, int(text))
    except ValueError as exc:
        raise ValueError(
            "ACCBAND_RESEARCH_MAX_OPENS_UTC_DAY must be an integer >= 0"
        ) from exc


# Soft cap on NEW MCP_DIRECTIONAL_PAPER opens per UTC day (PAPER+MAX_FLOW_BAND).
# Does not manage open positions; does not apply to F1 or CONTROLLED_LIVE.
ACCBAND_RESEARCH_MAX_OPENS_UTC_DAY = _profile_gated_accband_research_max_opens(
    os.getenv("ACCBAND_RESEARCH_MAX_OPENS_UTC_DAY", "12"),
    OPERATING_MODE,
    PAPER_TRADING_PROFILE,
)
