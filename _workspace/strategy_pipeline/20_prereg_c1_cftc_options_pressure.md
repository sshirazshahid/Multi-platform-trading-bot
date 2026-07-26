# 20 — Pre-registration: C1 CFTC asset-manager options-pressure → BTC perp

**Status:** FROZEN before any outcome computation  
**Date:** 2026-07-23  
**Candidate:** C1 from `_workspace/strategy_pipeline/18_final_pair_verdicts.json`  
**Expectation:** NO_GO (Fable hardened SCREEN; both-agree queue)  
**Paper:** Shen / Li / Luo, Finance Research Letters 2026 (asset-manager options positions predict second-week-ahead BTC futures returns)

## Hypothesis (null)

Asset-manager **options-only** net position changes (TFF Combined − TFF Futures-Only), after residualizing against contemporaneous and 1–8 week lagged BTC returns (delta-drift null), do **not** predict after-cost Tuesday→Tuesday BTCUSDT-perp returns at horizons t+1 / t+2 / t+3 beyond chance under frozen promotion gates.

## Signal construction (frozen)

1. Report family: **TFF only** (datasets `gpe5-46if` FutOnly, `yw9f-hn96` Combined). Not Disagg/Legacy.
2. Market: `BITCOIN - CHICAGO MERCANTILE EXCHANGE` (code 133741). Micro BTC logged diagnostically, **not** in primary gate.
3. Asset-manager net = `asset_mgr_positions_long − asset_mgr_positions_short` on each report.
4. Options-only net = Combined net − FutOnly net (matched on `report_date_as_yyyy_mm_dd`).
5. Signal = week-over-week Δ(options-only net).
6. **Delta-adjustment null (binding kill):** OLS residualize Δ against contemporaneous BTC Tue→Tue return and lags 1..8 of that return. Primary score = residual. If residual fails gates while raw Δ passes, classify under formulaic-alpha / lagged-return and NO_GO.
7. Sign convention: positive residual → LONG BTC perp; negative → SHORT. (Matches “pressure absorption” direction claimed by the paper; if inverted, both signs are counted as multiplicity ×2 and DSR uses n_trials accordingly — **frozen: test both signs as 2 direction variants**, n_dir=2.)

## Tradeable calendar / no-lookahead

- Positions as-of Tuesday `T` (report date).
- Publication lag: release assumed **Friday 15:30 America/New_York** on the Friday after `T`, unless that Friday is a US federal holiday → next NYSE business day 15:30 ET. (Explicit assumption: PRE has no release-timestamp field; holiday calendar = US federal via hardcoded 2020–2026 list in screen script.)
- Entry: first 1h bar open **after** release timestamp (UTC).
- Horizons (joint multiplicity, never t+2 alone):
  - **H1 (t+1):** exit at Tuesday open closest to `T + 7d` (mostly untradeable / partial — still measured).
  - **H2 (t+2):** exit at Tuesday open closest to `T + 14d`.
  - **H3 (t+3):** exit at Tuesday open closest to `T + 21d`.
- Hold return = close-to-close of BTC-USDT 1h bars from entry bar → exit bar, charged costs below.
- If entry is after the horizon exit timestamp → trade skipped (untradeable).

## Universe / sample

- Price: `data/ohlcv_cache/BTC-USDT_1h.parquet`
- Funding: venue preference `binance > bybit > bitget` from `data/funding_history/{venue}_BTC.csv`; first with full settlement coverage in [entry, exit].
- In-sample paper window (for diagnostics only): 2020-01-21 → 2025-03-04.
- **Primary OOS gate window:** 2025-03-05 → latest available (untouched post-paper).
- **ETF-era split (required reporting):** pre-2024-01-11 vs post-2024-01-10 (spot ETF launch). Single-regime profit is a binding caveat, not automatic GO.
- High-downside-risk conditioned cell: if claimed, requires n≥30 else INSUFFICIENT_DATA for that cell (unconditioned is primary).

## Cost model (charge all)

- Fees: `config.FEE` futures taker per side for chosen venue (default binance 5 bps/side if unspecified).
- Slippage: 5 bps open + 5 bps close.
- Funding: every settlement in [entry, exit] charged to the held side from realized history (no averages).
- Position: 3% account notional, unlevered; single BTC name so concurrency N/A.
- All-in cost floor expected ~30–40 bps/week at 1× (taker RT + ~21 settlements/week) — screen charges realized, not this estimate.

## Gates (frozen — never loosen)

From `core/promotion_gate.py` + MC:

| Gate | Threshold |
|------|-----------|
| MIN_DSR | ≥ 0.10 |
| MAX_PBO | ≤ 0.5 |
| OOS-WR | ≥ 0.55 |
| MC P(total>0) | ≥ 0.95 |
| MC maxDD p95 | ≤ 0.25 |
| Min n (OOS primary) | ≥ 30 trades else INSUFFICIENT_DATA |

**Multiplicity n_trials:** 3 horizons × 2 directions = **6**. DSR/PBO computed against n_trials=6. A single horizon/direction cannot be promoted alone.

## Verdict rules

- **GO:** residualized signal passes ALL gates on primary OOS for at least one (horizon, direction) AND neighbors are not all dead in a way that implies horizon-mining (require the winning horizon’s mean to remain same-sign on ≥1 adjacent horizon, or else NO_GO as horizon-mined).
- **NO_GO:** fails any gate, or residual fails while raw passes (delta-drift kill), or t+2-only with dead H1/H3.
- **INSUFFICIENT_DATA:** OOS n<30 after coverage filters; name exact harvest command.

## Artifact paths

- Raw harvest: `data/cftc_cot/tff_fut_only_btc_raw.json`, `data/cftc_cot/tff_combined_btc_raw.json`, `data/cftc_cot/manifest.json` (gitignored).
- This prereg: `_workspace/strategy_pipeline/20_prereg_c1_cftc_options_pressure.md` + `.json`
- Screen output: `_workspace/strategy_pipeline/20_screen_c1_cftc_options_pressure.{md,json}`
- Audit: `_workspace/strategy_pipeline/20_audit_c1_cftc_options_pressure.md`

## Prereg content hash

Computed immediately after this file is written; recorded in the companion JSON and journal **before** `research/screen_cftc_options_pressure.py` runs.
