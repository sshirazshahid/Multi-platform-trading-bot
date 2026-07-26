# 30 — Screen: AccBand frac dual-goal
*Generated: 2026-07-23T20:17:38Z | Verdict: **CONFIRMED_NO_GO***

Prereg hash OK: `True` (md `f5de9f0aff9d…`).
Evidence: published after-cost geometry sim (`05_accuracy_band_sim.md`).

## Joint gates
BE_WR≤0.59 ∧ WR∈[0.59,0.67] ∧ EV>0 ∧ PF>1 ∧ n≥80.

| cell | frac | WR | BE_WR | W/L | EV(R) | PF | joint |
|------|------|-----|-------|-----|-------|----|-------|
| G035 | 0.35 | 0.657 | 0.818 | 0.223 | -0.242 | 0.427 | False |
| G040 | 0.4 | 0.649 | 0.808 | 0.238 | -0.243 | 0.440 | False |
| G045 | 0.45 | 0.638 | 0.796 | 0.256 | -0.244 | 0.452 | False |
| G050 | 0.5 | 0.627 | 0.782 | 0.279 | -0.244 | 0.469 | False |
| G055 | 0.55 | 0.613 | 0.766 | 0.306 | -0.245 | 0.484 | False |
| G060 | 0.6 | 0.598 | 0.749 | 0.336 | -0.246 | 0.499 | False |
| G070 | 0.7 | 0.569 | 0.713 | 0.402 | -0.246 | 0.531 | False |
| G080 | 0.8 | 0.540 | 0.680 | 0.469 | -0.246 | 0.551 | False |
| S_hi | 0.5 b0.55/s0.45 | 0.626 | 0.778 | 0.286 | -0.245 | 0.479 | False |
| S_mid | 0.5 b0.45/s0.35 | 0.650 | 0.805 | 0.243 | -0.243 | 0.451 | False |
| S_wide | 0.6 b0.65/s0.55 | 0.601 | 0.744 | 0.343 | -0.246 | 0.518 | False |
| G050_m40 | 0.5 | 0.627 | 0.782 | 0.279 | -0.244 | 0.469 | False |

## Verdict
**CONFIRMED_NO_GO** — 0/12 cells clear joint gates.
AccBand WR-in-band and after-cost profit are mutually exclusive on the measured no-edge MCP path: every cell has expectancy_r≈-0.24 and breakeven_wr≥0.68. Dual goal requires EDGE, not frac retuning.

## Implication
- Do **not** retune AccBand fracs expecting profit.
- AccBand may still target WR-in-band for research; profit must come from
  validated/evidence lanes (F1 carry, event probes) after promotion gates.
- Mid-band research geometry remains ≈ global 0.50 / buy 0.45 / sell 0.35
  (05 recommendation), with BAND_REGIME_FILTER on.
