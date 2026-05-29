# Update plan: agents / data / execution to lift prediction, trading & profitability (2026-05-29)

## Context & honest framing (read first)

You asked to "add/update/build any agent/MCP/sub-agent that increases prediction, trading &
profitability." Before any code, the truth that governs the whole plan:

- **The bot is already a mature multi-agent system.** `core/agents/` has a shadow ensemble
  (Trend / MeanReversion / Pattern / Liquidity / Scalp agents + `AgentCoordinator` + A/B
  controller + kill-criteria + promotion alerts), run by `ShadowRunner` **in shadow only —
  it never places orders**, and graduated by `multi_agent_promotion_gate`. `mcp_brain.py`
  already scores **10 layers over 7 data sources** (price, order book, funding, sentiment,
  SMC). `meta_filter.py` already does rule-based selection.
- **More technical-analysis agents will NOT add edge.** Every prior result was **NO_EDGE**
  (443-alpha sweep, funding/carry search, scalp replay), and `mcp_brain`'s layers + the
  443 alphas already exhaust price/order-book/funding/sentiment-derived signal. Another
  EMA/RSI/pattern agent = overfitting + cost, not profit.
- **The fee floor is the wall.** On the 0.8%/1.3% bracket, breakeven win-rate is **≈48.6%**
  (taker fees + slippage). Nothing graduates to live until it beats this *after costs*.
- **Capital is ~1300 USDT.** Even a real edge yields modest absolute USD; this plan is about
  *finding* edge rigorously and *not bleeding* fees — not a promised money machine.

So the only two things that can move profitability are **(1) cut cost** and **(2) add a
genuinely new information class** — both measured falsification-first, both entering live
only through the existing promotion gate. You selected all four levers; they're sequenced
below by expected value × realism × cost, cheapest/most-reliable first.

Guardrails (unchanged from prior work): no `OPERATING_MODE`/latch change, `_execute_open`
untouched, everything new runs in **shadow** first, gated by `multi_agent_promotion_gate` /
`promotion_gate`. New heavy deps (e.g. torch) stay out of the bot's `requirements.txt`.

## Where new pieces plug in (existing seams — reuse, don't rebuild)

```
                         NEW data harvester (cached, rate-limited)
                                   │  (new ctx keys: derivs/flows/social)
                                   ▼
bot_engine._shadow_ctx_for_symbol ──► ctx{symbol, ohlcv_*, +NEW}
                                   │
                                   ▼
   ShadowRunner.tick ──► AgentCoordinator ──► [existing agents] + [NEW BaseAgents]
                                   │                 │ propose(ctx) -> Proposal
                                   │                 ▼
                          RiskAgent.review ──► ExecutionAgent.execute_shadow
                                   │                 │ (LOG ONLY)
                                   ▼                 ▼
                          warehouse.shadow_decisions ───► multi_agent_promotion_gate
                                                              │ PROMOTE/HOLD/REJECT
                                                              ▼  (manual review, never auto-live)
                                                         live entry path (unchanged)
```

Every new agent is a `core/agents/*.py` subclass of `BaseAgent` returning a `Proposal`
(`agent_id` = its name → that's its identity in `shadow_decisions`). It is added to the
agent list in `core/shadow_runner.py`. No other wiring changes.

---

## Lever 1 (build first) — Execution / cost edge  ⟶ highest, most reliable EV

**Why first:** mathematically, cutting round-trip cost by X% is identical to adding X% of
win-rate — and it needs *no new alpha*. The bot already has `smart_executor.py`,
`order_manager.py`, `sim_execution.py`, and a `MAKER_ONLY` config, so this is an *upgrade +
measurement*, not a greenfield build.

**Falsification-first steps**
1. `scripts/exec_cost_audit.py` — from `warehouse.trades`, measure realized per-trade cost
   (entry+exit fees + slippage vs mid) and the **maker-fill rate** actually achieved. This
   establishes the real cost we pay today (the prior backtests *assumed* `COST_TAKER`).
2. `core/agents/maker_exec_optimizer.py` (shadow) — for each would-be entry, simulate a
   **maker-first** placement (post-only limit at/inside touch, timeout → taker fallback) and
   log projected fill price + maker/taker outcome to `shadow_decisions`, alongside the
   current taker-style fill. Reuse `bracket_outcome`'s cost model from
   `scripts/scalp_replay_backtest.py` (`COST_MAKER` vs `COST_TAKER`).
3. **GATE:** does maker-first lift after-cost EV *and* hold an acceptable fill rate (missed
   fills in fast moves are the catch)? Only then route a fraction of live entries maker-first
   via the existing executor — governed by the promotion gate, not a manual flip.

**Deliverable:** an honest "what fees actually cost us" number + a measured maker-vs-taker
EV delta. This alone may be the difference between −EV and breakeven.

---

## Lever 2 — New-information agents  ⟶ the only class that can add real predictive edge

**Why:** these read data *not derived from price*, so they're orthogonal to the NO_EDGE
TA sweep. Build cheapest-information-first; each is falsified exactly like the Kronos work
(directional IC → after-cost OOS EV) **before** it's trusted.

**Honest data caveat (decides feasibility):** the `crypto-market-rank` capability is a
*Claude Code skill available to me interactively* — the bot runs 24/7 headless and needs a
**programmatic HTTP API it can poll**. So each new-info agent needs a real data source the
bot's host can reach (network-allowlist permitting). Order by cost/availability:

- **2a. Derivatives-microstructure agent (free public APIs, do first):**
  `core/data_sources/derivs.py` harvests **open-interest change, liquidations, funding-rate
  term structure, long/short ratio** from Binance/Bybit *public* endpoints (no key needed;
  `mcp_brain` already pulls Binance funding, so the access pattern exists). New ctx keys
  (`oi`, `liquidations`, `funding_term`, `ls_ratio`). `core/agents/derivs_flow_agent.py`
  proposes from squeeze/again-the-crowd setups (e.g. long-liquidation cascade + funding
  flip).
- **2b. Smart-money / social agent (needs a reachable provider):**
  `core/agents/smart_money_agent.py` + `social_flow_agent.py` consuming smart-money inflow /
  top-trader positioning / social-hype ranks. Requires picking a provider with a pollable
  API (and confirming the bot host can reach it). Built behind the same
  `core/data_sources/` interface so it's swappable.

**Per-agent falsification harness:** `scripts/newinfo_probe.py` (generalize
`kronos_ic_probe.py`) — for each new signal, directional IC + hit-rate + significance on a
held-out window, then after-cost bracket EV via `scalp_replay_backtest.run_strategy`.
**GATE:** no IC / doesn't beat 48.6% after costs → STOP, document, don't wire. Only survivors
become shadow agents.

---

## Lever 3 — Meta-labeling selector agent  ⟶ take only the high-EV subset

**Why & when:** `meta_filter.py` itself notes you "cannot train a logistic model on 168
trades with net-negative expectancy." Meta-labeling (López de Prado) only works once enough
labeled decisions exist. So this is **gated on data accumulation** from Levers 1–2 running in
shadow.

**Steps**
1. `core/agents/meta_label_agent.py` — a calibrated classifier (reuse
   `core/probability_calibrator.py` + `core/calibration.py`) trained on the
   `predictions ⋈ trades` / `shadow_decisions` join to predict **P(TP-before-SL)** for a
   *primary* signal, and only pass-through entries above a calibrated-EV threshold.
2. Score with the TP-hit metric panel (AUC / precision@k / Brier / after-cost EV-by-bucket —
   the framework from the previous answer), walk-forward, with `core/stat_tests.py`
   (PBO/DSR) to guard against overfit selection.
3. **GATE:** selective subset beats the unfiltered base after costs, OOS, PBO<0.5 → shadow →
   promotion gate.

---

## Lever 4 — Kronos as a shadow agent  ⟶ conditional on its own gates

Wrap the already-built `core/kronos_forecaster.py` as `core/agents/kronos_agent.py`
(`propose` → long/short `Proposal` from `forecast()` `p_up`/`exp_ret`). **Only after** Kronos
clears its own Phase 2 IC + Phase 3 after-cost gates (which need the weights on your machine +
post-cutoff candles — see `research/kronos_eval_2026_05_29.md`). Until then it stays a probe,
not an agent. Torch stays isolated in `requirements-kronos.txt`; the agent imports the
forecaster lazily so the bot runtime never pulls torch.

---

## Optional — actual MCP server (only if you want LLM-driven *analysis*, not trading edge)

If by "MCP" you mean a real **Model Context Protocol server** (so an LLM/Claude can query the
bot's warehouse, positions, and agent stats via tools): `mcp/trading_server.py` via the
`mcp-builder` skill, exposing **read-only** tools (`get_open_positions`,
`query_shadow_performance`, `get_agent_leaderboard`, `explain_last_decision`). This improves
**observability and your ability to ask questions**, not trading edge, and never places
orders. Build only if you want it.

## Critical files

- **New agents:** `core/agents/maker_exec_optimizer.py`, `derivs_flow_agent.py`,
  `smart_money_agent.py`, `social_flow_agent.py`, `meta_label_agent.py`, `kronos_agent.py`.
- **New data:** `core/data_sources/derivs.py` (+ provider interface); cached/rate-limited.
- **New probes:** `scripts/exec_cost_audit.py`, `scripts/newinfo_probe.py` (generalize
  `scripts/kronos_ic_probe.py`).
- **Edit (small, additive):** `core/shadow_runner.py` (register new agents);
  `core/bot_engine.py:318 _shadow_ctx_for_symbol` (add new ctx keys from the harvester).
- **Reuse:** `core/agents/base_agent.py` (`Proposal`/`BaseAgent`), `coordinator.py`,
  `execution_agent.py`, `risk_agent.py`; `multi_agent_promotion_gate.py`, `promotion_gate.py`;
  `scripts/scalp_replay_backtest.py` (`run_strategy`/`bracket_outcome`/`COST_*`);
  `probability_calibrator.py`, `calibration.py`, `stat_tests.py`; `warehouse` tables
  (`shadow_decisions`, `predictions`, `trades`).
- **Untouched:** `_execute_open` live path, `OPERATING_MODE`, latches, bot `requirements.txt`.

## Verification (per lever)

- Probe scripts print IC / after-cost WR & EV with the explicit 48.6% breakeven + per-fold
  breakdown; leakage/lookahead guards asserted.
- New agents: unit tests with a stubbed `ctx` (pattern: `tests/test_kronos_forecaster.py`'s
  injected stub) → `propose()` returns well-formed `Proposal`s; `ShadowRunner` tick test
  shows rows land in `shadow_decisions`; `multi_agent_promotion_gate` test on synthetic
  windows. `ruff` clean; full `pytest tests/` green.
- Confirm in each PR: `_execute_open` unchanged, no `OPERATING_MODE` flip, bot imports clean
  without optional heavy deps.

## Suggested sequence (and why)

1. **Lever 1 (execution/cost)** — highest, surest EV; no new alpha needed.
2. **Lever 2a (free derivatives data)** — cheapest genuinely-new information.
3. **Lever 2b (smart-money/social)** — once a pollable provider is confirmed reachable.
4. **Lever 3 (meta-labeling)** — after shadow data accumulates.
5. **Lever 4 (Kronos agent)** — after Kronos clears its own gates.
6. Optional MCP server — anytime, for observability only.

## Likely outcome (stated honestly)

Given the NO_EDGE base rate, Levers 2–4 will *probably* fail their falsification gates — and
that's a valid, money-saving result. **Lever 1 (cost) is the one most likely to actually move
profitability**, because it attacks the proven wall (fees) rather than hunting for elusive
alpha. No capital is risked at any step until something clears the promotion gate.
