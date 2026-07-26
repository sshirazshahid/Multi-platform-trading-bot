---
name: trading-risk-management
description: Audit or implement fail-closed risk gates for this crypto futures bot, including position sizing, leverage and exposure caps, startup reconciliation, exchange-side protection, kill switches, and CONTROLLED_LIVE authorization. Use for any order-approval, capital-at-risk, margin, drawdown, flatten, risk-state, or live-readiness change; trigger on requests such as "size this trade", "raise leverage", "allow more positions", or "make live trading safe".
---

# Trading Risk Management

## Overview

Treat risk as an authorization boundary, not a strategy preference. Strategies may propose an entry; only the centralized risk/order path may approve and transmit it. Preserve safe defaults and reject missing, stale, contradictory, or unverifiable state.

For this repository, the initial `CONTROLLED_LIVE` ceiling is:

- Effective leverage: at most **1.0x**.
- Concurrent positions: at most **1** across all venues and strategies.
- Stop-defined risk: at most **0.1% of current equity per trade**.
- Aggregate gross notional: at most **2% of current equity**.

These are ceilings, not targets. Do not loosen them because a backtest, a small live trial, or raw win rate looks favorable. Any change requires a new evidence review, tests, owner authorization, and a synchronized signed checklist.

## When to Use

- Review or change order approval, quantity, leverage, margin, exposure, or stop logic.
- Add or verify daily-loss, drawdown, anomaly, clock-drift, or data-staleness breakers.
- Audit startup reconciliation, missing protection, orphan orders, or crash recovery.
- Decide whether a strategy may enter `CONTROLLED_LIVE`.
- Investigate an unexpected position, fill, liquidation risk, or risk-state mismatch.

Do not use this skill to claim profitability or to authorize live trading. A passing audit proves only that the supplied risk contract is internally safe enough for the next review gate.

## Prerequisites

- Work from the repository root with Python 3.9+ and the project environment installed.
- Read `config.py`, `core/live_gate.py`, `core/risk_manager.py`, `core/risk_governor.py`, `core/portfolio_risk.py`, and the exchange/order path before editing.
- Read `docs/CONTROLLED_LIVE_CHECKLIST.md`; treat stale or contradictory checklist values as a live blocker.
- Obtain current equity, open positions, open entry/protection orders, exchange filters, and server-time freshness from read-only sources.
- Keep `OPERATING_MODE=PAPER` and `CONTROLLED_LIVE_ENABLED=false` during development and tests. Never sign the owner checklist or send a live order on the user's behalf.
- Read `references/risk-contract.md` when evaluating live readiness or changing any limit.

## Workflow

1. **Map every order path.** Trace strategy intent through policy, risk approval, order manager, adapter, acknowledgement, fill, and protection. Fail the review if any adapter reference or direct order call bypasses the central gate.
2. **Freeze the live profile.** Verify the 1.0x leverage, one-position, 0.1%-equity risk, and 2%-equity gross ceilings at configuration, runtime gate, and order-submission layers. Use the strictest value when layers disagree.
3. **Size from a valid stop.** Compute `risk_amount = fresh_equity * 0.001`, then `raw_qty = risk_amount / abs(entry - stop)`. Quantize downward. Recompute post-quantization risk and notional, including fees, slippage, and funding. Reject invalid stop distance, stale equity, minimum-size conflicts, or cap breaches; never move the stop to make a desired size fit.
4. **Aggregate before approval.** Include filled positions, partially filled entries, open entry orders, correlated same-direction exposure, and every venue. Reserve risk when an order is accepted so concurrent signals cannot race past the caps.
5. **Reconcile before entries.** After all live authorization and read-only preflight gates pass, fetch positions and open orders, adopt only provably bot-owned intents, cancel unsafe bot-owned entries, and verify exchange-native reduce-only stop protection for every position. Persist `protection_pending` before placement and clear it only after read-back verification.
6. **Enforce breakers durably.** Persist soft halt, flatten, anomaly, and manual-reset state atomically. Stale market/account data, excessive clock drift, dead private streams, unresolved positions, or failed protection must pause new entries. Flatten independently per venue only through an explicitly authorized, idempotent emergency path.
7. **Test hostile cases.** Cover zero/negative stop distance, exchange minimums, partial fills, concurrent approval races, stale equity, clock drift, restart during protection, orphan positions/orders, failed cancel/flatten, and duplicate retries. Assert no live-network calls in unit tests.
8. **Validate the contract manifest.** Create a review manifest using `references/risk-contract.md`, then run:

```bash
python skills/trading-risk-management/scripts/validate_risk_contract.py \
  --input reports/risk/risk-contract.json \
  --output-dir reports/risk
```

9. **Gate the result.** Treat any validator error, failed test, stale checklist, missing reconciliation evidence, or unverified protection as `BLOCKED`. A pass does not arm live execution; it is one input to the separate owner-controlled promotion process.

## Output

Produce:

- `reports/risk/risk-contract-report.json` with deterministic pass/fail checks.
- A concise audit listing inspected order paths, cap values, reconciliation/protection evidence, test commands, failures, and remediation owners.
- An explicit final status: `PASS_FOR_REVIEW` or `BLOCKED`; never `LIVE_APPROVED`.
- Tests for every modified risk boundary and restart/failure case.

Do not output or log credentials, full balances, signed checklist text, or commands that arm `CONTROLLED_LIVE`.

## Resources

- `references/risk-contract.md` - manifest schema, invariants, and rejection rules. Read for every live-readiness audit.
- `scripts/validate_risk_contract.py` - offline deterministic contract validator; it never imports bot runtime code or contacts an exchange.
- `scripts/tests/test_validate_risk_contract.py` - validator regression tests.
- Repository gates: `core/live_gate.py`, `core/risk_manager.py`, `core/risk_governor.py`, `core/portfolio_risk.py`.
- Repository safety tests: `tests/test_live_risk_caps.py`, `tests/test_live_startup_hardening.py`, `tests/test_operating_modes.py`, and `tests/test_paper_no_live_writes.py`.
