# Futures Strategies Deep-Research Report — 2026-07-24
*Generated: 2026-07-24 | Sources: 30 | Confidence: High (consensus/null findings), Medium (vendor/practitioner numbers)*

Owner request: "/deep-research and /plan FUTURE trading strategies." Routed through the
strategy-evidence-pipeline: this document is the scout/research stage (3 parallel web scouts +
refuted-families-ledger check). No firecrawl/exa MCP configured; native web search used.
Prior art: this is a strict DELTA + uncovered-families pass on top of
[`24_deep_research_futures_2026-07-23.md`](24_deep_research_futures_2026-07-23.md) — carry/TA/ML/
unlock/VPIN/cross-sectional findings from that report are NOT repeated here.

Sub-questions: (1) delta since 2026-07-23; (2) liquidation-cascade / OI-flush reversion;
(3) exchange-announcement events (delisting reopen, leverage-tier/margin changes);
(4) funding regime 2026 + new carry expressions; (5) options-derived signals (C2 feasibility);
(6) cross-exchange lead-lag at retail latency; (7) OI×funding regime classification;
(8) adverse-evidence duty.

## Executive summary

**The external delta since yesterday is NULL** — no June–July 2026 paper or rigorous practitioner
study proposes a costed, multiplicity-controlled crypto-perp strategy. Two things still moved:
(a) **C2 Deribit gamma-expiry got a peer-reviewed mechanism paper** (*Finance Research Letters*,
Sep 2026: intraday BTC reversal around **daily** expirations, concentrated on ATM-OI>p90 /
negative-gamma days) **plus a free forward data path** (self-archived Deribit chain snapshots) —
its INSUFFICIENT_DATA reopen condition from the 2026-07-22 adjudication is now actionable; and
(b) **liquidation-cascade reversion** surfaced as the strongest genuinely-unscreened mechanism —
with zero rigorous after-cost backtests anywhere and a binding data problem (throttled/undercounted
liquidation feeds). Everything else confirms existing refutations: lead-lag is maker/HFT-only,
wide cross-sectional carry collapses out-of-sample and fails min-notional at $420, VRP overlays on
perps are volatility timing (not edge), and funding compression persists (F1 correctly idle).

## 1. Delta scan since 2026-07-23 — NULL

- [AutoQuant (arXiv 2512.22476)](https://arxiv.org/pdf/2512.22476) (preprint, pre-dates window):
  validation infrastructure, not a strategy; its own CSCV/PBO diagnostic shows "substantial
  residual overfitting risk" even after two-stage screening. Confirms our harness design.
- [Frontiers in Blockchain 2026](https://www.frontiersin.org/journals/blockchain/articles/10.3389/fbloc.2026.1811716/full)
  (peer-reviewed): no strategy on 5-min microstructure forecasts survives Binance VIP-0 fees;
  gradient boosting overfits to worse-than-chance. Confirms the refuted ML-forecaster family.
- [IJFS 14(5):103, MDPI 2026](https://ideas.repec.org/a/gam/jijfss/v14y2026i5p103-d1926363.html)
  (peer-reviewed; 26 exchanges, 812 symbols, Nov 2025–Jan 2026): descriptive integration
  econometrics, no tradable strategy. Two reusable facts: spreads peak ~2h after funding
  settlement (significant), and mid-tier exchanges lead Binance more often than the reverse at
  hourly Granger horizons.
- **Explicit null:** no June–July 2026 arXiv q-fin (2606/2607.*) or SSRN paper proposing a
  costed, multiplicity-controlled crypto-perp strategy was found.

## 2. Liquidation-cascade / OI-flush reversion — strongest unscreened mechanism

- Mechanism: forced liquidations are price-insensitive fire sales; order-book depth collapses
  60–80% and $1M-order slippage rises 3–10x during cascades — the overshoot is the reversion
  fuel. Rigorous microstructure documentation:
  [Slippage-at-Risk, arXiv 2603.09164](https://doi.org/10.48550/arxiv.2603.09164) (Hyperliquid
  book data incl. 2025-10-10 cascade: $2.1B liquidated in 12 min, OI $9.8B→$6.1B) and
  [ADL clustering, arXiv 2512.01112](https://arxiv.org/abs/2512.01112).
- Practitioner (vendor) anecdotes of post-cascade bounces: BTC +4.2%/24h after Oct-2025,
  ~10% after Nov-2025, Feb-2026 flush recovered in 48h; decay 2–10h
  ([MethodAlgo](https://www.methodalgo.com/press/research/liquidation-heatmap-research)).
- **No rigorous after-cost strategy backtest exists** — that is the gap a screen would fill.
- **Binding data problem:** Binance `!forceOrder@arr` pushes only the latest liquidation per
  symbol per 1000ms ([Tardis docs](https://docs.tardis.dev/historical-data-details/binance-futures));
  independent measurement shows severe notional undercount
  ([Yavas](https://www.linkedin.com/pulse/binance-liquidation-stream-analysis-berke-yavas-hlbvf));
  Bybit `allLiquidation` arrives with 2–3s lag
  ([truetech](https://truetech.dev/blockchain-development/services/blockchain-infrastructure/exchange-liquidations-data-scraping.html));
  Coinglass aggregates are paid and built on the same throttled feeds. Point-in-time history =
  paid (Tardis, forceOrder since 2020-01) or self-collection from now.
- **Decision (recorded):** no vendor spend — forward self-collection with documented undercount
  caveats; in-event costs stressed to 30–60 bps (spreads blow out exactly when the signal fires);
  altcoin-flush and major-cascade variants pre-registered separately; event definition must not
  reduce to the refuted OI-divergence directional signal.
- Expectation: **NO_GO prior (~25% GO)**. Collector build queued after the VPIN screen closes.

## 3. Exchange-announcement events

- **Delisting reopen accelerant:** Binance flips the contract `deliveryDate` field in
  `fapi/v1/exchangeInfo` at announcement moment — a structured, sub-second, PIT-verifiable
  detector without parsing announcement pages
  ([FMZ Quant, May 2026](https://blog.mathquant.com/2026/05/18/shorting-binance-delisted-perpetuals-a-grid-strategy-from-monitoring-to-auto-execution.html);
  also evidence the mechanism is actively traded → crowding risk). Catalogs helping forward
  accrual only: [Arbitron](https://arbitron.app/listings),
  [CryptoListing.ws](https://cryptolisting.ws/latest-listings/). At ~3–6 futures delistings/month
  across venues, +30 covered events ≈ 6–12 months forward. **Still INSUFFICIENT_DATA; timeline
  unchanged but the harvester got cheaper and cleaner.**
- **Leverage-tier / margin-change events:** zero crypto-perp studies found. TradFi mechanism
  demonstration only (CME silver margin hikes Dec-2025 preceding ~17% forced selloff,
  [DiscoveryAlert](https://discoveryalert.com.au/exchange-margin-mechanisms-precious-metals-2026/), vendor).
  Raw event material exists (e.g. [OKX tier adjustments](https://www.okx.com/help/okx-to-adjust-position-tiers-of-several-futures-20250804)).
  **INSUFFICIENT_DATA — harvest-first**, folded into the same announcement harvester as a
  separately pre-registered sibling event class (pooling heterogeneous events = multiplicity trap).

## 4. Funding regime 2026 + carry expressions

- **Compression persists, no easing:** avg BTC/USDT funding 0.0031–0.0056%/8h across venues
  (Binance 0.0045%, Bybit 0.0040%); positive only ~65% of periods vs ~92% long-run norm;
  cross-venue spread ≈0.0025%/8h ≈ **2.7% annualized** — far below our 10–28 bps round-trip
  ([Bitsgap Q2-2026](https://bitsgap.com/blog/what-a-spot-hedge-on-perpetuals-really-cost-in-2026), vendor).
  Practitioner heuristic: "tradeable" ≥0.030%/8h episodes last 2–3 weeks and are historically
  rare ([SatoshiMacro](https://satoshimacro.com/tools/crypto/derivatives/funding-rate-heatmap/)).
  **F1 stays structurally idle — correctly.** The existing ≥30 positive-net-edge-episode gate-log
  reopen condition already captures any revival; no action.
- **Wide cross-sectional carry: refuse.** Vendor backtests look strong
  ([Keel](https://usekeel.io/strategies/funding-carry) Sharpe 1.69 net;
  [Pandabull](https://pandabull.io/insights/read/hyperliquid-delta-neutral-backtest-funding-strategy)
  self-admits margin infeasibility shorting low-caps) but the honest counter-evidence is decisive:
  [ML4Trading 19-perp case](https://ml4trading.io/case-studies/crypto-funding-arbitrage/) —
  validation Sharpe **+0.80 collapses to −1.17 in holdout (−247%)**. At $420 a balanced 60–100-name
  book is min-notional infeasible ($4–7/leg). Soft carry is adjacent to refuted directional-funding.
- **Hyperliquid hourly funding as F1 timing conditioner (data signal only):** HL settles hourly →
  reprices ~8x faster than CEX 8h prints; "leads market shifts" is vendor claim only
  ([ArbitrageScanner](https://arbitragescanner.io/blog/hyperliquid-cex-funding-arbitrage),
  [Supa.is](https://supa.is/article/hyperliquid-funding-rate-history-export-calculate-trading-cost-2026)).
  Free public API history. Cheap local screen against `data/carry_gate_log.jsonl`. NO_GO-lean;
  F1-ADJACENT (extensions screen-eligible per ledger).

## 5. Options-derived signals — C2 evidence bar materially upgraded

- **Peer-reviewed mechanism (the find):** Weiss et al., *Finance Research Letters* 107 (Sep 2026)
  110340 — statistically and economically significant intraday BTC return reversal around
  **daily** Deribit expirations; concentrated on days with ATM OI >90th percentile; strongest
  under negative cumulative gamma exposure; pattern = down-move ~1h pre-expiry, reversal within
  ~90 min; ~$50M/yr wealth transfer
  ([doi:10.1016/j.frl.2026.110340](https://doi.org/10.1016/j.frl.2026.110340);
  [summary](https://www.securities.io/bitcoin-options-expiration-spot-price-reversals/)).
- **Free forward data path:** Deribit `public/get_book_summary_by_currency?kind=option` returns
  full-chain OI/IV/mark in one free REST call
  ([Deribit docs](https://docs.deribit.com/articles/options-data-collection-best-practices)).
  Self-archiving 2 snapshots/day gives point-in-time ATM-OI + GEX. Daily expirations → ≥30
  forward events in ~4–6 weeks unconditioned; conditioned cell (OI>p90 ∧ GEX<0) ~3–4 months.
  Laevitas free tier = 1 week history ([laevitas.ch](https://www.laevitas.ch/)); Amberdata
  historical OI is paid ([docs](https://docs.amberdata.io/http/market/options-open-interest)) —
  forward self-archive is the free path. **C2 moves from INSUFFICIENT_DATA to
  feasible-with-forward-harvest**; prereg `33_prereg_c2_gamma_expiry` frozen this pass.
- **Standalone DVOL/skew/max-pain conditioners: NO_GO.** IV-premium signal regime-dependent,
  per-window OOS Sharpe −7 to +15, "not consistently profitable"
  ([TanvirCCC](https://github.com/TanvirCCC/options-implied-crypto-signals)); XGBoost-DVOL:
  statistical edge, economic Sharpe −13.4
  ([Zenodo](https://doi.org/10.5281/zenodo.17985415)); max-pain pinning "largely missing from
  recent expiries" per Wintermute
  ([CoinDesk Jun-2026](https://www.coindesk.com/markets/2026/06/25/forget-max-pain-bitcoin-is-well-below-the-usd72-000-magnet-ahead-of-usd10-billion-options-expiry)).

## 6. Cross-exchange lead-lag at retail latency — refuse

- Discovery lead exists sub-second: Binance leads Hyperliquid ~700ms (29/29 assets), Lighter
  ≤100ms ([Arrakis, Feb 2026](https://arrakis.finance/blog/crypto-price-discovery), practitioner).
- The kill: follow-through capture is **"highly profitable at maker fees and completely dead at
  taker fees"** — profitable at 0.04–0.08% maker round-trip, 43.9% at 0.12% mixed, **0.9% at
  0.20% taker** ([LeadEdge validation](https://leadedge.dev/blog/validation), vendor-adjacent).
- No 2025–26 study shows Binance→Bybit/Bitget lead-lag ≥1s surviving taker costs at 100–500ms
  retail latency. **Maker/HFT-only → not feasible for this bot. Do not screen.**

## 7. OI×funding joint regime classification — conditioning-only brief

- Practitioner 4-quadrant OI-change × funding-sign framework on 2,523 days of BTC
  ([Axel Adler Jr](https://axeladlerjr.com/bitcoin-open-interest-funding-rate/)): descriptive, no
  OOS test, no costs, no multiplicity. Structural caveat worth keeping: FR-per-dollar-of-OI now
  beats absolute OI (CME dilutes the signal).
- Nothing rigorous shows OI/funding states classify forward vol/drift after costs. Mechanism
  (leverage crowding → cascade vulnerability) is real; evidence is practitioner-grade.
- **In scope ONLY as an internal `band_regime_filter`-class veto-refinement study on our own
  warehouse data** (OI-divergence stays refuted as a directional signal). Queued behind VPIN —
  same veto-overlay family. Expectation modest-or-null.

## 8. VRP expressions on perps — refuse

- Profit-taking overlays on crypto perp L/S portfolios are **volatility timing, not reversion
  capture** — mean falls, vol falls more; the only execution-robust variant adds ~0.25 Sharpe and
  fails Bonferroni (p≈0.53) ([Tanaka, Zenodo](https://doi.org/10.5281/zenodo.20840128), preprint).
  Mirrors the refuted long-only-TSMOM row ("halves drawdown, no profit edge").
- Target-vol rebalancing-boundary work is cost-*reduction* engineering, not edge
  ([Springer FMPM 2025](https://link.springer.com/article/10.1007/s11408-025-00486-5)).

## 9. Adverse-evidence anchors (2026, recorded up-front)

- [Frontiers in Blockchain 2026](https://www.frontiersin.org/journals/blockchain/articles/10.3389/fbloc.2026.1811716/full)
  (peer-reviewed, strongest): leakage-controlled 5-min statistical edge annihilated by
  turnover × fees; flexible ML worse than chance; no cross-asset generalization.
- [Market Maker's Dilemma, arXiv 2502.18625](https://ar5iv.labs.arxiv.org/html/2502.18625):
  imbalance strategies negative even at best-tier fees (1.5bp taker).
- Retail base rates: 65–80% of perp retail accounts net-negative over rolling 12-month windows
  ([Skrumble 2026](https://skrumble.com/learn/what-is-perpetual-futures/), vendor-compiled);
  97% of persistent day traders unprofitable after 300+ days
  ([compilation](https://cryptoemotions.com/percentage-of-traders-who-lose-money-in-crypto/)).
- [CFCI 2026](https://decentralised.news/crypto-friction-cost-index-2026-what-active-crypto-traders-actually-pay-in-hidden-costs-every-year)
  (vendor): funding drag, not fees, dominates active-account friction — reinforces F1's
  net-edge gate discipline.

## Key takeaways

1. **Nothing new to wire; nothing reopens a refuted family.** The honest instruments remain: F1
   carry (validated, regime-idle), 7 log-only shadow probes, band-geometry PAPER lane.
2. **Two clocks start now (data only):** Deribit chain snapshots (C2, prereg frozen at `33_*`)
   and the exchange-announcement harvester (delisting n=34→30+ covered, tier-change sibling class).
3. **Queue order is binding:** VPIN veto screen first (prereg `27_*` already hashed; fresh UTC
   day 2026-07-25) → C2 after ≥30 events → liquidation-cascade prereg (with Codex cross-check)
   after VPIN closes.
4. **Wire criteria unchanged:** CONFIRMED_GO screen → log-only shadow → ≥30 resolved forward
   events → frozen promotion gate (DSR≥0.10, PBO≤0.5, OOS-WR≥0.55, AUC≥0.60) → owner sign-off.

## Sources

1. [arXiv 2603.09164](https://doi.org/10.48550/arxiv.2603.09164) — Slippage-at-Risk; Oct-2025 cascade microstructure (rigorous)
2. [arXiv 2512.01112](https://arxiv.org/abs/2512.01112) — ADL/cascade clustering (rigorous)
3. [MethodAlgo](https://www.methodalgo.com/press/research/liquidation-heatmap-research) — post-cascade bounce anecdotes (vendor)
4. [Tardis Binance docs](https://docs.tardis.dev/historical-data-details/binance-futures) — forceOrder 1/sec/symbol throttle; paid history
5. [Yavas liquidation-stream analysis](https://www.linkedin.com/pulse/binance-liquidation-stream-analysis-berke-yavas-hlbvf) — notional undercount (practitioner)
6. [truetech](https://truetech.dev/blockchain-development/services/blockchain-infrastructure/exchange-liquidations-data-scraping.html) — Bybit allLiquidation lag (practitioner)
7. [DiscoveryAlert](https://discoveryalert.com.au/exchange-margin-mechanisms-precious-metals-2026/) — CME margin-hike mechanism (vendor)
8. [OKX tier adjustment](https://www.okx.com/help/okx-to-adjust-position-tiers-of-several-futures-20250804) — raw event class example
9. [FMZ Quant](https://blog.mathquant.com/2026/05/18/shorting-binance-delisted-perpetuals-a-grid-strategy-from-monitoring-to-auto-execution.html) — deliveryDate-flip detection (practitioner, unaudited)
10. [Arbitron](https://arbitron.app/listings) / [CryptoListing.ws](https://cryptolisting.ws/latest-listings/) — forward event catalogs
11. [Bitsgap Q2-2026](https://bitsgap.com/blog/what-a-spot-hedge-on-perpetuals-really-cost-in-2026) — funding levels/regime (vendor)
12. [SatoshiMacro](https://satoshimacro.com/tools/crypto/derivatives/funding-rate-heatmap/) — spread heuristics (vendor)
13. [Keel](https://usekeel.io/strategies/funding-carry) — cross-sectional carry (vendor)
14. [Pandabull](https://pandabull.io/insights/read/hyperliquid-delta-neutral-backtest-funding-strategy) — margin infeasibility admission (vendor)
15. [ML4Trading](https://ml4trading.io/case-studies/crypto-funding-arbitrage/) — holdout collapse −247% (practitioner)
16. [ArbitrageScanner](https://arbitragescanner.io/blog/hyperliquid-cex-funding-arbitrage) / [Supa.is](https://supa.is/article/hyperliquid-funding-rate-history-export-calculate-trading-cost-2026) — HL funding lead claims (vendor)
17. [Weiss et al., FRL 107 (2026) 110340](https://doi.org/10.1016/j.frl.2026.110340) — daily-expiry BTC reversal (peer-reviewed) + [summary](https://www.securities.io/bitcoin-options-expiration-spot-price-reversals/)
18. [Deribit data-collection docs](https://docs.deribit.com/articles/options-data-collection-best-practices) — free full-chain REST
19. [Laevitas](https://www.laevitas.ch/) / [Amberdata](https://docs.amberdata.io/http/market/options-open-interest) — historical options data pricing
20. [TanvirCCC](https://github.com/TanvirCCC/options-implied-crypto-signals) — IV-premium signal instability (practitioner)
21. [Zenodo 17985415](https://doi.org/10.5281/zenodo.17985415) — DVOL ML economic Sharpe −13.4 (preprint)
22. [CoinDesk Jun-2026](https://www.coindesk.com/markets/2026/06/25/forget-max-pain-bitcoin-is-well-below-the-usd72-000-magnet-ahead-of-usd10-billion-options-expiry) — max-pain absence (news/Wintermute)
23. [Zenodo 20840128](https://doi.org/10.5281/zenodo.20840128) — profit-taking overlays = vol timing (preprint)
24. [Springer FMPM 2025](https://link.springer.com/article/10.1007/s11408-025-00486-5) — target-vol boundary = cost engineering (peer-reviewed)
25. [arXiv 2512.22476](https://arxiv.org/pdf/2512.22476) — AutoQuant validation framework (preprint)
26. [Frontiers in Blockchain 2026](https://www.frontiersin.org/journals/blockchain/articles/10.3389/fbloc.2026.1811716/full) — 5-min fee kill (peer-reviewed)
27. [IJFS 14(5):103](https://ideas.repec.org/a/gam/jijfss/v14y2026i5p103-d1926363.html) — integration; post-settlement spread peak (peer-reviewed)
28. [Arrakis](https://arrakis.finance/blog/crypto-price-discovery) — sub-second discovery leads (practitioner)
29. [LeadEdge](https://leadedge.dev/blog/validation) — lead-lag dead at taker (vendor-adjacent)
30. [Axel Adler Jr](https://axeladlerjr.com/bitcoin-open-interest-funding-rate/) — OI×funding quadrants (practitioner); [arXiv 2502.18625](https://ar5iv.labs.arxiv.org/html/2502.18625) — MM dilemma; [Skrumble](https://skrumble.com/learn/what-is-perpetual-futures/), [cryptoemotions](https://cryptoemotions.com/percentage-of-traders-who-lose-money-in-crypto/), [CFCI 2026](https://decentralised.news/crypto-friction-cost-index-2026-what-active-crypto-traders-actually-pay-in-hidden-costs-every-year) — adverse base rates

## Methodology

3 parallel scouts, ~14 web queries total across academic (arXiv/SSRN/ScienceDirect/Springer/
Frontiers/MDPI), practitioner (GitHub research, vendor backtests), and news sources. Every
candidate checked against the refuted-families ledger before routing; single-source claims
labeled; republications de-duplicated. Assignments: (A) liquidation/announcement events;
(B) carry/basis/options; (C) literature delta + lead-lag + OI×funding + adverse anchors.
No files were written by scouts; all pipeline artifacts (this report, queue updates, `33_*`
prereg, harvesters) were produced by the orchestrator after plan approval.
