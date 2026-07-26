# 15c — Edge Screen: Stablecoin Depeg Micro-Reversion (Scout B Candidate 1)

Phase-2 edge-screener, strategy-evidence-pipeline run 2026-07-16.
Candidate brief: `_workspace/strategy_pipeline/14_scout_b_spot_2026-07-16.md` (Candidate 1).
Screen code: `research/screen_stablecoin_depeg.py` (only new code file).

---

## PRE-REGISTRATION (frozen 2026-07-16 ~18:45 UTC, BEFORE any outcome computation ran)

### Hypothesis
Buying the discounted leg of a Binance stablecoin spot pair (USDC/USDT, FDUSD/USDT)
when a 1-minute close deviates >= theta bps below 1.0000, and exiting at peg
restoration (or a hard stop / timeout), earns after-cost positive expectancy at
retail that survives the frozen promotion gates — including the binding
negative-skew tail test (FDUSD 2025-04-02 $0.87-class events) against
MC maxDD p95 <= 0.25 at full-notional exposure.

### Universe and venue
- Binance spot only: `USDC/USDT`, `FDUSD/USDT`, pooled as ONE strategy (two sleeves).
- FDUSD/USDT is effectively Binance-only; USDC/USDT venue expansion is out of scope
  for this screen (only relevant if GO).

### Sample period and data
- 2024-07-16 00:00 UTC -> 2026-07-15 23:59 UTC (2 years). Deliberately extends the
  1-yr local 1h cache backwards so the 2025-04-02 FDUSD tail event ($0.87) is
  IN-SAMPLE for the tail test — the local 1h parquets (2025-05-31 -> 2026-05-31)
  exclude it and are too coarse for honest entry/exit timing.
- Data: 1m klines from the keyless public bucket `data.binance.vision`
  (monthly zips 2024-07..2026-06 + daily zips 2026-07-01..latest available),
  harvested by `research/screen_stablecoin_depeg.py --harvest` into
  `data/ohlcv_cache/{USDC,FDUSD}-USDT_1m.parquet` (ts = epoch seconds, repo convention;
  data/ is never committed — public repo rule).
- The 1h top-up is NOT needed: the 1m harvest supersedes the 1h grid entirely.
- Timestamp-unit note: Binance vision kline files switched units over time; the
  loader auto-detects s/ms/us by magnitude. Missing months/days are recorded, never
  synthesized.
- Egress check (2026-07-16 18:40 UTC): data.binance.vision and api.binance.com both
  reachable from this sandbox (HTTP 200 on a monthly zip HEAD; /api/v3/ping OK). No
  blocked fetches so far; any later block will be recorded.

### Signal and execution rules (frozen)
- `dev_bps(t) = (1.0000 - close_1m(t)) * 1e4` (discount only; premium side NOT traded).
- Pair starts ARMED. Entry: first ARMED bar with `dev_bps >= theta` -> BUY at the
  NEXT available 1m bar's OPEN (never the wick, never the trigger bar).
- Exit (first satisfied on 1m closes; fill at NEXT available bar open):
  1. REPEG: close >= 0.9998 (within 2 bps of peg)
  2. STOP: close <= entry_fill * 0.92 (charter 8% stop-loss guardian; fill at next
     bar open — realized loss CAN exceed 8% on gap bars; that is the honest model)
  3. TIMEOUT: 2880 bars (48h) after the entry-fill bar
- After any exit the pair is DISARMED; it re-ARMS only after a close >= 0.9998
  (episode de-duplication — one depeg episode = at most one trade per variant).
- Missing-bar handling: fills use the next available bar's open; if no bar exists
  within 60 minutes of a required fill, the event is EXCLUDED and counted.
- One position per pair; both pairs may hold simultaneously (overlap reported).

### Variants (TRUE multiplicity, frozen)
- theta in {10, 20, 30} bps -> `n_trials = 3` for DSR. Exit level (0.9998), stop
  (-8%), timeout (48h) are single fixed values — NOT searched.
- No other variants were tried before this pre-registration; none will be added
  after outcomes are seen. If any exploratory variant is ever run, it must be
  added to the true trial count.

### Cost model (BINDING)
- **Fees — ACTUAL, pulled live 2026-07-16 ~18:42 UTC** via authenticated Binance
  `sapi/v1/asset/tradeFee` (ccxt `fetch_trading_fee`) with this account's keys:
  - USDC/USDT: maker 0.0, taker **0.0**
  - FDUSD/USDT: maker 0.0, taker **0.0**
  - The Binance zero-fee promotion on these pairs is CONFIRMED REAL for this
    account today. BINDING fee = 0 bps/side. HONESTY CONDITION: promo fees are
    revocable; any GO verdict is explicitly conditional on a fee re-pull staying
    0 — at standard `config.FEE` spot taker (10 bps/side) the cost floor rises by
    20 bps round-trip, which is reported as a sensitivity row and would likely
    reverse a marginal GO. Historical fills inside the backtest window are charged
    at the binding 0-fee model; the standard-fee sensitivity bounds the risk that
    past promo windows differed.
- **Slippage — repo convention (binding)**: 5 bps entry + 5 bps exit (REPEG/TIMEOUT),
  10 bps on STOP exits. Non-binding sensitivity at 1 bp/side is reported because
  live tops-of-book measured today are 0.1 bp (USDC) / 1.0 bp (FDUSD) wide — the
  convention is conservative by ~5-50x for these books in calm regimes (stressed
  books are wider, which is when entries happen; hence the convention stays binding).
- **Funding**: n/a — pure spot, unlevered, no shorting.
- Round-trip binding cost floor: 10 bps (zero fee + 2x5 bps slip); 15 bps on stopped
  trades; 30 bps at the standard-fee sensitivity.

### Sizing for the MC / tail gate (binding)
- FULL NOTIONAL per event (weight 1.0 of strategy capital), unlevered — the scout's
  binding-test requirement: a strategy that wins 99% of the time and dies in the
  tail must show that death here. Cross-pair simultaneous events are reported as a
  caveat (full-notional-each overstates deployable exposure; conservative for tail).
- The equity curve compounds per-event returns in entry-time order.

### Frozen gates (never loosened; NaN fails closed)
Per theta variant, ALL must pass for GO:
- n >= 30 resolved events (MIN_N = MC_MIN_TRADES = 30); variants below are not
  evaluable; if NO variant reaches n >= 30 -> verdict INSUFFICIENT_DATA.
- mean net return > 0 (after binding costs)
- WR >= 0.55 and OOS-WR >= 0.55 (`core/walk_forward.WalkForward`, n_splits=4,
  embargo=1, exit-overlap purge — same audited helper as the listing/unlock screens)
- DSR >= 0.10 (`core/stat_tests.deflated_sharpe`, n_trials=3, sr_var=1/max(2,n))
- PBO <= 0.5 (`core/stat_tests.pbo` CSCV on a weekly-bucket matrix: rows = ISO weeks
  of the sample, cols = 3 theta variants, cell = sum of that variant's net event
  returns in that week, 0 when none; n_partitions=16)
- AUC >= 0.60 of the frozen pre-outcome score `tanh(dev_trigger_bps / (2*theta))`
  against win/loss (Mann-Whitney; required so a GO is probe-integrable)
- MC block bootstrap (`core/decision/monte_carlo.monte_carlo_trade_sequence`):
  P(total > 0) >= 0.95 AND maxDD p95 <= 0.25 at full notional.

### What NO_GO looks like (declared in advance)
- No theta variant clears the full battery. Expected failure modes:
  (a) expectancy below the 10 bps round-trip slippage floor (theta=10 gross capture
      ~8 bps < 10 bps floor — it exists mainly as the multiplicity-honest low anchor);
  (b) MC maxDD p95 > 0.25 driven by stop-outs in Apr-2025-class tails at full
      notional — the binding negative-skew test;
  (c) OOS instability: events clustered in one or two regime episodes so
      walk-forward OOS folds are empty or WR collapses;
  (d) reversion slower than the 48h timeout for a material event fraction.
- Frequency-gate prior: few events ~= luck. n per variant is reported with an
  independent-episode count (cross-pair events overlapping within +-24h counted as
  ONE market event in the caveat line). A pass on n but with <10 independent
  market episodes is flagged for the auditor even if gates pass.
- INSUFFICIENT_DATA looks like: harvest gaps (recorded per missing month) or all
  variants n < 30. Exact re-harvest command will be named.

### Multiplicity ledger (true count)
3 variants (theta grid) x 1 exit spec x 1 stop x 1 timeout = 3 trials. Nothing else
tried for this candidate before this document was frozen. Prior pipeline screens are
separate candidates with their own registered counts.

---

*Results section appended below only AFTER the above was frozen. The screen script's
event engine is validated by a `--selftest` mode on synthetic bars (code correctness
only — synthetic data is never used for verdict evidence).*

---

## RESULTS (run 2026-07-16 ~23:55 local; JSON: `15c_screen_depeg.json`)

### Execution record
- Selftest: 7/7 synthetic-bar cases pass (entry/exit fills, cost math, stop slip,
  timeout, de-dup, gap exclusion, unresolved-at-end, premium-side no-trade).
- Harvest: data.binance.vision, 24 monthly + 15 daily zips per pair, **zero missing
  files, zero blocked fetches**. 1,051,200 in-window 1m bars per pair,
  bar_completeness = 1.0000 (no gaps). Window includes the FDUSD 2025-04-02 tail
  (in-window min 1m close **0.8806**); USDC in-window min close 0.9947.
- No exploratory variants were run before or after; true trial count stayed 3.

### VERDICT: **NO_GO**

| gate (binding costs: 0 fee + 5bps/side slip) | theta=10 (n=75) | theta=20 (n=32) | theta=30 (n=11) |
|---|---|---|---|
| evaluable (n>=30) | yes | yes | **no** (n<30) |
| mean net / event | **-8.3 bps** | **-29.0 bps** | (-64.2 bps) |
| win rate | **0.067** | **0.250** | (0.636) |
| OOS-WR (walk-forward) | 0.067 | 0.208 | (0.625) |
| DSR | ~0 | ~0 | (0.004) |
| PBO (weekly CSCV, 3 cols) | 0.0 PASS | 0.0 PASS | — |
| AUC frozen score | 0.986 PASS | 0.740 PASS | (0.607) |
| MC P(total>0) | 0.000 | 0.012 | (0.258) |
| MC maxDD p95 (full notional) | 0.072 PASS | **0.257 FAIL** | (0.161) |
| exit mix REPEG/STOP/TIMEOUT | 30/0/45 | 6/1/25 | 3/1/7 |

### Why it fails (mechanism-level, not cost-artifact)
1. **The re-peg does not arrive in time.** TIMEOUT dominates every variant
   (60-78% of exits; median hold = 2881 min = the full 48h). FDUSD in particular
   trades at a *persistent* discount (it sits at ~0.9978 today, still disarmed at
   sample end) — so "buy the discount, sell at re-peg" degrades into "buy the
   discount, wait 48h, sell at a similar-or-worse discount, pay the round trip."
   The scout's mechanism (immediacy premium harvested at restoration) assumes
   restoration is fast; on 1m Binance data 2024-26 it usually is not, at these
   thresholds, within 48h.
2. **The negative-skew tail is real and binding, exactly as pre-registered.** The
   Apr-2025 FDUSD event produced a stopped trade realizing **-8.27%** (worse than
   the -8% stop — gap-through, honestly modeled at next-bar open). At theta=20 that
   single class of event pushes MC maxDD p95 to 0.257 > 0.25: the scout's "wins
   often, dies in the tail" test fires.
3. **Not a fee artifact and not a slippage artifact.** Fee truth: the Binance
   zero-fee promo is REAL (live-pulled 0.0/0.0 on both pairs) and the binding model
   already uses it. Even at the most generous non-binding sensitivity (zero fee +
   1 bp/side slip), mean net stays NEGATIVE at every theta (-0.3 / -20.9 / -56.0
   bps), though WR turns cosmetically positive (0.57/0.63/0.82) — pennies in front
   of the tail steamroller. At standard 10 bps taker (promo ends) everything is
   deeply negative. There is no cost model under which the pre-registered strategy
   clears the gates.

### Caveats / auditor notes
- theta=30 is NOT evaluable (n=11 < 30); its numbers are shown in parentheses for
  context only — they are also negative, so no INSUFFICIENT_DATA claim hides a
  possibly-good deep-threshold variant: the two deep STOP/TIMEOUT tails hit it too.
- Cross-pair overlap is small (4/3/0 events); independent 24h market episodes:
  65 / 29 / 11 — n is genuine at theta=10/20, so NO_GO rests on adequate frequency.
- The high AUC of the frozen score (deeper trigger overshoot discriminates outcome)
  is stranded alpha-shaped noise on a negative-mean base; it does not rescue a gate.
- FDUSD carries 56/75 (theta=10) of events — the verdict is FDUSD-dominated;
  USDC-only has too few events to evaluate separately (19/5/1), which is itself
  informative: USDC barely depegs on Binance at these thresholds.
- Ledger row (`refuted-families-ledger`: stablecoin-peg mean-reversion, long-discount
  spot expression, thresholds 10-30 bps / 48h) is PENDING honesty-auditor
  confirmation per pipeline protocol — screener does not self-certify.

### Artifacts
- Screen code: `research/screen_stablecoin_depeg.py` (only new code file; selftest inside)
- Data: `data/ohlcv_cache/USDC-USDT_1m.parquet`, `data/ohlcv_cache/FDUSD-USDT_1m.parquet`
  (2024-07-01 -> 2026-07-15; data/ is never committed — public repo)
- JSON verdict: `_workspace/strategy_pipeline/15c_screen_depeg.json`
