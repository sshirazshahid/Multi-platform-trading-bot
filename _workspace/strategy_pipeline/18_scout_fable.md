# 18 — Scout (Fable): Delta-Only Futures Sweep (2026-07-22)

Phase 1A of the dual-model pipeline run 2026-07-22 (`18_context_2026-07-22.md`). Fable voice, independent of Codex GPT-5.6-Sol (its output NOT read before writing this). Research only — no code, no backtests, no trades.

**Scope:** evidence published or materially updated since **2026-07-17** (last exhaustive sweep = `14_scout_a_futures_2026-07-16.md` + `14_scout_c_reopen_sweep_2026-07-16.md`). Settled ground not re-surveyed.

**Ledger compliance:** `refuted-families-ledger` read first, including the 07-17 rows (F1 percentile-selectivity, stablecoin depeg, wrapper discount), the Open delisting row, and the 07-19 bundle-MR shadow additions (zfade cfg365 / rsi2 cfg226).

**Bottom line: ONE candidate (ADJACENT, low priority, expectation NO_GO) + forward-accrual notes. The 5-day delta contains exactly one genuinely new research item.** Web tools ran normally — coverage NOT limited.

---

## Candidate 1 — Quarter-hour opening order-imbalance → 4–12h return drift (Binance majors)

**What it is.** At deterministic clock boundaries (:00/:15/:30/:45), scheduled algorithmic execution (TWAP-style bots) fires in synchronized bursts on Binance USDT perps. The candidate expression for us: measure signed order-flow imbalance in the ~10s opening window of each quarter-hour boundary and test whether it predicts returns over the following **4–12 hours** — the only horizon in the paper compatible with our 5-min decision cadence and cost structure. The paper's other finding (10-second opening-return predictability) is **latency-infeasible for this stack and excluded from the candidate up front**.

**Mechanism (why someone pays).** Execution schedulers pay for predictability: institutional/algorithmic flow synchronizes on round clock times (trade-size roundness drops sharply inside the bursts — a behavioral signature of algos, not retail), and the imbalance revealed at those forced synchronization points partially reflects information or persistent parent-order flow that continues to press price for hours. This is a structural constraint (schedule adherence) someone pays to keep — not a chart shape.

**External evidence.**
- **Primary (the delta item):** Kim & Hansen, *"The Quarter-Hour Effect: Periodic Algorithmic Trading and Return Predictability in Cryptocurrency Futures"*, [arXiv 2607.09426](https://arxiv.org/abs/2607.09426), **v1 2026-07-10 / v2 2026-07-16** — posted at the boundary of the last sweep and not covered by it; new to the pipeline. Peter Reinhard Hansen is the author of the SPA data-snooping test — top-tier econometrics pedigree; acknowledgments cite an independent data validation/replication by Wade Kimbrough; Ripple UBRI funding disclosed. Data: aggregate trade data, six Binance USDT perps (BTC, ETH, XRP, SOL, DOGE, ADA), **2021-01-01 → 2024-10-31**. Findings (full-text fetch 2026-07-22): quarter-hour opening 10s windows carry ~26% more trades / 32% higher dollar volume / 26% larger absolute returns than ordinary minutes; opening returns predictable out of sample (rolling-window protocol); **opening order imbalance has "medium-horizon predictive content" for 4–12h returns**, weaker at finer clock frequencies.
- **Binding caveats (stated plainly):** (a) **NO transaction-cost modeling anywhere** — predictability-grade, not tradability-grade; effect sizes for the 4–12h horizon are not quantified in bps in the accessible text; (b) **NO multiplicity control** (HAC(30) SEs only — notable given Hansen invented SPA); (c) sample **ends 2024-10-31**, ~21 months stale; (d) the paper's own two-stage decomposition attributes the longer-horizon component "mainly" to the part of imbalance **spanned by observable price-volume state variables** — i.e., it may collapse into the locally refuted formulaic-alpha space. This is the kill-shot risk and must be the pre-registered null.
- **Family support (weak, flagged):** Shynkevich, *"Trading Periodicity and Algorithmic Divide in Cryptocurrency Markets"*, [Journal of Futures Markets, DOI 10.1002/fut.70089](https://onlinelibrary.wiley.com/doi/10.1002/fut.70089) — peer-reviewed, same periodicity family; ⚠ paywalled (402 on fetch), publication date unverified (~2026), single-source/abstract-grade.

**Data to test it LOCALLY.**
- `ohlcv_cache` 15m parquets can test only the coarse bar-level periodicity component — they **cannot** construct order imbalance (needs trade-level aggressor flags).
- **Exact harvest (free, public):** Binance historical aggTrades dumps at `https://data.binance.vision/data/futures/um/monthly/aggTrades/<SYMBOL>USDT/` (ms timestamps, price, qty, isBuyerMaker) for BTC/ETH/XRP/SOL/DOGE/ADA, **2025-01 → 2026-07**. This window is entirely POST the paper's sample end — the screen would be a genuine out-of-sample replication by construction, the strongest design available to us.
- Pre-registered question (for edge-screener, if advanced): does quarter-hour opening imbalance predict 4–12h returns **incrementally over observable price-volume state variables** (the paper's own decomposition controls), after costs, on 2025–26 data? Null = no incremental after-cost content. Frozen gates as usual.
- `derivs_history.jsonl` (hourly) is too coarse; not usable here.

**Costs / feasibility @$420.** The 4–12h horizon amortizes costs far better than any prior intraday-periodicity idea: one round trip ≈ 10–12 bps taker + slippage on Binance majors (tightest books we trade). Feasibility therefore hinges entirely on the unquantified effect size — if the conditional 4–12h drift is under ~15–20 bps per event, it is dead on arrival, and the paper gives no number. Signal timing is easy for our stack (boundary + 10s, evaluated at the next 5-min cycle). No new venue, no new instrument, no borrow leg.

**Novelty-vs-ledger: ADJACENT.** Clock-time conditioning is the home turf of the refuted hour-of-day/seasonality row (0 survive OOS, 2026-06-02), and the paper's own decomposition points the multi-hour component toward the refuted formulaic-alpha space (443+ tested, best IR≈0.45 pre-cost, 2026-05-25). The genuinely untested sliver is the signal variable itself — trade-level order-flow imbalance at deterministic algorithmic-schedule points — which no local screen has ever constructed (we have never had aggTrades-level data). **Does NOT meet the reopen bar** (no after-cost accounting, no multiplicity control, stale sample) — so this is a NEW-sliver ADJACENT candidate for a screen, not a reopen of either refuted row. **Ranked LOW; registered expectation NO_GO** (most likely outcome: the imbalance content is spanned by price-volume controls and/or under the cost floor). Gated on the aggTrades harvest.

---

## Candidate 2 — none

Nothing else in the 2026-07-17 → 2026-07-22 window qualifies. Directions checked and empty:
- **New q-fin/SSRN postings this week:** only 2607.09426 (above). The LLM multi-agent portfolio paper ([arXiv 2501.00826](https://arxiv.org/abs/2501.00826), rev 2026-06-16) predates the window and is ML-forecaster/advisory territory (Kronos-family row + our advisory layer) — not a candidate.
- **Funding-carry delta:** nothing new; the standing anchors remain Borri et al. (carry Sharpe negative in 2025) and MDPI Two-Tiered — both already on the ledger. SSRN 6185958 (Zhang, *Funding Rate Mechanism in Perpetual Futures*, Feb 2026) is a theory/mechanism-design paper, predates the window, no tradable claim — noted only.
- **Delisting/unlock/listing event evidence:** no new measured studies; only forward accrual (below).

**A zero-to-one candidate delta over 5 days is the expected outcome and is reported as such.**

---

## Probe-evidence + reopen-bar deltas (1-line each)

- **Delisting forced-flow (Open row, n=34 < 30/variant floor):** July 2026 Binance wave accrues ~**+4 token-level events** — ALCX, ARDR, NFP, POND futures auto-settled 2026-07-02, spot removed 2026-07-10 ([U.Today](https://u.today/binance-unveils-next-delisting-wave-as-four-crypto-tokens-lose-support), [Crypto-Economy](https://crypto-economy.com/binance-to-delist-4-tokens-as-exchange-tightens-listing-standards/)); the 2026-07-17 removals (GLM/BTC, KNC/BTC, ONT/BTC, XAI/USDC spot pairs; six margin pairs) are **pair-level only, NOT token delistings — do not count them** ([CoinEdition](https://coinedition.com/binance-to-delist-four-spot-trading-pairs-on-july-17-2026/)). Reopen accrual pace on track with the row's 8–12 events/yr estimate.
- **Unlock-short probe:** no new external evidence since the Tigro Blanc corroboration (already on the ledger row); July 2026 unlock calendar is moderate ($376M/145 projects — [MEXC](https://www.mexc.com/news/1186512), context only, not evidence).
- **Listing-short probe, TSMOM-20d, breakout-60d, bundle-MR (zfade/rsi2):** no new external evidence in the window; forward logs remain the instrument.
- **F1 carry:** no new evidence beyond the standing 2025-negative-Sharpe anchor; nothing this week changes the regime-idle diagnosis.
- **Reopen bar:** nothing published 07-17→07-22 meets it for any refuted family. The Quarter-Hour paper explicitly fails it (no costs, no multiplicity control) despite its pedigree.

---

## Source list (accessed 2026-07-22)

| # | Source | Date | Grade / flags |
|---|---|---|---|
| 1 | [Kim & Hansen, Quarter-Hour Effect, arXiv 2607.09426](https://arxiv.org/abs/2607.09426) ([full text](https://arxiv.org/html/2607.09426)) | v1 07-10 / v2 07-16 2026 | Full text fetched; preprint (not peer-reviewed); no cost model, no multiplicity control; independent replication noted in acknowledgments |
| 2 | [Shynkevich, Trading Periodicity & Algorithmic Divide, JFM fut.70089](https://onlinelibrary.wiley.com/doi/10.1002/fut.70089) | ~2026 (unverified) | ⚠ 402 paywalled — title/journal-grade only, single source |
| 3 | Binance July-2026 delisting wave: [U.Today](https://u.today/binance-unveils-next-delisting-wave-as-four-crypto-tokens-lose-support), [Crypto-Economy](https://crypto-economy.com/binance-to-delist-4-tokens-as-exchange-tightens-listing-standards/), [CoinEdition](https://coinedition.com/binance-to-delist-four-spot-trading-pairs-on-july-17-2026/) | Jul 2026 | Trade press; used only for event-accrual counting |
| 4 | [SSRN 6185958, Zhang, Funding Rate Mechanism](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=6185958) | Feb 2026 | Theory paper; predates window; noted, not advanced |
| 5 | [arXiv 2501.00826, LLM multi-agent crypto portfolio](https://arxiv.org/abs/2501.00826) | rev 2026-06-16 | Predates window; refuted-family territory; not advanced |
| 6 | [MEXC July-2026 unlock calendar](https://www.mexc.com/news/1186512) | Jul 2026 | Context only |

*Scout complete. One ADJACENT candidate (screen-eligible sliver, LOW priority, expectation NO_GO, gated on aggTrades harvest); zero reopens; delisting Open-row accrual +4.*
