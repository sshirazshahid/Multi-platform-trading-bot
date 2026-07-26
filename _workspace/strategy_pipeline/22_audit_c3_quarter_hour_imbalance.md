# 22 — Audit: C3 quarter-hour opening imbalance (pilot)

**Auditor:** honesty pass (main loop)  
**Date:** 2026-07-23  
**Screen verdict:** NO_GO (matches registered expectation)

## Binding checks

| Check | Result |
|-------|--------|
| Prereg hash frozen before harvest/screen | PASS (`7b33c639…`) |
| Pilot scope BTC+ETH, 2026-04→06 only | PASS (8736 events/symbol) |
| Opening 10s stream-discard harvest | PASS (~2.7GB raw → compact parquet) |
| Residual vs price-volume controls | PASS (ret_1h, ret_4h, log_vol_1h) |
| After-cost (taker+slip+funding) | PASS |
| Frozen gates (DSR/PBO/OOS-WR/MC) | PASS — 0/6 aligned residual variants pass |
| Expansion bar ≥20 bps | FAIL — best **aligned** OOS mean **−18.5 bps** (H12), all horizons negative |
| Delta-drift kill | NOT triggered (raw also NO_GO) |
| Live integration | NO-OP (NO_GO) |

## Findings

1. **Primary kill:** Incremental residual imbalance does not clear costs. Aligned OOS means: H4 **−23.3 bps**, H8 **−33.0 bps**, H12 **−18.5 bps**. WR 0.38–0.47 vs 0.55 floor. MC P(total>0) ≤0.08 on all aligned cells.
2. **Paper null supported:** Effect directionally absent or inverted on post-paper OOS (2026 Q2 pilot), consistent with Kim & Hansen's own warning that medium-horizon content may be spanned by price-volume state variables.
3. **Contrarian H8 residual at −7.0 bps** is the least-negative cell but still below the 20 bps expansion bar and fails WR/MC — not promotable and not the paper-aligned hypothesis.
4. **Overlap / multiplicity:** Joint PBO ≈0.078 (informational); n_trials=6 honored. Non-overlapping per (symbol, horizon) reduces pseudo-replication vs naive event stacking.
5. **Ledger placement:** ADJACENT to refuted hour-of-day/seasonality + formulaic-alpha rows — add scoped refutation for **quarter-hour opening imbalance → 4–12h drift** on Binance majors; does NOT reopen either family.

## Verdict

**CONFIRMED_NO_GO** — drop; no shadow probe. Full 6-symbol / 2025–26 harvest **not authorized** (pilot failed expansion bar by wide margin).

## Artifacts

- `_workspace/strategy_pipeline/22_prereg_c3_quarter_hour_imbalance.{md,json}`
- `_workspace/strategy_pipeline/22_screen_c3_quarter_hour_imbalance.{md,json}`
- `data/aggtrades_qh/` (gitignored harvest)
