# 20 — Audit: C1 CFTC options-pressure

**Auditor role:** honesty-auditor (sequential main-loop; no team debate needed — screen already NO_GO and MC kill is unambiguous)  
**Inputs:** `20_prereg_*.md/json`, `20_screen_*.md/json`, `research/screen_cftc_options_pressure.py`, `data/cftc_cot/manifest.json`  
**Date:** 2026-07-23

## Verdict: CONFIRMED_NO_GO

Screener verdict `NO_GO` is **upheld**. Zero unresolved attacks that would flip the call. No probe integration.

## Findings (severity-ranked)

### F1 — Capital-preservation MC kill on best cell (BINDING)
Best residual variant `H2_long_on_pos` (n_OOS=69): mean +1.00%, WR 0.609, DSR 0.532 (≥0.10), OOS-WR 0.577 (≥0.55), single-col PBO 0.0 — **but** MC `p_total_positive=0.8855 < 0.95` and `max_drawdown_p95=0.776 ≫ 0.25`. Same failure mode class as listing-short equal-notional and breakout deep-run. Not salvageable by re-tuning without a NEW prereg.

### F2 — Publication-lag calendar is assumed, not observed
PRE has no release-timestamp field. Screen uses Friday 15:30 ET + hardcoded US federal holiday list (prereg-disclosed). A holiday-list miss could shift a few entries by 1 day — insufficient to rescue MC maxDD 0.78. Logged as known residual, not NEEDS_WORK for this NO_GO.

### F3 — Per-variant PBO on 1-column is weak; joint PBO used
Joint PBO across 6 variants ≈ 0.103 (passes ≤0.5). Does not change F1.

### F4 — Delta-drift kill did not fire
`delta_drift_kill_any=false` — residual path is the right primary; raw Δ did not uniquely pass where residual failed into a false GO. Good.

### F5 — Horizon-mining check clean
No GO keys; `horizon_mined=false`. H2 is the least-bad cell but still MC-dead; H1 means negative.

### F6 — Charter / ledger
Family was NEW (regulated trader-class positioning). After this screen it becomes a scoped ledger NO_GO. No live-path edits. Prereg sha256 frozen before run: `2765e269…`. Harvest hashes recorded.

## Independent spot-check
Re-read screen JSON gates for H2_long_on_pos: MC passes=false matches printed md. Costs use `config.FEE['binance_futures_taker']` + `SLIPPAGE['pct_open']` + realized funding via `window_funding_covered` — consistent with after-cost-screening skill.

## Disposition
- Integration: **no-op** (see `20_integration_report_c1.md`)
- Ledger: add Refuted row for CFTC asset-manager options-pressure (CME BTC TFF Combined−FutOnly residual, weekly hold, after-cost)
- Next queued: C3 quarter-hour imbalance (measurement pilot) — only after owner continues loop
