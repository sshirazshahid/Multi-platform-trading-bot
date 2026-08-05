# 46 — S1 adjudication dossier: lane `pullback_ma20_4h` (probe #7, arm `pullback_ma20_rsi14_4h_v1`)

*Date: 2026-07-30 | Loop iteration per `19_dual_model_loop_protocol.md` S1 | Shared dossier — both models receive THIS file only; verdicts are independent.*

## Lane identity

- Owner's stated strategy ("use what I have said", 2026-07-22): SMA50>SMA200 trend filter; entry close>SMA20 ∧ RSI14 cross↑55; exits RSI14>70 or close<SMA20; stop entry−1.5×ATR14; 4h bybit, 43-symbol universe.
- Probe: `core/agents/pullback_momentum_probe_agent.py`, log-only, frozen score tanh((RSI14−55)/15), never re-tuned. Zero selection applied post-launch.
- Ledger status at launch: family REFUTED (textbook trend 0/40 OOS 2026-06-13; indicator-confluence 2026-06-08); probe recorded as owner-directed forward measurement, expectation NO-PROMOTE, adverse anchor arXiv 2606.00060 (momentum +31.16% gross → −45.93% net) documented UP-FRONT.
- Reopen bar: checked 2026-07-25 (`reports/deep-research_pullback-momentum-reopen-bar_2026-07-25.md`) — falsified 3 ways (IJFE 14/15 survivor failure; SSRN 2326253 PBO 55% on random walk; DSR trial-count logic). Family stays refuted.

## Frozen-gate metrics (funnel snapshot 2026-07-30T03:40Z, resolved window 2026-07-23 → 2026-07-30)

| Gate | Value | Threshold | OK |
|---|---|---|---|
| n_resolved | 37 | ≥30 | ✓ |
| oos_wr | 0.1081 | ≥0.55 | ✗ |
| auc | 0.50 | ≥0.60 | ✗ |
| net_after_cost_pnl | −116.55 USDT | >0 | ✗ |
| expectancy | −3.15/trade | >0 | ✗ |
| profit_factor | 0.149 | >1.0 | ✗ |
| dsr (single-stream proxy) | 0.0 | ≥0.1 | ✗ |

Gate verdict (arithmetic, fail-closed): **FAILED — 6 of 7 substantive gates.**

## Exit-path breakdown (warehouse `shadow_outcomes` × `shadow_pullback_probe`, n=37)

| Exit path | n | wins | net USDT | avg R |
|---|---|---|---|---|
| time (42-bar bound) | 21 | 4 | −39.33 | −0.209 |
| stop_loss | 16 | 0 | −77.22 | −1.124 |
| **RSI>70 profit exit** | **0** | 0 | — | — |

Gross −107.73, fees 8.82 → cost share of loss ≈ 8%. The loss is signal, not friction.

## Statistics

- 4/37 wins → one-sided 95% Clopper-Pearson upper bound on true WR = **0.2305**. The 0.55 gate — and the owner's 63–67% band — are excluded (binomial p vs 0.55: 2.4e-08).
- The designed profit path (RSI>70) has fired **0 times in 37 resolutions** (consistent with 0/12 at the interim check 2026-07-24). Entries systematically precede reversion below MA20 or stop-out.
- Direction of travel: interim 12 resolved → mean −0.728R; now 37 resolved → mean −0.605R. No convergence toward positive expectancy.

## Question for adjudication (answer independently)

1. **PROMOTE-WORTHY or NO?** (frozen gate is arithmetic; a reviewer cannot argue past a failed threshold — this question is formally about whether any dossier-worthy surprise exists)
2. **If NO: RETIRE the probe (stop accrual) or CONTINUE accruing?** Considerations: sample already decisive at 37≥30; probe cost is small but nonzero (43-symbol 4h eval per 5-min tick); continued accrual has no remaining decision value unless one argues regime dependence; owner's stated strategy — retirement note must be honest and final. Retirement mechanics: `SHADOW_PULLBACK_PROBE_ENABLED=false` in `.env`, takes effect at next OWNER-ATTENDED restart (no unattended bot bounce; standing rule).
3. **Ledger action:** proposed row update closing probe #7 with final forward numbers.

Both-agree required for any action. Split → parked, surfaced to owner.
