# Accuracy + Profitability Levers for This Bot: Research Report
*Generated: 2026-08-11 | Sources: 18+ local/external | Confidence: High on local doctrine; Medium on external carry yields*

## Executive Summary

Nothing in current public research **authorizes a new live directional edge** for this Binance/Bybit/Bitget PAPER bot. External literature still points strongest at **delta-neutral funding/cash-and-carry when rates clear all-in costs** ([Kraken Learn](https://www.kraken.com/learn/futures-trading-funding-rate-arbitrage); [Button](https://button.xyz/blog/funding-rate-arbitrage)), while this repo’s **F1 carry remains structurally idle** (edges below cost). Directional “accuracy” (high WR) without positive after-cost expectancy is **geometry, not profit** — today’s PAPER UTC-day lane shows WR≈40% and EV≈−0.26R (`data/goal_progress.json`). The highest-ROI path is: keep AccBand honesty + maker-first costs, remediate F1 only when gate-log net edge returns, advance **OI×funding veto** and **C2** when accrual clears n≥30, and apply **meta-labeling only on validated F1** (queue row 9) — never on a no-edge MCP primary ([arXiv 2606.00060](https://arxiv.org/abs/2606.00060) via local 63_ disposition).

## 1. What “accuracy” vs “profitability” must mean here

| Metric | Bot truth | External anchor |
|--------|-----------|-----------------|
| Win rate | AccBand targets band by **geometry** (TP frac of SL); not an edge claim | Break-even WR = SL/(TP+SL); high WR can still lose ([Gainium EV math](https://gainium.io/tools/winrate)) |
| Expectancy | Net after fees/slip/funding per closed outcome | EV = WR×avg win − (1−WR)×avg loss |
| Live status | PAPER + MAX_FLOW_BAND; SCALP tier off; no CONTROLLED_LIVE from narrative | Paper vs live gap often 20%+ when fees wrong ([ChangeHero bot guide](https://changehero.io/blog/best-ai-crypto-trading-bot-overview-comparison/)) |

**Local snapshot (2026-08-11):** `paper_futures_current_utc_day` WR 0.40, expectancy −0.256R, `INSUFFICIENT_SAMPLE`; prior day similar. Listing-short probe 1/30 resolved.

## 2. Families with the best *honest* profit path (still gated)

### A. Funding / cash-and-carry (F1) — still the only validated *family*
- Mechanism: long spot + short perp (or venue carry) when funding persistently above all-in costs ([Kraken](https://www.kraken.com/learn/futures-trading-funding-rate-arbitrage); [AlgoKing harvest](https://algos.pro/posts/2026-05-08-funding-rate-harvest-neutral-crypto-carry/)).
- Risks: rate flip, basis, fees eating edge at compressed rates ([Button](https://button.xyz/blog/funding-rate-arbitrage)).
- **This bot:** F1 runner idle; edges reported −25…−41 bps. Gate: ≥30 **positive** net-edge episodes in `carry_gate_log.jsonl` before remediation force-on (queue #5). HL conditioner harvest has **246** lines — accrue toward paired screen (queue #4), no F1 force-on.

### B. Meta-labeling / sizing — accuracy of *when* to trade
- López de Prado-style primary direction + secondary take/skip/size ([Syntium](https://syntiumalgo.com/meta-labeling-for-trading-signals/); [Dukesan framework](https://github.com/Dukesan-ai/crypto-ml-trading-framework)).
- **Binding local rule (63_):** meta-labeling on a **no-edge primary only reduces bleed**; does **not** beat doing nothing. Queue row 9 = **F1-only** meta-sizing after hashed prereg. Do **not** meta-label MCP AccBand as “accuracy upgrade.”

### C. Regime / veto overlays — accuracy of *skipping*
- OI×funding joint regime (queue #3) with jump-penalty HMM note (arXiv 2402.05272) — **INTERNAL veto**, never directional.
- Soft-stale entry block + band regime filter (optional) — already ops doctrine.
- Maker-first blended fee realism: wrong fee constant destroys EV ([JackTrader fee checklist](https://dev.to/jacktrader/grid-bot-fees-are-quietly-eating-your-returns-the-quant-fee-checklist-2cbc)).

### D. Accrual clocks that can unlock screens
| Clock | Local count now | Gate |
|-------|-----------------|------|
| Deribit C2 snaps | **34** jsonl lines | Screen only at ≥30 **events/cell** + percentile history |
| HL funding | **246** lines | ≥30 paired HL×local episodes |
| Carry gate log | **154k** lines | Need positive net-edge subset ≥30 |

## 3. What does **not** make this bot profitable (refuse)

- Whale alert / Arkham chase → RECORD-NO-ACTION ([28_](_workspace/strategy_pipeline/28_whale_flow_verdict.md)).
- Public Pine “profitable” scripts / ICT / V33X marketing ([prior pine report](docs/research/deep-research_pine_tv_brave_simulation_2026-08-11.md)).
- VPIN jump veto, liq-cascade fade — CONFIRMED_NO_GO.
- MA/EMA/RSI/pullback live installs — ledger STOP / refuted.
- Escalating B13 smart-money to required entry without GO.
- Live scalp tier without after-cost GO (`SCALP_TIER_ENABLED` stays false).
- Guaranteeing “accuracy” by raising WR alone while EV stays negative.

## 4. Ranked actions for *this* codebase (decision-oriented)

1. **Measure, don’t invent:** keep PAPER AccBand + `run_research_loop_tick` + MC `paper_research` (shipped `efdea7e`). Optimize exits/geometry only under hashed prereg when SP1 n≥30 (queue #6 locked).
2. **F1 revival watch:** daily scan of carry gate log for after-cost positive episodes; only then remediation code.
3. **Next screenable research:** draft hashed prereg for **OI×funding veto** (warehouse-local) — Stage-0 fire counts before full screen.
4. **C2:** continue Deribit accrual; do not early-screen at n=34 raw lines.
5. **HL×F1 conditioner:** Stage-0 when ≥30 paired episodes exist.
6. **Execution hygiene:** verify maker-share in PAPER fills; stress fees in econ gate (already paper_fallback under MAX_FLOW_BAND).

## Key Takeaways

- **No new GO found** that “makes the bot trade profitably with accuracy” today.
- Profit path = **carry when edge clears costs** + **vetoes that cut bleed** + **honest AccBand measurement**.
- Accuracy path ≠ higher WR marketing; it is **meta/veto on a validated primary** and **cost-correct fills**.
- Next concrete research ticket: **OI×funding veto prereg** (local data) while C2/HL clocks run.

## Sources

1. [Kraken — Funding rate arbitrage](https://www.kraken.com/learn/futures-trading-funding-rate-arbitrage)  
2. [Button — Funding arb at scale](https://button.xyz/blog/funding-rate-arbitrage)  
3. [AlgoKing — Funding harvest](https://algos.pro/posts/2026-05-08-funding-rate-harvest-neutral-crypto-carry/)  
4. [Axel Adler — BTC cash-and-carry](https://axeladlerjr.com/bitcoin-cash-and-carry-strategy/)  
5. [Syntium — Meta-labeling](https://syntiumalgo.com/meta-labeling-for-trading-signals/)  
6. [Dukesan — Meta-labeling framework](https://github.com/Dukesan-ai/crypto-ml-trading-framework)  
7. [ChangeHero — AI bot overview](https://changehero.io/blog/best-ai-crypto-trading-bot-overview-comparison/)  
8. [Gainium — WR/EV math](https://gainium.io/tools/winrate)  
9. [JackTrader — Fee blend](https://dev.to/jacktrader/grid-bot-fees-are-quietly-eating-your-returns-the-quant-fee-checklist-2cbc)  
10. [JackTrader — Maker fill probability](https://dev.to/jacktrader/maker-taker-economics-for-grid-bots-when-post-only-actually-pays-4ihm)  
11. Local: `30_edge_queue`, `28_whale_flow_verdict`, `63_` learning dispositions, `goal_progress.json`, evolve ADR 2026-08-11  

## Methodology

Sub-questions: (1) What external strategies still claim after-cost profit? (2) What improves precision without inventing edge? (3) What does *this* bot’s queue and live metrics allow next? (4) What must stay refused?

WebSearch/WebFetch 2025–2026 sources + local warehouse clocks. Firecrawl/Exa unavailable. No strategy code wired from this report.
