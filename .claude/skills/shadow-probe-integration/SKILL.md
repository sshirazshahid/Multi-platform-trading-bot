---
name: shadow-probe-integration
description: How to wire a screen-confirmed strategy candidate into the bot as a LOG-ONLY shadow probe agent so live-market evidence accrues at zero capital risk. Use when applying a strategy, integrating a GO candidate, adding a shadow/probe agent, or updating the program with a new strategy signal. Never for candidates without a CONFIRMED GO screen verdict — check _workspace/strategy_pipeline/03_audit_findings.md first.
---

# Shadow Probe Integration

"Apply the strategy" means THIS under the charter: the candidate trades on paper inside the live loop, its decisions are logged and honestly resolved, and only gate-passing evidence can promote it — by owner decision, never automatically.

## The pattern (proven precedent: core/agents/tp_probe_agent.py)
1. New agent class in `core/agents/` computing would-have decisions (entry/exit levels, sizing) each cycle. NO orders, ever. The docstring must state the hypothesis and forbid reading hit-rates without resolved after-cost `net_pnl` (the TP-probe precedent — its ~78% hit-rate was a geometry artifact with −EV after cost).
2. Register with `core/shadow_runner.py` (the log-only ensemble). Decisions land in `warehouse.shadow_decisions`.
3. Resolution is automatic and NOT yours to reimplement: `core/shadow_resolver.py` replays closed candles (SL-first tie-break, censoring guard, fees + slippage, funding) into `shadow_outcomes`. Custom PnL math in a probe is a bug.
4. Read results via the `trading_bot_shadow_vs_live` MCP tool or `shadow_outcomes` queries.

## Hard boundaries (charter — non-negotiable)
- LOG-ONLY until `core/promotion_gate.py` passes on resolved outcomes AND the owner explicitly promotes. Integration never touches `mcp_brain` decision output, `order_manager` order paths, risk gates, or config defaults that change live behavior.
- The PAPER/CONTROLLED_LIVE double latch is untouchable. WIDEN-SL is forbidden everywhere.
- The repo is PUBLIC: no data/ artifacts, keys, or absolute user paths in committed code or tests.

## Definition of done
TDD tests green + full suite green; probe visible in `shadow_decisions` within one cycle of a bot restart; `_workspace/strategy_pipeline/04_integration_report.md` documents what logs where, how to read the verdict, and the promotion criteria.
