# Scalp viability + deep bug audit (2026-05-28)

Prior 2026-05-23 diagnostic preserved at `tasks/todo.bak.2026-05-23-diagnostic.md`.

## The question (user)
"Fix why it's losing. Build a scalper that wins $1-2/trade on FUTURES 15-60m. Ship it."

## VERDICT (advisor-corrected, backtest-confirmed)
The bot is -EV because the **entry has no edge**, not because of fixable bugs.
A clean fixed bracket on a no-edge entry yields ~38% WR BY CONSTRUCTION
(= SL/(SL+TP)); fees push it to -EV. **Exits cannot manufacture EV.**

Decisive backtest (`scripts/scalp_replay_backtest.py`, look-ahead-free, 32 syms):
| test | best-cohort OOS WR | EV/trade | vs breakeven 48.6% |
|---|---|---|---|
| bot's actual entries, clean bracket | 27.8% | -0.40% | FAIL (anti-predictive) |
| momentum scalp (taker) | 41% longs | -0.14% | FAIL |
| mean-reversion scalp (taker) | 44.7% shorts | -0.07% | FAIL |
| momentum (MAKER, charitable) | 48.4% longs | +0.015% | marginal, regime-only |
| meanrev (MAKER, charitable) | 51.5% shorts | +0.071% | marginal, regime-only |

Maker-fee positives DECAY to negative by the latest time fold (regime artifact)
and assume maker entries always fill (false for scalps). **Not a shippable
$1-2/trade engine.** Dominant lever = FEES (taker -0.09% -> maker ~0%), not signal.

## Bug audit — most already fixed in prior sessions (verified, not assumed)
- [x] B1 discretionary `mcp_brain_close` — ALREADY disabled (Phase 39, 2026-05-09).
- [x] B4 `longs_only` — ALREADY enforced (mcp_brain.py:3087).
- [x] SCALP path is live-wired & mechanically working (mcp_brain.py:3052+) — no edge.
- [~] B3 `r_multiple` ~50% null — BENIGN: null only for reconciled/external/ghost
      closes that have no known stop distance (order_manager.py:1715). Analytics
      gap, not a money leak. Optional: write a fallback r from realized/ATR.
- [ ] Ghost-class exits (`reconciled_from_exchange` -$37) — real accounting class;
      prior 2026-05-23 diagnostic flagged a May 22-23 regression. Re-check current.

## Test baseline
- 1330 passed / 19 failed / 2 skipped. ALL 14 named failures are in UNTRACKED
  prior-session WIP (`test_accuracy_tracker.py` + `test_bybit_transfer_and_circuit_breaker.py`,
  the incomplete prediction-agent/circuit-breaker subsystem). Tracked suite green.
  NOT regressions from this session. Won't commit that WIP.

## Ship (responsible)
- [ ] Commit: backtest harness + this finding doc + research record. Keep PAPER.
- [ ] Do NOT flip OPERATING_MODE / CONTROLLED_LIVE_ENABLED. Do NOT present as profit.
- [ ] Honest report: fee floor is the wall; a real edge (orderflow/L2/funding-timing)
      or a maker/VIP fee tier is the prerequisite — neither currently in hand.

## Review
(fill after verification workflow lands)
