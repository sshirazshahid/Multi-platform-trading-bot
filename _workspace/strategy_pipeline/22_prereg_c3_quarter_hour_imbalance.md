# 22 — Pre-registration: C3 quarter-hour opening imbalance → 4–12h perp drift

**Status:** FROZEN before any outcome computation  
**Date:** 2026-07-23  
**Candidate:** C3 from `_workspace/strategy_pipeline/18_final_pair_verdicts.json`  
**Expectation:** NO_GO (Codex MODIFY kill-first pilot; both models registered NO_GO)  
**Paper:** Kim & Hansen, *Quarter-Hour Effect*, [arXiv 2607.09426](https://arxiv.org/abs/2607.09426)

## Hypothesis (null)

Signed aggressor order-flow imbalance in the **opening 10 seconds** of UTC quarter-hour boundaries (:00/:15/:30/:45) does **not** predict after-cost Binance USDT-M perp returns at 4/8/12-hour horizons **incrementally over frozen price-volume controls**, on post-paper OOS data.

Latency-infeasible sub-second opening-return trading is **excluded** (not measured).

## Pilot scope (frozen — expand only if ≥20 bps incremental mean AND gates pass)

| Field | Value |
|-------|-------|
| Symbols | BTCUSDT, ETHUSDT |
| Window | 2026-04-01 00:00 UTC → 2026-06-30 23:59 UTC |
| Venue | Binance USDT-M aggTrades (`data.binance.vision`) |
| Expand trigger | Aligned residual variant mean ≥ **20 bps** after all costs **and** passes frozen gates → NEW prereg for 6-symbol / full 2025–26 harvest |
| Else | NO_GO + ledger row |

## Signal construction (frozen)

1. **Boundary** `T`: UTC clock time with `minute % 15 == 0`, `second == 0`, `microsecond == 0`.
2. **Opening window:** `[T, T + 10s)` on `transact_time` (ms).
3. **Aggressor split:** `is_buyer_maker == false` → taker buy qty; `true` → taker sell qty (quote = qty × price for dollar imbalance, primary; qty imbalance logged).
4. **Raw imbalance (primary):** `(buy_dollar − sell_dollar) / (buy_dollar + sell_dollar + 1e-12)`.
5. **Controls at T (from 1h OHLCV, no lookahead):**
   - `ret_1h`: log(close[T_bar−1]/close[T_bar−2])
   - `ret_4h`: log(close[T_bar−1]/close[T_bar−5])` (4 completed hours before boundary hour)
   - `log_vol_1h`: log1p(volume of hour ending at boundary open)
6. **Residual signal:** OLS residual of raw imbalance on `[ret_1h, ret_4h, log_vol_1h]` (intercept), fit expanding on in-pilot history with **minimum 200 finite rows** before first residual; pre-200 events use full-sample fit on available (logged).
7. **Delta-drift kill:** if raw passes gates while residual fails on the same (horizon, direction), classify as price-volume-spanned → NO_GO.

## Trade rules (frozen)

- **Entry price:** last aggTrade price in opening window; if zero trades in window → skip event.
- **Entry time:** `T + 10s` (signal fixed; price from last trade in window).
- **Horizons (joint multiplicity):** H4 = 4h, H8 = 8h, H12 = 12h after entry.
- **Exit price:** close of first 1h bar with `bar_open >= entry_time + horizon`.
- **Directions (n_dir = 2):**
  - **aligned:** long if residual > 0 else short
  - **contrarian:** opposite (multiplicity only; not promotable alone)
- **Overlap / netting:** one open trade per symbol; skip boundary events whose entry falls before prior exit (non-overlapping).
- **Universe per event:** both symbols evaluated independently.

## Costs (charge all)

- Fees: `config.FEE["binance"]["futures_taker"]` per side (default 5 bps).
- Slippage: 5 bps open + 5 bps close.
- Funding: sum of settlements in `(entry, exit]` from `data/funding_history/binance_{BTC,ETH}.csv` if present; else 0 with warning (pilot bias conservative on missing funding).

## Gates (frozen — never loosen)

| Gate | Threshold |
|------|-----------|
| MIN_DSR | ≥ 0.10 |
| MAX_PBO | ≤ 0.5 |
| OOS-WR | ≥ 0.55 |
| MC P(total>0) | ≥ 0.95 |
| MC maxDD p95 | ≤ 0.25 |
| Min n (per variant, pooled OOS) | ≥ 30 else INSUFFICIENT_DATA |

**Multiplicity n_trials:** 3 horizons × 2 directions = **6**. DSR uses n_trials=6.

## OOS split

- **Pilot IS (diagnostics):** first 50% of boundary events by time (within pilot window).
- **Primary OOS gate:** second 50% of boundary events by time (untouched half).

## Verdict rules

- **GO:** residual **aligned** variant passes ALL gates on OOS AND ≥20 bps mean AND adjacent horizon same-sign (anti horizon-mining) AND no delta-drift kill.
- **NO_GO:** any failure, or incremental mean < 20 bps, or contrarian-only pass.
- **INSUFFICIENT_DATA:** OOS n < 30 or harvest missing.

## Artifacts

- Harvest: `data/aggtrades_qh/` manifest + `{BTC,ETH}USDT_qh_events.parquet` (gitignored)
- Prereg: `_workspace/strategy_pipeline/22_prereg_c3_quarter_hour_imbalance.{md,json}`
- Screen: `_workspace/strategy_pipeline/22_screen_c3_quarter_hour_imbalance.{md,json}`
- Audit: `_workspace/strategy_pipeline/22_audit_c3_quarter_hour_imbalance.md`
- Harvest cmd: `python scripts/harvest_binance_aggtrades_qh.py`

## Prereg content hash

Recorded in companion JSON **before** harvest/screen run.
