# Confidence cannot escalate leverage above STANDARD

- **Date:** 2026-06-06
- **Status:** decided
- **Decider:** owner

## Context
18-agent full-bot audit found the MCP score is non-predictive (corr ≈ -0.008 with outcomes), yet score-based confidence could escalate leverage tiers (STRONG 4x / CONVICTION 5x / AGGRESSIVE 10x).

## Decision
`CONFIDENCE_LEVERAGE_ESCALATION=False` in config.py — only STANDARD and SCALP tiers are reachable; confidence never raises leverage.

## Why
Escalating size on a signal with no predictive power adds variance with no expectancy. Reverses the phase51 escalation change.

## Revisit when
A signal shows statistically positive score→outcome correlation in PAPER (CI excluding 0, n ≥ 100 trades).

## Links
commit d489670; config.py LEVERAGE_TIERS; CLAUDE.md "Configuration" section
