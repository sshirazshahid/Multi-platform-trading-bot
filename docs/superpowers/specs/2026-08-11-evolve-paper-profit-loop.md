# ADR: Evolve PAPER profit loop (2026-08-11)

**Status:** Accepted  
**Owner choices:** **1A** evolve existing bot (no greenfield wipe); **2A** maximize measured PAPER expectancy + AccBand WR discipline via evidence gates. **No CONTROLLED_LIVE** from this work. Profitability is a hypothesis to measure, not a guarantee.

**Context:** Ultrathink request to redesign language, system, tools, monitoring, backtest, strategies, pairs, and scalp — answered by hardening the existing Binance/Bybit/Bitget PAPER stack rather than deleting it.

## Decisions

### Language
Keep **Python 3.9+**. Exchange RTT dominates local CPU; ccxt clients, warehouse, and pytest already ship. Rust/Python hybrid is out of scope until PAPER expectancy is proven and latency is the binding constraint.

### System design
Authority remains **deterministic MCP scorer + hard gates**. Shadow probes, Mission Control, read-only MCP server, and Claude advisor are observability / bonuses only. Phase 1 ops stay: warehouse orphan `reconcile_flat`, soft-stale entry latch, `max_hold_force_flat`, `SCALP_TIER_ENABLED` default false.

### Tools / connectors / skills / MCP
- Exchanges: `exchanges/{binance,bybit,bitget}_client.py` via ccxt  
- Research: `.claude/skills/strategy-evidence-pipeline` + scout/screener/auditor/integrator  
- Ledger: refuted-families — answer REFUTED without re-screen  
- Introspection: `mcp_server/trading_bot_mcp.py` (read-only)  
- Ops: Mission Control + `scripts/launcher_supervisor.py`  
- Explicitly **not** built: live Arkham/whale entry, new live scalp, Freqtrade migration, Rust rewrite

### Continuous monitoring
Engine timers + `health_watchdog` + soft-stale latch + Mission Control. This ADR’s ship adds PAPER expectancy / probe-floor / soft-stale / econ-gate summary on MC status, plus a fail-closed research-loop tick (no auto strategy install).

### Continuous TA / backtesting
Warehouse + `research/screen_*.py` after-cost screens; prereg hash before outcomes; Stage-0 trigger counts; honesty audit. TV/Pine = optional cross-check only.

### Strategies
Only via evidence pipeline → log-only shadow → frozen `promotion_gate` + owner sign-off. Edge queue advances one item at a time; whale/on-chain remains RECORD-NO-ACTION.

### Pairs
`TRADING_MODE=all` + StrategySpec PAPER futures + runtime universe/meta/score filters. No static “five coins for profit.”

### Scalp
`SCALP_TIER_ENABLED` stays default **false**. No new live scalp. AccBand short-hold = geometry research, not a scalp strategy.

### Self-upgrade (defined)
Warehouse + learning reports; scheduled research tick + promotion funnel; self-heal adapters; **never** auto-promote to CONTROLLED_LIVE. Machine adaptive path remains gated by `SIGNAL_SOURCE`.

## Non-goals
- Delete everything / greenfield rewrite  
- CONTROLLED_LIVE enablement  
- Guaranteed profitable accuracy  
- Live whale / F&G SHORT authority  
- Live scalp without after-cost GO  
- OOB `flatten_all` on hung heartbeat  
- Mass file deletion (see cleanup inventory — DELETE-CANDIDATE only)

## Consequences
- Operators see measured PAPER expectancy and probe floor progress without confusing them for edge.  
- Research loop cannot silently install strategies.  
- Cleanup inventory documents vestigial code without destroying evidence history.
