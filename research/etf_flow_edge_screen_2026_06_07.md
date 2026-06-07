# Spot-BTC-ETF net-flow edge screen (WS3b) — 2026-06-07

**Verdict: NO_EDGE.** The one genuinely-new, *non-price* information source — US spot-Bitcoin-ETF
daily net flow (Farside, 618 daily obs, 2024-01-11 → 2026-06-05, $54B cumulative) — does **not**
predict BTC forward returns past the frozen time-series gate. Tested cheaply, honestly; null stops here.

## Why this source (and not another Kronos)

Kronos and every prior screen read the **same candlesticks**. ETF net flow is the opposite: a
non-price, institutional-demand *quantity* with no analog in the OHLCV substrate — exactly the kind
of orthogonal source where a faint edge *could* live that the price-based screens are structurally
blind to. (Stablecoin supply, the other orthogonal candidate, already screened NO_EDGE; liquidations
remain a forward-harvest.)

## Method (leakage-controlled)

- **Signal:** rolling-sum net flow over W trading days. **Causal lag of 1 day** — Farside publishes
  day-T flow after the US close (known only ~T+1 UTC, *after* BTC's day-T 00:00-UTC close), so the
  flow window ending at T-1 predicts the return from close T forward. Weekends/holidays = 0 net flow
  (no creations/redemptions — correct, not imputation).
- **Gate (frozen, same as the stablecoin screen):** `core/alpha_zoo/ts_regime.timeseries_regime_gate`
  — |t| ≥ 3.5 on rank-correlation AND label-shuffle null exceeded by ≥ 3.5σ AND sign-consistent in
  BOTH halves (OOS stability), on **non-overlapping** forward returns (step = horizon).
- **Grid:** windows {5, 10, 20}d × horizons {3, 5, 10}d = 9 trials. BTC daily close from the local
  cache. `core/data_feeds/etf_flow_feed.py` + `scripts/run_etf_flow_edge_screen.py` (+ feed tests).

## Result

| win | h | n | rho | t | z_null | h1 | h2 | both | gate |
|----:|--:|--:|----:|--:|------:|---:|---:|:----:|:----:|
| 5  | 3  | 291 | +0.043 | 0.73 | −0.10 | −0.00 | +0.07 | no  | no |
| 5  | 5  | 175 | +0.002 | 0.03 | −1.24 | −0.09 | +0.12 | no  | no |
| 5  | 10 | 87  | +0.053 | 0.49 | −0.55 | −0.03 | +0.16 | no  | no |
| 10 | 3  | 290 | +0.075 | 1.28 | +0.67 | +0.07 | +0.08 | yes | no |
| 10 | 5  | 174 | +0.103 | 1.36 | +1.04 | +0.14 | +0.08 | yes | no |
| **10** | **10** | **87** | **+0.204** | **1.92** | **+1.76** | **+0.10** | **+0.26** | **yes** | **no** |
| 20 | 3  | 289 | +0.044 | 0.75 | −0.06 | −0.01 | +0.09 | yes | no |
| 20 | 5  | 173 | +0.079 | 1.04 | +0.40 | +0.04 | +0.10 | yes | no |
| 20 | 10 | 86  | +0.131 | 1.21 | +0.63 | +0.05 | +0.18 | yes | no |

**No cell clears the gate.** The strongest is **10d-flow → 10d-forward** (rho +0.204, both halves
positive, magnitude monotone in horizon) — a faint, directionally-plausible tendency (more recent
net inflow → higher forward return). But **t = 1.92 vs the 3.5 bar**, shuffle-z 1.76 vs 3.5, and
n = 87 (non-overlapping, 2.4y). Across 9 trials that is **noise, not an edge**.

## Honest call

NO_EDGE per the pre-registered gate. The faint 10d whisper is **not** promoted — re-parameterizing
until it passes would be cross-screen multiple-testing on exhausted data (the exact trap). The only
legitimate follow-up is **forward validation**: the feed is built and cheap to re-run, so this cell
can be re-checked as more out-of-sample months accrue, with no new fitting. If a real flow→return
lead existed it would be larger and clear 3.5; what's here is consistent with flows being mostly
*coincident* with price, not leading, net of cost. Capital (~$1,300) remains the binding constraint
regardless. Nothing wired to the live bot; cost was ~1 feed module + 1 screen.
