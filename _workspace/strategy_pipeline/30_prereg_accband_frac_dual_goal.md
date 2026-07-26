# 30 — Prereg: AccBand frac / min_tp dual-goal sweep
*Frozen BEFORE any sweep outcomes | Date: 2026-07-23*

## Binding honesty

This is a **geometry / payoff** pre-registration under PAPER + MAX_FLOW_BAND only.
It does **not** claim directional edge. Screen-13 already showed band buckets
after-cost negative at prior fracs. Success requires joint economics, not WR alone.

Baseline measurement (must not be altered by this prereg):
[`30_cohort_bel_measure_2026-07-23.md`](30_cohort_bel_measure_2026-07-23.md)
— cohort BE_WR 0.671, W/L 0.491, needed W/L ≥ 0.695 for BE at 59%.

## Population

- Mode: `OPERATING_MODE=PAPER` + `PAPER_TRADING_PROFILE=MAX_FLOW_BAND` only.
- Lane: MCP directional AccBand entries only (not deep_breakout, not shadow probes, not F1).
- Venues: existing Binance / Bybit / Bitget discovery universe (no universe change in this prereg).
- Evaluation window: closed trades with `ts_exit` after the sweep epoch stamp written at run start.
- Minimum sample per cell: **n ≥ 80** closed AccBand outcomes (fail-closed INSUFFICIENT_DATA below).

## Frozen cells (m = 12; Bonferroni α = 0.05/12)

Global `ACCURACY_TP_FRAC_OF_SL` grid × optional side splits. Side cells use buy/sell
overrides; global-only cells leave buy/sell unset (inherit global).

| cell_id | tp_frac_of_sl | tp_frac_buy | tp_frac_sell | min_tp_pct |
|---------|---------------|-------------|--------------|------------|
| G035 | 0.35 | — | — | 0.50 |
| G040 | 0.40 | — | — | 0.50 |  ← current production-ish
| G045 | 0.45 | — | — | 0.50 |
| G050 | 0.50 | — | — | 0.50 |
| G055 | 0.55 | — | — | 0.50 |
| G060 | 0.60 | — | — | 0.50 |
| G070 | 0.70 | — | — | 0.50 |
| G080 | 0.80 | — | — | 0.50 |
| S_hi | 0.50 | 0.55 | 0.45 | 0.50 |
| S_mid | 0.50 | 0.45 | 0.35 | 0.50 |  ← near current side split spirit
| S_wide | 0.60 | 0.65 | 0.55 | 0.50 |
| G050_m40 | 0.50 | — | — | 0.40 |

No other fracs, no ADX/vol knobs, no entry-score changes in this prereg.
`BAND_REGIME_FILTER_ENABLED` must be **constant** across all cells (see bleed-controls
artifact — fixed TRUE for the sweep cohort).

## Joint GO gates (ALL required; else NO_GO / INSUFFICIENT_DATA)

Per cell, after-cost on warehouse `realized_pnl`:

1. `breakeven_wr = avg_loss / (avg_win + avg_loss) ≤ 0.59`
2. `win_rate ∈ [0.59, 0.67]`
3. `mean(realized_pnl) > 0` (expectancy)
4. `profit_factor = gross_profit / abs(gross_loss) > 1.0`
5. Multiplicity: if testing mean>0 via bootstrap, apply Bonferroni m=12; descriptive
   point estimates still must clear (1)–(4) even if bootstrap is deferred.

If **zero** cells clear all four point gates → **CONFIRMED_NO_GO** for AccBand
dual-goal geometry under this grid. Do **not** loosen gates, do **not** pick
best WR cell, do **not** promote to CONTROLLED_LIVE.

## Forbidden during / after sweep

- Changing `MCP_ENTRY_MIN_SCORE`, economic gate mode, or StrategySpec to inflate WR.
- Installing MA/RSI/MACD/SuperTrend entries.
- Re-tuning cells after peeking at outcomes (new grid = new prereg + new hash).
- Claiming GO on WR-in-band alone.

## Run protocol (future UTC heavy-stage)

1. Confirm this file’s sha256 matches the frozen hash in the companion JSON.
2. Stamp `sweep_epoch_utc` before flipping any env frac.
3. One cell at a time OR offline replay on cached AccBand candidate outcomes —
   prefer offline first-touch replay if available to avoid burning live PAPER
   capital; if offline unavailable, sequential PAPER cell accrual with epoch stamps.
4. Write screen artifact `30_screen_accband_frac_dual_goal.{md,json}` only after hash lock.
5. Honesty-auditor pass before any `.env` production frac change.

## Expectation

**NO_GO** is the prior (screen-13 + cohort BE_WR). A GO cell would be surprising and
still requires owner sign-off before any live path change.
