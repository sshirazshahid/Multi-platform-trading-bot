# 20 — Integration report: C1 CFTC options-pressure

**Date:** 2026-07-23  
**Result:** NO-OP (no shadow probe added)

## Why nothing was wired

| Stage | Result |
|-------|--------|
| Prereg | Frozen sha256 `2765e26912747c008bd9b97605bfc5b5b7676b2c40ffe9c26a5f9f6e73ec5732` |
| Harvest | 704 FutOnly + 704 Combined CME BTC/Micro rows → `data/cftc_cot/` |
| Screen | `NO_GO` — 0/6 residual variants pass frozen gates |
| Audit | `CONFIRMED_NO_GO` — binding MC capital-preservation fail on best cell |

Best cell `H2_long_on_pos`: after-cost OOS mean +1.0%, WR 60.9%, but Monte Carlo P(profit)=0.886 < 0.95 and maxDD p95=77.6% > 25%. Installing a probe would only measure a capital-unsafe path; charter forbids that.

## What the program still has (unchanged)

Existing log-only shadow fleet + PAPER directional path under `MAX_FLOW_BAND`. No new MCP. No live decision-path diff.

## Artifacts

- `_workspace/strategy_pipeline/20_prereg_c1_cftc_options_pressure.{md,json}`
- `_workspace/strategy_pipeline/20_screen_c1_cftc_options_pressure.{md,json}`
- `_workspace/strategy_pipeline/20_audit_c1_cftc_options_pressure.md`
- `research/screen_cftc_options_pressure.py`
- `scripts/harvest_cftc_tff_btc.py`
- `tests/test_screen_cftc_options_pressure.py` (4 passed)
