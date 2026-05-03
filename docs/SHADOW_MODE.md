# Shadow Mode — Multi-Agent Architecture

**Status:** Phase A complete (2026-05-02). Live impact: zero.

## Why this exists

User directive 2026-05-02: build a multi-agent system that takes futures
trades on 5m/15m candles. Built against the agent's prior recommendation
(see `tasks/multi_agent_shadow_plan.md` "Acknowledgments"). Shadow-first
staging chosen so the live bot's soak window (May 5 SOAK_PLAN verdict)
runs uninterrupted; promotion to live is gated by Phase B + C criteria.

## Architecture

```
┌─────────────────────────────────────────────────┐
│ LIVE PATH (untouched)                            │
│   BotEngine main thread (schedule.run_pending)   │
│   + SL/TP daemon thread                          │
└─────────────────────────────────────────────────┘
                    │ same tick stream
                    ▼
┌─────────────────────────────────────────────────┐
│ SHADOW PATH (parallel, log-only)                 │
│ shadow-runner daemon thread (5m cadence)         │
│   ShadowRunner.tick()                            │
│   └── AgentCoordinator                           │
│         ├─ TrendAgent       (1h+4h)              │
│         ├─ ScalpAgent       (5m+15m primary)     │
│         ├─ MeanReversionAgent (15m RSI extremes) │
│         │     │ proposals (ThreadPoolExecutor)   │
│         │     ▼                                  │
│         ├─ RiskAgent        (veto)               │
│         │     │                                  │
│         │     ▼                                  │
│         └─ ExecutionAgent   (logs, NO orders)    │
│              │                                   │
│              ▼                                   │
│       warehouse.shadow_decisions                 │
└─────────────────────────────────────────────────┘
                    │
                    ▼
        KillCriteriaMonitor → auto-disable on:
          - shadow fee burn > 2× live (≥100 decisions)
          - shadow WR < 30% (≥100 decisions)
          - shadow drawdown halts > 3 in 7d
          - shadow thread blocks main > 5s
```

## Files

| Path | Purpose |
|---|---|
| `core/agents/base_agent.py` | `BaseAgent` abstract + `Proposal` dataclass |
| `core/agents/indicators.py` | EMA / RSI / ATR / ADX (self-contained) |
| `core/agents/trend_agent.py` | 1h/4h trend follower (mirrors live MCP spirit) |
| `core/agents/scalp_agent.py` | **5m/15m candles primary** (per user reading-a) |
| `core/agents/mean_reversion_agent.py` | 15m RSI extremes + Bollinger touch |
| `core/agents/risk_agent.py` | Central veto (halts, blacklist, hours, sanity) |
| `core/agents/execution_agent.py` | Log-only simulator → `shadow_decisions` |
| `core/agents/coordinator.py` | Parallel propose → serial veto → execute |
| `core/agents/projected_sizer.py` | Dual-notional projection (current vs $200 alt) |
| `core/agents/kill_criteria.py` | Monitor + auto-disable persistence |
| `core/shadow_runner.py` | Per-tick orchestrator |
| `core/bot_engine.py` (extended) | `_shadow_thread` + `_shadow_*` helpers |
| `scripts/shadow_vs_live_report.py` | Daily compare markdown report |

## Configuration

Set via environment or `config.SHADOW_MODE`:

| Key | Default | Meaning |
|---|---|---|
| `SHADOW_MODE_ENABLED` | `true` | Master switch |
| `SHADOW_TICK_INTERVAL_S` | `300` | Cadence in seconds |
| `SHADOW_ALT_NOTIONAL` | `200.0` | Alternative-sizing comparison notional |
| `SHADOW_KILL_FEE_BURN_X` | `2.0` | Multiple-of-live fee-burn that triggers kill |
| `SHADOW_KILL_WR_FLOOR` | `0.30` | Minimum WR before kill |
| `SHADOW_KILL_MIN_DECISIONS` | `100` | Sample size before kill criteria evaluable |
| `SHADOW_KILL_MAX_HALTS_7D` | `3` | Max drawdown halts in 7d before kill |

## Reading the data

```sql
-- All shadow decisions in the last 24 hours
SELECT agent_id, symbol, side, decision, p_win,
       projected_notional_current, projected_notional_alt,
       sim_pnl, projected_pnl
FROM shadow_decisions
WHERE ts >= strftime('%s','now','-24 hours')
ORDER BY ts DESC;

-- Per-agent performance
SELECT agent_id, COUNT(*) n,
       ROUND(SUM(sim_pnl), 2) sum_cur,
       ROUND(SUM(projected_pnl), 2) sum_alt,
       ROUND(AVG(CASE WHEN sim_pnl > 0 THEN 1.0 ELSE 0.0 END), 3) wr
FROM shadow_decisions
WHERE decision='ALLOW' AND ts >= strftime('%s','now','-7 days')
GROUP BY agent_id;
```

## Promotion gate (Phase B)

Shadow data feeds `core/promotion_gate.py`. Daily evaluation; promotion
to Phase C cutover **requires all of**:
- Shadow Sharpe LB(95%) ≥ Live Sharpe UB(95%)
- Shadow rolling 7d WR ≥ 0.40
- Shadow fee burn ≤ 1× live (tighter than KillCriteria's 2×)
- No shadow agent crash or main-thread block in 7 days

User must explicitly advance to Phase C even after `PROMOTE` evaluates true.

## Cutover (Phase C)

A/B traffic split: 10% → 25% → 50% → 100% across 5 days, gated by
continued daily promotion-gate eval. Auto-rollback if WR < 35% for 48h.

## Verifying it's live

```bash
# 1. Logs
grep "shadow-runner thread started" logs/bot_*.log
grep "\[Shadow\]" logs/bot_*.log | tail -20

# 2. Warehouse
sqlite3 data/warehouse.sqlite "SELECT COUNT(*), MIN(ts), MAX(ts) \
  FROM shadow_decisions WHERE ts >= strftime('%s','now','-1 hour');"

# 3. Tests
pytest tests/test_shadow_runner_multi_agent.py tests/test_coordinator.py \
       tests/test_kill_criteria.py tests/test_strategies_smoke.py -v

# 4. Dry-run the daily report
python scripts/shadow_vs_live_report.py --window-hours 24
ls reports/shadow_compare_*.md | tail -1
```

## Disabling

```bash
# Temporary (next process):
SHADOW_MODE_ENABLED=false python main.py

# Permanent (kill criteria persistence):
echo '{"killed": true, "reason": "manual"}' > data/shadow_kill_state.json
```
