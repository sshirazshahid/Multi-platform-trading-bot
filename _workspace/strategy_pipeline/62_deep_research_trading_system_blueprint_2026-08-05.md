# Trading System Blueprint: Plan, Design, Ops, Strategies
*Generated: 2026-08-05 | Sources: 20+ external + this repo | Confidence: High (architecture); Medium (external edge claims)*

**Scope:** Answers for *this* Multi-platform trading bot (`overhaul/de-emotion`), not a greenfield fantasy. External research is used to validate or pressure-test what we already run.

**Binding doctrine (repo):** strategy-evidence pipeline (prereg → after-cost screen → audit → log-only shadow → frozen promotion). De-Emotion: LLM / F&G / news sentiment stay **off the entry path**. Modes: OBSERVATION → PAPER → CONTROLLED_LIVE (double latch).

---

## Executive Summary

Build (and keep rebuilding) a trading bot as a **layered risk machine with research lanes**, not as a chart-guessing chatbot. Industry 2025–26 stacks converge on: WebSocket market + derivatives feeds → feature store → deterministic strategies → hard risk gates → execution with exchange-native stops → out-of-band watchdog ([Agentic Trading Hub](https://cedric-hidot.com/en/projects/agentic-trading-hub); [Edgeless Lab autonomous perps](https://edgelesslab.com/blog/autonomous-perp-trading-stack/); [NexusFi monitoring](https://nexusfi.com/a/automation/trading-bot-monitoring)).

This repo already implements that shape: `BotEngine` + `mcp_brain` / machine signals, `order_manager` + `sim_execution`, `risk_manager`, shadow probes, `health_watchdog`, Mission Control, F1 carry, liquidation harvest, and a log-only F&G+liq SHORT-bias recorder (prereg 61). Scalping and chart patterns are **fee-fragile** and must clear after-cost gates or stay paper/shadow ([LedgerMind scalping 2026](https://theledgermind.com/automated-crypto-scalping-strategies/); [AutoQuant arXiv:2512.22476](https://arxiv.org/pdf/2512.22476)).

Sentiment belongs in **intel / veto telemetry**, not as the trade authority — De-Emotion is the correct production stance after this bot’s own evidence.

---

## 1. How to plan and set up a trading bot

### Plan (phased)

| Phase | Goal | Exit criteria |
|-------|------|----------------|
| 0 | Keys, venues, PAPER mode, warehouse empty-OK | Heartbeat green; no live latch |
| 1 | Data plane: OHLCV, funding, liquidations, positions reconcile | Harvesters writing; watchdog quiet |
| 2 | Decision plane: scorer + risk + sim fills | PAPER opens with provenance |
| 3 | Research plane: screens + shadow probes | Funnel JSON updating |
| 4 | Ops plane: Mission Control, schtasks, email alerts | Stuck/orphan alerts actionable |
| 5 | Live | Owner-signed checklist + CONTROLLED_LIVE double latch only |

### Setup (this machine / repo)

1. `.env` from `.env.example` (Binance / Bybit / Bitget). Never commit secrets.
2. `OPERATING_MODE=PAPER`; profile via `.env` only (supervisor inherits env — restart supervisor after `.env` edits).
3. Exchange clients under `exchanges/`; runtime under `core/`; config package under `config/`.
4. Windows: `TradingBot-24x7` schtask flag-less; intel tasks (promotion funnel, liquidations, regime bias) staggered.
5. Verify boot banner in-process (EntryFloor / AccBand / EconGate) — never trust a fresh `python -c import`.

External parallel: paper-to-live graduation with Sharpe / DD / WR gates before capital ([Edgeless Lab](https://edgelesslab.com/blog/autonomous-perp-trading-stack/)).

---

## 2. How to design the trading system

### Layer diagram

```text
[Feeds] WS OHLCV / funding / liq / news_cache
    ↓
[Features] FeatureVector, regime, microstructure
    ↓
[Signal] MCP / machine / F1 carry  ——  [Shadow probes] log-only
    ↓
[Gates] meta-filter, economic gate, band regime, strategy_spec, risk
    ↓
[Exec] limit-prefer + exchange SL/TP  |  sim_execution in PAPER
    ↓
[State] positions.json + warehouse.sqlite
    ↓
[Ops] health_watchdog · Mission Control · promotion_funnel
```

### Design principles (external ∩ local)

- **Deterministic core, optional AI advisory.** Multi-agent LLM consensus is popular ([Agentic Hub](https://cedric-hidot.com/en/projects/agentic-trading-hub); [DEV Claude bot](https://dev.to/dineshstack/how-i-built-an-ai-crypto-trading-bot-with-claude-ai-426e)) but must sit *above* hard gates — never replace them. This repo’s De-Emotion removes sentiment from entries.
- **Cost-first backtests.** Fee-only backtests inflate results vs funding+slippage ([AutoQuant](https://arxiv.org/pdf/2512.22476); [Kiploks](https://kiploks.com/research/how-to-backtest-crypto-strategy-realistic-assumptions)).
- **Fail closed on missing authorization** (strategy_spec, live gate, incident latch).
- **Separate research from production.** ShadowRunner probes cannot place orders.

Hard portfolio rails (charter): ≤3% risk/trade, ≤12% total exposure, −8% stop guardian, ≤2.5× leverage ceiling in doctrine (runtime tiers may differ under PAPER research profiles — treat PROFILE as explicit owner choice).

---

## 3. Tools / connectors / plugins / skills / MCP

### Use (already in stack)

| Kind | What | Role |
|------|------|------|
| Connector | ccxt + venue clients | Exec + market |
| Runtime | `main.py` → `BotEngine` | Loop |
| Research skills | `strategy-evidence-pipeline`, ledger, after-cost screening | Edge discipline |
| Ops skills | gstack `/qa` `/ship` `/investigate` `/browse` | DevEx |
| MCP (local) | `mcp_server/trading_bot_mcp.py` | **Read-only** warehouse / shadow_vs_live |
| UI | Mission Control (`mission_control/`), `dashboard.py` | Operator |
| Harvesters | funding, liquidations, microstructure, intel synthesis | Data plane |
| Recorder | `scripts/record_regime_short_bias.py` | Log-only F&G+liq env |

### Evaluate / build carefully

| Candidate | Verdict for *this* bot |
|-----------|-------------------------|
| [mcp-ccxt](https://github.com/dante1989/mcp-ccxt) / [OmniTrade MCP](https://github.com/Connectry-io/omnitrade-mcp) | Useful for **agent ops**, not for bypassing order_manager |
| [OKX Agent Trade Kit](https://github.com/okx/agent-trade-kit) | Venue-native MCP; only if OKX is a first-class venue; prefer `--read-only` for agents |
| Firecrawl/Exa | Research MCP (not configured here → WebSearch fallback) |
| GBrain `/setup-gbrain` | Cross-session memory; blocked until CLI present |
| Ruflo agents | Dev swarm only — not bot runtime |

**Rule:** Any MCP that can `create_order` stays out of unsupervised agent loops. Execution authority stays in `order_manager` + live_gate.

---

## 4. Continuous market / trend / liquidation / news monitoring

| Stream | Mechanism | Cadence | Decision path? |
|--------|-----------|---------|----------------|
| Price/vol | Exchange WS / OHLCV cache | continuous | Yes (features) |
| Funding | `funding_history`, carry gate log | 8h / harvest | F1 / probes |
| Liquidations | `harvest_liquidations.py` → `liquidations_history.jsonl` | continuous WS | Research / prereg 41, 61 |
| F&G | alternative.me via `record_regime_short_bias.py` → `news_cache` | hourly schtask | **No** (log-only) |
| News | `news_scanner` / cache | ~30m | **No** on entries (De-Emotion) |
| Trends | MCP EMA/ADX/RSI; TSMOM/breakout probes | cycle | Scoring / shadow |
| Operator | Mission Control + watchdog email | continuous | Ops |

Honesty check (2026-08-05 live): F&G 27 + Binance ALL long liq ~$50M fired Θ25M cell only — vendor ~$208M multi-venue prints ≠ our forceOrder series.

---

## 5. Stale / stuck positions

### Failure modes (external)

- Local state lags fills → duplicate entries / WEL blowups ([Passivbot #980](https://github.com/enarjord/passivbot/issues/980)).
- Restart timestamp skew deadlocks trailing ([Passivbot #1323](https://github.com/enarjord/passivbot/issues/1323)).
- Best practice: reconcile vs exchange every ≤60s; kill-switch usually **blocks entries** first; flatten only on max DD / catastrophic divergence ([Rataash](https://rataash.com/blog/kill-switches-reconciliation-idempotency/); [NexusFi](https://nexusfi.com/a/automation/trading-bot-monitoring)).
- Exchange-native reduce-only SL survives bot crash ([NexusFi risk](https://nexusfi.com/a/automation/automated-risk-controls)).

### This bot’s handling

| Class | Detection | Action |
|-------|-----------|--------|
| Orphan warehouse OPEN | `health_watchdog._check_stuck_open_positions` vs `positions.json` | Alert (WARN); operator reconcile |
| Stale heartbeat | watchdog `heartbeat_stale` | Alert / heal path |
| Stale maker intents | `stale_maker_intents` | WARN — resolver starvation class |
| Model age | gate_health soft multiplier / warn | Surface staleness |
| Time / band exits | accuracy-band / tier time-exit / planned TP | Close by geometry rules |
| Missing exchange SL | `_sl_failed` + EMERGENCY alert | Manual / heal |
| Incident latch | `risk_incident_latch.json` | Blocks new entries until cleared |

### Recommended upgrade path (not auto-shipped)

1. **Reconcile loop:** exchange positions ↔ `positions.json` ↔ warehouse OPEN every N minutes; auto-close warehouse orphans when exchange flat.
2. **Max-hold force-flat:** per strategy family (scalp shorter than swing); escalate limit → aggressive limit → market with reject fallback.
3. **Never flatten on soft staleness alone** (spread/latency) — block entries + alert; flatten on max DD / ghost position confirmed.
4. Out-of-band watchdog process that can flatten if main heartbeat dies ([NexusFi](https://nexusfi.com/a/automation/trading-bot-monitoring)).

---

## 6. Continuous TA, backtesting, sentiment

### Technical analysis (continuous)

- Live: MCP required conditions (EMA gap, alignment, RSI, ADX) + bonuses; ATR SL/TP.
- Research: screens in `research/`, probes in `core/agents/`.
- Chart patterns: academic edge was stronger in early/inefficient BTC markets ([Mt.Gox study](https://link.springer.com/article/10.1186/s40854-025-00763-2); [SSRN TA crypto](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=3387950)). Commercial “82% success” pattern rates ([altFINS](https://altfins.com/knowledge-base/chart-patterns/)) are **not** after-cost promotion evidence — treat as scout seeds only.

### Backtesting

- Always include fees, funding, slippage; stress 1.5–2× costs ([AutoQuant](https://arxiv.org/pdf/2512.22476); [Forvest](https://forvest.io/blog/backtest-optimization-crypto/)).
- Walk-forward + holdout; track DSR / PBO (bot already has promotion_gate notions).
- Prereg hash **before** outcomes (pipeline binding rule).

### Sentiment

- Log / regime telemetry only (prereg 61).
- Do not wire F&G or Twitter into `mcp_brain` / `order_manager` (purity tests enforce).
- Optional future: sentiment as **veto** under a new prereg, never as sole entry signal.

---

## 7. Solid scalping FUTURES (L/S) + SPOT mechanism

### Futures scalping (production-grade shape)

External consensus ([TraderNest](https://tradernest.ai/blog/scalping-strategies); [Mudrex](https://mudrex.com/learn/scalp-trading-in-crypto-futures/); [WazirX](https://wazirx.com/blog/scalping-strategies-in-crypto-futures/)):

- Liquid majors only; check book depth before size.
- Leverage tool not edge — prefer low/isolated (3–5× cited; respect local leverage policy).
- Define invalidation **before** entry; targets must clear round-trip fees (fee-to-gross &lt; ~25%).
- Setups: VWAP reclaim, EMA pullback, ORB — with regime matrix (MR vs trend).
- Kill: daily loss circuit, max consecutive losses, fill-rate / slippage monitors.

**This bot:** SCALP is a **leverage tier / time-exit family**, not legacy `ScalpingStrategy`. AccBand geometry is WR-by-construction research, not proven edge. Ship scalp only behind strategy_spec + economic gate + owner profile.

### Spot mechanism

- Separate capital lane (`spot_manager` / allocator): HOLD / SCALE_OUT / SELL / HEDGE.
- Spot has **no** exchange-side SL by design — local monitor + smaller size or futures hedge.
- Prefer maker / limit within 0.2% of ask (charter). DCA live remains hard-OFF unless owner flips.

### Dual book control

- Correlation manager reduces double-count risk.
- Max exposure 12%; never scalp spot + futures same beta without explicit hedge policy.

---

## 8. Test → simulate → find → implement strategies

```text
Idea → ledger check (refuted?) → prereg + hash
  → Stage-0 fire rate (≥30?) → after-cost screen
  → honesty audit (both-agree) → CONFIRMED_GO?
      NO → ledger / ACCRUE
      YES → shadow probe only → ≥30 resolved + frozen gate → owner sign-off → paper/live
```

- PAPER: `sim_execution` (slippage, wick SL/TP, funding).
- Tests: pytest TDD for probes and gates (`tests/test_regime_short_bias.py`, purity, etc.).
- Implement via shadow-integrator patterns; never “just enable” MCP SHORT from narrative.

---

## 9. Reading charts / profitable patterns (L/S/SPOT)

**Operator method (research, not live authority):**

1. Higher TF bias (4h/1d structure) → lower TF trigger.
2. Confirm with volume / liquidity; skip thin books.
3. Long: HH/HL + pullback to MA/VWAP + RSI not blown; Short: LH/LL + failed reclaim + funding crowded long (measure, don’t mythologize).
4. Spot: prefer trend-follow with wider stops; avoid scalp-fee death.
5. Log every candidate to warehouse; compare forward WR/expectancy after costs.

**Promotion bar:** pattern scanner WR claims without stressed costs = scout only.

---

## 10. Learn / research / trade / monitor / upgrade / restructure

| Loop | Cadence | Artifact |
|------|---------|----------|
| Trade | 90s–5m cycles | positions, mcp_decisions |
| Monitor | watchdog + MC | alerts, latch |
| Research | ≤1 scout/day protocol | `_workspace/strategy_pipeline/` |
| Learn | learning_engine / knowledge_model | insights HTML |
| Upgrade | evidence GO only | probe → funnel → signed dossier |
| Restructure | De-Emotion / Phase N audits | delete dead paths; purity tests |
| Refactor | TDD + surgical diffs | no speculative frameworks |

**Rebuild trigger:** expectancy persistently negative after cost stress, or architecture blocks (economic_gate, latch storms). Rebuild by **cutting authority**, not adding indicators.

---

## Key Takeaways

1. Keep De-Emotion + evidence pipeline; do not “AI-trade” liquidations/F&G.
2. Ops excellence (reconcile, stuck orphans, exchange SL) beats new patterns.
3. Scalp futures only if after-cost expectancy survives 2× fee/slip stress on liquid names.
4. MCP for agents = read-only by default; never unsupervised `create_order`.
5. Upgrade via shadow → frozen gate → owner sign-off — never narrative SHORTS.

---

## Sources

1. [TraderNest Scalping 2026](https://tradernest.ai/blog/scalping-strategies)
2. [Mudrex crypto futures scalp](https://mudrex.com/learn/scalp-trading-in-crypto-futures/)
3. [LedgerMind automated scalping](https://theledgermind.com/automated-crypto-scalping-strategies/)
4. [WazirX scalp basics](https://wazirx.com/blog/scalping-strategies-in-crypto-futures/)
5. [Tim Warren futures risk](https://timwarrentrading.com/blog/2026-05-18-risk-management-for-crypto-futures-traders)
6. [Agentic Trading Hub](https://cedric-hidot.com/en/projects/agentic-trading-hub)
7. [Edgeless Lab perp stack](https://edgelesslab.com/blog/autonomous-perp-trading-stack/)
8. [DEV Claude trading bot](https://dev.to/dineshstack/how-i-built-an-ai-crypto-trading-bot-with-claude-ai-426e)
9. [icyponds/TradeBot](https://github.com/icyponds/TradeBot)
10. [AutoQuant arXiv:2512.22476](https://arxiv.org/pdf/2512.22476)
11. [Kiploks realistic backtests](https://kiploks.com/research/how-to-backtest-crypto-strategy-realistic-assumptions)
12. [Forvest backtest optimization](https://forvest.io/blog/backtest-optimization-crypto/)
13. [For Traders bias in backtesting](https://fortraders.com/blog/how-to-avoid-bias-in-backtesting)
14. [Mt.Gox chart patterns (Financial Innovation)](https://link.springer.com/article/10.1186/s40854-025-00763-2)
15. [SSRN Technical Analysis and Cryptocurrencies](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=3387950)
16. [altFINS pattern guide](https://altfins.com/knowledge-base/chart-patterns/)
17. [OKX Agent Trade Kit](https://github.com/okx/agent-trade-kit)
18. [mcp-ccxt](https://github.com/dante1989/mcp-ccxt)
19. [OmniTrade MCP](https://github.com/Connectry-io/omnitrade-mcp)
20. [NexusFi bot monitoring](https://nexusfi.com/a/automation/trading-bot-monitoring)
21. [Rataash kill-switches](https://rataash.com/blog/kill-switches-reconciliation-idempotency/)
22. [NexusFi automated risk](https://nexusfi.com/a/automation/automated-risk-controls)
23. [Passivbot stale position #980](https://github.com/enarjord/passivbot/issues/980)
24. Repo: `core/health_watchdog.py`, `core/regime_short_bias.py`, strategy-evidence pipeline skill

## Methodology

Searched 10+ web queries (scalping, monitoring, backtest overfitting, chart patterns, MCP trading, stale positions). Deep-read several full pages via fetch. Cross-checked against local modules (watchdog stuck/orphan, De-Emotion purity, prereg 61). Firecrawl/Exa MCP not configured — WebSearch/WebFetch used as substitute.

Sub-questions covered: setup, system design, tooling/MCP, continuous monitoring, stale/stuck, TA/backtest/sentiment, scalp L/S+spot, strategy lifecycle, chart reading, continuous improvement.
