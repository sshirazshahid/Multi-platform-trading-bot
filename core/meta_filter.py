"""
core/meta_filter.py — Rule-based quality gate (spec §8, §11).

A deterministic pre-execution filter that turns "the scorer says this is
an entry" into "is this a historically-winning or historically-losing
setup?". V1 is *rules-first* and *interpretable* by design — we cannot
train a logistic model on 168 trades with net-negative expectancy, so
we encode domain guardrails derived from the spec and post-mortem.

The filter returns one of:

    ALLOW  — proceed to execution
    SKIP   — don't execute; log the reason in the warehouse candidate row
    REVIEW — escalate to ClaudeCode pre_trade_review (Phase D)

Meta-filter is ORTHOGONAL to the risk engine. It only sees features —
position sizing, pause rules, and daily-loss caps remain exclusively
with RiskManager (spec §12: "Risk engine has precedence over every
other module").
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from core.features import FeatureVector


Decision = Literal["ALLOW", "SKIP", "REVIEW"]


@dataclass
class MetaDecision:
    decision: Decision
    reason:   str
    size_multiplier: float = 1.0   # meta-filter can only *reduce*, never amplify


# ── Rule thresholds ──────────────────────────────────────────────────
# All percentile thresholds are against the trailing 30-day per-symbol
# window. A SKIP here is never a permanent ban — re-enters automatically
# when the feature distribution drops out of the danger zone.
SPREAD_PCTL_SKIP    = 0.95   # top 5% of spreads in the window
VOL_PCTL_SKIP       = 0.95   # top 5% of ATR% in the window
LOSS_STREAK_SKIP    = 3      # consec losses; paired with low-confidence guard
LOW_CONFIDENCE      = 0.75   # below which a loss streak forces SKIP
OB_IMBALANCE_REVIEW = 0.70   # |imbalance| against trade direction → REVIEW

# Warehouse evidence (May 2026): mcp_score 70-80 bucket netted +$8.2 across
# 54 trades, but the 80+ bucket netted -$7.9 across 46. The "very confident"
# tail is regime-blind / overfit. Pair the high-score signal with vol_pctl —
# top-decile ATR% is the common factor in the worst losers there.
HIGH_SCORE_TRAP_MIN_SCORE = 85.0
HIGH_SCORE_TRAP_VOL_PCTL  = 0.85


class MetaFilter:
    """Stateless, deterministic evaluator.

    Stateless because every input comes from the FeatureVector. Deterministic
    because every threshold is a constant — same vector in, same decision out.
    The *only* memory effect in the pipeline is through the percentile
    windows which live in the warehouse.
    """

    def evaluate(self, fv: FeatureVector, *, side: str = "buy",
                 confidence: float | None = None,
                 mcp_score: float | None = None) -> MetaDecision:
        """Return MetaDecision for this candidate.

        Rules are applied in priority order — first match wins.

        `mcp_score` is the 0-100 algorithmic score from MCPBrain. Optional —
        when supplied, the over-confident-high-vol trap rule fires.
        """
        # ── SKIP: spread in danger zone ──────────────────────────────
        if fv.spread_pctl is not None and fv.spread_pctl > SPREAD_PCTL_SKIP:
            return MetaDecision(
                "SKIP",
                f"spread_pctl={fv.spread_pctl:.2f}>{SPREAD_PCTL_SKIP}",
            )

        # ── SKIP: volatility trap ────────────────────────────────────
        if fv.vol_pctl is not None and fv.vol_pctl > VOL_PCTL_SKIP:
            return MetaDecision(
                "SKIP",
                f"vol_pctl={fv.vol_pctl:.2f}>{VOL_PCTL_SKIP}",
            )

        # ── SKIP: over-confident-high-vol trap (mcp_score 80+ bucket) ─
        if (mcp_score is not None
                and mcp_score >= HIGH_SCORE_TRAP_MIN_SCORE
                and fv.vol_pctl is not None
                and fv.vol_pctl > HIGH_SCORE_TRAP_VOL_PCTL):
            return MetaDecision(
                "SKIP",
                f"mcp_score={mcp_score:.0f}>={HIGH_SCORE_TRAP_MIN_SCORE} "
                f"+ vol_pctl={fv.vol_pctl:.2f}>{HIGH_SCORE_TRAP_VOL_PCTL}",
            )

        # ── SKIP: don't revenge-trade (loss streak + low confidence) ─
        if (fv.recent_loss_streak is not None
                and fv.recent_loss_streak >= LOSS_STREAK_SKIP
                and confidence is not None
                and confidence < LOW_CONFIDENCE):
            return MetaDecision(
                "SKIP",
                f"loss_streak={fv.recent_loss_streak}>={LOSS_STREAK_SKIP} "
                f"+ conf={confidence:.2f}<{LOW_CONFIDENCE}",
            )

        # ── SKIP: regime-hostile setups ──────────────────────────────
        if fv.regime_label == "chop":
            return MetaDecision("SKIP", "regime=chop (4h ADX < 15)")
        if fv.regime_label == "squeeze":
            return MetaDecision("SKIP", "regime=squeeze (4h BB width < 1%)")

        # ── REVIEW: order book strongly opposes trade direction ──────
        if fv.ob_imbalance is not None:
            imb = fv.ob_imbalance
            if side == "buy" and imb <= -OB_IMBALANCE_REVIEW:
                return MetaDecision(
                    "REVIEW",
                    f"ob_imbalance={imb:.2f} bearish vs buy",
                )
            if side == "sell" and imb >= OB_IMBALANCE_REVIEW:
                return MetaDecision(
                    "REVIEW",
                    f"ob_imbalance={imb:.2f} bullish vs sell",
                )

        # ── Default: ALLOW at full size ──────────────────────────────
        return MetaDecision(
            "ALLOW",
            "no_rule_fired",
            size_multiplier=1.0,
        )
