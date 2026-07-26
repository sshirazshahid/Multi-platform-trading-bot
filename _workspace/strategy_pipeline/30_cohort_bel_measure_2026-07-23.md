# 30 — MAX_FLOW_BAND cohort BE_WR measurement
*Generated: 2026-07-23T17:54:47Z | Source: warehouse + goal_progress + heartbeat*

## Verdict

**Dual goal not yet mathematically possible on measured payoffs.** Every window fails `breakeven_wr ≤ 0.59`. Cohort and 7d books need ~0.70 win/loss ratio for a 59% break-even; measured ratios are **0.49 (cohort)** and **0.43 (7d)**.

Machine-readable: [`30_cohort_bel_measure_2026-07-23.json`](30_cohort_bel_measure_2026-07-23.json)

## Profile / knobs in force

| Knob | Value |
|------|-------|
| Mode / profile | PAPER / MAX_FLOW_BAND |
| Cohort epoch | 1784809530.65 (post-halt-fix reboot) |
| `ACCURACY_TP_FRAC_OF_SL` | 0.40 |
| `ACCURACY_TP_FRAC_BUY` / `SELL` | 0.35 / 0.30 |

## Primary windows

| Window | n | WR | avg_win | avg_loss | W/L | BE_WR | clears ≤0.59? | EV | PF |
|--------|---|----|---------|----------|-----|-------|---------------|----|----|
| Cohort since profile | 25 | 20.0% | 0.199 | 0.406 | 0.491 | **0.671** | NO | −0.285 | 0.12 |
| Rolling 7d | 66 | 37.9% | 0.154 | 0.357 | 0.430 | **0.699** | NO | −0.164 | 0.26 |
| Rolling 30d | 832 | 31.5% | 0.378 | 0.606 | 0.624 | **0.616** | NO | −0.296 | 0.29 |

Needed W/L for BE_WR=0.59: **≥ 0.695**. Gap: cohort needs ~+41% relative win size (or smaller losses).

## Goal lanes (same session)

All mature directional lanes: `NEGATIVE_AFTER_COST_ECONOMICS`. Profile cohort: `INSUFFICIENT_SAMPLE` (n=22).

## Exit / fill notes (cohort)

- SL 18 / −$7.93 vs TP 7 / +$0.80 — stop dollars dominate.
- MAE avg 0.43% > MFE avg 0.29% — adverse selection.
- Maker vs taker_fallback both negative in this immature cohort (n small; prior autopsy still favors maker over chase).

## Implication for Phase A frac sweep

Any pre-registered frac/min_tp cell must jointly clear:

1. measured `BE_WR ≤ 0.59`
2. realized WR ∈ [0.59, 0.67]
3. after-cost expectancy > 0 and PF > 1

Current frac 0.30–0.40 compresses TP enough that (1) fails even before WR recovers into band. Sweep must explore **higher** fracs (wider TP) until W/L clears 0.695 — accepting that theoretical geometry WR may fall; joint gates decide.

## Success criteria status

| Criterion | Status |
|-----------|--------|
| WR ∈ [0.59, 0.67] | FAIL (cohort 20%, 7d 38%) |
| expectancy > 0 | FAIL |
| PF > 1 | FAIL |
| BE_WR ≤ 0.59 | FAIL |
