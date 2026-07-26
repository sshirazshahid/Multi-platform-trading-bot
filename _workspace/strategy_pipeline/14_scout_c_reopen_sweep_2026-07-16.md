# 14 — Scout C: Reopen-Bar Sweep + Evidence Updates (2026-07-16)

Scout C (strategy-scout) | Phase 1 of strategy-evidence-pipeline run 2026-07-16. Adversarial literature hygiene only — **no new candidates, no code, no trades**. This file is authoritative for this sub-question.

**Reopen bar (verbatim from `refuted-families-ledger`):** "Peer-reviewed or equivalently rigorous 2025+ evidence with genuine out-of-sample validation, FDR/DSR-grade multiplicity control, and after-cost accounting on liquid crypto at retail venues. Quote it verbatim. Meeting the bar earns a SCREEN (via `strategy-evidence-pipeline`) — never a build."

**Method / coverage:** 10 web searches + 8 targeted fetches (2026-07-16). Sources already reviewed in `07_scout_candidates_2026-07-11.md` and `deep-research_crypto-strategy-patterns_2026-07-08.md` were NOT re-reviewed. Settled SKIPs (retail market-making, vol-risk-premium, CEX↔DEX arb, latency arb) were not re-searched. Access failures this pass: SSRN 6632838 (403, retried once as instructed), SSRN 6579278 (403, retried once), ScienceDirect S1059056026002716 (403), ResearchGate 403391704 (403), Tandfonline 10.1080/14697688.2026.2653663 (403). Web tools worked normally — coverage NOT limited.

---

## TASK 1 — REOPEN-BAR SWEEP: VERDICT BY FAMILY GROUP

**Bottom line: NOTHING found in 2025–2026 literature meets the reopen bar for ANY refuted family. Every refuted-table row stands.** Per-group detail with the closest candidates adjudicated:

### Group A — Technical patterns (RSI-MR, candlesticks, indicator-confluence, hour-of-day/seasonality)
**Verdict: bar NOT met. One close call, adjudicated below.**

- **Closest candidate of the entire sweep:** *"Intraday price forecasts using candlestick patterns in cryptocurrency markets"*, International Review of Economics & Finance, published **April 2026** ([ScienceDirect](https://www.sciencedirect.com/science/article/pii/S1059056026002716)). Per search-visible abstract coverage: 55 TA-Lib reversal patterns, hourly OHLC, ~400 cryptos / 2,000 pairs / 36 exchanges, ~200M observations; "Profitability is assessed using the **stepwise superior predictive ability test** to mitigate data-snooping" (search-snippet quote); Bullish Harami / Hikkake / Bearish Harami / Hanging Man reported significant, "consistent across time, market conditions, cryptocurrencies, and exchanges."
  **Why it does NOT meet the bar:** (1) **after-cost accounting is unverified** — full text 403 on both ScienceDirect and ResearchGate; no visible cost model or net-return numbers; the visible claim is statistical predictability, not retail after-cost tradability. (2) **Sample is July 2018 – January 2022** — it ends 4.5 years ago, entirely predating this system's own after-cost candlestick refutation (1,989 tests, 0 survivors, run 2026-06-07 on current data at our exact venues/fees). Peer review + SPA multiplicity are genuinely present, but the bar's after-cost element cannot be confirmed and the evidence is stale relative to our fresher local refutation. **Ledger row stands.** *Follow-up (conditional, low priority): if the full text ever opens AND shows after-cost survival of its four named patterns, the correct action is a narrow re-adjudication of exactly those patterns at hourly frequency — a screen, never a build.* Single-source flag: abstract details reconstructed from search snippets, not the paper itself.
- RSI-MR 2025–2026: searched; only vendor/blog content (UEEx, Stoic.ai, Changelly — all marketing-grade). No qualifying study. Bar not met.
- Seasonality/calendar: nothing 2025+; the literature remains pre-2024 ("turn-of-the-candle" Heliyon 2023; calendar-anomaly Cogent 2023). Bar not met.
- Umbrella confirmation: Wei (2024, Int. J. Finance & Economics, ["Cryptocurrencies and Lucky Factors"](https://onlinelibrary.wiley.com/doi/full/10.1002/ijfe.2863)) — 7,846 technical rules × 12 cryptos with snooping control: only a tiny non-pattern set survives. 2024 (pre-bar) and its direction *supports* the refutation anyway.

### Group B — Textbook trend/breakout + TSMOM (incl. long-only TSMOM row)
**Verdict: bar NOT met. Two 2025–2026 items adjudicated, both fail.**

- **[arXiv 2602.11708](https://arxiv.org/pdf/2602.11708), "Systematic Trend-Following with Adaptive Portfolio Construction" (Bui & Nguyen, Talyxion Research, 12 Feb 2026).** Full text read this pass (PDF extracted locally). Claims: "OOS" Jan 2022–Dec 2024, Sharpe 2.41, maxDD −12.7%, cost model 4bps taker + linear slippage + funding. **Fails the bar:** (a) NOT peer-reviewed (arXiv cs.CE preprint from a corporate research shop, gmail contact); (b) the "OOS" window is **burned by design selection** — the timeframe comparison (Table 5: H6 chosen because it wins on the evaluation window), the (α,λ) sensitivity surface, and the 70/30-vs-50/50 allocation choice are all reported ON the evaluation period; (c) multiplicity control is bootstrap-vs-benchmark only — no DSR/FDR/reality-check across the searched design space; (d) internal inconsistency: §4.1 says OOS ends Dec 2024 but Figure 1 caption says "Jan 2022 – Oct 2025"; (e) monthly grid-search re-optimization is the ablation's second-largest Sharpe contributor (ΔSR 1.07) — the edge substantially IS the re-fitting. Win rates 54.2%/47.8%/41.3% (bull/sideways/bear) — all below our 0.55 OOS-WR gate and the owner's 65% floor. **Does not reopen the family.**
- **[SSRN 5209907](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=5209907), Zarattini, Pagani & Barbon, "Catching Crypto Trends"** ([Concretum page](https://concretumgroup.com/catching-crypto-trends-a-tactical-approach-for-bitcoin-and-altcoins/) fetched): Donchian-channel ensemble + vol sizing on top-20 liquid cryptos; claims "Sharpe ratio above 1.5" and "annualized alpha of 10.8% relative to Bitcoin." **Abstract-grade only** — no visible data period, no OOS protocol, no multiplicity control, no after-cost numbers on the accessible page; SSRN working paper, not peer-reviewed. Bar not met. (Relevant as weak family-supportive context for the breakout-60d probe — see Task 2.)
- Han, Kang & Ryu ([SSRN 4675565](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=4675565), "…under Realistic Assumptions"): pre-dates the bar (2024) and cuts AGAINST reopening — "accounting for transaction costs and daily price fluctuations, many momentum portfolios are liquidated and many with statistically significant returns earn insignificant profits" (search-snippet quote; snippet-grade flag).
- Gbadebo 2026 TSM-vs-CSM comparative ([ResearchGate 406476873](https://www.researchgate.net/publication/406476873)): explicitly "does not incorporate transaction costs, slippage, funding rates, or liquidity constraints" — inadmissible against an after-cost bar.

### Group C — Kalman/cointegration pairs (435 pairs, 0 FDR survivors, 2026-06-06)
**Verdict: bar NOT met. Two peer-reviewed 2025–2026 papers exist; both fail on multiplicity and/or staleness.**

- **Tadi & Witzany, "Copula-based trading of cointegrated cryptocurrency pairs", Financial Innovation, 13 Jan 2025** ([Springer, open access](https://link.springer.com/article/10.1186/s40854-024-00702-7)) — full page fetched. Peer-reviewed 2025+ ✓; after-cost ✓ (Binance futures 2/4 bps, best config net 75.2% ann., Sharpe 3.77). **Fails:** (a) **no multiplicity control** — multiple copula families × entry thresholds (α₁ ∈ {10,15,20}%) × two cointegration tests reported with the best highlighted, zero FDR/DSR adjustment; (b) **no genuine holdout** — rolling 3-week-formation/1-week-trading cycles "each cycle sharing three quarters of its data with its previous or subsequent cycle"; (c) data **Jan 2021 – Jan 2023** — ends 3.5 years ago, fully inside the window our own 435-pair FDR screen already covered; (d) market-order execution on 20 alt perps with slippage beyond taker fee unmodeled. Does not reopen.
- **Quantitative Finance 2026 GA-optimized pairs paper** ([Tandfonline](https://www.tandfonline.com/doi/full/10.1080/14697688.2026.2653663), 403 — abstract-grade from search): 229 pairs, data Aug 2021 – Jan 2024, genetic-algorithm-optimized thresholds. GA optimization with no visible snooping control is the textbook overfitting surface; period ends Jan 2024. Does not meet the bar on visible evidence. Single-source flag (snippets only).

### Group D — Directional funding signals + formulaic alphas
**Verdict: bar NOT met — and the new 2026 evidence points the OTHER way.**

- **MDPI Mathematics 14(2):346, "The Two-Tiered Structure of Cryptocurrency Funding Rate Markets" (Jan 2026)** ([link](https://www.mdpi.com/2227-7390/14/2/346)) — previously 403'd abstract-only in the 07-08 pass; now published with numbers visible in coverage: 35.7M one-minute observations, 749 symbols, 26 exchanges (11 CEX / 15 DEX). Finding: ~17% of observations show economically significant cross-venue funding spreads, but "only 40% of top opportunities generate positive returns after transaction costs and spread reversals" (search-snippet quote). This *reinforces* both the directional-funding refutation and our dispersion CONFIRMED_NO_GO row (costs + reversals consume most of the apparent spread). Evidence UPGRADE for the ledger's context, not a reopen.
- **IRFA 2026 factor-selection paper** ([ScienceDirect S1057521925004764](https://www.sciencedirect.com/science/article/abs/pii/S1057521925004764)): "just two to three factors can eliminate all significant portfolio alphas" in crypto (turnover volatility, bid-ask spread, blockchain-native metrics). Reinforces the formulaic-alphas refutation (443+ tested locally, best IR≈0.45 pre-cost). Snippet-grade, single-source flag.

### Group E — ML price forecasters (Kronos family row)
**Verdict: bar NOT met — the one rigorous 2026 study is a REFUTATION-REINFORCER.**

- **Bysik & Ślepaczuk, "Machine Learning-Based Bitcoin Trading Under Transaction Costs" ([arXiv 2606.00060](https://arxiv.org/html/2606.00060), Univ. of Warsaw, 19 May 2026)** — full text read. This is the methodological standard the bar asks for (27-fold non-anchored walk-forward, strict temporal integrity, **Holm-adjusted p-values**, 10k-replication circular block bootstrap, 10bps all-in costs) and its conclusion is negative: naive ML strategies go from +73.5%/+181.8% frictionless to **−64.0%/−98.6% after 10bps**; a cost-aware filter (cutting trades 10,619→251, >99% turnover reduction) restores XGBoost long-only to +65.4% ARC / Sharpe 1.09, **but "no statistically significant Sharpe-ratio outperformance [vs buy-and-hold] after Holm correction; bootstrap 95% CI for SR difference includes zero"**, with fold-level instability and configuration fragility. Rigorous 2026 evidence that ML forecasting does not beat a 1-trade passive benchmark after costs at retail cost levels. Ledger row stands, strengthened.

### Group F — Remaining rows (scalping, grid/DCA, OI-divergence, dominance/ETF-flow, band-geometry filters, settlement-window timing, quarterly leg-swap, dispersion, full-stake listing-short)
**Verdict: bar NOT met — nothing 2025–2026 found addressing any of these at academic rigor.**

- Scalping / grid / DCA / OI-divergence / dominance / ETF-flow: searches returned vendor content only. No qualifying evidence.
- Quarterly-futures basis leg-swap (NO_GO 2026-07-11): the **AEA 2026 conference paper "Perpetual Futures and Basis Risk: Evidence from Cryptocurrency"** ([program page](https://www.aeaweb.org/conference/2026/program/paper/ByyFEfr4)) reports quarterly futures decline **8–10% more than spot** during large market events vs ~3% for perps (abstract-grade). This adds independent crash-risk evidence AGAINST the dated-future leg — supports the NO_GO, no reopen.
- Settlement-window timing (NO_GO 2026-07-11): no new measured pre-settlement-drift study found (unchanged from 07-08 finding of "insufficient external data"; our own screen has since refuted it locally).

---

## TASK 2 — IN-SHADOW PROBE EVIDENCE UPDATES

### 2a. Capital-scaled listing-short (ListingShortProbeAgent)
**Delta: NONE.** Searched "new perpetual futures listing price decline short 2026" — nothing new beyond the already-cited FMZQuant 2023 study; results were long/short-ratio sentiment noise. Registered hypothesis unchanged; the probe's forward log remains the primary evidence stream.

### 2b. Pre-unlock short (UnlockShortProbeAgent)
**Delta: MODERATE STRENGTHEN of direction + STRONG independent validation of the audit's sizing/tail-risk reasoning. Timing contradiction persists unresolved.**

1. **SSRN 6632838 ("72-Hour Shock") full-text retry: still HTTP 403** (retried once 2026-07-16 as instructed). Note from search coverage: the April 2026 version reportedly has "replication data and analysis code made publicly available" — if the repo link ever surfaces outside the SSRN paywall, the W-window adjudication could be checked against his raw events. Remains abstract-grade, single-source.
2. **NEW independent practitioner backtest** — Tigro Blanc, ["I Backtested Shorting Token Unlocks — Here's Why I'm Not Trading It Yet"](https://medium.com/coinmonks/i-backtested-shorting-token-unlocks-heres-why-i-m-not-trading-it-yet-42e237d40d9a) (Coinmonks, 10 Apr 2026; full text read). 59 Binance USDT perps matched to DefiLlama emissions, 36–38 large events (FDR>5%-of-supply trigger), 22 qualified trades: beta-adjusted CAR[−30,+30] **−15.05%** ("95% bootstrap CI = [−24.82%, −5.54%]"), WR 77.3%, avg net +8.88%/trade, PF 2.12 — **but maxDD −86.6%: "One extreme squeeze destroyed the strategy economics."** Author's verdict: not tradable standalone; "if the max drawdown is −86.6%, it does not matter that the win rate is 77.3%." Also proposes an explicit frequency gate for event strategies (<~20 events/yr = don't trade standalone; his estimate 16.9 events/yr).
   **Read-through for our probe:** (i) independent corroboration of effect direction and magnitude on a different event set/data source — the unlocks.app 236-event study is no longer the only measured source, though it remains the only *controlled* one; (ii) his blow-up is on unhedged full-size event sizing — **exactly the failure mode our audit pre-empted with the 3%/12% capital-scaled caps**; his result is an external replication of why our full-stake sibling (listing-short) was CONFIRMED_NO_GO on sizing; (iii) his frequency concern matches our registered "~months at 1–3 qualifying events/month" pace and the ≥30-resolved promotion floor; (iv) his entry is T−1 with ~21d hold (post-unlock exposure) vs our W1 T−28d / W2 T−14d exit-at-T — the Kim (post-unlock) vs unlocks.app (pre-unlock drift) timing contradiction is still not adjudicated externally; our own screen's adjudication stands as registered. Single-source flag (practitioner blog, self-admitted "not a fully mature walk-forward production backtest").
3. **No replication, critique, or contradiction of the unlocks.app 236-event study (2026-06-29) found** — secondary coverage (KuCoin, Phemex 2026 guides) merely repeats its numbers. It remains single-source, methodologically strongest.

### 2c. TSMOM-20d probe (TsmomProbeAgent)
**Delta: WEAK-TO-NEUTRAL — new 2026 material is consistent with the registered NO-PROMOTE expectation; nothing strengthens the reopen case.**

- AdaptiveTrend (arXiv 2602.11708, Feb 2026), whose own agenda is pro-trend, reports vanilla post-cost TSMOM on 2022–2024 crypto at **Sharpe 0.65 (1M lookback) / 0.54 (3M), maxDD −34.8% / −38.2%** — marginal after costs even in a paper selling trend-following, with vol-scaling required to reach 1.83. Consistent with our regime-fragility registration.
- Han/Kang/Ryu (2024, snippet-grade): realistic assumptions liquidate many momentum portfolios; TSM significance often becomes economic insignificance.
- Nothing 2026 provides multiplicity-controlled after-cost evidence FOR short-lookback crypto TSMOM. Probe continues as registered; forward log remains the instrument.

### 2d. Breakout-60d probe (BreakoutProbeAgent)
**Delta: WEAK SUPPORTIVE (family-level), evidentially immaterial.**

- Zarattini/Pagani/Barbon (SSRN 5209907): Donchian-ensemble on top-20 liquid cryptos claims Sharpe >1.5 and 10.8% ann. alpha vs BTC with a cost-reduction portfolio construction — the same family as the Codex deep-run winner. Abstract-grade, no visible OOS/multiplicity control, working paper. Does not change probe status or the family's refuted-table row; forward paper evidence remains the only admissible instrument.
- **SSRN 6579278 (Lim, cascade anatomy) full-text retry: still HTTP 403** (retried once). No bearing on the four probes; remains abstract-grade for the (measurement-only) cascade candidate from the 07-11 brief.

---

## TASK 3 — HONESTY METASTUDY UPDATE (additions to system priors)

1. **The execution-conversion margin dominates model quality** — Bysik & Ślepaczuk (arXiv 2606.00060, May 2026, Univ. of Warsaw): "The main obstacle in hourly cryptocurrency trading is not only weak predictability, but also the way forecasts are converted into trades." A >99% turnover cut was required for ANY net-positive ML result, and even then buy-and-hold was not beaten after Holm correction. **Prior update: reinforces the Jun-4 diagnosis (cost/turnover is the tractable margin, alpha is not) with the most rigorous 2026 evidence yet. Any future screen whose edge shrinks as its trade count grows should be treated as presumptively cost-artifacted.**
2. **The funding-arb opportunity set is real but mostly cost-consumed** — MDPI Mathematics Jan 2026 (Two-Tiered): 17% of 1-minute observations show significant cross-venue spreads; only ~40% of TOP opportunities are net-positive after costs and spread reversals. **Prior update: quantitative external anchor for why F1 survives only with maker-first execution and why the dispersion extension failed our screen — selection INTO the visible spread is where the loss hides.**
3. **Crypto factor "alphas" are mostly redundant** — IRFA 2026: 2–3 factors (turnover volatility, bid-ask spread, chain metrics) absorb all significant portfolio alphas (snippet-grade, single source). Consistent with our formulaic-alphas refutation.
4. **Frequency gate for event strategies** — practitioner-grade but well-argued (Tigro Blanc, Apr 2026): event strategies below ~20 trades/yr cannot be statistically distinguished from luck at retail horizons. **Our ≥30-resolved-events-per-arm promotion floor already embodies this; treat any future event-driven GO with n/yr < ~15 as multi-year-to-promote BY DESIGN and say so up front.**
5. **Dated-futures crash-basis risk** — AEA 2026 conference paper: quarterly futures fall 8–10% more than spot in stress events vs ~3% for perps (abstract-grade). Supports the quarterly leg-swap NO_GO and adds a standing prior: fixed-leg carry instruments carry MORE stress-convexity than perps, not less.
6. **Retail algo-profitability claims: no update.** The "60% of retail algo traders profitable" claim still traces to vendor/affiliate content ([tv-hub 2026 guide](https://www.tv-hub.org/guide/is-automated-trading-profitable)); 2026 secondary aggregations (Traders Union: 75–89% of customers lose) are non-academic. The 07-08 verdict — treat as marketing — stands unchanged; no rigorous 2026 audited-sample study found.

---

## SOURCE LIST (all accessed 2026-07-16)

| # | Source | Date | Grade / flags |
|---|---|---|---|
| 1 | [Bysik & Ślepaczuk, ML Bitcoin trading under transaction costs, arXiv 2606.00060](https://arxiv.org/html/2606.00060) | 19 May 2026 | Full text read; preprint (Univ. of Warsaw QFRG); most rigorous item of the sweep |
| 2 | [Tigro Blanc, unlock-short backtest, Coinmonks](https://medium.com/coinmonks/i-backtested-shorting-token-unlocks-heres-why-i-m-not-trading-it-yet-42e237d40d9a) | 10 Apr 2026 | Full text read; practitioner, single-source, self-flagged small-n |
| 3 | [Bui & Nguyen, AdaptiveTrend, arXiv 2602.11708](https://arxiv.org/pdf/2602.11708) | 12 Feb 2026 | Full text read (local PDF extraction); NOT peer-reviewed; eval-window design selection |
| 4 | [Tadi & Witzany, copula pairs, Financial Innovation](https://link.springer.com/article/10.1186/s40854-024-00702-7) | 13 Jan 2025 | Full page read; peer-reviewed; no multiplicity control; data ends Jan 2023 |
| 5 | [Candlestick intraday forecasts, IREF, S1059056026002716](https://www.sciencedirect.com/science/article/pii/S1059056026002716) | Apr 2026 | **403 — abstract-grade from snippets**; peer-reviewed + SPA control; after-cost UNVERIFIED; sample ends Jan 2022 |
| 6 | [MDPI Mathematics 14(2):346, Two-Tiered funding structure](https://www.mdpi.com/2227-7390/14/2/346) | Jan 2026 | Published (was 403 abstract in 07-08 pass); numbers via coverage snippets |
| 7 | [QF 2026 GA-optimized pairs](https://www.tandfonline.com/doi/full/10.1080/14697688.2026.2653663) | 2026 | 403 — snippet-grade, single-source |
| 8 | [Zarattini/Pagani/Barbon, Catching Crypto Trends, SSRN 5209907](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=5209907) ([Concretum page](https://concretumgroup.com/catching-crypto-trends-a-tactical-approach-for-bitcoin-and-altcoins/)) | ~2025 | Concretum page fetched; abstract-grade; working paper |
| 9 | [AEA 2026, Perpetual Futures and Basis Risk](https://www.aeaweb.org/conference/2026/program/paper/ByyFEfr4) | 2026 | Abstract-grade, single-source |
| 10 | [Han/Kang/Ryu, momentum under realistic assumptions, SSRN 4675565](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=4675565) | 2024 | Snippet-grade; pre-dates bar; direction supports refutation |
| 11 | [Wei, Lucky Factors, IJFE](https://onlinelibrary.wiley.com/doi/full/10.1002/ijfe.2863) | 2024 | Pre-dates bar; supports refutation |
| 12 | [IRFA factor selection, S1057521925004764](https://www.sciencedirect.com/science/article/abs/pii/S1057521925004764) | 2026 | Snippet-grade, single-source |
| 13 | SSRN [6632838](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=6632838) + [6579278](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=6579278) | — | **Both still 403 after one retry each (2026-07-16)** |
| 14 | [tv-hub automated-trading guide](https://www.tv-hub.org/guide/is-automated-trading-profitable), [Traders Union 2026](https://tradersunion.com/interesting-articles/retail-crypto-trading-study/) | 2026 | Vendor/secondary — cited only to confirm the marketing-claim verdict stands |

*Scout C run complete. No ledger rows reopened; no candidates produced (by design). Recommended ledger touch-ups for the orchestrator (evidence-context only, no verdict changes): (a) note MDPI Two-Tiered is now published with after-cost numbers on the dispersion NO_GO row; (b) note the Tigro Blanc independent corroboration + tail-risk replication on the unlock-short shadow row; (c) note the AEA 2026 basis-risk abstract on the quarterly leg-swap row.*
