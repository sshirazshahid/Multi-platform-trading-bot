---
name: futures-universe-edge-research
description: Screen broad centralized-exchange USDT perpetual-futures universes and turn mover observations into causally valid, promotion-safe research evidence. Use when asked to scan hundreds or thousands of perpetuals, investigate hourly/daily/weekly movers, design futures strategies, repair a futures backtest, compare candidate edges, or decide whether PAPER/SHADOW evidence is ready for manual promotion review.
---

# Futures Universe Edge Research

## Overview

Build evidence, not trading promises. Separate cheap all-contract screening from expensive feature work, require point-in-time futures data, and keep every candidate in RESEARCH/PAPER/SHADOW until an untouched validation and mature shadow cohort pass the fail-closed gate.

## When to Use

Use this skill for:

- broad USDT perpetual discovery across one or more venues;
- hourly, daily, or weekly positive and negative mover research;
- funding/basis carry, cross-sectional momentum/reversal, liquidation-conditioned reversal, or volatility-breakout hypotheses;
- futures replay, walk-forward, leakage, survivorship, cost, or promotion audits;
- requests to improve prediction accuracy or profitability using new agents, skills, or research automation.

Do not use it to enable LIVE, place orders, tune against a final holdout, or claim guaranteed profitability.

## Prerequisites

- Read [`references/universe_and_evidence_contract.md`](references/universe_and_evidence_contract.md) before changing universe, replay, or promotion logic.
- Confirm `OPERATING_MODE` and `ENTRY_POLICY` without printing secret values.
- Keep live-entry authorization unchanged. Run data collection and replays offline or in PAPER/SHADOW.
- Use the repository virtual environment and existing exchange adapters. Reuse exchange instances and batched all-ticker endpoints.
- Record observed/event time separately from local received time. Only closed bars may generate bar-based decisions.

## Workflow

### 1. Establish the empirical baseline

Query mature, closed PAPER/SHADOW outcomes and group them by strategy, venue, symbol, side, regime, and exit type. Deduplicate repeated decisions from the same setup. Report fees, spread, slippage, funding, partial fills, unresolved outcomes, and confidence intervals. If evidence is negative or right-censored, state `NO_EDGE` and preserve the safety latch.

### 2. Build the point-in-time contract universe

For every venue, persist active and inactive linear USDT perpetual contracts with listing/delisting time, base/quote/settle, contract multiplier (including `1000`-prefixed contracts), tick/lot/min-notional rules, and margin tier when available.

Fetch all tickers once per venue per refresh. Filter on freshness, listing age, executable quote volume, spread, side-specific depth, and data completeness. Rank both winners and losers by percentage return, ATR/volatility surprise, volume anomaly, and executable dollar capacity. Never rank candidates primarily by absolute USDT price change.

### 3. Shortlist before expensive work

Keep the full universe in cheap snapshot storage. Deduplicate the top and bottom names across 1h, 24h, and 7d horizons into a bounded shortlist. Only then fetch OHLCV, funding, open interest, mark/index, liquidation, and book history or invoke models/LLMs. The shortlist is observational; it must not mutate the live execution universe.

### 4. State a falsifiable hypothesis

Specify the economic mechanism, exact event clock, entry decision, next-event fill rule, exit/abstention logic, capacity constraint, expected holding period, and regimes where the edge should disappear. Prefer a small set of hypothesis families with a plausible source of return over large indicator grids.

### 5. Run an honest futures replay

Use genuine futures files/metadata only. Include funding settlements, basis/mark/index, contract changes, delistings, spread, side-specific depth, fees, slippage, rejects, partials, latency/outages, margin tiers, and liquidation. Place all venues/symbols on one chronological portfolio queue. Decide at event `t`; fill at the next eligible event or recorded book snapshot.

Use anchored or rolling walk-forward folds with purge and embargo. Reserve symbol, time, and venue holdouts plus one untouched final holdout. Track every tried configuration for multiple-testing correction. Never reuse the final holdout to choose thresholds.

### 6. Stress and reject aggressively

At minimum test 1x, 1.5x, and 2x fee/spread/slippage/funding assumptions, delayed fills, missing data, exchange outage, symbol delisting, and correlated simultaneous signals. Require a stable parameter plateau rather than one isolated optimum. Apply DSR/FDR and aligned CSCV-PBO to the same chronological return units.

### 7. Validate the evidence artifact

Create JSON matching the contract reference. From the repository root, run the validator with the repository environment. For Git Bash:

```bash
venv/Scripts/python skills/futures-universe-edge-research/scripts/validate_candidate_evidence.py --input path/to/candidate.json
```

For PowerShell:

```powershell
venv\Scripts\python skills\futures-universe-edge-research\scripts\validate_candidate_evidence.py --input path\to\candidate.json
```

The validator reads one evidence file and emits its result to standard output only. It performs no filesystem writes and never promotes a strategy. Exit code 2 means invalid or `RESEARCH_ONLY`; exit code 0 means only that the evidence is eligible for SHADOW or separate manual review. Treat `RESEARCH_ONLY` as a hard stop. `ELIGIBLE_FOR_MANUAL_CONTROLLED_LIVE_REVIEW` still requires repository live gates and explicit human approval.

### 8. Forward test before promotion

Require 30-60 days of fully matured, event-deduplicated SHADOW evidence and at least 100 independent resolved setups, preferably 500 for intraday strategies. Operational mismatches, naked-position time, unmodeled fills, or stale-data decisions must be zero before manual review.

## Output

Return or persist:

- universe snapshot counts, rejection counts/reasons, and bounded multi-horizon shortlist;
- a candidate specification with economic rationale and abstention states;
- data lineage and event-time coverage, including delisted contracts;
- chronological walk-forward/holdout results with stressed costs and uncertainty;
- concentration, capacity, regime, and parameter-stability tables;
- a validator result containing `decision`, `failures`, `warnings`, and observed metrics;
- an explicit deployment state: `RESEARCH_ONLY`, `PAPER`, `SHADOW`, or manual-review eligible.

Never output an instruction that silently enables live execution.

## Resources

- [scripts/validate_candidate_evidence.py](scripts/validate_candidate_evidence.py) - deterministic, stdlib-only fail-closed evidence checker.
- [references/universe_and_evidence_contract.md](references/universe_and_evidence_contract.md) - required universe, replay, validation, and shadow fields.
