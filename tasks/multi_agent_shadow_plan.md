# Multi-Agent Shadow Build — Plan
**Date:** 2026-05-02
**Branch:** `fix/stop-loss-streak-live-risk-trim` (or new branch — TBD)
**Build mode:** Shadow-first, zero live impact during Phase A

---

## Acknowledgments (on the record)

1. **Built against my prior recommendation.** I pushed back twice (Option 1: soak; Option 2: shadow predictor only). User explicitly chose Option 4 (full multi-agent rewrite) + 5m–15m candle-based entries on a $791 account, knowing the engineering risk and the negative-edge baseline.
2. **Live bot is NOT touched during Phase A.** May 5 SOAK_PLAN verdict runs unchanged. If soak fails, we have new data that may change this design.
3. **Notional sizing will be revisited.** 5m–15m signals at $4–8 notional are fee-fatal regardless of timeframe — at ~$200 notional the math works. Shadow projects BOTH current sizing and a $200-notional candidate so user sees the economics side-by-side before any live cutover.

## Disambiguation result

**Reading (a) — 5m/15m candles as primary entry input.** The bot already uses 15m as a "timing bonus" on top of 1h/4h trend; this build promotes 5m + 15m to primary trigger, with 4h trend retained as a confirmation filter (not a gate).

NOT (b) — i.e., we are NOT making it a 5–15 minute hold-time scalper. Hold time is determined by exit logic (TP/SL/age/trail) as today.

---

## Architecture

```
┌──────────────────────────────────────────────────────────┐
│ LIVE PATH (untouched)                                     │
│ BotEngine main thread                                     │
│   schedule.run_pending()                                  │
│   ├─ _claude_portfolio_cycle (5m)  → MCPBrain → orders    │
│   ├─ _run_mcp_position_monitor (2m)                       │
│   └─ ... (existing)                                       │
│ SL/TP thread (10s, daemon)                                │
└──────────────────────────────────────────────────────────┘
                            │ same tick stream
                            ▼
┌──────────────────────────────────────────────────────────┐
│ SHADOW PATH (new)                                         │
│ ShadowRunner thread (5m, daemon)                          │
│  └─ AgentCoordinator                                      │
│       ├─ ScalpAgent       (5m + 15m candles primary)      │
│       ├─ TrendAgent       (1h + 4h, mirrors live MCP)     │
│       ├─ MeanReversionAgent (15m RSI extremes)            │
│       │     │                                             │
│       │     ▼                                             │
│       ├─ RiskAgent        (veto: Spec§12, halts, caps)    │
│       │     │                                             │
│       │     ▼                                             │
│       └─ ExecutionAgent   (logs only — NO orders)         │
│              │                                            │
│              ▼                                            │
│       warehouse.shadow_decisions                          │
│       (existing schema; +agent_id, +projected_notional)   │
└──────────────────────────────────────────────────────────┘
                            │
                            ▼
              KillCriteriaMonitor (every 30m)
              auto-disables shadow on:
                - fee burn > 2× live after 100 decisions
                - WR < 30% after 100 decisions
                - drawdown halts > 3 in 7d
                - shadow thread blocks main > 5s
```

---

## Phase A — Shadow build (~5 days, no live impact)

### Task A.1 — Schema migration ✅ DONE
**Files:** `core/warehouse.py`, `tests/test_shadow_decisions_schema.py`
- [x] Test: assert `shadow_decisions` has columns `agent_id`, `proposal_id`, `proposed_at`, `vetoed_by`, `veto_reason`, `projected_notional_current`, `projected_notional_alt`, `projected_pnl`, `projected_fee`
- [x] Add columns via `ALTER TABLE` (idempotent try/except OperationalError pattern)
- [x] Run, green, commit: 4 new tests pass, 605 total / 0 failures

### Task A.2 — Agent base class
**Files:** `core/agents/__init__.py`, `core/agents/base_agent.py`, `tests/test_agent_base.py`
- [ ] Test: abstract `BaseAgent` requires `name`, `propose(ctx) -> Optional[Proposal]`; subclass without `propose` raises TypeError
- [ ] Define `Proposal` dataclass: `agent_id, symbol, side, entry, sl, tp, confidence, reason, ts`
- [ ] Implement `BaseAgent` with `name`, `propose`, `on_outcome` hooks
- [ ] Commit: `feat(shadow): BaseAgent abstract + Proposal dataclass`

### Task A.3 — TrendAgent (mirrors live MCPBrain)
**Files:** `core/agents/trend_agent.py`, `tests/test_trend_agent.py`
- [ ] Test: given the same OHLCV input as live MCPBrain, TrendAgent emits Proposal with same direction
- [ ] Implementation: thin wrapper around `MCPBrain._score_coin` and existing 4-required + 6-bonus scoring; converts internal score → Proposal
- [ ] Commit: `feat(shadow): TrendAgent mirrors live MCP scoring`

### Task A.4 — ScalpAgent (5m/15m candles primary)
**Files:** `core/agents/scalp_agent.py`, `tests/test_scalp_agent.py`
- [ ] Test: synthetic 5m breakout → emits buy Proposal with tight SL
- [ ] Test: synthetic 5m breakdown → emits sell Proposal
- [ ] Test: 4h trend opposite direction → no proposal (confirmation filter blocks)
- [ ] Implementation:
  - Fetch 5m + 15m candles (cached via existing OHLCV fetcher)
  - 5m EMA9/EMA21 cross + RSI(14) sweet spot 40–70 (long) or 30–60 (short)
  - 15m confirmation: EMA9 above/below EMA21 in same direction
  - 4h trend: directional, blocking only if opposite (i.e. 4h downtrend blocks longs)
  - SL: 0.5 × ATR(5m, 14) — tighter than live's 1.5 × ATR(1h)
  - TP: 1.5 × SL distance (lower R:R reflects shorter horizon)
- [ ] Commit: `feat(shadow): ScalpAgent — 5m/15m candle-primary entries`

### Task A.5 — MeanReversionAgent
**Files:** `core/agents/mean_reversion_agent.py`, `tests/test_mean_reversion_agent.py`
- [ ] Test: 15m RSI < 25 + price near 30d low → emits buy
- [ ] Test: 15m RSI > 75 + price near 30d high → emits sell
- [ ] Implementation: RSI(14) extremes + Bollinger touch + ADX < 25 (chop filter)
- [ ] Commit: `feat(shadow): MeanReversionAgent — RSI extremes`

### Task A.6 — RiskAgent (central veto)
**Files:** `core/agents/risk_agent.py`, `tests/test_risk_agent.py`
- [ ] Test: proposal during Spec§12 halt → vetoed with reason "halt_active"
- [ ] Test: proposal outside ALLOWED_HOURS_UTC → vetoed
- [ ] Test: proposal exceeding correlation cap → vetoed
- [ ] Implementation: reads `risk_state.json`, `config.BLACKLIST_HARD`, `ALLOWED_HOURS_UTC`, correlation manager. Returns `(approved: bool, veto_reason: str)`.
- [ ] Commit: `feat(shadow): RiskAgent — central proposal veto`

### Task A.7 — ExecutionAgent (log-only)
**Files:** `core/agents/execution_agent.py`, `tests/test_execution_agent.py`
- [ ] Test: `execute(proposal)` writes a row to `shadow_decisions` with simulated fill, never calls `OrderManager.open_position`
- [ ] Test: simulated PnL via `SimExecutionModel` is realistic (slippage + fees applied)
- [ ] Implementation: takes approved Proposal → simulates fill via existing `core/sim_execution.py` → writes row with `projected_pnl`, `projected_fee`, both notional projections (current + $200)
- [ ] Commit: `feat(shadow): ExecutionAgent — log-only simulator`

### Task A.8 — AgentCoordinator
**Files:** `core/agents/coordinator.py`, `tests/test_coordinator.py`
- [ ] Test: end-to-end — three agents emit proposals, RiskAgent vetoes one, ExecutionAgent logs the other two
- [ ] Test: agents run in parallel threads, total wall-time ~ max(agent_time), not sum
- [ ] Implementation: `ThreadPoolExecutor(max_workers=N_agents)` for parallel proposal generation; serialized RiskAgent veto pass; serialized ExecutionAgent log
- [ ] Commit: `feat(shadow): AgentCoordinator — parallel proposals, serialized veto/exec`

### Task A.9 — ShadowRunner
**Files:** `core/shadow_runner.py`, `tests/test_shadow_runner.py`
- [ ] Test: `ShadowRunner.tick(market_snapshot)` writes shadow_decisions rows but never calls `order_manager.open_position` (mock + assert not called)
- [ ] Test: tick is idempotent — same input twice doesn't double-write
- [ ] Implementation: subscribes to BotEngine's cached tick stream (no new exchange calls), invokes Coordinator
- [ ] Commit: `feat(shadow): ShadowRunner — orchestrates tick → coordinator → log`

### Task A.10 — Wire ShadowRunner into BotEngine
**Files:** `core/bot_engine.py`, `config.py`
- [ ] Test: with `SHADOW_MODE_ENABLED=False`, no shadow thread starts; with `True`, daemon thread `shadow-runner` is alive
- [ ] Add config flag `SHADOW_MODE_ENABLED=True` (default true; fail-safe: any kill criteria flips to False at runtime)
- [ ] Add `_shadow_thread` to `BotEngine.run()` mirroring `_sltp_thread` pattern (daemon, watchdog'd, restart on death)
- [ ] Schedule `shadow_runner.tick()` on existing 5m portfolio cycle (no new cycle)
- [ ] Commit: `feat(shadow): wire ShadowRunner into BotEngine alongside live`

### Task A.11 — Projected sizing (the notional fix)
**Files:** `core/agents/projected_sizer.py`, `tests/test_projected_sizer.py`
- [ ] Test: same proposal, two notional projections — current sizing rules + $200 fixed alternative
- [ ] Implementation: `project(proposal, balance) -> (notional_current, notional_alt)` using existing `risk_manager.calculate_position_size` for current, fixed $200 for alt. Both written to shadow_decisions.
- [ ] Commit: `feat(shadow): projected sizer — current vs $200 notional`

### Task A.12 — KillCriteriaMonitor
**Files:** `core/agents/kill_criteria.py`, `tests/test_kill_criteria.py`
- [ ] Test: synthetic 100-decision window with fee burn > 2× live → triggers kill
- [ ] Test: shadow WR 25% after 100 decisions → triggers kill
- [ ] Test: shadow drawdown halts > 3 in 7d → triggers kill
- [ ] Test: shadow thread blocked > 5s → triggers kill
- [ ] Implementation: scheduled every 30m, queries warehouse for shadow stats, sets `SHADOW_MODE_ENABLED=False` at runtime + sends notifier on trigger. Persists state to `data/shadow_kill_state.json`.
- [ ] Commit: `feat(shadow): KillCriteriaMonitor — auto-disable on bad shadow stats`

### Task A.13 — Daily compare report
**Files:** `scripts/shadow_vs_live_report.py`, `tests/test_shadow_compare_report.py`
- [ ] Test: synthetic shadow + live data → correct rolling stats
- [ ] Implementation: outputs PnL, WR, fee burn, drawdown side-by-side. Markdown report saved to `reports/shadow_compare_<date>.md`
- [ ] Wire into existing `_daily_self_check` 00:00 UTC subprocess pattern
- [ ] Commit: `feat(shadow): daily shadow-vs-live compare report`

### Task A.14 — Smoke regression test for agent layer
**Files:** `tests/test_strategies_smoke.py` (extend existing) or `tests/test_agents_smoke.py`
- [ ] Add: BaseAgent, ScalpAgent, TrendAgent, MeanReversionAgent, RiskAgent, ExecutionAgent, Coordinator, ShadowRunner — all import + instantiate + minimum-cycle smoke
- [ ] Verify ShadowRunner.tick never invokes order_manager.open
- [ ] Commit: `test(shadow): smoke regression for agent layer`

### Task A.15 — Documentation
**Files:** `docs/SHADOW_MODE.md`, update `CLAUDE.md`
- [ ] Architecture diagram + agent responsibilities + kill criteria + cutover playbook
- [ ] Update CLAUDE.md "Bot Architecture" with shadow path
- [ ] Commit: `docs(shadow): SHADOW_MODE.md + CLAUDE.md update`

### Task A.16 — Final integration verify
- [ ] Full test suite green: `pytest tests/ -q` (target: 615+ passed, 0 failed)
- [ ] Bot starts cleanly with `SHADOW_MODE_ENABLED=True`
- [ ] After 1h: shadow_decisions table has rows from all 3 entry agents
- [ ] After 1h: live bot has placed orders unchanged from baseline
- [ ] Commit: tag the branch `shadow-build-phase-A-complete`

---

## Phase B — Promotion gate (1 day, additive, only after Phase A complete + 7 days of shadow data)

### Task B.1 — Promotion gate evaluator
**Files:** `core/promotion_gate.py` (extend), `tests/test_promotion_gate.py`
- [ ] Reads 7-day shadow_decisions + 7-day live trades
- [ ] Computes: shadow Sharpe LB (bootstrap 95%), shadow WR, shadow fee burn ratio, drawdown halt count
- [ ] Returns `PROMOTE | HOLD | REJECT(reason)`
- [ ] Promotion criteria — ALL must hold for 7 consecutive days:
  - Shadow Sharpe LB ≥ Live Sharpe UB
  - Shadow rolling 7d WR ≥ 40%
  - Shadow fee burn ≤ 1× live (tighter than KillCriteria's 2× — promotion bar is higher)
  - No shadow agent crash or main-thread block in 7d
- [ ] Persist evaluation to `data/promotion_log.jsonl`

### Task B.2 — Promotion dashboard
**Files:** `dashboard.py`, `tests/test_dashboard_promotion.py`
- [ ] New panel: shadow vs live (PnL, WR, Sharpe, fees) + promotion countdown
- [ ] Visual go/no-go status

### Task B.3 — Promotion-gate alert
- [ ] When PROMOTE evaluates true, notifier sends operator email with summary + "press button to advance to Phase C"
- [ ] User explicit confirmation required to advance — no auto-promotion

---

## Phase C — Live cutover (only after explicit user advance from Phase B)

### Task C.1 — A/B traffic split
- [ ] `core/bot_engine.py`: per-candidate routing by random weight `pct_to_shadow`
- [ ] Auto-ramp: PROMOTE → 10% → 25% → 50% → 100% across 5 days, gated by daily re-evaluation of promotion criteria
- [ ] Auto-rollback to 0% if WR < 35% for 48h or any kill criteria triggers

### Task C.2 — Cutover playbook + commit
- [ ] `docs/CUTOVER.md` — operator playbook
- [ ] Final commit gates cutover behind continuous promotion-gate evaluation

---

## Kill criteria (always-on during Phase A; auto-disables shadow)

| Trigger | Threshold | Window | Action |
|---|---|---|---|
| Shadow fee burn > 2× live | absolute | 100 decisions | Disable shadow, notify |
| Shadow WR < 30% | absolute | 100 decisions | Disable shadow, notify |
| Shadow drawdown halts > 3 | count | 7 days | Disable shadow, notify |
| Shadow thread blocks main > 5s | latency | any | Disable shadow, notify |
| Any agent crashes the process | crash | any | Disable shadow, notify, restart bot |

---

## Build estimate

- Phase A: ~5 focused days (16 tasks)
- Phase B: ~1 day (3 tasks)
- Phase C: gated by your explicit advance after seeing Phase B data

**Total commit budget: ~20 commits, each task = 1 commit, TDD-driven (tests first).**

---

## Sign-off needed

Before I write Task A.1, please confirm:
1. The plan is what you expected (especially Phase A scope)
2. Branch: continue on `fix/stop-loss-streak-live-risk-trim`, or create new `feat/multi-agent-shadow`?
3. The default `SHADOW_MODE_ENABLED=True` is OK (it can run alongside live with zero order impact, just costs ~5–10% extra CPU per cycle)
