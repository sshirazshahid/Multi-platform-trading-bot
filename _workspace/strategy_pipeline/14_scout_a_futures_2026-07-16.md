# 14 — Scout A: Futures/Derivatives-Side Candidates (2026-07-16)

Scout A (futures/derivatives sub-question) | Phase 1 of strategy-evidence-pipeline run 2026-07-16. Research only — no screening code, no backtests, no trades.

**Ledger compliance:** `refuted-families-ledger` read first. Nothing below re-proposes a refuted row (incl. the three rows added since the last scout pass: funding-settlement-window timing 07-11, quarterly-basis leg-swap 07-11, band-geometry positive selection 07-12) or touches the four in-shadow probes (capital-scaled listing-short, pre-unlock short, TSMOM-20d, breakout-60d — evidence updates on those are scout C's lane).

**Local data freshness (checked 2026-07-16 23:1x local):**
- `data/funding_history/`: **492 venue-symbol CSVs** (expanded from 137 since 07-11), newest file written today 23:17 — current.
- `data/ohlcv_cache/`: 638 parquet files (635×1h + 15m subset), newest 2026-07-14 — ~2 days stale; standard top-up before any screen.
- `data/funding_oi/`: 10 files, current to 2026-07-16; OI depth still only ~30d (Binance API cap) — unchanged constraint.
- `data/derivs_history.jsonl`: hourly multi-venue snapshots since 2026-05-29, current to today 23:01.

---

## Candidate 1 — Cross-venue delisting-short (capital-scaled, event-driven)

**What it is.** When a major venue (Binance primarily; Bybit/Bitget secondarily) announces DELISTING of a token's spot and/or perp, short the token's perp — on a venue that still lists it (cross-venue expression), or on the delisting venue itself up to its reduce-only cutoff — entering at the first post-announcement decision cycle and exiting before contract suspension/settlement. Sized with the capital-scaled template (3% per-trade / 12% exposure caps, unlevered) that already passed the MC maxDD gate for the sibling listing-short.

**Mechanism (why someone pays, not shape).** A delisting is a scheduled liquidity-death event against a public calendar: holders on the delisting venue MUST exit or be force-settled (Binance auto-settles remaining futures positions at a 30-min average index price, charging taker fees); market makers withdraw immediately (their inventory becomes unhedgeable); and the token permanently loses its largest demand/access pool — historically ~65% survive only on smaller exchanges and ~15% ever regain a major listing. That is structural forced flow, the mirror image of the listing-effect family. The seller is paying to escape a constraint (venue access disappearing), not reacting to a price pattern.

**External evidence (2025–2026, measured).**
- Announcement-day drops, multiple independent 2025–2026 event waves (trade press, each reporting exchange-measured prices): SYS −33.8%, ATA −33.3%, PHB −31.6%, MLN −27.7%, FARM −23.3% in one wave ([Yahoo Finance](https://finance.yahoo.com/markets/crypto/articles/4-binance-delisting-targets-tumble-153227077.html)); A2Z −16.2%, IDEX −17.6% initial → −33% daily, FORTH −16.3%, NTRN −15.2% (Apr 2025 wave, [DailyCoin](https://dailycoin.com/binances-april-1-delistings-hammer-8-altcoins-in-minutes/)); NFP/POND ~−20% ([CoinGape](https://coingape.com/binance-warns-of-delisting-these-tokens-price-drop-ahead/)); five altcoins double-digit down, May 2026 ([BeInCrypto](https://beincrypto.com/binance-delist-5-altcoins-may-2026/)).
- ⚠ Single-source vendor claim: delisted tokens lose ~80% of market value within 30 days of notice ([Volity](https://volity.io/crypto/delisting/) — education/vendor content, unaudited; treat as hypothesis, not fact).
- Peer-reviewed family support (2026): [Yang, Asia-Pacific Journal of Financial Studies](https://onlinelibrary.wiley.com/doi/10.1111/ajfs.70045) — Binance delistings are predictable from public signals (price drops, risk-topic news, Reddit; XGBoost+SHAP). Full text paywalled (402 today) — abstract-grade; it measures predictability of the EVENT, not post-announcement returns. ⚠ Search-snippet-grade stat needing verification in Phase 2: ~104 Binance spot delisting announcements Jan-2023→Mar-2025, average notice ~3 days, ~79% announced only 2 days ahead.
- Event-study support (older): negative crypto events show significant cumulative abnormal returns in a (−5,+10d) window at 1% significance ([Ünsal & Özkan-type event study, IDEAS/RePEc 2022](https://ideas.repec.org/a/ahs/journl/v7y2022i1p16-31.html)) — direction-consistent, pre-2025, family support only.
- Mechanics (venue-official): Binance futures delisting = reduce-only 30 min before suspension, auto-settlement at 30-min average index, taker fee on forced settlement ([Binance FAQ](https://www.binance.com/en/support/faq/delisting-of-futures-contracts-dd60dfbf654d4055aa6b217ea6d5ddba)). No formal academic study of post-announcement RETURN drift on futures specifically was found — the local screen is what establishes it.

**Data to test it.**
- **Blocker to resolve first (harvest, not improvise):** a historical delisting-announcement calendar with timestamps. Binance publishes a complete public announcement archive (delisting category: `binance.com/en/support/announcement/list/161`; the CMS article-list endpoint is scrapeable). Bybit exposes a public announcement API (`GET /v5/announcements/index?locale=en-US&type=delistings` via ccxt/raw HTTP). Bitget has an announcements page. Harvest 2023→2026 with announcement timestamps — the archive is complete regardless of whether the token survived, which kills selection bias at the signal stage.
- Prices: `data/ohlcv_cache` covers only currently-listed symbols → **survivorship risk is real and must be bounded**: for each announcement, pull OHLCV from the venues that STILL list the token (ccxt historical works there); count and report the events where no venue still serves data instead of dropping them silently.
- Funding charged to the short leg: `data/funding_history` (492 CSVs) where covered; per-event top-up via existing `scripts/backfill_funding_history.py` otherwise. Non-negotiable — delisting-doomed coins get crowded shorts and deeply negative funding (shorts PAY); this is the same honest cost that dominated the listing-short screen.
- Sub-signal to log in the same harvest: leverage-tier/margin-reduction and "monitoring tag" announcements often precede delisting (consistent with Yang's predictability result); no measured study found — record them as covariates, do not trade them.

**Costs / feasibility @$420.** Perp short = 2 fee legs + realized (likely negative) funding. Capital-scaled caps already proven compatible with the account and with the MC maxDD gate on the sibling family. Honest risks: (a) a large share of the announcement-day drop happens within MINUTES — at a 5-min decision cycle the screen must measure entry at announcement+1 cycle, never at the announcement print; (b) delistings come in WAVES (4–11 tokens same day) → correlated concurrent shorts; the screen must model concurrent-MTM account equity from the start (the exact issue that killed full-stake listing-short); (c) short window: if the ~2–3-day notice stat verifies, holds are days not weeks; (d) exit must be forced ≥1 day before the earliest venue suspension to avoid reduce-only traps and settlement-fee treatment.

**Novelty-vs-ledger: NEW.** Not on the ledger. Mirror-image mechanism of the in-shadow listing-short but a different event, different calendar, different expression (cross-venue survival leg). Not "directional funding signals" (funding is a charged cost here, not the signal).

---

## Candidate 2 — Funding-percentile persistence selectivity for F1 (carry-lane entry/exit refinement)

**What it is.** Condition F1 carry ENTRIES on the coin's funding sitting in a high percentile of its OWN trailing distribution (e.g., top quartile of 30–90d) with a persistence requirement, and condition EXITS on funding decaying below its trailing median — instead of (or on top of) the current level-based gate. The testable claim: percentile+persistence selection concentrates the same capital in fewer, longer, richer carry episodes, raising realized net carry per unit of round-trip cost. Delta-neutral throughout; no direction taken, ever.

**Mechanism.** Identical risk transfer to validated F1 (leveraged longs pay for hedged inventory); the refinement claim is that funding richness is autocorrelated — persistent demand imbalances (a coin trending in retail attention) decay over days, not one settlement — so the level-gate admits marginal episodes whose funding decays before round-trip costs amortize. Selectivity is the margin, and it matters MORE now: the average carry level has compressed.

**External evidence.**
- [Borri, Liu, Tsyvinski & Wu, "Cryptocurrency as an Investable Asset Class: Coming of Age" (arXiv 2510.14435, 2025)](https://arxiv.org/html/2510.14435v2): crypto carry (long spot + short perp, funding-driven) annualized Sharpe **6.45 over 2020–2025 full sample, fell to 4.06 from 2024, turned NEGATIVE in 2025**; funding mean ~8%/yr at 0.8% vol in-sample. Top-tier author group. This is double-edged and must be stated plainly: it strengthens the case for selectivity AND is the most credible external evidence yet that the F1 family's tailwind weakened in 2025 — worth surfacing to the owner regardless of this candidate's fate.
- [MDPI, "The Two-Tiered Structure of Cryptocurrency Funding Rate Markets"](https://www.mdpi.com/2227-7390/14/2/346) — persistent structural funding differences (abstract-grade; full text 403 since 07-08).
- Practitioner-grade persistence notes only (e.g., [MetaMask education](https://metamask.io/news/monitoring-perps-funding-rate-trends-signals): assets show "consistently elevated funding over multiple days or weeks") — ⚠ no rigorous study of the SPECIFIC percentile-entry claim was found. This candidate's evidence is thin externally and rests on being cheaply, fully measurable locally — the same status settlement-window timing had before it screened NO_GO. That precedent is a live warning, not a footnote.

**Data to test it.** Entirely local, zero acquisition cost: `data/funding_history` (492 venue-symbol CSVs, multi-year, current to today) for percentile/persistence construction; F1 paper-soak logs (warehouse + carry runner gate pass/fail logging since Rev5) as the incumbent baseline. **The null hypothesis is the CURRENT gate, not zero:** `f1_entry_gate` already requires funding>0 now, 7d-average>0, trailing-settlement mean, contango, and net edge ≥ cost-multiple — the screen must show percentile+persistence beats that incumbent after costs, or the verdict is NO_GO.

**Costs / feasibility @$420.** Best of this batch: no new positions, no new fee legs, no new margin — a selection-rule change on the only validated, already-capitalized lane. Failure mode is benign (F1 unchanged). Fewer/longer episodes would also cut round-trip churn, the dominant F1 cost.

**Novelty-vs-ledger: ADJACENT** — the ledger's Validated section explicitly makes carry extensions screen-eligible. Distinct from all three refuted carry-adjacent rows: settlement-window timing (intra-window offsets — this is cross-settlement selection), dispersion hold-until-flip (venue-pair spread — this is single-venue-pair F1 as-is), quarterly basis leg-swap (instrument choice — this keeps the perp). Not directional funding (delta-neutral; funding is the harvest, not a price signal).

---

## Candidate 3 — Korean cross-listing lag (Upbit/Bithumb announcement pump on our venues) — forward measurement ONLY

**What it is.** When Upbit/Bithumb announce a listing of a token that ALREADY trades as a USDT perp on Binance/Bybit/Bitget, the token pumps on our venues within minutes. Proposed strictly as a zero-cost forward measurement: log Korean listing notices with timestamps and measure our-venue drift at announcement +5m/+30m/+2h/+24h — to establish whether ANY exploitable drift (long continuation or fade-short reversal) survives our latency. No trading candidate until that dataset exists.

**Mechanism.** A Korean listing opens a large, structurally segmented retail demand pool (KRW pairs, historical kimchi premium, no shorting on Upbit) to the token — a genuine access/demand shock against a public announcement, not a chart shape. Arbitrageurs transmit the Korean bid onto global venues immediately.

**External evidence.** Measured magnitudes, weak timing granularity: tokens surge 30–100% on international exchanges "within minutes" of an Upbit notice ([CCN analysis](https://www.ccn.com/analysis/crypto/upbit-listing-pump/)); vendor measurement: Upbit listings max expected return 51.9%, Bithumb 108.9%, strongest >800% intraday, with sub-second announcement feeds marketed as necessary ([DataMaxi+, Sep 2025](https://medium.com/@datamaxiplus/the-rise-of-korean-exchange-listing-alpha-capturing-upbit-and-bithumb-listing-pumps-9c9055388f15) — ⚠ vendor selling the feed, single source for the specific numbers); 2026 event examples: PLUME +50% ([CCN](https://www.ccn.com/analysis/crypto/plume-upbit-listing-price-surge/)), 9-altcoin wave ([Yahoo Finance](https://finance.yahoo.com/markets/crypto/articles/upbit-listing-announcement-triggers-price-033217292.html)). The post-pump "sharp reversal" is narrative-grade — no measured reversal study found.

**Data to test it.** Does not exist locally and CANNOT be reconstructed historically without announcement timestamps: harvest Upbit notices (public notice board, `upbit.com/service_center/notice`; scrapeable archive with timestamps) + Bithumb equivalents, join to `ohlcv_cache` 15m/1h bars. Historical notice timestamps are retrievable, so a retrospective drift-at-lag measurement IS possible before committing to forward collection — that retrospective join is the Phase-2 ask.

**Costs / feasibility @$420: the long side is latency-infeasible as-is.** 30-min news scan + 5-min decision cycle + 10s polling against a move that completes in minutes means we would systematically buy the top. Only two honest outcomes: (a) measurement shows residual drift at +30m or later → a candidate exists; (b) it doesn't → close the question for good. Either way the measurement is nearly free.

**Novelty-vs-ledger: NEW** (not on the ledger; distinct from the in-shadow Binance-perp listing-short probe: different venue class, different direction, different window).

---

## Directions searched and NOT advanced (with reasons — do not re-search without new evidence)

- **Quarterly/expiry-day flow effects (CME or CEX dated futures):** searched; nothing measured beyond options "max-pain" narratives (e.g., [Cryptopotato](https://cryptopotato.com/massive-11b-end-of-quarter-options-expiry-could-rattle-crypto-markets-today/)-grade coverage of the Jun-2026 $9.3B expiry). No mechanism-grade, quantified drift study found → INSUFFICIENT DATA, no candidate. (Distinct from — and does not reopen — the refuted quarterly-basis leg-swap row.)
- **New-perp funding-cap-pinned carry (collect extreme negative funding on day-1 listings, delta-neutral):** mechanism is real (day-1 listings frequently pin deeply negative), but the collecting side requires LONG perp + SHORT spot, and spot borrow on day-1 listings is unavailable or extortionate at retail — the borrow leg kills it at $420. The perp-perp expression is cross-venue dispersion, whose per-settlement fat-tail failure mode is already a refuted-row finding (2026-07-09). The [BitMEX 2025Q3 derivatives report](https://www.bitmex.com/blog/2025q3-derivatives-report) (Oct 2025) documents arbitrage-enforced funding ceilings (BitMEX BTC pinned at exactly 0.01%/8h for 78% of the period) but provides no cap-episode persistence stats. No candidate.
- **ADL / insurance-fund mechanics:** active academic literature exists ([arXiv 2512.01112](https://arxiv.org/pdf/2512.01112), [arXiv 2602.15182](https://arxiv.org/pdf/2602.15182), [arXiv 2603.15963](https://arxiv.org/pdf/2603.15963) — ADL optimization/design) but it is exchange-design research, not a retail-harvestable edge; any tradable implication routes through the post-cascade family, which is already forward-collection-only (07-11 candidate 4). No new candidate.
- **Leverage-tier/margin-change announcements as a standalone signal:** no measured study found; folded into Candidate 1's harvest as a logged covariate.

## Ranking for edge-screener (Phase 2)

1. **Candidate 1 (cross-venue delisting-short)** — strongest measured event evidence of this pass (multiple independent 2025–26 waves, −15% to −34% announcement-day, peer-reviewed predictability support), proven capital-scaled probe template, clear harvest path. Gated on the announcement-calendar harvest; survivorship must be bounded, concurrent-MTM modeled from day one, entry measured at announcement+1 cycle.
2. **Candidate 2 (funding-percentile persistence selectivity for F1)** — best feasibility (zero new capital, refines the validated lane), fully local data, but thin external evidence for the specific claim and a freshly refuted sibling (settlement timing) as precedent; null hypothesis = incumbent gate. The Borri et al. carry-turned-negative-2025 finding should be surfaced to the owner independently of the screen result.
3. **Candidate 3 (Korean cross-listing lag)** — real, measured mechanism but latency-infeasible for the obvious trade; advance ONLY as the retrospective notice-timestamp join / forward drift measurement, which is nearly free and closes the question either way.

## Source list

1. Yahoo Finance — 4 Binance delisting targets tumble (announcement-day drops) — https://finance.yahoo.com/markets/crypto/articles/4-binance-delisting-targets-tumble-153227077.html
2. DailyCoin — Binance Apr-1 delistings hammer 8 altcoins in minutes — https://dailycoin.com/binances-april-1-delistings-hammer-8-altcoins-in-minutes/
3. BeInCrypto — 5 altcoins double-digit losses after Binance delisting call (May 2026) — https://beincrypto.com/binance-delist-5-altcoins-may-2026/
4. CoinGape — Binance delisting warning, NFP/POND ~−20% — https://coingape.com/binance-warns-of-delisting-these-tokens-price-drop-ahead/
5. Volity — delisting education, "−80% within 30 days" (⚠ vendor, single source) — https://volity.io/crypto/delisting/
6. Yang 2026, AJFS — predictive/explainable models for Binance delistings (⚠ paywalled; abstract-grade) — https://onlinelibrary.wiley.com/doi/10.1111/ajfs.70045
7. Binance FAQ — futures delisting mechanics (reduce-only, 30-min settlement average, taker fee) — https://www.binance.com/en/support/faq/delisting-of-futures-contracts-dd60dfbf654d4055aa6b217ea6d5ddba
8. IDEAS/RePEc — event study, negative crypto events CARs (−5,+10d) (2022; family support) — https://ideas.repec.org/a/ahs/journl/v7y2022i1p16-31.html
9. Borri, Liu, Tsyvinski, Wu (arXiv 2510.14435, 2025) — crypto carry Sharpe 6.45 (2020–25) → 4.06 (2024) → negative (2025) — https://arxiv.org/html/2510.14435v2
10. MDPI — two-tiered funding-rate market structure (⚠ abstract-grade, 403) — https://www.mdpi.com/2227-7390/14/2/346
11. MetaMask education — funding persistence practitioner note (weak) — https://metamask.io/news/monitoring-perps-funding-rate-trends-signals
12. CCN — dissecting Upbit listing pumps (30–100% within minutes) — https://www.ccn.com/analysis/crypto/upbit-listing-pump/
13. DataMaxi+ (Sep 2025) — Korean listing alpha, Upbit 51.9% / Bithumb 108.9% max expected (⚠ vendor selling feed, single source) — https://medium.com/@datamaxiplus/the-rise-of-korean-exchange-listing-alpha-capturing-upbit-and-bithumb-listing-pumps-9c9055388f15
14. CCN — PLUME +50% on Upbit listing — https://www.ccn.com/analysis/crypto/plume-upbit-listing-price-surge/ ; Yahoo — Upbit 9-altcoin wave — https://finance.yahoo.com/markets/crypto/articles/upbit-listing-announcement-triggers-price-033217292.html
15. BitMEX 2025Q3 derivatives report (Oct 2025) — funding floor/ceiling structure — https://www.bitmex.com/blog/2025q3-derivatives-report
16. ADL design literature (not advanced): arXiv 2512.01112, 2602.15182, 2603.15963
17. Cryptopotato — $11B quarter-end expiry coverage (narrative-grade; direction not advanced) — https://cryptopotato.com/massive-11b-end-of-quarter-options-expiry-could-rattle-crypto-markets-today/

*Coverage note: web research ran normally (not LIMITED). Wiley AJFS and MDPI full texts paywalled (402/403) — findings from those flagged abstract-grade. The "104 announcements / ~2–3-day notice" statistic is search-snippet-grade and must be re-established from the harvested announcement archive itself in Phase 2, not quoted forward.*
