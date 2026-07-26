# Scout verdict

**Two genuinely novel, futures-executable candidates survived, but both are SCREEN-only. Neither is ready for shadow or live capital.** The first has usable local OOS potential; the second is blocked by missing historical options-position data.

The binding ledger and prior sweeps were applied: [refuted-families ledger](/D:/Downloads/Trading_Bot/.claude/skills/refuted-families-ledger/SKILL.md), [futures scout](/D:/Downloads/Trading_Bot/_workspace/strategy_pipeline/14_scout_a_futures_2026-07-16.md), and [reopen sweep](/D:/Downloads/Trading_Bot/_workspace/strategy_pipeline/14_scout_c_reopen_sweep_2026-07-16.md).

## Candidate 1 — CME asset-manager option-position pressure, traded through BTCUSDT perp

### What it is

Once weekly, derive asset managers’ **options-only** BTC position from the CFTC futures-and-options-combined report minus its futures-only report. Residualize the weekly position change using the paper’s exact non-momentum specification, then trade BTCUSDT on Binance, Bybit, or Bitget during the paper’s second-week-ahead window.

A no-lookahead implementation would consume Friday’s 15:30 ET release and hold approximately Tuesday-to-Tuesday of the following week. The paper’s exact sign and variable construction must be reproduced from its equations before preregistration.

### Mechanism

Asset managers use options to transfer downside and convexity risk, particularly in high-downside-risk states. Their non-momentum position adjustment proxies residual hedging/inventory pressure that other participants must absorb.

This is materially weaker than a proven forced-flow mechanism: the authors’ Granger tests indicate that futures returns lead the position changes, so they interpret the result as a **predictive association consistent with risk transfer**, not causal price impact.

### External evidence

The peer-reviewed 2026 study uses weekly CME data from **21 January 2020 through 4 March 2025** and reports that asset-manager options positions selectively predict the second-week-ahead BTC futures return beyond futures positions. The result survives alternative returns, subsamples, and OOS forecasting and is concentrated in high-downside-risk states. [Shen, Li & Luo, *Finance Research Letters*, 2026](https://www.sciencedirect.com/science/article/pii/S1544612326008287).

**Single-source flag:** the accessible paper does not demonstrate a net trading strategy after fees, funding, or offshore-perp basis; no DSR/FDR-style correction is visible. The CFTC also warns that classifications reflect traders’ predominant reported business, not the reason for each individual position. Reports normally contain Tuesday positions and are released Friday, making publication-lag correction mandatory. [CFTC release mechanics](https://www.cftc.gov/MarketReports/CommitmentsofTraders/index.htm).

### Data to test locally

- Add historical CFTC futures-only and futures-plus-options reports; derive options-only asset-manager positions exactly as the paper does.
- Use `BTC-USDT_1h.parquet` for the frozen Tuesday-to-Tuesday return window. The untouched period from March 2025 onward supplies roughly 16 months of genuinely newer data.
- Use `binance_BTC.csv`, `bybit_BTC.csv`, and `bitget_BTC.csv` to charge every funding settlement during each hold.
- Evaluate each venue separately before any pooled result; include Friday publication delays, holidays, spread, slippage, and mark-to-market drawdown.
- Null benchmark: always-flat, plus BTC directional exposure with the same holding calendar but randomized signal signs.

### Costs and $420 feasibility

Operationally feasible at 1× isolated exposure. At 10–20% account notional ($42–$84), a taker round trip costs about **$0.04–$0.10**, before spread and funding, using approximately 5–6 bps per side. Bybit currently quotes 2 bps maker/5.5 bps taker, while Bitget quotes 2/6 bps; verify the account-specific Binance rate before testing. [Bybit fee schedule](https://www.bybit.com/en/help-center/article/Trading-Fee-Structure), [Bitget fee documentation](https://www.bitget.com/en-CA/support/articles/12560603828198).

Low turnover helps, but a week-long perp hold crosses many funding settlements. No leverage is justified until after-cost expectancy, clustered-bootstrap probability of profit, and maxDD gates pass.

### Novelty versus ledger

**NEW, eligible for a narrow screen.** It uses regulated trader-class risk-transfer data, not price indicators, funding direction, OI divergence, seasonality, or ETF-flow timing.

Fail-closed rule: if the result disappears after Friday publication lag or is explained by lagged BTC returns, classify it under the ledger’s **formulaic-alpha / textbook directional** refutations and drop it.

---

## Candidate 2 — Negative-gamma option-expiry reversal, executed only in BTCUSDT perp

### What it is

On Deribit BTC option expiries with both:

1. unusually high at-the-money expiring open interest, and  
2. negative estimated cumulative dealer gamma,

measure the pre-expiry dealer-hedging move and take the opposite BTCUSDT-perp position immediately after the 08:00 UTC expiry, using the paper’s exact return window.

No option is traded and no options account is required; options positioning is only the external state variable.

### Mechanism

Dealers short gamma must hedge procyclically—buying as BTC rises and selling as it falls—to remain within risk limits. As contracts expire, that price-insensitive hedge demand disappears or unwinds, producing the measured reversal. Option holders pay dealers to warehouse convexity, while dealers’ mandated hedging creates the temporary underlying-perp flow.

### External evidence

A peer-reviewed 2026 study documents statistically and economically significant BTC return reversals around Deribit option expiration. Effects are strongest with elevated ATM OI and negative cumulative gamma; activity also rises in Deribit perpetuals and settlement-index spot markets. The authors estimate roughly **$50 million per year** in associated wealth transfers. [Weiss et al., *Finance Research Letters*, online June 2026](https://www.sciencedirect.com/science/article/pii/S1544612326008688).

Deribit confirms daily BTC options expire at **08:00 UTC**, with the delivery index calculated over 07:30–08:00 UTC. [Deribit contract policy](https://support.deribit.com/hc/en-us/articles/25944688876957-Contract-Introduction-Policy), [inverse-option specification](https://support.deribit.com/hc/en-us/articles/31424939096093-Inverse-Options).

**Single-source and contradiction flags:** accessible text does not disclose an after-cost trading rule, genuine holdout, or multiplicity correction. A 2025 SSRN study reaches the opposite economic conclusion, estimating BTC gamma exposure at only 0.025% of daily option volume and therefore too small to move spot materially. [Lachowicz, November 2025](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=5782822).

### Data to test locally

- Local `BTC-USDT_15m.parquet` can measure 07:30–08:00 pressure and post-08:00 reversal, subject to confirming its venue provenance and filling missing history.
- The three BTC funding CSVs supply venue-specific funding, although a same-session post-expiry trade should normally avoid most funding exposure.
- Missing blocker: historical Deribit chain snapshots containing strike, expiry, OI, implied volatility and Greeks. Current public APIs expose OI and option summaries for forward collection, but they do not solve the historical archive. [Deribit public market-data API](https://docs.deribit.com/api-reference/market-data/public-get_book_summary_by_currency).
- Request the authors’ stated available-on-request data or obtain a timestamped institutional archive. Do not substitute today’s OI for historical OI.
- Freeze the paper’s exact GEX convention, ATM-OI cutoff, entry, exit, and event exclusions before observing Binance/Bybit/Bitget results.

### Costs and $420 feasibility

Operationally feasible but **economic feasibility is unproven**. At $42–$84 notional, a two-taker-fill event costs approximately $0.04–$0.10 plus spread and slippage. The 5-minute decision cycle can enter after the 08:00 settlement; 10-second polling is adequate for event detection but not for competing during the 07:30–08:00 settlement calculation itself.

Use 1× isolated exposure and a small capital fraction. Because the accessible paper gives no net-per-event expectancy, this candidate remains data-gated.

### Novelty versus ledger

**NEW, but currently unscreenable without historical option-chain data.** It is distinct from:

- funding-settlement timing—the forcing event is option expiry;
- hour-of-day seasonality—the trade requires expiring ATM OI and negative gamma;
- generic mean reversion—the reversal is admitted only by an external dealer-hedging state.

If expiry/GEX conditioning adds no incremental value over simply fading the preceding return, drop it under the ledger’s **RSI/mean-reversion and formulaic-alpha** rows.

## Proposals dropped at the scout gate

- **Cross-sectional 8–10-week loser-minus-winner reversal:** a May–June 2026 SSRN preprint reports Sharpe 1.19–1.38, but it is a price-ranked contrarian alpha with no visible after-cost, genuine OOS, or multiplicity-controlled result. Dropped under **“Formulaic alphas—443+ tested, best IR≈0.45 pre-cost”** and the system’s mean-reversion exclusions. [Kiefer & Nowotny, 2026](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=6703978).
- **Order-book toxicity/absorption trading:** the 2026 evidence concerns passive execution at sub-minute horizons, while a peer-reviewed six-coin study finds no microstructure strategy survives realistic retail fees. It is infeasible with the repo’s OHLCV-only history and 5-minute decisions, so it is not advanced. [Frontiers, June 2026](https://www.frontiersin.org/journals/blockchain/articles/10.3389/fbloc.2026.1811716/full).

## New 2026 evidence on existing probes

- **Listing-short:** no new measured 2026 study beyond the evidence already recorded on 17 July; current listing announcements are observations, not a controlled backtest.
- **Unlock-short:** no new measured evidence beyond the ledger’s April 2026 Tigro Blanc replication; July unlock calendars are narrative/event lists only.
- **TSMOM-20d:** new-to-this-sweep peer-reviewed evidence is adverse—momentum is absent among nine survivor coins and profitable in the rolling top-30 only after trimming, with results described as highly sample-dependent. [Grobys, Sandretto & Äijö, *Finance Research Letters* 92, 2026](https://iris.unito.it/retrieve/handle/2318/2137833/2029867/1-s2.0-S1544612326001339-main%20%281%29.pdf).
- **Breakout-60d:** no new 2026 OOS, multiplicity-controlled, after-cost evidence found; status remains unchanged.
- **Bundle-MR zfade/RSI2:** the Kiefer–Nowotny cross-sectional reversal preprint is distant family support only—not RSI2/zfade, not after-cost, not genuine OOS, and single-source—so it does not reopen the ledger row.
- **Reopen bar:** **nothing found meets it.** The strongest new rigorous microstructure study is negative after retail fees, so no refuted family is reopened.
