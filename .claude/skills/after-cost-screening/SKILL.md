---
name: after-cost-screening
description: How to build and run an honest, pre-registered, after-cost strategy screen on THIS bot's local data (warehouse, harvested funding, OHLCV backfills) with the frozen promotion gates. Use whenever screening or backtesting a strategy candidate, evaluating "does X have edge", validating a signal, or turning a research idea into a GO/NO_GO verdict. Required reading before writing any screen script.
---

# After-Cost Screening

## Pre-registration (before any code runs)
Write into the verdict file FIRST: hypothesis, universe, sample period, cost model, gate thresholds, and what NO_GO looks like. Moving thresholds after seeing results invalidates the screen — that is the overfitting front door this repo has spent months closing.

## Local data map
- `data/warehouse.sqlite` — trades (incl. mfe/mae as of C7), candidates, shadow_decisions, shadow_outcomes (incl. ltp_* counterfactual fields), trade_events. Read-only interrogation: `warehouse_reader.py` or the `trading_bot_*` MCP tools.
- Funding/OI history — `scripts/harvest_funding_carry.py` outputs + the keyless Binance daily downloader (scheduled since 2026-06-29); the F1 lane's own history in `data/`.
- OHLCV — `scripts/backfill_universe_ohlcv.py`, `scripts/backfill_perps_ohlcv.py` outputs. Listing dates are derivable locally as first-candle timestamps (the FMZQuant method).
- Missing data → verdict INSUFFICIENT_DATA naming the exact harvest command. Never synthesize data.

## Cost model (charge ALL of it)
- Fees per venue from `config.FEE` (authoritative): futures maker ~1–2bps / taker ~5–7.5bps per side.
- Slippage convention: 5bps open/close, 10bps stop (+0.5× observed spread where the sim models it).
- Funding: settlement-aligned, charged to the side that pays. Shorts on negative-funding tokens PAY — this kills naive listing/unlock shorts; model it from realized funding history, not averages.
- Touch ≠ fill for any resting-limit assumption (see `core/shadow_resolver.limit_tp_counterfactual` for the honest model).

## Gates (frozen — never loosen)
- `core/promotion_gate.py`: MIN_DSR≥0.10, MAX_PBO≤0.5, OOS-WR≥0.55 (+AUC≥0.60 for model gates). NaN fails closed.
- Walk-forward: `core/walk_forward.py` (TimeSeriesSplit + embargo + purge).
- Monte Carlo: `core/decision/monte_carlo.py` (block bootstrap; P(total>0)≥0.95, maxDD p95≤0.25).
- Report DSR/PBO against the TRUE number of variants tried, including abandoned ones.

## Output convention
`reports/<screen>_<date>.md` + JSON verdict `{candidate, hypothesis, n, after_cost_metrics, gates, verdict: GO|NO_GO|INSUFFICIENT_DATA}`; workspace copy at `_workspace/strategy_pipeline/02_screener_verdicts.md`. TDD any new script (tests in `tests/`, script in `research/` or `scripts/`). One screen = one commit on a research branch; never push unasked. NO_GO verdicts get a new row in `refuted-families-ledger`.
