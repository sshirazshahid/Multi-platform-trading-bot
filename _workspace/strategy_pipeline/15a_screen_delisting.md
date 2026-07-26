# 15a — Edge-Screener: DELISTING FORCED-FLOW (2026-07-16)

Phase-2 screen, strategy-evidence-pipeline run 2026-07-16. Merged candidate: scout A candidate 1
(capital-scaled perp short on delisting announcement) + scout B candidate 2b (spot bounce on
surviving venues). Screen script: `research/screen_delisting_flow.py`; tests:
`tests/test_screen_delisting_flow.py`. Protocol: `.claude/skills/after-cost-screening/SKILL.md`.

---

## PRE-REGISTRATION (written 2026-07-16, BEFORE any screen code ran)

Everything in this section was frozen before the harvest and screen executed. Results appear
only in the RESULTS section appended after the run. Moving any threshold below after seeing
results invalidates the screen.

### Event definition and independence

- **Event source:** Binance announcement archive, delisting category (catalogId=161,
  `https://www.binance.com/bapi/apex/v1/public/apex/cms/article/list/query`), verified live
  today (413 articles, depth to 2022).
- **Qualifying event:** article whose title matches `^Binance Will Delist <TICKERS> on
  YYYY-MM-DD` — the full spot-delisting wave (the event class behind every measured crash in
  the scout brief). Announcement timestamp = `releaseDate` (ms, UTC).
- **Excluded-and-counted title classes (never traded, never silently dropped):**
  "Notice of Removal of Spot/Margin/Cross-Margin Trading Pairs" (pair removal ≠ full delist),
  "Binance Alpha Will Remove …", "Binance Futures Will Delist …" (perp-only delist; counted as
  covariate), "Monitoring Tag" articles, all other titles.
- **Independence rule:** one announcement = ONE event, regardless of how many tokens it names
  (batch waves are one market event — the unlock-short pseudo-replication lesson). ALL gate
  statistics run on per-event observations: the equal-weighted mean of token-level net returns
  within the event. Token-level counts are reported as diagnostics only, never gated on.
- **Sample period:** announcements with releaseDate in [2023-01-01, 2026-07-01) UTC.
  **AMENDMENT A1 (2026-07-16, PRE-RESULTS):** extended to **[2022-03-01, 2026-07-01)**.
  Basis: after harvesting ONLY the announcement archive (no price/funding data harvested,
  zero returns computed), the qualifying-event count was 28 — below the frozen MC floor of
  30, which would have foreordained INSUFFICIENT_DATA. The archive is complete from
  2022-02-17 and contains 6 additional parseable events (2022-03-01 … 2022-12-15). Window
  extension is a power-increasing, outcome-blind amendment; every threshold, cost, entry/exit
  rule, and gate is unchanged. Flagged for the honesty-auditor.
- **Event-dedup rule (declared pre-run):** a spot-delist announcement whose bases are all
  contained in announcements of the prior 21 days is the SAME market event (re-announcement)
  and is dropped-and-counted.
- **Removal time convention:** removal date at 03:00 UTC (Binance's standard delisting hour).

### Sub-claim 2a — capital-scaled perp short (announcement → pre-cutoff)

- **Hypothesis:** after a Binance full spot-delisting announcement, the named tokens' Binance
  USDT perps drift further down from the first full post-announcement 1h bar close into the
  pre-removal cutoff, net of ALL costs (taker fees both legs, slippage, realized funding
  charged to the short), sized capital-scaled 3%/12% unlevered.
- **Entry:** close of the first 1h bar with bar-open ts ≥ announcement ts (effective fill
  1–2h after the announcement — strictly LATER than the live bot's 5-min cycle, so the screen
  understates rather than overstates capturable edge; intra-hour timing is unmeasurable at 1h
  bars and is flagged for the auditor).
- **Exit variants (3, pre-registered):**
  - **E1 (primary, = the registered hypothesis):** min(spot_removal_ts − 24h,
    perp_data_end − 24h) — out before the reduce-only/settlement window on either leg.
  - **E2:** entry + 3d; **E3:** entry + 7d. E2/E3 require full perp-data coverage of the
    window (exit bar within 12h of target) else exclude-and-count. KNOWN BIAS, declared now:
    tokens whose perp died early are excluded from E2/E3 — precisely the most doomed names —
    so E2/E3 are robustness arms only; E1 is the verdict-bearing arm.
- **Price source:** data.binance.vision monthly 1h kline dumps, futures/um (verified today to
  retain delisted symbols, e.g. NFPUSDT, SYSUSDT → 200). Tokens with no Binance USDT perp at
  announcement → excluded-and-counted (`no_perp`).
- **Funding (the pre-declared killer cost):** realized funding summed over [entry, exit),
  short RECEIVES positive / PAYS negative. Source: data.binance.vision
  futures/um/monthly/fundingRate dumps (retained for delisted symbols; verified 200);
  cross-checked against `data/funding_history/binance_{BASE}.csv` where both exist. Windows
  not fully bracketed by funding data → excluded-and-counted, never guessed.
- **Costs:** `config.FEE["futures_taker"]` = 5 bps/side + `config.SLIPPAGE` 5 bps/side
  (pct_open/pct_close) → 20 bps round trip + realized funding.
- **Sizing:** 3% of account equity per token short, 12% total exposure cap → max 4 concurrent
  positions, chronological acceptance; ties inside one batch broken alphabetically
  (deterministic, return-blind). CLAUDE.md §2 compliant, unlevered.

### Sub-claim 2b — spot bounce on surviving venue (long)

- **Hypothesis:** after the announcement crash, buying the token on a venue that keeps the
  listing earns a positive after-cost bounce as panic flow is absorbed.
- **Survivor venue:** **Bitget spot** via `/api/v2/spot/market/history-candles` — chosen
  because it was verified today to serve historical candles even for symbols Bitget itself
  later delisted (PONDUSDT → data), which BOUNDS SURVIVORSHIP; Bybit's kline API rejects
  delisted symbols (BAKE/HIFI/POND all `Not supported`) and would silently censor the sample.
  Tokens never listed on Bitget spot → excluded-and-counted.
- **Entry:** same rule as 2a on Bitget spot 1h bars; entry bar must have volume > 0.
- **Exit variants (3):** entry + 1d / + 3d / + 7d; exit bar within 12h of target and
  volume > 0; ≥50% of hold-window bars must print volume > 0 (dead-book filter,
  excluded-and-counted).
- **Costs:** `config.FEE["bitget_spot_taker"]` = 10 bps/side + slippage 5 bps/side +
  **pre-registered survivor-book half-spread haircut 25 bps/side** (no historical book data
  exists; documented 4–10% post-delisting spreads say 25 bps is generous TO the strategy) →
  80 bps round trip. Sensitivity at 0 and 100 bps half-spread reported, NON-gating; the gate
  runs on the 25 bps base case only.
- **Sizing:** same capital-scaled 3%/12% template (long side).

### Controls

Per-event control return = equal-weighted BTC+ETH spot return over the same [entry, exit]
window (data.binance.vision spot 1h). 2a beats control iff mean(control − token) > 0 (the
short's edge is idiosyncratic, not market beta); 2b beats control iff mean(token − control) > 0.

### Frozen gates (never loosened; NaN fails closed)

Per variant, on the per-EVENT after-cost series:

1. after-cost mean > 0
2. WR ≥ 0.55 and walk-forward OOS-WR ≥ 0.55 (`core/walk_forward.WalkForward`, n_splits=4,
   embargo=1, time-purge of train events still open at test start — precedent implementation)
3. DSR ≥ 0.10 (`core/stat_tests.deflated_sharpe`, **n_trials = 6** = the TRUE variant count of
   this screen: 2 sub-claims × 3 exit variants; first registration of the delisting family)
4. PBO ≤ 0.5 (CSCV across the 3 exit-variant columns per sub-claim, common events)
5. Monte Carlo (`core/decision/monte_carlo.monte_carlo_trade_sequence`) on the capital-scaled
   per-event account-return sequence: P(total>0) ≥ 0.95, maxDD p95 ≤ 0.25, min 30 events
6. **Realized concurrent-MTM account maxDD ≤ 0.25**, computed on the hourly-marked account
   equity curve of all accepted positions simultaneously (the rev3-audit lesson: per-trade MC
   understates wave-overlap drawdown ~2×)
7. beats control

### Verdict definitions (frozen)

- **GO:** a variant passes ALL gates (per sub-claim).
- **NO_GO:** ≥1 variant evaluable (n_events ≥ 30) and no variant passes all gates.
- **INSUFFICIENT_DATA:** no variant reaches 30 covered events, or a harvest source is
  unreachable — named with the exact harvest command. Never synthetic data.
- A partial or errored run is never reported as a verdict.

### Multiplicity ledger (TRUE count of variants tried)

6 gated variants (2a×{E1,E2,E3}, 2b×{1d,3d,7d}). Cost sensitivities (2b spread 0/100 bps) are
reporting-only, not selection candidates. No other variants were tried before or during this
screen; if any get added, this count and the DSR change BEFORE results are read.

### Scope bounds (declared now)

- Cross-venue PERP short expression (short on a surviving venue's perp) is NOT screened this
  pass — Binance-perp is the primary 2a expression; a survivor-perp variant would need Bitget
  perp funding alignment and is recorded as a follow-up, not silently merged.
- Bybit's own delisting archive (444 articles, API verified) is harvested for coverage
  cross-check only; Bybit-announced events are NOT gated this pass.
- Sub-signals (monitoring tags, margin removals) are counted as covariates, never traded.

### Data/harvest plan

- Cache dir: `data/delisting_screen/` (gitignored data tree; PUBLIC repo — never committed).
- Steps: (1) page CMS archive → `announcements.json`; (2) parse qualifying events; (3) per
  token: vision um-futures 1h klines + fundingRate monthlies spanning announce−7d → exit+2d;
  Bitget spot history-candles for the same window; (4) vision spot 1h BTCUSDT/ETHUSDT
  2022-12→2026-07 as control; (5) run screen, emit JSON.

---

## RESULTS (run completed 2026-07-17 00:07 UTC; JSON: 15a_screen_delisting.json)

### Verdicts

| Sub-claim | Verdict | Binding reason |
|---|---|---|
| **2a perp short** (E1 pre-cutoff / E2 +3d / E3 +7d) | **INSUFFICIENT_DATA** | n_events = 17 / 25 / 18 — every variant below the frozen 30-event MC floor; gates not evaluable, fail closed |
| **2b spot bounce** (+1d / +3d / +7d, Bitget survivors) | **INSUFFICIENT_DATA** | n_events = 11 / 11 / 11 — below the floor; gates not evaluable, fail closed |

INSUFFICIENT_DATA is the formal pre-registered verdict. The point evidence, however, is
**affirmatively adverse for both legs** — see below. Neither leg should be re-proposed on
"more data" grounds without addressing the squeeze-tail finding.

### Universe and coverage (survivorship bounded, not hidden)

- Archive: 413 Binance delisting-category articles (Bybit archive: 444, cross-check only).
  Title classes: 233 pair-removal, 69 futures-only-delist, 43 margin, 8 alpha, 36 full
  spot-delist (2 unparseable — the 2022 options notices), 1 monitoring.
- **34 qualifying events, 138 token-legs**, 2022-03-01 → 2026-07-01 (Amendment A1 window).
- 2a exclusions: 77/138 legs had no Binance USDT perp; 63 variant-legs lacked full realized
  funding coverage (includes all July-2026 windows — vision publishes fundingRate monthly).
  Funding source: 120 variant-legs from data.binance.vision dumps, 0 needed the local CSVs.
- 2b exclusions: **112/138 legs not servable on Bitget spot** (mostly pre-2024 events —
  Bitget's spot archive thins out; its history-candles endpoint DOES serve later-delisted
  symbols, so this bound is venue-coverage, not delisting-censoring); 5 entry-bar zero-volume,
  2 exit-bar zero-volume.
- This is close to the structural ceiling: the entire announcement archive contains only ~34
  usable events; no harvest command can manufacture more history.

### 2a — capital-scaled perp short: after-cost numbers (per-EVENT, funding-charged)

| | E1 pre-cutoff | E2 +3d | E3 +7d |
|---|---|---|---|
| n_events (tokens) | 17 (29) | 25 (58) | 18 (33) |
| event mean | **−124.9%** | **−11.3%** | **−114.6%** |
| event median | −0.02% | +5.5% | +5.5% |
| WR / OOS-WR | 0.47 / 0.50 | 0.64 / 0.70 | 0.72 / 0.67 |
| DSR (n_trials=6) | ~0.0000 | 0.006 | ~0.0001 |
| MC P(total>0) / maxDD p95 | 0.31 / **1.34** | 0.60 / 0.26 | 0.31 / **1.32** |
| acct mean/event (3% stakes) | −3.45% | +0.09% | −2.95% |
| **realized concurrent-MTM acct maxDD** | **0.81** | 0.16 | 0.65 |
| beats control | NO | NO | NO |
| PBO (across exits) | 0.31 | 0.31 | 0.31 |

**The tail IS the story — verified against raw prices, not a parser artifact:**
- **ALPACA (announced 2025-04-24, removed 2025-05-02): entry 0.05296 → E1 exit 1.19 =
  +2,147% price move against the short, PLUS cumulative funding −0.962 (shorts paid ~96% of
  notional over the hold). One 3% unlevered stake = ~−67% of account equity.** This is the
  widely documented May-2025 ALPACA delisting squeeze, present in Binance's own kline dumps.
- HIFI (2025-09): +77% announcement→pre-removal. REN (2024-12): +21%. ANT (2024-02): +24%.
  UNFI +27% in 3 days, CHESS +25%, MLN +17%, RDNT +18% — squeezes into delisting are a
  recurring regime, not one anomaly.
- Funding confirmed as the pre-declared killer cost: negative on 21/29 E1 legs, mean −5.0%
  of notional per hold (E2: 35/58 negative, mean −2.2%).
- Read: the median delisting-short is roughly break-even to mildly positive (E2/E3 medians
  +5.5%, WR 0.64–0.72), but the family carries an unbounded-loss right tail (short squeeze on
  a dying book with punitive funding) that breaches the capital-preservation gates by 3–5×
  even at 3% unlevered stakes. Same failure class that killed full-stake listing-short —
  magnitude far worse (22× vs 2–3× per-stake tails). An 8% stop would have to fill INSIDE a
  coordinated squeeze on an illiquid, delisting-bound book to save this — not credible, and
  not part of the registered hypothesis.

### 2b — survivor-venue spot bounce: after-cost numbers (per-EVENT)

| | +1d | +3d | +7d |
|---|---|---|---|
| n_events (tokens) | 11 (21) | 11 (20) | 11 (20) |
| event mean | −2.7% | −9.3% | −17.1% |
| WR / OOS-WR | 0.36 / 0.50 | 0.18 / 0.25 | 0.09 / 0.13 |
| MC P(total>0) | 0.04 | 0.00 | 0.00 |
| beats control | NO | NO | NO |

- **Cost-free sensitivity kills it too:** at ZERO spread haircut and zero fees the token-level
  means are −1.5% (+1d), −7.9% (+3d), −19.7% (+7d). There is no cost model under which the
  covered sample bounces — the "overshoot" keeps falling through the removal window,
  monotonically worse with horizon. Direction consistent with 2a's median drift.
- n=11 is a permanent ceiling on our venues' history (Bybit's API censors delisted symbols
  entirely; Bitget only reaches ~2024). Formal verdict stays INSUFFICIENT_DATA, but every
  measurable variant is after-cost negative, control-losing, and 0/3 MC-positive.

### What would change the verdicts

- **2a:** nothing available today. Forward accrual (~8–12 events/yr) reaches the 30-event
  floor in ~1–2 years, but the observed squeeze tail already demonstrates a maxDD-gate breach
  at any stake sizing that preserves capital — a gate-passing future sample would need the
  squeeze regime (REN 2024-12, ALPACA 2025-05, HIFI 2025-09, CHESS/RDNT/MLN 2026) to be
  absent, which this sample contradicts. A stop-loss-overlay variant would be a NEW pre-registration and
  must model fill realism inside squeezes on dying books (touch ≠ fill).
- **2b:** only alternative-venue historical archives (MEXC/Gate) could extend n — outside the
  bot's three venues, so evidentially weak for our expression; forward accrual otherwise.
- Recommended ledger handling: no NO_GO row (verdicts are INSUFFICIENT_DATA), but the
  squeeze-tail finding should be recorded in the pipeline log so delisting-shorts are not
  re-proposed as "free money" — the honest summary is *"median mildly positive, tail fatal,
  funding punitive, capital gates unmeetable."*

### Multiplicity accounting (final)

6 gated variants as pre-registered (2a×3 exits, 2b×3 horizons), DSR n_trials=6. The two 2b
spread sensitivities were reporting-only and selected nothing. One pre-results amendment (A1,
window extension) — declared above before any price/funding data was touched.

### Run integrity

- Tests: 29/29 passing (`tests/test_screen_delisting_flow.py`).
- Screen exit code 0; harvest cache `data/delisting_screen/` (597 files, gitignored).
- One harvest-layer fix mid-run BEFORE any results existed: Bitget signals unknown symbols
  with HTTP 400 + body code 40034; the first run aborted on hard-error accounting. No
  threshold or rule was touched.
- Control basket = BTC+ETH Binance spot over each event window.
