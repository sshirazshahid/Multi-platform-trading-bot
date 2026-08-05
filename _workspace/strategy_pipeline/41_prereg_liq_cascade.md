# 41 — Pre-registration: Liquidation-cascade / OI-flush reversion

**Status:** FROZEN before any outcome computation / dual-model verdicts  
**Date:** 2026-07-29  
**Dossier:** `41_dossier_liq_cascade.md`  
**Expectation:** NO_GO prior ~25%; this iteration likely ACCRUE_ONLY  
**sha256_md:** 13ee84e40f2604b660d763082f2681200532c1f6bd55cbbb21f4c9491085afaf

## Hypothesis (null)

Hourly Binance forceOrder liquidation spikes do **not** produce positive after-cost expectancy on the pre-registered long-after-long-flush / short-after-short-flush rules, on majors or FIT-alt universes, under stressed in-event costs.

## Data (frozen)

| Field | Value |
|-------|-------|
| Signal | `data/liquidations_history.jsonl` rows `{hour, symbol, long_usd, short_usd, count}` |
| Source | Binance USD-M `wss://fstream.binance.com/market/ws/!forceOrder@arr` via `scripts/harvest_liquidations.py` |
| Undercount | Accepted as binding measurement error; do not “correct” with vendor fills post-hoc |
| Prices | Local 1h OHLCV cache `data/ohlcv_cache/{COIN}-USDT_1h.parquet` (ccxt-sourced) |
| Leakage | Signal for hour `t` uses liq aggregated **during** hour `t` (known at hour close). Entry = close[t]. Target = close[t+H] − close[t]. No future liq. |

## Universes (separate arms — never pooled for multiplicity)

| Arm ID | Symbols | Role |
|--------|---------|------|
| `majors_btc_eth` | BTC, ETH | Primary |
| `fit_alt_flush` | ALGO, ARB, AVAX, LINK | Secondary; from 37_ FIT_BAND_PAPER ex-ETH |

## Signal construction (frozen)

For each arm, per symbol, hourly series `L_t = long_usd`, `S_t = short_usd`.

1. **Absolute cascade (primary):**  
   - Long-flush event if `L_t ≥ Θ` and `L_t ≥ S_t`  
   - Short-flush event if `S_t ≥ Θ` and `S_t ≥ L_t`  
2. **Threshold grid Θ (USD notional in hour):** `{1e6, 5e6}` for majors; `{1e5, 5e5}` for FIT-alt (lower because Stage-0 showed FIT rarely hits 1e6).  
3. **Z-score overlay (secondary multiplicity cell):** trailing 168h past-only z of `L` or `S` ≥ 2.5 **and** absolute `≥ Θ_min` where `Θ_min = 1e6` (majors) / `1e5` (FIT). Past window = `[t-168, t)` — excludes `t`.

**Forbidden:** OI level, OI change, funding sign alone, RSI/MA, or any price-pattern entry. Liq USD flow only.

## Trade rules (frozen)

| Field | Value |
|-------|-------|
| Direction | Long-flush → **LONG**; short-flush → **SHORT** |
| Entry | Taker at 1h close of signal hour `t` |
| Horizons H | `{4, 12}` hours (bounded holds; covers 2–10h practitioner decay window without overnight sprawl) |
| Exit | Taker at close[t+H]; no discretionary SL/TP inside window for the screen accounting (per-event loss = path move) |
| Sizing | 1R = notional stake; report bps and R |
| Overlap | If multiple events overlap on same symbol, keep first; skip until flat |

## Costs (frozen)

| Leg | Primary | Stress |
|-----|---------|--------|
| Round-trip fee+slip in-cascade | **30 bps** | **60 bps** |
| Funding | Charge any settlement crossed in [t, t+H] from local funding history if present; else charge 1× default 1 bp/8h conservative |

Net return = raw close-to-close return in trade direction − cost.

## Stage-0 feasibility (stopping rule — binding)

Before any after-cost mean test: count triggers per (arm × Θ × side) on the JSONL distribution.

- If **every** cell in an arm has triggers **&lt; 30** → that arm = `INSUFFICIENT_DATA` (Open ledger section; not a NO_GO refutation).  
- Empirical pre-hash check (2026-07-29): majors at Θ=1e6 have hundreds of hours; FIT at Θ=1e5 may clear n≥30 on LINK-dominated cells only — FIT arm may still fail Stage-0 for ALGO/ARB/AVAX individually (evaluate per-symbol and as equal-weight basket separately; basket = extra multiplicity cell).

## Gates (after Stage-0 pass only — future screen day)

Joint multiplicity: arms × Θ × {abs, z-overlay} × H × {primary cost, stress} with Holm correction.

A cell GO only if **all** hold after cost:
- n_OOS ≥ 30  
- mean net &gt; 0  
- OOS-WR ≥ 0.55  
- MC P(total&gt;0) ≥ 0.95  
- maxDD p95 ≤ 0.25 (1R stake accounting, 3%/12% caps if capital-scaled variant added later — **not** in this prereg)

## Variants explicitly OUT of scope (need NEW prereg)

- Vendor-complete liquidation history (Tardis)  
- Sub-hour entry (1m)  
- OI×funding joint veto (queue #3)  
- Directional VPIN / QH imbalance  

## Adjacent tooling note

`scripts/run_liquidation_edge_screen.py` exists (10 bps, z=2.5, H∈{1,4,12}, beta-adjusted). It is **not** this prereg’s outcome engine. Any screen claiming this hash must implement the **30/60 bps** cost grid and arm split above; do not relabel old 10 bps outputs as this prereg’s results.

## Implement authority from dual-model (this UTC day)

Verdicts allowed: `ACCRUE_ONLY` | `SCREEN_NOW` | `STOP`.  
Full after-cost screen consuming this hash = **separate** heavy UTC day if `SCREEN_NOW` + ai-reviewer APPROVE.
