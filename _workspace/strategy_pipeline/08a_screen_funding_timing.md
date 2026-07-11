# 08a — Screen: Funding-Settlement-Window Timing (F1 refinement)

Screener: edge-screener | Phase 2 of strategy-evidence-pipeline | 2026-07-11
Candidate 1 of 07_scout_candidates_2026-07-11.md. Research paths only; no live code touched.

---

## PRE-REGISTRATION (frozen 2026-07-11, written BEFORE any result computation — never edited after)

### Hypothesis
Conditional on F1's entry gate otherwise passing, shifting the delta-neutral carry entry to a
tighter pre-settlement offset (final 15m bar before the funding settlement) — or to immediately
post-settlement — changes realized net carry per episode versus F1's CURRENT entry-timing
baseline. The comparison baseline is current F1 behavior, NOT zero.

### The honest baseline (measured from code, not assumed)
F1 is ALREADY settlement-window timed. The Rev-5 open gate only passes when
`time_to_next_funding_min ∈ [20, 180]` (`F1_MIN_TIME_TO_FUNDING_MIN = 20.0`,
`F1_MAX_TIME_TO_FUNDING_MIN = 180.0`, `research/funding_carry_lab.py:534-535`), plus
current funding > 0, 7d-avg funding > 0, contango (perp mark ≥ spot mid), leg spreads ≤ 5 bps,
edge ≥ max(15 bps, 3× round-trip cost) over `hold_settlements = 21`
(`core/carry_runner.py:47`). The runner passes every ~15 min, so entry time within the
[20,180]-min window is approximately uniform. **Baseline entry = the −105 min offset**
(15m-grid point nearest the window midpoint), holding all other gate conditions identical.

### Variant grid (frozen; ALL variants counted for DSR/PBO)
Entry offsets relative to settlement timestamp, at 15m bar closes, identical exit
(hold through the same `hold_settlements = 21` horizon, same exit rule as baseline):

| Variant | Entry offset | What it tests |
|---|---|---|
| V1 | −15 min | "enter 5–15 min before settlement" (finest honest grid point; sub-15m offsets are NOT measurable on local data and are NOT claimed) |
| V2 | −30 min | near-settlement entry |
| V3 | −60 min | early-window entry |
| V4 | +15 min after settlement | post-settlement entry (misses settlement s0, avoids any pre-settlement basis richening) |
| B (baseline) | −105 min | current F1 behavior (mid-window of [20,180]) |

n_trials for the Deflated Sharpe = 4 (V1–V4), plus any later-abandoned variants if added.

### Primary metric (per settlement event i, per variant V)
Δᵢ(V) = [funding captured by V − funding captured by B]
      + [delta-neutral basis PnL over the differential holding window, signed for
         short-perp/long-spot: −(basis(t_V) − basis(t_B)) when funding > 0]
      − [cost delta]
in bps of notional. Cost delta = 0 for V1–V3 and B (identical legs, identical crossing count,
same registered cost model); V4 differs from B by the funding of the skipped settlement
(exactly known from funding history) plus the basis change across the settlement instant.

**Basis is required.** basis(t) = perp price(t) − spot price(t), from either
(preferred) Binance premium-index klines or perp 15m OHLCV minus local spot 15m OHLCV.
A perp-only or spot-only price series CANNOT stand in for basis in a delta-neutral position —
substituting one would fabricate the exact quantity under test. Refused.

### Event universe (F1-regime proxy, mirrors the live gate on historically available fields)
Settlement events on binance and bybit (bitget funding history starts 2026-06-05 — too short)
for the frozen 15-coin F1 universe (`F1_EXPANDED_UNIVERSE_2026_07_05`) where:
- funding rate at s0 > 0, AND
- trailing 7d mean funding > 0, AND
- expected edge over 21 settlements ≥ max(15 bps, 3× cost) using the trailing 7d mean
  (same `f1_net_expected_edge_bps` arithmetic as the live gate).
Fields not reconstructible historically (order-book depth, atomic fillability, liq buffer)
are noted as unmodeled screen-vs-live divergence, exactly as in prior F1-family screens.

### Cost model (identical to F1's registered model — not softened)
Round trip (taker, binance) = spot RT fee (2 × 10 bps) + perp RT fee (2 × 5 bps)
+ 4 crossings × 5 bps slippage = **50 bps** (`scripts/run_f1_carry_paper.py::carry_round_trip_cost_frac`,
`DEFAULT_SLIP_FRAC = 0.0005`, fees from `config.FEE` / `core.cost_model.round_trip_fee`).
Bybit/bitget from their own `config.FEE` keys. Funding settlement-aligned from
`data/funding_history/` realized prints — never averages.

### Frozen gates (never loosened)
- DSR ≥ 0.10 on the per-event Δ series of the best variant, penalized for n_trials = 4.
- PBO ≤ 0.5 across the variant returns matrix (CSCV, `core/stat_tests.pbo`).
- OOS-WR ≥ 0.55: share of walk-forward OOS events with Δ > 0 (`core/walk_forward.py`,
  TimeSeriesSplit + embargo + purge).
- Monte Carlo (`core/decision/monte_carlo.py`): P(total > 0) ≥ 0.95 and maxDD p95 ≤ 0.25 on
  the timing-modified equity curve if the improvement is applied to F1's traded curve.
- NaN in any gate input fails closed.

### What NO_GO looks like (declared in advance)
No variant clears ALL gates against the baseline; or the sign of Δ is unstable across
walk-forward folds or across binance vs bybit; or the best variant's improvement is smaller
than the unmodeled fill-quality uncertainty near settlement (spreads widen into settlement;
the live gate's 5 bps leg-spread cap cannot be verified historically). A positive mean Δ that
fails any single frozen gate is NO_GO, not "promising".

### Required data (checked AFTER this section was written)
1. Settlement timestamps + realized rates — `data/funding_history/` (topped up 2026-07-11).
2. Spot 15m OHLCV for the F1 coins — `data/ohlcv_cache/<BASE>-USDT_15m.parquet`.
3. **Perp 15m OHLCV or premium-index (basis) history for the F1 coins** — the basis leg.
4. Overlap window across 1–3 long enough for walk-forward (pre-registered minimum:
   ≥ 120 qualifying settlement events per venue after the regime filter, ≥ 5 folds).

---

## DATA AVAILABILITY (verified 2026-07-11, local cache only — no new acquisition this pass)

| Input | Status | Detail |
|---|---|---|
| Funding history | ✅ | binance + bybit 2021-01-01 → 2026-07-11 for all 15 F1 coins (SUI from 2023-05-03 listing); bitget only 2026-06-05 → (excluded, pre-registered) |
| Spot 15m OHLCV | ⚠ stale + short | `BTC-USDT_15m.parquet` spans **2026-04-10 → 2026-06-14** only (~65 days, ends 4 weeks ago); same window for the other 34 15m files; ZEC has no 15m file (14/15 coins) |
| Perp 15m OHLCV (F1 coins) | ❌ ABSENT | `data/ohlcv_cache/` perp files (`-USDTUSDT_` suffix) exist ONLY for 14 commodity/equity bases (AAPL, CL, COIN, …) at 1h/4h/1d. Zero perp price files for any F1 coin at any timeframe. The 635 crypto parquets are SPOT (`scripts/backfill_universe_ohlcv.py` filters `m.get("spot")` — verified in source) |
| Premium-index / basis history | ❌ ABSENT | `data/derivs_history.jsonl` rows carry lsr/oi/funding only — no mark, no index, no price. No basis archive exists anywhere locally |
| F1 paper-lane dual-leg snapshots | ❌ insufficient n | `data/carry_positions.json` real spot+perp fills exist only since Rev-5 PAPER start (~2026-07-02), far below the 120-event minimum |

The basis leg — the entire measurable content of the entry-timing question for a delta-neutral
position — cannot be computed from any local data. Per the after-cost-screening skill:
missing data → INSUFFICIENT_DATA naming the exact harvest command. Never synthesize.

## VERDICT: INSUFFICIENT_DATA

**Exact blocking gap:** no perp-vs-spot basis series at ≤15m granularity for the F1 universe.

**Exact harvest commands to unblock (in preference order):**
1. **Premium-index klines (preferred — measures basis directly, keyless):** new script
   `scripts/backfill_premium_index.py` hitting Binance `GET /fapi/v1/premiumIndexKlines`
   (symbol=`<BASE>USDT`, interval=`15m`, full available history) for the 15 F1 bases, writing
   `data/ohlcv_cache/<BASE>-USDT_premidx_15m.parquet`. This is the same premium the funding
   rate is computed from — the exact quantity the screen needs.
2. **Perp 15m OHLCV:** extend `scripts/backfill_perps_ohlcv.py` (currently hardcoded to
   `ANALYSIS_ONLY_BASES`) with `--bases BTC ETH BNB SOL LINK TRX SUI ATOM DOGE ALGO ZEC ADA XRP LTC GRT
   --timeframes 15m 1h` → writes `data/ohlcv_cache/<BASE>-USDTUSDT_15m.parquet`.
3. **Refresh the stale spot 15m layer:** re-run `scripts/backfill_universe_ohlcv.py` for the
   15m timeframe (current files end 2026-06-14).

**Not run in substitution:** a spot-only "drift around settlement" study was considered and
refused — spot pays no funding and carries no basis; its drift neither confirms nor refutes
the timing edge and would invite a false verdict either way.

**Context for the re-run:** because F1 already gates entries into [20,180] min pre-settlement,
the realistic upside is bounded by intra-window basis drift over ≤ 3 hours — expect small Δ;
the gates, not the narrative, decide.

The pre-registration above is FROZEN for the re-run once the backfill lands; re-running with
different thresholds or a different variant grid invalidates the screen.

---

## EXECUTION-FREEZE ADDENDUM (2026-07-11, written BEFORE the re-run executes — audit 08d unblock)

Implementation conventions frozen NOW, before any premium-index data is screened. Hypothesis,
variant grid, n_trials=4, cost model, and gates above are untouched.

1. **Basis source:** Binance `GET /fapi/v1/premiumIndexKlines` 15m (values = premium fraction)
   AND — per audit finding C1-a, so the pre-registered bybit arm is not silently dropped —
   Bybit `GET /v5/market/premium-index-price-kline` 15m. Stored under `data/premium_index/`
   as `{venue}_{BASE}_15m.parquet`. basis(t) in bps = 1e4 × close of the 15m bar CLOSING at t
   (openTime = t − 15m).
2. **Event clock:** s0 = realized settlement print ts from `data/funding_history/{venue}_{BASE}.csv`.
   Exit t_X = ts of the 21st captured settlement (s20 for B/V1–V3). Exit is identical across
   variants, so exit basis cancels in Δ; it enters only the baseline episode return used for
   the MC curve.
3. **Regime filter (no-lookahead):** trailing 7d mean = mean of realized prints in
   [s0 − 7d, s0) — strictly BEFORE s0. "Funding at s0 > 0" uses the s0 print itself, exactly
   as frozen (the F1 gate's "current funding" analogue). Expected-edge check:
   `f1_net_expected_edge_bps(funding_per_settlement = trailing 7d mean, hold_settlements = 21,
   round_trip_cost_frac = venue cost)` ≥ max(15 bps, 3 × cost_bps). Venue cost: binance
   0.0050 (frozen §cost model); bybit from config.FEE: 2×10 bps spot + 2×6 bps perp +
   4×5 bps slip = 0.0052.
4. **Funding deltas:** V1–V3 vs B capture identical settlements → funding delta = 0 exactly.
   V4 (+15 min) misses s0 → funding delta = −rate(s0) (in bps; a cost, since rate(s0) > 0 in
   this universe).
5. **⚠ Frozen-sign implementation note (flagged pre-run, adjudication → honesty-auditor):**
   the frozen primary metric writes the basis term as −(basis(t_V) − basis(t_B)) for
   short-perp/long-spot. Direct arithmetic (position PnL = basis(entry) − basis(exit); same
   exit ⇒ Δ = basis(t_V) − basis(t_B)) gives the OPPOSITE sign. The screen executes the
   frozen text EXACTLY as the primary gated metric, and reports the sign-corrected series as
   a clearly labeled diagnostic. If the two would produce different verdicts, that is stated
   and the auditor adjudicates; the screener does not silently rewrite frozen text.
6. **Walk-forward:** 5 splits (pre-registered "≥ 5 folds"), embargo 1 event, purge = drop
   train episodes whose hold overlaps the test fold's earliest entry (same construction as
   the listing-short screens). OOS-WR = pooled share of OOS events with gated Δ_best > 0.
7. **MC curve:** baseline per-episode net return = Σ funding(s0..s20) + [basis(t_B) −
   basis(t_X)] − venue cost_frac; timing-modified = baseline + Δ_best. MC block bootstrap over
   the chronological per-episode modified series (episodes overlap — stated as a known
   limitation, same for baseline and variant so the Δ comparison is unaffected).

No result below this line existed when this addendum was written.

---

## EXECUTED RESULTS (2026-07-11, run AFTER the freeze + addendum above)

Data landed: `scripts/backfill_premium_index.py` → `data/premium_index/`, 30 series
(15 F1 bases × binance + bybit), 15m premium fractions; binance depth to 2019-12
(BTC 229,566 bars), bybit to 2020-10 (BTC 200,987 bars). Screen:
`research/screen_funding_timing.py`. An earlier partial run (29/30 series, binance_LTC
mid-write) was DISCARDED and re-run on the complete set — partial runs are never verdicts.

Event universe after the frozen F1-regime filter: **binance 1,642 / bybit 944 qualifying
settlement events** (both ≥ the pre-registered 120 minimum), pooled n = 2,586.
Excluded and counted: 167,036 non-qualifying prints, 118 no-premium-bar, 30 no-trailing-prints.

### Gate table (FROZEN-sign primary metric; best variant V1 = −15 min)

| Gate | Threshold | Value | Pass |
|---|---|---|---|
| best mean Δ > 0 | >0 | +1.33 bps/episode (V1) | ✅ |
| best mean Δ > unmodeled fill bound | >5 bps | **1.33 bps** | ❌ |
| DSR (n_trials=4) | ≥0.10 | ~1.000 (n=2,586 makes a tiny mean significant) | ✅ |
| PBO (4-variant CSCV) | ≤0.5 | 0.000 | ✅ |
| OOS-WR (5 folds, embargo+purge) | ≥0.55 | **0.510** | ❌ |
| Fold sign stability | all same | **[+,+,+,+,−]** | ❌ |
| Venue sign stability | same sign | **binance +2.37 / bybit −0.48** | ❌ |
| MC P(total>0) (timing-modified curve) | ≥0.95 | 1.000 | ✅ |
| MC maxDD p95 | ≤0.25 | 0.064 | ✅ |

Variant means (frozen sign, bps): V1 +1.33, V2 +1.26, V3 +0.75, V4 **−11.17**.

### Sign-convention adjudication data (addendum §5)

Corrected-sign diagnostic: ALL variants negative (best V3 −0.75 bps; V4 −14.6 bps);
OOS-WR 0.468. **The frozen-vs-corrected discrepancy does NOT change the verdict** — NO_GO
under both conventions (`corrected_would_change_verdict: false`).

### VERDICT: **NO_GO**

Exactly the NO_GO declared in advance: the best improvement (~1.3 bps) is smaller than the
unmodeled fill-quality uncertainty near settlement (the historically unverifiable 5 bps
leg-spread cap), the sign is unstable across walk-forward folds AND across binance vs bybit,
and OOS-WR is a coin flip. One solid by-product finding: **V4 (entering +15 min
post-settlement) is decisively bad (−11 to −15 bps)** — skipping the s0 funding print costs
real money; F1's current pre-settlement entry timing is already on the right side, and
intra-window micro-timing adds nothing robust.
