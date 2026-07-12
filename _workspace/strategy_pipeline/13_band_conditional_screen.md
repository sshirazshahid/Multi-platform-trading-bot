# 13 — Pre-Registered Conditional-WR Screen on the Accuracy-Band Geometry

_Frozen 2026-07-12 (UTC) BEFORE any conditional outcome was computed. Edits to gates,
buckets, sources, or thresholds after this point invalidate the screen._
_Screen: `research/screen_band_conditional.py` · Verdicts: `13_band_conditional_screen.json`._

## Hypothesis
At the live accuracy-band geometry (TP = 0.35 x SL distance, floored at 0.5% of entry),
one or more measurable PRE-ENTRY condition families selects a subset whose after-cost
win rate is >= 68% (beating the 65.7% unconditional geometric baseline beyond its upper
CI) **with after-cost expectancy > 0** — i.e., real entry edge, not geometry reshaping.
Owner directive: WR (63–67 band) AND positive PnL must BOTH hold; only a conditional
filter can lift both.

## Universe & sample period
- Source: `data/warehouse.sqlite` `shadow_decisions` rows with non-null
  `entry_px/sl_px/tp_px` (identical filter to `research/sim_accuracy_band.py`, the
  sweep that produced the 65.7% baseline).
- Span: 2026-06-30 09:53 UTC -> screen run time (2026-07-12). n_raw = 15,007 at
  pre-registration (79 venue|symbol|timeframe groups; venues binance/bybit).
- **Replication guard subset:** ts <= 1783651564 (n = 8,900 ~= the 8,878-entry sweep
  snapshot cited in `.env`). The unconditional f=0.35 after-cost WR recomputed on this
  subset must be within **2.0pp of 65.7%**, else the entire screen is flagged
  JOIN_SUSPECT: results still reported, but **no bucket may be GO**.
- Baseline for comparison/H0: p0 = 0.657 (frozen; from the cited sweep).

## Outcome model (identical to the sweep — no re-invention)
- Keep each row's production `sl_px`; replace TP with
  `TP% = max(0.5, 0.35 x SL%)` via `research.sim_accuracy_band.swap_tp`
  (mirrors `core.mcp_brain._apply_accuracy_target`, floor included).
- Resolve with `core.shadow_resolver.resolve_one` on forward CLOSED candles from
  `scripts.resolve_shadow_outcomes.build_fetch_candles(_make_ccxt_fetcher())`:
  first-touch-wins, SL-first conservative tie-break, horizon censoring.
- Costs (resolver constants, the same model that produced the 65.7 baseline):
  fees 6bps/side taker; slippage 5bps open, 5bps exit, 10bps stop-loss.
- Win = `net_pnl > 0`. **Censored rows excluded, never counted as wins.**
- Entry-anchored by construction: candle slices begin at the entry bar; bars before
  entry never exist in the slice (phantom-TP rule satisfied).

## Condition families — EXACTLY SIX, frozen sources and buckets
All indicator values use ONLY bars whose bar-open timestamp is at or before entry ts,
taking the last CLOSED bar (forming bar dropped). Combinations of families are NOT
tested this pass (combinatorial multiplicity).

| # | Family | Source (frozen) | Buckets (frozen) |
|---|--------|-----------------|------------------|
| F1 | BTC 1h ATR regime | ATR(14) `utils.indicators.atr` on binance `BTC/USDT:USDT` 1h; ratio = entry-anchored ATR / median(ATR, trailing 30d) | `<0.7`, `0.7–1.3`, `>1.3` |
| F2 | Symbol own 1h ATR percentile | ATR(14) on the row's venue+symbol 1h bars; percentile rank of entry-anchored ATR vs its trailing 30d ATR values; rows with <20d history uncovered | `<25th`, `25–75th`, `>75th` |
| F3 | 4h ADX at entry | `utils.indicators.adx(period=14)` on venue+symbol 4h bars, last closed bar | `<20`, `20–30`, `>30` |
| F4 | Symbol spread percentile | nearest-in-time `candidates` row (same symbol, abs(dt) <= 1800s) `features_json.ob_spread_bps`; percentile vs that symbol's own candidates `ob_spread_bps` trailing 30d. DISCLOSED: no precomputed spread percentile exists in-span (`features` table ends 2026-06-14; `ob_spread_bps` present on only ~36% of in-span candidates). **Coverage floor: if <50% of resolved rows are joined+covered, family = INSUFFICIENT_DATA** (partial-coverage selection bias). | `<50th`, `>=50th` |
| F5 | Entry fill type | `trades.fill_type` (live/paper cohort). Pre-check at freeze time: 25 non-null (15 maker / 10 taker_fallback), **0** trades with mfe/mae -> counterfactual band outcome uncomputable; expected INSUFFICIENT_DATA (needs >= 300 per G2). | `maker`, `taker` |
| F6 | 4h EMA-gap magnitude | `abs(EMA20-EMA50)/EMA50*100` on venue+symbol 4h bars (`utils.indicators.ema`; mirrors `core/mcp_brain.py:2858`) | `0.15–0.30%`, `0.30–0.60%`, `>0.60%`; gap `<0.15%` reported as residual, NOT gated (no post-hoc buckets) |

**Excluded as ledger-refuted (not tested):** hour-of-day/seasonality,
candlestick/indicator-confluence, mcp_score buckets (measured non-predictive),
day-of-week.

## Frozen gates — a bucket is GO only if ALL hold (joint, per owner's ask)
- **G1** conditional after-cost WR at f=0.35 >= **68.0%**
- **G2** n_resolved >= **300** in the bucket
- **G3** time-split stability: chronological halves (median-ts split within bucket)
  BOTH >= **65.0%** WR
- **G4** after-cost expectancy > 0 in the bucket (mean `net_pnl` USD from
  `resolve_one`; expectancy in R reported alongside)
- **G5** multiplicity: one-sided exact binomial test vs H0 p0=0.657,
  Bonferroni-adjusted with **m = 16** (3+3+3+2+2+3 = the full pre-registered
  gated-bucket count, charged in full even where families end INSUFFICIENT_DATA);
  adjusted p < **0.05**
- **G6** flow retention: the bucket must retain >= **10%** of total resolved
  entries/day (report entries/day retained; a filter that kills the lane is not
  actionable)

Verdicts: **GO** (all gates pass) / **NO_GO** (computed, >=1 gate fails) /
**INSUFFICIENT_DATA** (feature missing or coverage floor breached; exact backfill named).

## What NO_GO looks like (stated in advance)
Every bucket WR within a few pp of the unconditional geometric baseline; expectancy
negative everywhere (the signal is no-edge — 0/2,400 prior pattern-mining survivors);
halves unstable on any bucket that clears G1 by chance. **Expected outcome: NO_GO
across the board.** Gates will NOT be softened to avoid it.

## Multiplicity honesty
Trials this pass = 16 gated buckets. No other bucketings, thresholds, or condition
families were computed before this freeze (feature-availability checks above touched
schemas and counts only — zero conditional outcomes were computed pre-freeze).

---

## RESULTS (appended after computation — nothing above this line may change)


_Computed 2026-07-12 11:27 UTC · research/screen_band_conditional.py · n_raw=15051, resolved=14555, censored=496, span=12.0d._

### Baseline recompute (guard)

- Full sample: WR **64.7%** (CI 63.9-65.4), n=14555, exp=-0.258R / -0.583$
- Replication subset (ts<=1783651564): WR **66.0%**, n=8900, deviation from 65.7% = 0.31pp -> guard **OK**

### Per-bucket results

| family | bucket | n | WR | CI | exp $ | exp R | e/day | p_adj | halves | verdict |
|---|---|---:|---:|:-:|---:|---:|---:|---:|:-:|---|
| f1_btc_atr_regime | <0.7 | 3203 | 55.6% | 53.9-57.4 | -0.875 | -0.384 | 266.3 | 1 | 58/54 | NO_GO |
| f1_btc_atr_regime | 0.7-1.3 | 11352 | 67.2% | 66.3-68.0 | -0.501 | -0.222 | 943.8 | 0.00631 | 62/73 | NO_GO |
| f1_btc_atr_regime | >1.3 | 0 | - | - | - | - | - | - | - | INSUFFICIENT_DATA |
| f2_symbol_atr_pctl | <25th | 5376 | 60.2% | 58.8-61.5 | -0.599 | -0.326 | 446.9 | 1 | 62/58 | NO_GO |
| f2_symbol_atr_pctl | 25-75th | 6639 | 67.3% | 66.2-68.4 | -0.489 | -0.222 | 551.9 | 0.0452 | 64/71 | NO_GO |
| f2_symbol_atr_pctl | >75th | 2516 | 67.4% | 65.5-69.2 | -0.799 | -0.205 | 209.2 | 0.645 | 70/65 | NO_GO |
| f3_adx_4h | <20 | 3040 | 67.6% | 65.9-69.2 | -0.385 | -0.224 | 252.7 | 0.226 | 76/60 | NO_GO |
| f3_adx_4h | 20-30 | 6163 | 68.1% | 66.9-69.3 | -0.485 | -0.209 | 512.4 | 0.000498 | 67/69 | NO_GO |
| f3_adx_4h | >30 | 5352 | 59.0% | 57.7-60.3 | -0.809 | -0.333 | 444.9 | 1 | 59/59 | NO_GO |
| f4_spread_pctl | <50th | 3308 | - | - | - | - | - | - | - | INSUFFICIENT_DATA |
| f4_spread_pctl | >=50th | 2439 | - | - | - | - | - | - | - | INSUFFICIENT_DATA |
| f6_ema_gap_4h | 0.15-0.30% | 928 | 78.5% | 75.7-81.0 | -0.058 | -0.060 | 77.2 | 2.09e-16 | 86/71 | NO_GO |
| f6_ema_gap_4h | 0.30-0.60% | 1910 | 64.9% | 62.7-67.0 | -0.457 | -0.259 | 158.8 | 1 | 64/65 | NO_GO |
| f6_ema_gap_4h | >0.60% | 10809 | 63.5% | 62.6-64.4 | -0.657 | -0.273 | 898.6 | 1 | 61/66 | NO_GO |
| f5_fill_type | maker | 15 | - | - | - | - | - | - | - | INSUFFICIENT_DATA |
| f5_fill_type | taker | 10 | - | - | - | - | - | - | - | INSUFFICIENT_DATA |

- Coverage: {'f1_btc_atr_regime': 14555, 'f2_symbol_atr_pctl': 14531, 'f3_adx_4h': 14555, 'f4_spread_pctl': 5747, 'f6_ema_gap_4h': 14555} of 14555 resolved; F6 residual (<0.15% gap, not gated): 908; F4 forward joins (candidate ts up to +30min after entry): 2774.
- Implementation notes (disclosed): F4 percentile needs >=30 trailing spread values; F2 needs >=480 ATR bars (20d). Neither is a gate change.

