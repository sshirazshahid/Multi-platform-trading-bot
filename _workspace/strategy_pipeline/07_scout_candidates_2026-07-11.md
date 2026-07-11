# 07 — Scout Candidate Briefs (2026-07-11)

Scout: strategy-scout | Phase 1 of strategy-evidence-pipeline. Research only — no screening code, no backtests, no trades.

**Ledger compliance:** `refuted-families-ledger` read first. Nothing below re-proposes a refuted family (RSI-MR, breakouts, candlesticks, confluence, Kalman pairs, directional funding, formulaic alphas, ML forecasters, seasonality, dominance/ETF-flow, $1–2 scalping, grid/DCA, OI-divergence, long-only TSMOM) or the two 07-09/07-10-resolved screens (binance∩bybit dispersion hold-until-flip = CONFIRMED_NO_GO; full-stake listing-short = CONFIRMED_NO_GO; capital-scaled listing-short = CONFIRMED_GO, already live as ListingShortProbeAgent).

**Data-freshness verification (owner requirement, done 2026-07-11):**
- Live keyless ccxt pulls succeeded on all three venues: BTC/USDT:USDT funding (binance 3.418e-05, bybit 1.0e-04, bitget −1.8e-05) and 1h candles current to 2026-07-11 11:00 UTC (closes 64,169 / 64,168.5 / 64,166.6 — venues agree).
- `data/ohlcv_cache/` (635 × 1h parquet): BTC-USDT spans 2023-05-26 → **2026-07-11 07:00 UTC** — current. 35 symbols also have 15m parquets.
- `data/funding_history/` (137 venue-symbol CSVs): binance_BTC tail = **2026-07-08 16:00 UTC** — ~3 days stale. Top-up via existing `scripts/backfill_funding_history.py` REQUIRED before any screen.
- `data/funding_oi/`: funding for 5 majors since 2019 is deep, but **OI is only 2026-05-30 → 2026-07-10** (Binance serves ~30 days of OI history). No multi-year OI archive exists locally.
- `data/derivs_history.jsonl`: hourly multi-venue snapshots since ~2026-05-29, current to today 16:01 local.

---

## Candidate 1 — Funding-settlement-window timing (F1 execution refinement)

**What it is.** Measure whether perp prices exhibit a small, repeatable drift/convergence into the 00/08/16 UTC funding settlements (and Bybit/Bitget equivalents), and whether F1 carry entries/exits timed against settlement timestamps capture measurably more net carry than time-agnostic execution. Two testable sub-claims: (a) pre-settlement price drift in the direction that offsets/augments the funding payment; (b) entry just before settlement collects a full accrual the position barely "worked" for.

**Mechanism (why, not shape).** Funding is a discrete cash transfer at a known timestamp. Rational holders who don't want to pay close before settlement; collectors open before it. That scheduled, mechanical flow — not price prediction — is the constraint someone pays to escape. It is the same risk-transfer mechanism F1 already harvests, refined in time.

**External evidence.** Settlement mechanics well documented ([Perpmate](https://perpmate.com/learn/understanding-funding-rates); [Wharton perpetual pricing, He/Jermann](https://finance.wharton.upenn.edu/~jermann/AHJ-main-10.pdf)). A credible *measured* pre-settlement drift study was NOT found in the 2026-07-08 deep-research pass (settled finding — not re-searched today). Evidence status: **insufficient external, fully measurable locally** — which is exactly what a screen is for.

**Data to test it.** Entirely local: 137 venue-symbol funding CSVs (multi-year, after top-up) × 1h OHLCV (635 symbols) × 15m OHLCV (35 symbols) for the intra-window shape. Zero acquisition cost.

**Costs / feasibility @$420.** Best of the batch: it is a timing change on the already-validated, already-capitalized F1 lane — no new positions, no new fee legs, no new margin. If drift exists it either adds carry or reduces cost; if not, F1 is unchanged.

**Novelty-vs-ledger: ADJACENT** — explicitly named screen-eligible in the ledger's Validated section ("settlement-window timing"). Not covered by the dispersion NO_GO row (different claim: timing vs venue-pair spread).

---

## Candidate 2 — Pre-unlock short on large early-stage token unlocks (capital-scaled, perp-expressed)

**What it is.** Short the perp of tokens facing large vesting-cliff unlocks, entering ~2–4 weeks BEFORE the unlock date and exiting at/near the unlock — not after it. Restrict to the measured effect zone: unlock value ≥10% of market cap, early-stage/thin-float tokens, non-insider allocations. Sized with the ListingShortProbeAgent capital-scaled template (3% per-trade / 12% exposure caps) that already passed the MC maxDD gate for the sibling listing-short.

**Mechanism.** A public, dated supply schedule triggers anticipatory selling/hedging by recipients (who often hedge via perps before tokens are liquid) and by front-runners. Structural flow against a known calendar — mechanically the same event-driven family as the validated listing-short probe, not indicator-based price prediction.

**External evidence (the reason this is re-opened and REDIRECTED).** [unlocks.app insights, "Do Token Unlocks Crash Prices? What 236 Events Show"](https://insights.unlocks.app/do-token-unlocks-crash-prices/), published **2026-06-29**: 236 unlock events Jun-2024 → Mar-2026 (prices through Jun-2026), mcap >$10M, BTC-beta-adjusted, matched-peer + age-matched + within-token placebo controls. Findings: raw 1-month median −16.26% (72.5% closed lower); controlled overall −4.85% median; early-stage tokens −16.02% (age-matched −14.8%); established tokens −2.57% (no significant effect); of 77 events with measurable effects, 73 had unlock-value/mcap ≥10% and 68 were non-insider. **Critical timing finding: pre-event drift −14.7% (1 month) / −9.1% (2 weeks); post-unlock movement minimal.** This is single-source but methodologically rigorous (controls + placebos), and it *contradicts* the earlier settled single source ([SSRN 52-event "72-Hour Shock"](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=6632838), post-unlock −16.97%) on WHEN the move happens. The screen must adjudicate the window on our own data; the naive post-unlock short briefed on 07-08 should be considered superseded.

**Data to test it.** Local: perp availability + prices from ohlcv_cache; realized funding charged to the short leg from funding_history (non-negotiable — hyped thin-float names often have negative funding where shorts PAY; this cost killed nothing yet but was the listing-short's biggest honest cost). **Blocker: a historical unlock calendar.** DefiLlama's free emissions endpoint now returns HTTP 402 (verified today — paywalled). Feasible alternatives to evaluate in Phase 2: scraping CryptoRank/Tokenomist/unlocks.app public calendars for historical cliff dates, or manual assembly of the ~77-event effect-zone subset. If no clean historical calendar is securable, the honest verdict is INSUFFICIENT_DATA, not improvisation.

**Costs / feasibility @$420.** Perp short = 2 fee legs + realized funding; capital-scaled caps already proven compatible with the account. Effect sizes (−9% to −15% over 2–4 weeks) dwarf round-trip costs IF the window replicates. Perp availability on Binance/Bybit/Bitget for early-stage tokens is good (all three list aggressively) but must be verified per event — events without a listed perp or funding coverage are excluded and counted, never guessed.

**Novelty-vs-ledger: NEW.** Not on the ledger. Distinct from the resolved listing-short rows (different event, different window). The 07-08 brief's "post-unlock short" was never screened; this candidate replaces it with a better-evidenced, differently-timed variant.

---

## Candidate 3 — Term-locked carry: quarterly-futures basis leg-swap for F1

**What it is.** When the annualized basis on Binance dated futures (e.g., BTCUSDT/ETHUSDT quarterlies) exceeds the trailing/expected funding APR of the equivalent F1 perp position, express the carry short leg via the quarterly instead of the perp: long spot + short dated future, hold to expiry. The basis is locked at entry — no funding-flip risk for the term.

**Mechanism.** Same risk transfer as F1 (leverage demand pays for hedged inventory), but the term structure lets the carrier choose between a floating rate (perp funding) and a fixed rate (dated basis). Selling the fixed leg when it is rich vs realized funding is a mechanical spread choice, not a directional view.

**External evidence.** [BIS Working Paper 1087, "Crypto carry"](https://www.bis.org/publ/work1087.pdf) — peer-reviewed-grade documentation that crypto futures carry is large, time-varying, and harvestable, with crash risk concentrated in deleveraging episodes (note: 2023 — predates the reopen-bar's 12-month preference; used here as family support, not as the screen's basis, since the family is already validated locally by F1). [Glassnode's annualized 3m-rolling-basis vs perp-funding series](https://studio.glassnode.com/charts/futures-annualized-yield?a=BTC) documents that dated basis is structurally less volatile than perp funding — the fixed-vs-floating spread this candidate trades. The ScienceDirect 2025 funding-arb study (already cited for F1) covers the floating side.

**Data to test it.** NOT cached locally — quarterly-futures OHLCV must be pulled fresh (keyless ccxt supports Binance delivery contracts; verified generally today via keyless access, contract-level pull is Phase 2 work). Perp funding comparison side is fully local. Expired-contract history on Binance is retrievable but limited; the screen may need to reconstruct basis from the last 2–4 quarterly cycles only — small-N must be stated, not hidden.

**Costs / feasibility @$420 — the honest weak point.** Binance-only (Bybit/Bitget dated liquidity thin/absent). Min notional ~100 USDT per leg → one BTC or ETH position = ~200 USDT across both legs ≈ 48% of the account, on top of F1's existing capital constraint. Sizing infeasibility has killed carry variants before; this candidate survives only if the screen shows the fixed-leg premium is large enough to justify displacing F1 capital, and the edge-screener must check leg minimums FIRST.

**Novelty-vs-ledger: ADJACENT** — an execution/instrument variant of validated F1. Not any refuted family (it is not "directional funding signals" — no direction is taken).

---

## Candidate 4 — Post-cascade dislocation reversion (forced-flow absorption) — measurement-first only

**What it is.** After a leverage-flush liquidation cascade, absorb the overshoot: enter contra to the cascade direction once forced flow is exhausted, exit on reversion toward pre-cascade fair value. Proposed here strictly as a *measurement* candidate — build the event catalog and measure, don't trade.

**Mechanism.** Liquidation engines are price-insensitive sellers/buyers; ADL and insurance-fund mechanics amplify. Absorbing forced flow is compensated liquidity provision (risk transfer), not price prediction. Mechanically distinct from refuted RSI-MR: the trigger is evidence of forced flow (OI collapse + liquidation prints), never an oscillator — if a screen ever degenerates to "price fell a lot, buy", it re-enters the refuted family and must stop.

**External evidence (2025+, event-grade).** [Lim, "Anatomy of a Crypto Cascade: Minute-Level Evidence from the October 2025 Crash" (SSRN 6579278)](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=6579278) — minute-level Binance/Bybit: futures led the crash, extreme futures-spot divergence, mark-price feedback loop (full text 403 today; abstract-grade). [Lim, "Two-Regime Liquidity Recovery After a Perpetual Futures Liquidation Cascade" (SSRN 6636998)](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=6636998) — Hyperliquid, Oct-10-2025: cascade compressed intraday (median spread 9.4bps, p95 87.5bps at hour 21 UTC), **intraday recovery well under way by end of day**; spreads within 1.2× pre-event across 23 days. [ResearchGate anatomy of Oct 10–11](https://www.researchgate.net/publication/396645981_Anatomy_of_the_Oct_10-11_2025_Crypto_Liquidation_Cascade_Macroeconomic_Triggers_Market_Microstructure_and_Systemic_Risk_Lessons) — ~$19B OI erased in 36h. These document overshoot-then-recovery as real; none quantifies a *retail-executable* after-cost reversion return — that gap is the screen.

**Data to test it — the honest problem.** Local OI history is only **2026-05-30 → 2026-07-10, 5 majors** (Binance ~30-day API cap); no multi-year OI archive exists, so a multi-year cascade catalog from OI is impossible locally. A price/volume proxy from 1h candles conflates cascades with news crashes (uninterpretable). Sub-hourly local data = 15m for 35 symbols only. And the documented recovery is compressed intraday — much of it inside the bot's 5-min decision cycle.

**Costs / feasibility @$420: WEAK at current infrastructure.** Recommended path if pursued at all: (a) start logging liquidation/forceOrder streams and hourly OI *forward* (extend the existing derivs harvester) to build the dataset the screen needs in 2–3 months; (b) meanwhile measure the few in-window events (Jun–Jul 2026) descriptively. Do NOT screen on proxy triggers over 1h history — that is the refuted-MR trapdoor.

**Novelty-vs-ledger: NEW (mechanism) / feasibility-limited.** Not on the ledger; flagged for adjacency policing vs RSI-MR and OI-divergence rows (this uses OI *level collapse* as event evidence, not OI-price divergence as a signal — the distinction must hold under audit).

---

## Explicit SKIPs (settled — cite, don't re-search)
- **Retail market-making / order-flow spread capture:** adverse selection dominates at retail polling latency; settled 2026-07-08 ([Tiniç & Sensoy](https://nottingham-repository.worktribe.com/OutputFile/40584797); [arXiv 2606.05882](https://arxiv.org/pdf/2606.05882)).
- **Volatility-risk-premium / options-implied-vol strategies:** no options venue on Binance/Bybit/Bitget USDT-perp+spot setup; no local options data. Settled 07-08.
- **CEX↔DEX funding arb:** custody/bridge/ops surface not built; revisit at larger equity. Settled 07-08.
- **Cross-exchange latency/price arbitrage:** 10s polling is orders of magnitude too slow; the venues' prices agreed to <0.01% in today's freshness pull.

## Ranking for edge-screener (Phase 2)
1. **Candidate 1 (settlement-window timing)** — zero data acquisition, zero new capital, refines the only validated lane. Highest actionable-per-effort.
2. **Candidate 2 (pre-unlock short)** — strongest new external evidence of this pass (236 events, controls, 2026-06-29); gated on securing a historical unlock calendar (DefiLlama free tier now 402 — verified today).
3. **Candidate 3 (quarterly basis leg-swap)** — sound mechanism, peer-reviewed-grade family support; capital-tightest, Binance-only, leg-minimum check first.
4. **Candidate 4 (post-cascade reversion)** — real mechanism, but local data cannot support an honest historical screen; forward data collection only.

## Source list
1. unlocks.app insights (2026-06-29), 236-event unlock study — https://insights.unlocks.app/do-token-unlocks-crash-prices/ (single source, high methodological quality; contradicts SSRN 6632838 on timing)
2. SSRN 6632838, "The 72-Hour Shock" (52 events) — https://papers.ssrn.com/sol3/papers.cfm?abstract_id=6632838 (superseded on timing by #1; both single-source)
3. SSRN 6579278, Lim, minute-level Oct-2025 crash — https://papers.ssrn.com/sol3/papers.cfm?abstract_id=6579278 (abstract-grade; full text 403)
4. SSRN 6636998, Lim, two-regime liquidity recovery (Hyperliquid) — https://papers.ssrn.com/sol3/papers.cfm?abstract_id=6636998
5. ResearchGate 396645981, Oct 10–11 2025 cascade anatomy — https://www.researchgate.net/publication/396645981
6. BIS WP 1087, "Crypto carry" — https://www.bis.org/publ/work1087.pdf (2023; family support only)
7. Glassnode, annualized perp funding vs 3m rolling basis — https://studio.glassnode.com/charts/futures-annualized-yield?a=BTC
8. Perpmate funding mechanics — https://perpmate.com/learn/understanding-funding-rates ; Wharton perp pricing — https://finance.wharton.upenn.edu/~jermann/AHJ-main-10.pdf
9. ScienceDirect 2025 funding-arb study (F1 family anchor, already in ledger) — https://www.sciencedirect.com/science/article/pii/S2096720925000818

*Coverage note: web research ran normally (not LIMITED). SSRN full texts 403'd; findings from those papers are abstract/secondary-coverage grade and flagged as such.*
