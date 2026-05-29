# Execution-cost audit + agent-upgrade shipment (2026-05-29)

Shipped the *measurement + gate-feeding plumbing* from `agent_upgrade_plan_2026_05_29.md`. The
falsification-gated edge work could not ship this session because the data to falsify it does not
exist yet (see "Data constraints"). This is the honest resolution of "ship it."

## Headline finding (Phase 1 — execution-cost audit, LIVE only)

`scripts/exec_cost_audit.py` over **498 CONTROLLED_LIVE** closed trades ($21,368 notional):

| component                  | $ total | % of notional |
|----------------------------|---------|---------------|
| fees (modeled rate*)       | 11.56   | 0.0541%       |
| spread (fill−mid)          | 0.12    | 0.0006%       |
| slippage (NOT captured†)   | 0.00    | 0.0000%       |
| funding                    | −0.02   | −0.0001%      |
| **TOTAL COST (floor)**     | **11.67** | **0.0546%** |

Realized PnL **−$110.95** (net of fees — basis: this reconciles to the known live tape) → implied
gross alpha ≈ **−$99**.

**Measured cost is a minority of the bleed; the loss is alpha-dominated.** The $11.67 is a **floor**
(fees only, modeled), so cost-share is *at least* ~11%; even generously bounding the unmeasured live
slippage and fee-rate error, cost stays well under half — the dominant term is **negative-alpha
entries**. Cutting taker→maker lowers the bracket breakeven WR by **≤3.81 points** (43.81% → 40.00%,
a ceiling — the SL leg still crosses as taker, so the real saving is smaller) — worth single-digit
dollars over this whole tape. *This tempers the plan's "fees are the wall" premise:* fees are a
wall, but a small one relative to the missing edge. **Profitability is gated on edge, not cost** —
consistent with every prior NO_EDGE result.

\*`trades.fee` is computed from hardcoded `_fee_rate` constants at close, **not** the actual
exchange-charged fee (no VIP discount, no real maker/taker split). For ground truth, the
`--live-fills` path would pull `fetch_my_trades` (stubbed in this build — not run).
†Live slippage is never written back to the warehouse (sim-only), hence identically $0 — so TOTAL
COST is a lower bound, not comprehensive.

**Maker-soak verdict: not yet answerable.** The maker-only flip was today (commit 40990a2,
2026-05-29 01:17); only 1 post-flip live trade exists. Cumulative maker-fill rate (all-config) =
44.9% (109 limit / 134 market). Re-run the audit in ~1–2 weeks once post-flip N is meaningful.

## What shipped (all falsification-first, nothing touches the live order path)

1. **`scripts/exec_cost_audit.py`** — the live-only cost decomposition above (re-runnable).
2. **Per-trade `fill_type` instrumentation (forward)** — additive nullable `trades.fill_type`
   column; `core/smart_executor.py` tags `_fill_type` (`maker`/`taker`/`maker_partial`) on its
   return paths; `core/order_manager.open_position` threads it into `record_trade_open`. So the
   maker soak can be judged on per-trade ground truth going forward (paper rows stay NULL). 4 tests
   cover the executor tag and the warehouse column **separately**; the `open_position` →
   `record_trade_open` seam is a trivial additive assignment defaulting to `None` (can't harm live
   trading) but is **not** integration-tested end-to-end.
3. **`core/data_sources/derivs.py` + `scripts/harvest_derivs.py`** — Binance free public
   derivs harvester (long/short account ratio, taker buy/sell, OI history, funding), fail-open,
   **persists every snapshot to `data/derivs_history.jsonl`** for forward accumulation. Verified
   live (BTC lsr 1.73 / ETH 2.83 / SOL 3.58). 4 tests. **No trading agent, no DataCoordinator/ctx
   wiring yet** — that is gated.

## Data constraints (why the edge work is gated, not shipped)

- **Derivs falsification is data-blocked.** Binance `/futures/data/` endpoints retain only **~21
  days** (verified: oldest bar 2026-05-08 at limit=500). A leakage-clean multi-month OOS derivs
  backtest is impossible until history accumulates forward. → run `scripts/harvest_derivs.py` on a
  schedule (Task Scheduler/cron, hourly); revisit the probe + agent in ~2–3 months.
- **Maker verdict is data-blocked** (post-flip N=1, above).

## Killed / deferred

- **Lever 4 (Kronos agent) — KILLED.** Kronos failed its own after-cost gates this session
  (`research/kronos_eval_2026_05_29.md`); wrapping it would contradict the verdict.
- **Lever 3 (meta-labeling) — deferred** (needs accumulated labeled shadow decisions).
- **Lever 2b (smart-money/social) — deferred** (needs a confirmed pollable provider).
- **MCP observability server — deferred** (observability, not edge; on request).

## How to act on this

1. Schedule `python scripts/harvest_derivs.py` hourly to start the derivs clock.
2. Re-run `python scripts/exec_cost_audit.py --since-days 14` in ~2 weeks to judge the maker soak on
   `fill_type` ground truth.
3. The honest bottom line: cost optimization can recover single-digit dollars; it cannot fix a −$99
   alpha hole. Any real profitability still requires a validated edge the bot does not yet have.

## Verification

`ruff` clean on all new files; full `pytest tests/` = **1358 passed / 19 pre-existing-WIP fail
(test_accuracy_tracker, test_bybit_transfer_and_circuit_breaker — unrelated) / 2 skipped**. The +8
vs the 1350 baseline are the new fill_type (4) + derivs (4) tests. No regression from the
warehouse/executor/order_manager edits (all additive). `_execute_open`, `OPERATING_MODE`, latches,
and the bot `requirements.txt` are untouched.
