# 48 — S1 adjudication dossier: lane `rsi2_4h_cfg226` (bundle-MR TRACKER arm)

*Date: 2026-07-31 | Loop iteration per `19_dual_model_loop_protocol.md` S1 | Shared dossier — both models receive THIS file only; verdicts are independent.*

## Lane identity

- Arm: `rsi2_cfg226` (MR-B): RSI(2) < 10 with-trend long (price>EMA200), mirror short RSI(2) > 90 below EMA200; TP 0.8×ATR14, SL 2.0×ATR14, 12-bar time-stop; 4h bybit linear perps. Probe: `core/agents/bundle_mr_probe_agent.py::Rsi2TrackerProbeAgent`, log-only, frozen score tanh((10−RSI2)/10) longs / tanh((RSI2−90)/10) shorts, never re-tuned.
- Provenance: owner's cloud bundle test 2026-07-19 (prereg + 1-shot OOS, ~432 configs swept). cfg226 was **THE TRACKER**: OOS WR in/near band (67–68%) but net NEGATIVE — "kept solely to measure the band-vs-profit tension forward." NOT a pipeline GO. Ledger status: RSI mean-reversion family REFUTED (5 coins × 3yr, NO_EDGE, 2026-06); probe recorded as owner-directed external-evidence forward measurement, expectation NO-PROMOTE, fragility disclosed up-front.
- Universe widened 5→43 symbols at 2026-07-20 deploy (stamped in funnel); 101 of 102 resolutions are post-widen.

## Frozen-gate metrics (funnel snapshot 2026-07-31T03:40Z; resolved window 2026-07-19 20:35Z → 2026-07-30 16:35Z)

| Gate | Value | Threshold | OK |
|---|---|---|---|
| n_resolved | 102 | ≥30 | ✓ |
| oos_wr | 0.6471 | ≥0.55 | ✓ |
| auc | 0.50 | ≥0.60 | ✗ |
| net_after_cost_pnl | −79.46 USDT | >0 | ✗ |
| expectancy | −0.779/trade | >0 | ✗ |
| profit_factor | 0.652 | >1.0 | ✗ |
| dsr (single-stream proxy) | 0.0403 | ≥0.1 | ✗ |

Gate verdict (arithmetic, fail-closed): **FAILED — 5 of 7 substantive gates.** WR passes; economics do not.

## Exit-path breakdown (warehouse `shadow_outcomes` × `shadow_decisions`, agent_id=Rsi2TrackerProbeAgent, n=102; script `48_rsi2_breakdown.py`)

| Exit path | n | wins | net USDT | avg R |
|---|---|---|---|---|
| take_profit (0.8×ATR) | 66 | 66 | +149.02 | +0.320 |
| stop_loss (2.0×ATR) | 31 | 0 | −214.76 | −1.077 |
| time (12-bar) | 5 | 0 | −13.72 | −0.390 |

- Friction split (per ai-reviewer binding condition C1, `46_review_pullback_ma20_4h.md`): fees 24.47 + slippage 23.50 + funding 0.08 = **48.05 USDT ≈ 60% of the −79.46 net loss**. Frictionless (gross) P&L = **−55.07 — still negative at zero cost.** The loss is geometry AND friction; removing all costs does not turn it positive.
- Geometry: avg win +2.258 vs avg loss −6.347 → payoff ratio **0.356**. Breakeven WR at this geometry = 6.347/(6.347+2.258) = **73.8%**; actual 64.7%. The repo's binding "geometry≠edge" caveat, reproduced forward.
- Symmetry: both sides negative (buy 46 trades −43.82; sell 56 trades −35.64). Not symbol-driven: worst symbol ZEC (4 trades, −37.35); ex-ZEC net still −42.12 over 98.

## Statistics

- Expectancy −0.779/trade, sd 4.50, t = −1.75, 95% CI **[−1.653, +0.095]** — the point estimate is negative but the sample does not yet exclude zero expectancy at 95%. Stated for honesty; the frozen gate is arithmetic and already failed on net/expectancy/PF/DSR regardless.
- Bundle-test prediction vs forward: OOS said "WR 67–68%, net negative"; forward delivered WR 64.7%, net −79.46 over 102. **The tracker's registered question — does the band-vs-profit tension persist live? — is answered: yes.** Mean R −0.140; no convergence toward positive expectancy over the window.
- WR 66/102: 95% Clopper-Pearson [0.546, 0.739] — consistent with the 63–67 band. The band is real; the money is not. High WR is manufactured by the 0.8-vs-2.0 ATR bracket, exactly as the geometry caveat predicts.

## Question for adjudication (answer independently)

1. **PROMOTE-WORTHY or NO?** (gate is arithmetic and fail-closed; this question is formally whether any dossier-worthy surprise exists)
2. **If NO: RETIRE the lane or CONTINUE accruing?** Considerations: the tracker's registered purpose (measure band-vs-profit tension forward) is fulfilled at n=102 ≥ 30 floor; expectancy CI has not yet excluded 0 (upper bound +0.095) but the gate is not CI-based; accrual is fast (~10/day) and marginal compute is near-zero because the arm shares ticks with ZfadeProbeAgent. **Mechanics constraint: there is NO per-arm flag — `SHADOW_BUNDLE_MR_PROBE_ENABLED` disables BOTH arms, and zfade_4h_cfg365 is still pending its own adjudication.** Options: (a) ADJUDICATION-CLOSE — close the ledger row now, mark the lane's verdict final (later resolutions do not reopen it), physically de-register when the zfade arm is adjudicated (one shared flag then retires the module); (b) add a per-arm env flag (log-only shadow-lane code change, shadow-integrator scope + tests); (c) CONTINUE as tracker with no closure. State which and why.
3. **Ledger action:** proposed row update closing the cfg226 tracker with final forward numbers (WR in band, net negative, breakeven-WR gap 9.1pp).

Both-agree required for any action. Split → parked, surfaced to owner.
