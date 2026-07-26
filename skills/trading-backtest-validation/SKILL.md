---
name: trading-backtest-validation
description: Validate crypto futures strategies and models with point-in-time data, deterministic replay, realistic execution costs, walk-forward tests, overfit diagnostics, artifact integrity, and mature shadow evidence. Use when backtesting, tuning, comparing, promoting, or retiring a strategy/model, or when someone asks whether performance, win rate, a model pointer, or a paper result is trustworthy enough for CONTROLLED_LIVE review.
---

# Trading Backtest Validation

## Overview

Try to falsify the edge. A profitable chart, high raw win rate, or brief live trial is not evidence of deployable expectancy. Promotion requires reproducible after-cost results, point-in-time feature integrity, stable out-of-sample behavior, valid model/replay artifacts, and mature forward shadow evidence.

This skill never authorizes live orders. The next gate remains the fail-closed `CONTROLLED_LIVE` risk and owner-signoff process: at most 1.0x effective leverage, one concurrent position, 0.1% equity risk per trade, and 2% gross exposure.

## When to Use

- Build or review historical simulations, execution/fill models, and replay pipelines.
- Tune features or parameters, compare strategies, or diagnose apparent performance.
- Validate a trained model, manifest, checksum, pointer, promotion gate, or fallback path.
- Decide whether a strategy may enter or leave mature shadow observation.
- Investigate divergence between replay, paper/shadow, testnet, and realized fills.

Do not use it to optimize against the final holdout, convert raw win rate into a profitability claim, or justify "trying it small" with real capital.

## Prerequisites

- Work from the repository root with the project environment installed.
- Freeze the strategy hypothesis, eligible universe, features, parameters, timeframes, entry/exit rules, cost model, and rejection thresholds before opening the final holdout.
- Use immutable, exchange-specific, point-in-time data with timestamps, gap reports, listing/delisting history, funding, and contract metadata.
- Read `core/backtester.py`, `core/cost_model.py`, `core/promotion_gate.py`, `scripts/machine_strategy_replay.py`, and relevant tests before changing validation logic.
- Read `references/evidence-contract.md` before preparing a promotion manifest.
- Keep all forward testing in shadow/PAPER or exchange demo/testnet. Never use real-money outcomes as an experiment.

## Workflow

1. **Pre-register the claim.** Record the strategy hash, configuration hash, data snapshot/hash, universe rule, target, holding horizon, cost assumptions, metrics, and pass/fail thresholds. Mark every exploratory comparison; do not quietly promote the best of many trials.
2. **Audit temporal integrity.** Prove features and labels use only information available at decision time. Test closed-candle boundaries, exchange receive/event timestamps, joins, resampling, funding publication times, delistings, and symbol survivorship. Reject any unresolved lookahead or timestamp ambiguity.
3. **Simulate executable fills.** Apply venue/tier fees on both sides, historical funding, spread, latency, slippage/impact, tick/lot/min-notional filters, partial fills, maker queue uncertainty, liquidation/margin rules, and reduce-only exits. For unresolved candles spanning TP and SL, use the adverse ordering or lower-timeframe evidence. A touch is not automatically a limit fill.
4. **Separate time correctly.** Preserve chronological train/validation/test partitions. Run at least four walk-forward out-of-sample segments across materially different volatility/liquidity regimes. Keep one untouched final holdout and never tune after seeing it.
5. **Measure economics and uncertainty.** Report net expectancy, profit factor, Sharpe/Sortino with stated annualization, drawdown magnitude/duration, tail loss, turnover, exposure, fee/funding/slippage drag, MAE/MFE, and sample size. Report raw win rate only alongside payoff distribution and after-cost expectancy.
6. **Test selection bias and stability.** Compute or document deflated Sharpe and probability of backtest overfitting where applicable. Perturb parameters, entry timestamps, costs, and universe membership; bootstrap/Monte Carlo the out-of-sample trade sequence. Reject edges dependent on one symbol, window, fill assumption, or parameter point.
7. **Prove replay and model integrity.** Re-run identical inputs in a clean process and require identical decision/result hashes. If a model is used, require a valid `ModelManifest`, artifact checksum, feature schema/order, time split, age limit, and atomic latest-pointer update. The live model gate must fail closed; silent rule fallback cannot satisfy a model-backed promotion.
8. **Accumulate independent mature shadow evidence.** Observe continuously for **30-60 days** and require at least **100 independent matured outcomes** produced by the production decision path. Use 60 days for low-frequency strategies or when 30 days does not cover the preregistered regimes. Deduplicate correlated re-emissions and exclude unresolved outcomes. Testnet validates plumbing; shadow market outcomes validate the signal.
9. **Compare forward divergence.** Attribute differences in signals, fills, fees, funding, latency, protection, and availability. Any unexplained material divergence, naked-position incident, replay mismatch, or artifact-integrity failure blocks promotion and restarts the affected evidence window.
10. **Validate the evidence manifest.** Populate `references/evidence-contract.md`, then run:

```bash
python skills/trading-backtest-validation/scripts/validate_evidence_manifest.py \
  --input reports/validation/evidence-manifest.json \
  --output-dir reports/validation
```

11. **Decide without shortcuts.** Return `PASS_FOR_REVIEW` only when every mandatory gate passes. Otherwise return `BLOCKED` with exact failed checks. Do not compensate for a failed gate with a high win rate, high in-sample score, discretionary judgment, or a small live allocation.

## Output

Produce:

- `reports/validation/evidence-contract-report.json` with deterministic failures and non-authorizing status.
- A versioned validation report containing hashes, date ranges, sample construction, all attempted variants, after-cost metrics, uncertainty intervals, regime/symbol contribution, and gate decisions.
- Replay artifacts sufficient to reproduce decisions from immutable inputs.
- A promotion decision of `PASS_FOR_REVIEW` or `BLOCKED`, never `LIVE_APPROVED`.

Do not overwrite source datasets, mutate the final holdout, hide failed trials, or publish credentials/account identifiers.

## Resources

- `references/evidence-contract.md` - required evidence manifest and interpretation rules.
- `scripts/validate_evidence_manifest.py` - offline deterministic evidence gate.
- `scripts/tests/test_validate_evidence_manifest.py` - evidence-gate regression tests.
- Repository implementation: `core/backtester.py`, `core/cost_model.py`, `core/promotion_gate.py`, `scripts/machine_strategy_replay.py`.
- Repository tests: `tests/test_machine_strategy_replay_integrity.py`, `tests/test_model_manifest.py`, `tests/test_promotion_gate.py`, and `tests/test_promotion_gate_honest.py`.
