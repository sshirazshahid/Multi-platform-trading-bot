# Viability baseline — 2026-07-30

**Plan:** Deploy Readiness + Edge Viability Loop (autoplan APPROVED)  
**Clock:** ends 2026-09-10 · early check **2026-08-13**  
**extend_used:** false  
**default_pivot:** preservation (EconGate=strict, cease new screens)

## Funnel snapshot (from `data/promotion_funnel.json`)

| Lane | State | Floor | Notes |
|------|-------|-------|-------|
| unlock_short_w1 / w2 | IDLE | 0/30 | per-arm (D3); proposals=0; calendar ≈55.7d OK; **0 future ≥10% cliffs** (D4) |
| listing_short | IDLE (scope) | 0/30 | D1: unanimous `SKIP_NOT_CRYPTO` → not STARVED; 0 crypto-native ENTERs |
| f1_carry | IDLE | 0/30 | best_edge_bps=0 all sample venues; entries_48h gate evals high, no positive edge |
| directional_paper_cohort | IDLE | 0/30 | expected under strict econ gate |

Baseline row above for listing/unlock was the pre-D1/D3 snapshot (listing STARVED / pooled unlock_short). Post-fix taxonomy is IDLE/scope + per-arm unlock; accrual counts unchanged.

## GO definitions (locked)

- **F1 GO:** ≥5 independent after-cost-positive carry episodes in window
- **Probe GO:** frozen `promotion_gate` PASS on a **named arm** + dossier staged + owner sign-off (dossier alone ≠ GO)
- **Week-2 early exit:** if unlock still 0 proposals AND listing still STARVED/0 actionable AND F1 still 0 positive → owner CONTINUE/PIVOT/STOP

## Next engineering (Week 0)

D1 listing STARVED classification · D2 listing dossier funnel · D3 unlock per-arm · T3/T7/T2/T1 measurement trust
