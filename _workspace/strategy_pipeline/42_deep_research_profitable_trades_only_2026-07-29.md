# Profitable Trades Only: Research + Action Report
*Generated: 2026-07-29 | Sources: 12 | Confidence: High (local ledger), Medium (external carry APR)*

**Owner ask:** “I want this trading bot to do Profitable trades ONLY. /deep-research and make it happen.”

## Executive Summary

No trading system can **guarantee** only profitable fills. What *can* be enforced is: **refuse opens with measured after-cost negative expectancy**. On this bot, MCP AccBand directional PAPER is **CONFIRMED_NO_GO for profit** (expectancy ≈ −0.24R; warehouse all-time −$674). The only ledger-validated profit class is **F1 funding/basis carry**, which is currently **idle** (latest gate log: `net_edge_bps` negative / funding ≤ 0). External research agrees: after-cost profit in crypto perps concentrates in delta-neutral carry when funding clears costs — not in indicator/AccBand directional stacks.

**Action taken:** set `MCP_DIRECTIONAL_ECONOMIC_GATE_MODE=strict` so AccBand/`paper_fallback` can no longer admit −EV directional OPENs. F1 remains allowed only when its own net-edge gate passes (already fail-closed).

## 1. Local evidence — why the bot was losing

- AccBand dual-goal screen: **0/12 cells** clear WR-in-band ∧ EV>0 ([ledger](.claude/skills/refuted-families-ledger/SKILL.md) 2026-07-24).
- Warehouse: all closed n≈2470, WR 34.6%, PnL ≈ −$674; recent 24h still net negative despite ~59% WR.
- `paper_fallback` (PAPER+MAX_FLOW_BAND) admits when TP clears stressed RT **without** a promoted model — restores flow, **not** edge (journal 2026-07-21).
- F1 `carry_gate_log.jsonl` recent samples: `ok=False`, reasons like `funding_rate <= 0`, `net_edge_bps` ≈ −100s.

## 2. External research — what is after-cost profitable

- Spot+short-perp / basis carry when funding persistently above all-in costs ([Kraken funding arb](https://www.kraken.com/learn/futures-trading-funding-rate-arbitrage); [BackQuant basis](https://www.backquant.com/learn/basis-trade); [Steyble perp basis 2026](https://steyble.com/blog/perp-basis-trading-capturing-carry)).
- Cross-venue funding spread capture — regime-dependent, friction-sensitive ([Steyble cross-exchange](https://steyble.com/blog/funding-rate-arbitrage-cross-exchange)).
- Directional TA / momentum / AccBand-style geometry: **not** supported as after-cost profit for this install (ledger STOP / NO_GO rows).

## 3. What “profitable only” means here (binding)

| Allowed | Blocked |
|---------|---------|
| F1 carry when `net_edge_bps` clears gate | MCP AccBand directional under `paper_fallback` |
| Future CONFIRMED_GO + owner-signed promotion | RSI/breakout/TSMOM/pullback live opens |
| Flat book when no edge | “Force trades” for WR research |

Guaranteed win-rate or guaranteed profit: **impossible**. Fail-closed no-edge is the honest product.

## 4. Implementation

1. `.env`: `MCP_DIRECTIONAL_ECONOMIC_GATE_MODE=strict` (was `paper_fallback`).
2. Supervisor restart so in-process banner shows `EconGate : mode=strict`.
3. AccBand may stay ON for telemetry; **opens** fail `economic_gate_model_missing` until a legitimate promoted model exists (none ever has).
4. F1 unchanged — enters only on positive net edge (currently none).

## Key Takeaways

1. Stop bleeding AccBand PAPER — **done via strict economic gate**.
2. Profit path = F1 when funding compresses ends — wait for edge, don’t force.
3. Do not reopen AccBand-for-profit without ledger reopen bar.

## Sources

1. Refuted-families ledger (AccBand dual-goal NO_GO; F1 validated)
2. Local warehouse + `data/carry_gate_log.jsonl`
3. Kraken / BackQuant / Steyble carry guides (2026)
4. Prior pipeline reports 30_/37_/40_

## Methodology

Sub-questions: (1) Can AccBand be profitable? (2) What external strategies clear costs? (3) What can this bot enforce today? (4) F1 live edge status?  
Firecrawl/Exa unavailable — WebSearch + local ledger/warehouse.
