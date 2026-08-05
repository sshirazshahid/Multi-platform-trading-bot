# Futures Trading Strategies: Research Report
*Generated: 2026-07-31 | Sources: ~22 unique URLs (web + arXiv) | Confidence: Medium–High on carry compression & cost-of-trading; Medium on practitioner cascade/pairs claims; Low that any new TA family clears this bot’s frozen promotion gate without a hashed prereg*

**Scope (defaults for this bot):** crypto **perpetual** futures first; traditional CTA/managed futures as contrast; after-cost / walk-forward honesty; map to current PAPER posture (F1-only until funding clears; AccBand = WR research, not edge).

**Tooling note:** Firecrawl/Exa MCP were not available in this environment; research used WebSearch + WebFetch/arXiv HTML. Coverage is thinner than a dual-MCP deep-research pass.

## Executive Summary

The durable story in **crypto perpetual futures** is structural: funding/basis carry and forced-flow microstructure beat chart-pattern “edge.” Academic Fact 9 evidence shows crypto carry was extraordinarily profitable historically (full-sample Sharpe ~6.45) but **compressed sharply from 2024 and turned negative in 2025** on the studied BTC funding construction ([arXiv 2510.14435](https://doi.org/10.48550/arxiv.2510.14435)). That matches this bot’s live F1 state: `idle_no_edge` with healthy feeds, not a dead runner.

Hourly / high-turnover directional strategies routinely look strong **gross** and collapse under ~10 bps costs unless a **cost-aware filter** slashes turnover ([arXiv 2606.00060](https://arxiv.org/html/2606.00060v1)). Retail multi-hypothesis suites with ~13 bps RT and multiplicity control report a clean **no-edge** default ([retail-crypto-alpha](https://github.com/Mykola-Quant/retail-crypto-alpha)). Institutional **CTA/managed futures** (multi-asset trend) remain a legitimate diversifier in 2026 YTD (~5–9% index range) but are a different product than single-venue crypto AccBand ([Top Traders Unplugged June 2026](https://www.toptradersunplugged.com/trend-following-performance-report-june-2026/)).

**Implication for this repo:** keep F1 fail-closed until net edge clears; do not reopen AccBand for fill count; any new family needs hashed prereg + after-cost screen — narrative blogs are not evidence.

## 1. Funding / basis carry (structural premium)

### Mechanism
Perps have no expiry; funding transfers between longs and shorts to anchor perp≈spot. Classic delta-neutral harvest: long spot / short perp when funding >0 (longs pay shorts); mirror when persistently negative ([Kraken learn](https://www.kraken.com/learn/futures-trading-funding-rate-arbitrage), [BTSE](https://www.btse.com/blog/funding-rate-arbitrage-tutorial/)).

### Evidence quality
- **Academic (binding):** Schmeling-style crypto carry on Binance BTC 8h funding, Aug 2020–May 2025: full-sample Sharpe ~6.45; **from 2024 Sharpe falls to ~4.06; turns negative in 2025**. Profit mostly from funding (~8% mean, ~0.8% vol in full sample). Compression threatens “synthetic dollar” / Ethena-class products ([arXiv 2510.14435 Fact 9](https://doi.org/10.48550/arxiv.2510.14435)).
- **Empirical CEX/DEX study:** funding arb vs HODL can show large gross returns in selected scenarios (paper claims up to ~115.9% / 6m with small loss tails) — treat as **scenario-sensitive**, not a free lunch; costs, leverage, and venue matter ([Blockchain Research and Applications 2025](https://doi.org/10.1016/j.bcra.2025.100354)).
- **Practitioner guides (2026):** majors often only mid-single-digit APY after competition; mid-caps higher but basis/exit risk; meme spikes are short-lived; borrow + fees can erase headline APY ([Hedonist intel](https://intel.hedonist.trading/blog/funding-rate-arbitrage-explained/), [Finder arb](https://finder-arbitrage.com/blog/funding-arbitrage)).

### Risks (not optional)
Rate flip, basis at unwind, liquidation despite hedge, fee amortization (often days–weeks to recover entry costs), venue/custody risk ([Kraken](https://www.kraken.com/learn/futures-trading-funding-rate-arbitrage)).

### Bot mapping
F1 is the correct family class. Current idle under funding≤0 / contango is **evidence-aligned**, not a bug. Do **not** loosen funding thresholds to force opens.

## 2. Directional / ML / high-turnover futures

### Cost is the strategy
Walk-forward hourly BTC (2018–2026, 27 folds): XGBoost/LSTM/iTransformer can be strongly positive **gross**; after **10 bps** costs, naive sign strategies go deeply negative (e.g. −64% to −99% ann. in cited configs). A cost-aware filter (trade only if |forecast| > cost threshold) cuts turnover and can restore selected positive nets (example long-only XGB ~65% ann., Sharpe ~1.09) — still fragile under bootstrap / model-selection tests ([arXiv 2606.00060](https://arxiv.org/html/2606.00060v1)).

### Retail pre-registration honesty
>12 configs (order flow, liquidations+OI, CVD, funding z, ORB, calendar, …) on BTC/ETH/SOL (+gold/oil): after ~0.13% RT + multiplicity, **no tradeable edge** at intraday–daily horizons; predictable move ≪ cost ([retail-crypto-alpha](https://github.com/Mykola-Quant/retail-crypto-alpha)).

### AccBand / WR-geometry implication
Hit-rate geometry without positive expectancy is **research**, not profit — consistent with this bot’s AccBand ~−0.24R class / dual-goal CONFIRMED_NO_GO posture.

## 3. Trend / TSMOM / CTA (two markets, don’t conflate)

### Crypto single-asset trend
Preprint AdaptiveTrend claims high Sharpes net of 4 bps + funding on multi-coin trend ([arXiv 2602.11708](https://doi.org/10.48550/arxiv.2602.11708)). **Caveat:** preprint, high claimed performance, turnover still material — treat as **unverified / high multiplicity risk** until adversarial audit. This bot already treats textbook TSMOM/breakout as REFUTED for promotion (shadow probes only).

### Institutional multi-asset CTA
2026 H1: SG Trend ~+9.12% YTD (June −1.17%); TTU TF ~+8.56%; Barclay CTA ~+5–6% class; June whipsaw in energy/commodities ([TTU June 2026](https://www.toptradersunplugged.com/trend-following-performance-report-june-2026/), [Barclay/FullFX](https://thefullfx.com/currency-traders-up-as-ctas-struggle-in-june/)). Valid diversifier for traditional portfolios — **not** a drop-in replacement for crypto AccBand PAPER.

## 4. Mean reversion / pairs / funding z-score

- Funding z / MR and short-horizon MR: usually die under RT costs in retail suites ([retail-crypto-alpha](https://github.com/Mykola-Quant/retail-crypto-alpha)).
- Perp pairs: one practitioner walk-forward claims +34–42% ann. at 4–8 bps RT with regime dependence (weak mid-2025 fold) ([Delphi Alpha](https://delphicalpha.substack.com/p/pairs-trading-part-2-backtest-results)) — **single-author, not peer-reviewed**; queue as QUEUED candidate only with hashed prereg + local data, not GO.
- Bundle-MR probes in this bot remain log-only / NO-PROMOTE until frozen gate.

## 5. Liquidation cascades / OI / positioning stress

**Mechanism narrative (plausible):** OI + funding + liquidation density maps crowded leverage; cascades are forced flow; fade exhaustion, don’t chase the cascade ([Bitbase](https://www.bitbase.com/blog/derivatives-positioning-signals), [Decentralised News](https://backend.decentralised.news/perpetual-futures-microstructure-funding-liquidations-and-price-dislocations-professionals-exploit-2026/)).

**Honest execution caveat:** detection ≠ profit. Cascade-fade claims show PF collapsing from ~2.5 to ~0.25 under worst-case fills; BTC often “dead,” SOL more “alive” in one practitioner autopsy — edge is fill/slippage assumptions ([Curupira](https://curupira.dev/blog/on-chain-data-alpha/), [cascade fade](https://curupira.dev/blog/cascade-fade-scalper-fading-liquidation-overshoots/)).

**Bot mapping:** liq-cascade family already in pipeline debate artifacts (41_*); treat as **measurement / INSUFFICIENT or NO_GO until local after-cost screen**, never live from blog PF.

## 6. Strategy taxonomy (after-cost ranking for *this* bot)

| Rank | Family | External evidence 2025–26 | Local bot posture | Next action |
|------|--------|---------------------------|-------------------|-------------|
| 1 | Funding/basis carry (F1) | Strong historically; **compressed / negative 2025** academic Fact 9 | Only ledger profit class when edge clears; now idle | Wait; no threshold loosen |
| 2 | Cost-filtered low-turnover directional / ML | Mixed; costs dominate unless filter | AccBand −EV research | Keep paused (F1-only) |
| 3 | Multi-asset CTA trend | Real in traditional futures 2026 YTD | Out of crypto AccBand scope | Not a PAPER AccBand substitute |
| 4 | Crypto TSMOM / breakout | Preprints optimistic; family REFUTED here | Shadow probes only | No promote |
| 5 | Pairs / MR / funding-z | Mostly retail no-edge; 1 blog survivor | Bundle-MR log-only | New prereg only |
| 6 | Cascade fade | Mechanism OK; fills are the edge | Prior pipeline caution | Needs local fill-honest screen |
| 7 | Scalp / quiet ATR | −EV after fees | Correctly vetoed | Keep protective gates |

## Key Takeaways

1. **Carry is still the right family — but the premium is compressed.** Academic 2025 negativity explains F1 idle better than “bot broken.”
2. **Turnover kills.** Without a cost gate that refuses sub-cost signals, directional futures strategies are a fee funnel ([2606.00060](https://arxiv.org/html/2606.00060v1)).
3. **Don’t confuse CTA index returns with crypto AccBand WR geometry.** Different markets, costs, and evidence bars.
4. **Cascade/pairs blogs are hypotheses.** Execution realism and multiplicity control decide; promotion stays owner-signed + frozen gate.
5. **Operational stance confirmed:** F1-only until funding/contango clear; restore `mcp_registry,algo_det` only by owner decision after edge returns — not to “make trades happen.”

## Sources

1. [arXiv 2510.14435 — Cryptocurrency as an Investable Asset Class](https://doi.org/10.48550/arxiv.2510.14435) — Fact 9 carry Sharpe collapse 2024–2025  
2. [arXiv 2606.00060 — ML BTC trading under costs](https://arxiv.org/html/2606.00060v1) — gross→net collapse; cost-aware filter  
3. [arXiv 2506.08573 — Designing funding rates](https://arxiv.org/abs/2506.08573) — funding mechanism theory  
4. [BCRA 2025 funding arb CEX/DEX](https://doi.org/10.1016/j.bcra.2025.100354) — empirical arb scenarios  
5. [Kraken — funding rate arbitrage](https://www.kraken.com/learn/futures-trading-funding-rate-arbitrage) — mechanics + cost threshold  
6. [BTSE — perp funding arb guide](https://www.btse.com/blog/funding-rate-arbitrage-tutorial/) — long/short funding flips  
7. [CoinUnited — perps & funding drag 2026](https://coinunited.io/en/research/crypto/crypto-perpetual-futures-complete-traders-guide-2026) — directional funding cost  
8. [Hedonist — funding arb 2026](https://intel.hedonist.trading/blog/funding-rate-arbitrage-explained/) — APY tiers / persistence myth  
9. [Finder — funding arbitrage](https://finder-arbitrage.com/blog/funding-arbitrage) — fee amortization  
10. [retail-crypto-alpha](https://github.com/Mykola-Quant/retail-crypto-alpha) — pre-spec retail no-edge suite  
11. [arXiv 2602.11708 — AdaptiveTrend](https://doi.org/10.48550/arxiv.2602.11708) — crypto trend preprint (unverified)  
12. [TTU Trend Following June 2026](https://www.toptradersunplugged.com/trend-following-performance-report-june-2026/) — CTA YTD  
13. [Full FX — CTA June 2026](https://thefullfx.com/currency-traders-up-as-ctas-struggle-in-june/) — Barclay CTA / crypto traders index  
14. [iMGP DBMF June 2026](https://www.imgp.com/us/video/imgp-dbi-managed-futures-strategy-etf-update-with-andrew-beer-june-2026/) — liquid alt CTA  
15. [Delphi Alpha — perp pairs WF](https://delphicalpha.substack.com/p/pairs-trading-part-2-backtest-results) — practitioner pairs  
16. [Bitbase — positioning stress model](https://www.bitbase.com/blog/derivatives-positioning-signals) — OI/funding/liq maps  
17. [Decentralised News — perp microstructure 2026](https://backend.decentralised.news/perpetual-futures-microstructure-funding-liquidations-and-price-dislocations-professionals-exploit-2026/) — cascade fade narrative  
18. [Curupira — on-chain signals](https://curupira.dev/blog/on-chain-data-alpha/) — detection vs fill realism  
19. [Curupira — cascade fade scalper](https://curupira.dev/blog/cascade-fade-scalper-fading-liquidation-overshoots/) — walk-forward claim + BTC death  
20. [Rupak Ghose — beyond fixed income](https://rupakghose.substack.com/p/moving-beyond-fixed-income) — CTA 2025 flat / 2026 rebound context  

## Methodology

- Sub-questions: (1) which futures families survive after costs in 2025–26? (2) is funding carry still alive? (3) directional/ML/trend status? (4) cascade/OI edge real or fill artifact? (5) traditional CTA vs crypto perps?
- ~12 search queries; deep-read arXiv HTML for 2510.14435 Fact 9, 2606.00060, plus practitioner pages via search digests.
- Separated academic vs blog; flagged single-source claims.
- Mapped explicitly to this bot’s F1-only / AccBand honesty (session state 2026-07-31).

## Pipeline gate (honesty)

This report is **research only**. It does **not** authorize new probes, AccBand reopen, F1 threshold changes, or CONTROLLED_LIVE.

**Prereg shipped (Candidate A):** `_workspace/strategy_pipeline/52_prereg_cost_aware_accband_kappa.md`  
(+ companion JSON with sha256) — cost-aware AccBand admit filter κ×stressed RT; expectation NO_GO; screen not run; AccBand allowlist stays F1-only until owner says otherwise.

Candidates still deferred: (B) pairs WF — needs harvest; (C) cascade-fade — already `41_prereg_liq_cascade.md`.

