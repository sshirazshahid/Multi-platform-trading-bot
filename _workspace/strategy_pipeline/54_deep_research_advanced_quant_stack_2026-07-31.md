# Advanced Quant Stack (Kalman / VPIN / PCA / HMM / Dispersion / LOXM): Research Report
*Generated: 2026-07-31 | Sources: ~26 unique | Confidence: High on VPIN dispute + LOXM identity + local ledger; Medium on Kalman/pairs & vol-dispersion (practitioner-heavy); Medium–Low on PCA-momentum blogs*

**Defaults:** After-cost / promotion-gate honesty for *this* Trading_Bot (crypto perps PAPER + F1-only posture). Equity-options families flagged as instrument-mismatch unless Deribit/CBOE stack exists.

**Tooling:** Firecrawl/Exa unavailable → WebSearch + deep-read digests + local pipeline ledger.

## Executive Summary

This laundry list mixes **three different jobs**: (1) *signals* (Kalman pairs, XS mean-reversion, PCA L/S), (2) *filters / risk* (VPIN, HMM regimes), (3) *execution / vol structure* (LOXM, earnings IV crush, index–stock dispersion). Only a subset is even in scope for a Binance/Bybit/Bitget perp bot without options.

**Hard local facts:** VPIN AccBand veto = **CONFIRMED_NO_GO** (`27_*`); cross-venue **funding** dispersion = **CONFIRMED_NO_GO**; textbook trend/breakout REFUTED for promote; F1 carry remains the only ledger profit class when edge clears — and funding is compressed ([`51_*`](51_deep_research_futures_strategies_2026-07-31.md), Fact 9).

**External pattern:** Kalman improves hedge dynamics but **spread scale vs fees** decides survival ([Portfolio Optimization book](https://portfoliooptimizationbook.com/book/15.6-kalman-pairs-trading.html)); crypto pairs often land **cost-breakeven** ([Anomiq](https://anomiq.io/blog/pairs-trading-crypto-mean-reversion/)); VPIN’s crash-forecast claim is **contested** ([Andersen & Bondarenko](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2305905)); LOXM is **JPMorgan’s RL equity execution engine**, not a retail alpha strategy ([InformaConnect](https://informaconnect.com/the-latest-in-loxm-and-why-we-shouldnt-be-using-single-stock-algos/)); vol-dispersion needs **options + hedging ops** and bleeds in correlation spikes ([Quant Decoded](https://quantdecoded.com/en/dispersion-trade-correlation-risk-premium-backtest)).

## 1. Kalman filter pairs trading

**Mechanism:** Model hedge ratio βₜ as a latent state; Kalman updates yield a more stationary residual than rolling OLS; trade z-score of residual ([book ch.15.6](https://portfoliooptimizationbook.com/book/15.6-kalman-pairs-trading.html), [MAS Digital](https://mas-digital.io/does-stat-arb-pairs-trading-work-in-crypto/)).

**Evidence:**
- Gross: Kalman often smoother equity / lower DD than OLS in textbook examples — **often ignoring costs** ([book](https://portfoliooptimizationbook.com/book/15.6-kalman-pairs-trading.html)).
- Binding constraint: if process-noise α makes spread variance too small, **edge vanishes after costs** ([book](https://portfoliooptimizationbook.com/book/15.6-kalman-pairs-trading.html)).
- Crypto: 4-leg RT often ~0.3–0.5%; profit/trade must clear that ([marketmaker.cc](https://marketmaker.cc/ky/blog/post/statistical-arbitrage-pairs-trading-crypto/)). Cointegrated-pair suite: gross real, net ≈ **cost-breakeven**, partly market-wide alt-vs-BTC drift ([Anomiq 137 pairs](https://anomiq.io/blog/pairs-trading-crypto-mean-reversion/)).
- One WF practitioner claims +Sharpe with OLS selection @ 4–8 bps; **Kalman at 5m overtraded** in their ablation ([Delphi Alpha](https://delphicalpha.substack.com/p/pairs-trading-part-2-backtest-results)) — single-author, not peer-reviewed.
- BTC futures hedging: Kalman hedge ratios beat constant OLS on risk reduction 2017–22 ([IEEE ICBC 2023](https://doi.org/10.1109/icbc56567.2023.10174915)) — **hedging**, not necessarily alpha.

**Bot mapping:** Bundle-MR probes exist (log-only). No live Kalman pairs. Candidate B from `51_*` still deferred — would need hashed prereg with 4-leg costs + funding + WF pair selection.

**Verdict:** Research-eligible; expectation **NO_GO / INSUFFICIENT** until local after-cost WF. Do not install live.

## 2. Earnings implied volatility crush

**Mechanism:** Pre-earnings IV inflates on event uncertainty; post-print IV collapses 50–80% even if the move matches the implied ([FlashAlpha guide](https://flashalpha.com/articles/complete-guide-trading-earnings-volatility), [Quantcha](https://quantcha.blob.core.windows.net/public/Research/2204-Quantcha-AnAnalysisOfTradingEarningsReleasesUsingOptions.pdf)).

**Evidence:** Structural crush is real; **naive short premium is not free money** — tails, assignment, concave surfaces ([Alexiou et al. 2025 via Options Jive](https://optionsjive.com/blog/how-to-trade-earnings-iv-crush-options-strategies-that-work/)). Long ATM straddles 1d before / 1d after showed strong CAGR in one 17y study but **−83% single-week** risk and costs not fully in ([SSRN 4832160](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=4832160)). Gao et al. found **positive** returns to buying straddles pre-earnings in their sample (event vol sometimes *cheap*) — so “always sell crush” is false ([Options Jive summary](https://optionsjive.com/blog/how-to-trade-earnings-iv-crush-options-strategies-that-work/)).

**Bot mapping:** Needs **equity/crypto options** + earnings calendar. This bot’s Deribit snap tasks ≠ full earnings IV crush book. Prior C2 gamma-expiry / Deribit candidates were data-starved.

**Verdict:** **Out of primary scope** for USDT-perp AccBand/F1. New product lane only with options data + hashed prereg.

## 3. Order flow toxicity (VPIN)

**Mechanism:** Volume-clock PIN variant; buckets of volume classified buy/sell; high VPIN ≈ toxic flow ([Easley–LdP–O’Hara lineage](https://hftradingbook.com/strategies/pin-vpin)).

**Evidence (dispute is binding):**
- Andersen & Bondarenko: after controlling volume/vol, **no incremental forecast power**; sensitive to classification; Flash Crash “warning” contested ([SSRN](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2305905), [2292602](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2292602)).
- 2026 practitioner consensus: useful **descriptor / execution gauge**, not proven standalone alpha ([HFT Book](https://hftradingbook.com/strategies/pin-vpin), [Micro Alphas](https://microalphas.com/vpin/)).

**Bot mapping:** `27_prereg_vpin_jump_veto` → screen **CONFIRMED_NO_GO** (mean VPIN≈0.127 → θ grid never fires; ΔEV=0). Directional VPIN = STOP. Reopen only via **new** prereg (e.g. percentile θ), never silent θ edit.

**Verdict:** Closed for AccBand veto. Optional future: execution-telemetry only (measurement), not trade.

## 4. PCA factor-neutral long/short

**Mechanism:** Compress return/feature covariance into K PCs; neutralize unwanted exposures; rank residuals → dollar-neutral L/S ([Phandas](https://github.com/quantbai/phandas), factor-zoo compression [ScienceDirect 2026](https://www.sciencedirect.com/science/article/abs/pii/S1057521926000645)).

**Evidence:** Crypto factor structure is sparse (liquidity/microstructure/on-chain often dominate vs classic HML) ([factor zoo zip](https://www.sciencedirect.com/science/article/abs/pii/S1057521926000645)). PCA-momentum blogs claim high Sharpes for K=2–4 ([PyQuantLab](https://pyquantlab.medium.com/pca-momentum-estimating-alpha-as-a-function-of-the-number-of-principal-components-k-d1a3314a8791)) — **unverified / high overfit risk**. Cost-aware dollar-neutral backtests mandatory; turnover kills.

**Bot mapping:** No PCA L/S engine in live path. Cross-section needs broad liquid universe + borrow/short perps ops (perps make shorting easier than equities).

**Verdict:** Research-only with WF + cost model. Expectation NO_GO until local IC/turnover survive. Not F1 substitute.

## 5. Cross-sectional mean reversion engine

**Mechanism:** Rank assets by recent residual / z vs cross-section; long losers / short winners (or funding-relative variants).

**Evidence:** Single-name EWMA-VWAP MR: powered **negative** after costs on 2025 1m data ([Anomiq](https://anomiq.io/blog/mean-reversion-crypto-backtest/)). Pairs MR: cost-breakeven ([Anomiq pairs](https://anomiq.io/blog/pairs-trading-crypto-mean-reversion/)). Funding-aligned XS case studies are intentionally low-power teaching examples ([ML4Trading funding](https://www.ml4trading.io/case-studies/crypto-funding-arbitrage/)). Bundle-MR / AccBand geometry already failed dual-goal profit locally (`30_*`).

**Bot mapping:** AccBand + RSI2/zfade probes = related family, log-only / research.

**Verdict:** Do not rebuild a “MR engine” for live. Align with F1-only + refuse −EV MR.

## 6. Hidden Markov regime detection

**Mechanism:** Latent states (calm / trend / crisis); Baum–Welch fit; Viterbi or filtered probs gate size/strategy ([QuestDB glossary](https://questdb.com/glossary/market-regime-detection-using-hidden-markov-models/), [QuantStart QSTrader](https://www.quantstart.com/articles/market-regime-detection-using-hidden-markov-models-in-qstrader/)).

**Evidence:** Best use is **risk filter** (block entries in high-vol state), not alpha. Soft posterior weights beat hard flips (churn) ([RegimeSense](https://github.com/moh1tt/RegimeSense) — demo). Label instability across retrains is a known ops hazard.

**Bot mapping:** Already have simpler regime tools: `BAND_REGIME_FILTER` (ADX/vol), BTC-trend soft-size, scalp quiet veto, F1 time/funding gates. Full HMM not required to get the *job* done.

**Verdict:** Optional research overlay; prefer **pre-registered** veto ΔEV test (same pattern as VPIN — which failed). Soft gates > hard strategy thrash.

## 7. Dispersion volatility arbitrage

**Clarify two meanings:**
| Term | Meaning | Local status |
|------|---------|--------------|
| **Vol dispersion** | Short index vol / long single-name vol (correlation risk premium) | Needs options; not implemented |
| **Funding dispersion** | Cross-venue funding differentials | Pipeline **CONFIRMED_NO_GO** (OOS-WR fail) |

**Vol-dispersion evidence:** Persistent implied>realized correlation on average; systematic short-index/long-stock vol earned ~4.8% ann. Sharpe ~0.62 **pre-cost** 2006–25, ugly in crises; after costs Sharpe ~0.38 class ([Quant Decoded](https://quantdecoded.com/en/dispersion-trade-correlation-risk-premium-backtest)). Edge must clear multi-name spreads + delta-hedge churn ([Navnoor](https://navnoorbawa.substack.com/p/how-vol-arb-desks-actually-make-money)).

**Bot mapping:** Equity/index options stack absent from live path. Do not confuse with funding-dispersion NO_GO.

**Verdict:** **Out of scope** for current perp PAPER. Separate venue/product decision.

## 8. LOXM execution algorithm

**What it is:** JPMorgan’s **deep RL limit-order / child-order placement** system for **agency equity execution** — trained on billions of real+simulated trades to minimize impact vs opportunity cost ([InformaConnect](https://informaconnect.com/the-latest-in-loxm-and-why-we-shouldnt-be-using-single-stock-algos/), [Best Practice AI](https://bestpractice.ai/ai-use-cases/case-studies/financial-services/jpmorgan-s-new-ai-program-for-automatically-executing-equity-trades-in-real-time-out-performed-current-manual-and-automated-methods-in-trial), [RL case study](https://doi.org/10.18690/978-961-286-485-9.20)). Portfolio-aware: avoid single-stock algos that finish liquid names first and leave factor tilts.

**What it is not:** A directional alpha strategy you “turn on” for BTC perps.

**Bot mapping:** Closest analogues: maker-first intents, slip model in `sim_execution`, stressed EconGate — **implementation shortfall hygiene**, not LOXM clone. Building bank-scale RL execution needs proprietary fill sim + compliance — unrealistic here.

**Verdict:** Steal **ideas** (portfolio-aware child orders, impact vs urgency); do not attempt LOXM replication. Maker-first + cost gates already match the *spirit*.

## Priority matrix for this bot

| Family | Job | After-cost external read | Local ledger | Next action |
|--------|-----|--------------------------|--------------|-------------|
| Kalman pairs | Signal | Promising gross; fees bind | No live | Optional NEW prereg (after `52_*`) |
| Earnings IV crush | Options event | Structural; tails | No options book | Skip / separate product |
| VPIN | Filter | Contested predictor | **NO_GO** `27_*` | Closed |
| PCA factor L/S | Signal | Sparse factors; blogs optimistic | None | Low priority research |
| XS mean reversion | Signal | Often cost-dead | AccBand/MR −EV | Refuse live |
| HMM regimes | Filter | Useful as gate | Soft gates exist | Optional ΔEV prereg only |
| Vol dispersion | Vol arb | Needs options; crisis skew | N/A | Skip |
| Funding dispersion | Carry RV | — | **NO_GO** | Closed |
| LOXM | Execution | Bank RL equity TCA | Maker/sim only | Idea transfer only |

## Key Takeaways

1. **Don’t install the Wall-Street checklist.** Most items are wrong instrument (options/equities) or already refuted locally (VPIN, funding dispersion, AccBand profit).
2. **Kalman pairs is the only semi-plausible *new* signal** in-scope for perps — and only behind 4-leg cost + WF prereg; Delphi’s own Kalman ablation overtraded.
3. **VPIN/HMM belong as filters**, and VPIN already failed the AccBand veto screen.
4. **LOXM = execution RL at JPM**, not an alpha toggle; keep improving maker/slip realism instead.
5. **Stay F1-only until funding clears**; queue remains `52_prereg_cost_aware_accband_kappa` before any pairs prereg.

## Sources

1. [Portfolio Optimization — Kalman pairs](https://portfoliooptimizationbook.com/book/15.6-kalman-pairs-trading.html)  
2. [MAS Digital — crypto stat arb](https://mas-digital.io/does-stat-arb-pairs-trading-work-in-crypto/)  
3. [marketmaker.cc — Kalman crypto pairs](https://marketmaker.cc/ky/blog/post/statistical-arbitrage-pairs-trading-crypto/)  
4. [Anomiq — 137 cointegrated pairs](https://anomiq.io/blog/pairs-trading-crypto-mean-reversion/)  
5. [Anomiq — XS/single MR tick study](https://anomiq.io/blog/mean-reversion-crypto-backtest/)  
6. [Delphi Alpha — perp pairs WF](https://delphicalpha.substack.com/p/pairs-trading-part-2-backtest-results/)  
7. [IEEE — Kalman BTC futures hedge](https://doi.org/10.1109/icbc56567.2023.10174915)  
8. [FlashAlpha — earnings IV crush](https://flashalpha.com/articles/complete-guide-trading-earnings-volatility)  
9. [Quantcha — earnings options](https://quantcha.blob.core.windows.net/public/Research/2204-Quantcha-AnAnalysisOfTradingEarningsReleasesUsingOptions.pdf)  
10. [SSRN 4832160 — 17y straddles](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=4832160)  
11. [Options Jive — IV crush research](https://optionsjive.com/blog/how-to-trade-earnings-iv-crush-options-strategies-that-work/)  
12. [HFT Book — PIN/VPIN honesty](https://hftradingbook.com/strategies/pin-vpin)  
13. [Andersen & Bondarenko VPIN dispute](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2305905)  
14. [Micro Alphas — VPIN](https://microalphas.com/vpin/)  
15. [Crypto factor zoo compression](https://www.sciencedirect.com/science/article/abs/pii/S1057521926000645)  
16. [Phandas factor engine](https://github.com/quantbai/phandas)  
17. [PyQuantLab — PCA momentum](https://pyquantlab.medium.com/pca-momentum-estimating-alpha-as-a-function-of-the-number-of-principal-components-k-d1a3314a8791)  
18. [QuestDB — HMM regimes](https://questdb.com/glossary/market-regime-detection-using-hidden-markov-models/)  
19. [QuantStart — HMM risk filter](https://www.quantstart.com/articles/market-regime-detection-using-hidden-markov-models-in-qstrader/)  
20. [RegimeSense](https://github.com/moh1tt/RegimeSense)  
21. [Quant Decoded — dispersion backtest](https://quantdecoded.com/en/dispersion-trade-correlation-risk-premium-backtest)  
22. [Quant Decoded — correlation premium](https://quantdecoded.com/en/dispersion-trading-correlation-risk-premium)  
23. [InformaConnect — LOXM](https://informaconnect.com/the-latest-in-loxm-and-why-we-shouldnt-be-using-single-stock-algos/)  
24. [Best Practice AI — LOXM trial](https://bestpractice.ai/ai-use-cases/case-studies/financial-services/jpmorgan-s-new-ai-program-for-automatically-executing-equity-trades-in-real-time-out-performed-current-manual-and-automated-methods-in-trial)  
25. [RL observations — LOXM case](https://doi.org/10.18690/978-961-286-485-9.20)  
26. Local: `27_*` VPIN NO_GO; funding-dispersion NO_GO; `30_*` AccBand; `51_*`/`52_*`/`53_*`

## Methodology

Sub-questions: (1) which items are signals vs filters vs execution? (2) after-cost survival for Kalman/XS MR? (3) VPIN scientific status vs local screen? (4) options families in-scope? (5) what is LOXM?

~12 queries; academic priority on VPIN dispute + LOXM identity; practitioner on pairs/dispersion with flags.

## Pipeline honesty

No new live path, no AccBand reopen, no LOXM build, no VPIN θ reopen.  
**Queue order:** keep `52_*` cost-aware AccBand κ-filter ahead of any Kalman-pairs prereg. F1-only until funding/contango clear.
