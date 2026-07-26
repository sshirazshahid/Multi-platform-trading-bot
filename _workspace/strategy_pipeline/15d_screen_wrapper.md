# 15d — Screen: Staked-ETH Wrapper Discount (wBETH / stETH vs ETH)

Edge-screener Phase 2, strategy-evidence-pipeline run 2026-07-16.
Candidate: scout B candidate 3 (`14_scout_b_spot_2026-07-16.md`) — NEW vs ledger.
Gated descriptive pass with a pre-registered STOP RULE. Screen code: `research/screen_wrapper_discount.py` (new, only file touched).

---

## PRE-REGISTRATION (written 2026-07-16, BEFORE any screen code ran)

### Hypothesis
The market ratio wrapper/ETH (wrapper = wBETH or stETH, all legs CEX spot closes) trades at
occasional discounts to its slow-accruing fair trend because sellers who cannot wait out the
2–5 day unstaking queue pay for immediacy. If the discount at entry exceeds the full 4-leg
round-trip cost (spot buy wrapper + spot sell wrapper at convergence + ETH-perp hedge
open/close + funding while hedged), buying the wrapper and hedging with an equal-notional
ETH-perp short captures the convergence.

### Universe
- `WBETH/USDT` spot 1h closes (Binance listing) — `data/ohlcv_cache/WBETH-USDT_1h.parquet`
- `STETH/USDT` spot 1h closes (Bybit/Bitget listing; Binance does not list STETH spot — venue noted, fee floor identical across venues per `config.FEE`) — `data/ohlcv_cache/STETH-USDT_1h.parquet`
- Denominator: Binance ETH/USDT **spot** 1h closes — `data/ohlcv_cache/ETH-USDT_qtrspot_1h.parquet` (provenance verified: `api/v3/klines` spot, per `scripts/backfill_quarterly_basis.py`)
- Hedge-leg funding: `data/funding_history/binance_ETH.csv` (realized 8h rates, covers window)

### Sample period
2025-05-31 20:00 → 2026-05-31 21:00 UTC — the full local wrapper coverage (n≈8,762 hourly
bars each, inner-joined on `ts`; `ts` = epoch-seconds, bar open). HONEST CAVEAT: cache is
~6.5 weeks stale as of run date. Top-up command if Stage 2 were ever to run:
`venv\Scripts\python.exe scripts\backfill_universe_ohlcv.py` (or the equivalent keyless ccxt
spot fetch for WBETH/STETH). Staleness does not block a Stage-1 distribution estimate on a
full year of data.

### Deviation measurement (both estimators pre-registered; no threshold optimization)
ratio_t = wrapper_close_t / eth_spot_close_t, deviation in log space.
- **Estimator A (primary, descriptive):** OLS linear fit of log(ratio) on time over the full
  sample; deviation_t = residual_t. Captures the wrapper accrual drift (~+3%/yr class for
  wBETH) as pre-registered by the scout (official conversion-rate series is not local; the
  slow-trend fit is the stated proxy).
- **Estimator B (sensitivity, causal):** trailing 720h (30d) rolling median of log(ratio),
  drift-corrected by adding (fitted annual drift × 15d); deviation_t = log(ratio_t) − corrected median.
- Discount side only: bars with deviation < 0; magnitude in bps = −10,000 × deviation.

### Cost model (charged in full, worst-case taker; `config.FEE` authoritative)
| Leg | Fee | Slippage (repo convention) | Total |
|---|---|---|---|
| Spot buy wrapper | 10.0 bps | 5.0 bps | 15.0 bps |
| Spot sell wrapper | 10.0 bps | 5.0 bps | 15.0 bps |
| ETH perp short open | 5.0 bps | 5.0 bps | 10.0 bps |
| ETH perp close | 5.0 bps | 5.0 bps | 10.0 bps |
| **Fixed subtotal** | | | **50.0 bps** |

- **Funding while hedged:** the short hedge RECEIVES positive funding / PAYS negative funding.
  Credit computed from REALIZED binance ETH 8h funding over the sample window:
  `credit_bps(D) = 10,000 × mean_8h_funding × D/8` for a hold of D hours, where D_ref = median
  duration of ≥20bps-deep discount episodes (fallback 24h if none exist).
- **Floor variants:**
  - `floor_nofunding` = 50.0 bps (zero funding credit assumed)
  - `floor_funding_adj` = 50.0 bps − max(0, credit_bps(D_ref)) — funding credit counted only
    if realized-positive (a realized-negative mean funding makes the floor WORSE, and is then added)
  - `floor_min` = min of the two = the most strategy-favorable floor. **The stop rule uses `floor_min`.**
- **Unmodeled and stated:** wrapper-book spread beyond the 5bps slippage convention (WBETH/STETH
  books are thinner than ETH/USDT) and touch≠fill. Both push the TRUE floor HIGHER, so a
  stop-rule fail against `floor_min` is robust.

### STOP RULE (pre-registered; scout's own recommendation, honored)
Let `p95_disc(wrapper)` = max over estimators {A, B} of the 95th percentile of discount
magnitude (bps, discount-side bars only) — deliberately the most strategy-favorable reading,
and itself a GENEROUS upper bound on gross capture (assumes entry at p95 depth, exit at zero
residual, no adverse tracking between legs).
- If `p95_disc < floor_min` for a wrapper → that wrapper is dead on expectancy.
- If BOTH wrappers are dead → **verdict = NO_GO on expectancy grounds; DO NOT build Stage-2 backtest.**
- If either wrapper survives → Stage 2: pre-register entry/exit/timeout + run the frozen gates
  (DSR≥0.10, PBO≤0.5, OOS-WR≥0.55, MC P(total>0)≥0.95, maxDD p95≤0.25), with the mixed
  spot+futures expression flagged and leg minimums at $420 checked FIRST
  (wBETH spot min ≈10 USDT; Binance ETH-perp min-notional 20 USDT class — verify live before any sizing claim).

### What NO_GO looks like (pre-registered)
Stage-1 stop rule fires on both wrappers, OR Stage 2 fails any frozen gate. NO_GO gets a
`refuted-families-ledger` row (staked-asset wrapper discount, CEX spot expression).

### Multiplicity / variants (TRUE count)
2 wrappers × 2 deviation estimators = 4 descriptive cells. Episode thresholds
{10, 20, 30, 40, 50, 75, 100 bps} are DESCRIPTIVE bins, not selectable entry parameters.
No parameter search of any kind occurs in Stage 1. No variants were abandoned before this file
was written.

### Verdict semantics
GO | NO_GO | INSUFFICIENT_DATA per `after-cost-screening` SKILL.md. Data verified present
(coverage table above) → INSUFFICIENT_DATA is not expected to fire.

---

## RESULTS (filled in AFTER the pre-registration above; script + selftest first)

Run 2026-07-16. Script: `research/screen_wrapper_discount.py` (selftest: 7 groups PASS —
the selftest caught and fixed one real bug pre-run: trailing-median drift-lag correction
uses (W−1)/2 bars, not W/2). JSON verdict: `_workspace/strategy_pipeline/15d_screen_wrapper.json`.
Deviation note: TDD checks live behind `--selftest` inside the script instead of `tests/`,
per the orchestrator's "new code in this one file only" restriction.

### Stage reached: STAGE 1 ONLY — the pre-registered stop rule fired on BOTH wrappers. No backtest was built.

| Wrapper | n (matched 1h bars) | best estimator | p50 | p90 | **p95** | p99 | max | floor_min | margin at p95 |
|---|---|---|---|---|---|---|---|---|---|
| WBETH | 8,762 | A (OLS) | 5.0 | 15.9 | **21.3 bps** | 61.7 | 1,760.6 | 49.96 bps | **−28.7 bps** |
| STETH | 8,762 | B (trail-median) | 5.3 | 20.8 | **27.9 bps** | 43.7 | 183.3 | 49.96 bps | **−22.1 bps** |

(All percentiles = discount-side magnitude, bps. Both estimators agreed within ~2 bps at p95
on both wrappers — the result is not estimator-sensitive.)

### Cost floor (realized)
- Fixed 4-leg worst-case taker: **50.0 bps** (2 × (10bps spot fee + 5bps slip) + 2 × (5bps perp fee + 5bps slip), `config.FEE` + repo slippage convention).
- Funding credit: realized mean 8h binance ETH funding in-window = +2.887e-05 (n=1,095
  settlements); at D_ref = 1h (median duration of ≥20bps episodes) the credit is **0.036 bps** — negligible. `floor_min` = 49.96 bps.
- Unmodeled wrapper-book spread and touch≠fill push the TRUE floor higher → the fail is robust.

### Episode structure (best estimator)
- ≥20bps: WBETH 95 episodes / STETH 116 — **median duration 1h** on both. These are transient
  hourly blips, not multi-day queue-priced discounts; there is no meaningful funding-credit
  horizon to harvest.
- ≥50bps (the shallowest depth that clears the floor): WBETH 12 episodes, STETH 9 — median
  duration 1–2h. Even entering at the EXACT episode-max depth (unobservable in real time),
  the after-cost margin at p99 depth is ~+12 bps (WBETH) / **negative** (STETH, p99 43.7 < 50).
- Tail exhibit: WBETH max dislocation 1,760 bps (one flash event, 1h-close basis; 3 episodes
  ever reached ≥100bps). Catching flash dislocations is a latency race this system loses at
  10s polling — not part of the pre-registered claim and not a basis for GO.

### Sanity checks that validate the measurement
- Recovered WBETH fair-trend drift: **+2.57%/yr** ≈ the expected ~3%/yr staking accrual;
  STETH drift +0.06%/yr ≈ flat, consistent with rebasing stETH. The fair-trend proxy behaved
  as pre-registered.
- Both estimators (full-sample OLS and causal trailing median) produce near-identical p95s —
  no estimator cherry-pick occurred (best-of-two was pre-registered as strategy-favorable).
- Static-level caveat (stated pre-run): both estimators absorb any PERSISTENT constant
  discount level. That is correct for the trade — a static offset is bought and sold at the
  same offset and is not capturable.
- Liquidity: WBETH median bar quote-volume 43.5k USDT overall vs 206k during ≥20bps episodes
  (dislocations coincide with heavy flow); STETH ~120k vs ~112k. Volume presence is NOT a
  fill/spread claim — book depth is unmeasurable from 1h OHLCV, stated.

### VERDICT: **NO_GO** (expectancy grounds, pre-registered stop rule)
p95 discount magnitude (21.3 / 27.9 bps, most-favorable estimator) sits at roughly HALF the
most-strategy-favorable cost floor (49.96 bps) on both wrappers. The gross-capture upper
bound at p95 does not clear costs even before unmodeled wrapper spread. The scout's own
prior ("normal-regime deviations do not clear four legs; likely NO_GO, cheaply provable")
is confirmed. Frozen Stage-2 gates: not reached — nothing was loosened, nothing was fitted.

- Multiplicity honesty: 4 descriptive cells (2 wrappers × 2 estimators), zero parameter
  search, zero abandoned variants beyond what the pre-registration lists.
- Ledger: NO_GO row for "staked-asset wrapper discount (CEX spot + perp hedge)" is due in
  `refuted-families-ledger` PENDING honesty-auditor confirmation (per pipeline protocol,
  verdicts go to the auditor before being treated as results).
- Data caveats carried: window is 2025-05-31→2026-05-31 (cache ~6.5 weeks stale; a top-up
  cannot plausibly move p95 by 2×); STETH venue is Bybit/Bitget-listed (fee floor identical);
  1h closes understate intra-hour extremes, but intra-hour wicks are not reliably fillable at
  10s polling — the 1h-close basis is the honest fill-realism choice for this account.
