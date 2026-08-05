# 55 — Pre-registration: Micro Two-Sided Inventory (MTSI)

**Status:** FROZEN before any outcome computation  
**Date:** 2026-08-01  
**Family:** `mtsi_inventory_v1`  
**Class:** Sub-HFT maker inventory MM (SPOT + FUTURES sims)  
**Expectation:** **NO_GO** on CEX majors after maker fees + adverse selection  
**Owner posture:** F1-only allowlist remains; this prereg does **not** reopen AccBand or enable CONTROLLED_LIVE.

## 0. Doctrine freeze

1. Quote Up and Down at different times (inventory/time drives live side).
2. Hard gross inventory: `|q_usd| ≤ 1.0` always.
3. Mild fair-value tilt only (microprice residual); never MCP AccBand score.
4. Edge = many micro clips; `max_clip_usd ≤ 1.0` invariant.

## 1. Null hypothesis

Under pre-registered AS quoting + fee floor + adverse-selection haircut, mean after-cost clip PnL on BTC/ETH is **not** positive at κ-grid cells below.

## 2. Universe / data

| Field | Value |
|-------|-------|
| Symbols | BTC/USDT, ETH/USDT |
| Venues | bybit (sim); fees from config maker tables |
| Spot path | fee only (no funding) |
| Futures path | maker fee + funding cashflow on inventory |
| Price path | synthetic geometric Brownian mid OR local OHLCV if present — fill model is tape-cross (no look-ahead) |

## 3. Signal / control (frozen)

```
r = mid - q_norm * gamma * sigma2 * tau + fv_tilt
half_spread = max(fee_floor_bps/1e4, as_half_spread)
bid = r - half_spread; ask = r + half_spread
```

- At `|q_usd| ≥ 1.0`: quote **only** the reduce side.
- `fv_tilt = clip(microprice_residual, ±tilt_max)` with `tilt_max` from cell.
- Clip notional: `min(clip_usd, 1.0 - |q_usd|)` when increasing inventory.

## 4. Cells (m fixed)

| Cell id | gamma | half_spread_floor_bps | tilt_max_bps | market |
|---------|-------|----------------------|--------------|--------|
| F1 | 0.1 | 2.0 | 0.5 | futures |
| F2 | 0.3 | 3.0 | 1.0 | futures |
| F3 | 0.5 | 4.0 | 1.5 | futures |
| S1 | 0.1 | 10.0 | 0.5 | spot |
| S2 | 0.3 | 12.0 | 1.0 | spot |
| S3 | 0.5 | 15.0 | 1.5 | spot |

`m = 6`. Bonferroni α = 0.05/6 if bootstrap used.

## 5. Costs (frozen)

- Futures maker fee: `0.0001` (bybit_futures_maker).
- Spot maker fee: `0.001` (bybit_spot_maker).
- Adverse selection haircut: `0.5 × half_spread` charged on every fill (pre-registered, not fitted).
- Futures funding: `inventory_usd * funding_rate_8h` accrued each sim bar marked as funding period.

## 6. Joint GO gates (ALL required per cell)

1. `n_clips ≥ 200` else `INSUFFICIENT_DATA` (does not shrink m)
2. `mean(clip_pnl_usd) > 0`
3. `profit_factor > 1.0`
4. `max_|q_usd| ≤ 1.0` (invariant — fail closed if violated)
5. `max_single_clip_usd ≤ 1.0`
6. Optional: bootstrap P(mean>0) ≥ 0.95 after Bonferroni — informational

## 7. Verdict rules

- **GO:** ≥1 cell clears all gates AND an adjacent same-market cell is same-sign on mean PnL.
- **NO_GO:** otherwise (expected).
- Promotion still requires frozen gate ≥30 RESOLVED + owner sign-off. Shadow probe only on GO.

## 8. Forbidden

- Reopen `APPROVED_PAPER_STRATEGIES` from this prereg alone
- Enable `ENABLE_DCA` / `ENABLE_REBALANCE`
- Use MCP AccBand score as FV tilt
- Post-hoc cell additions
- Cite Hyperliquid rebate anecdotes as local GO
- CONTROLLED_LIVE
