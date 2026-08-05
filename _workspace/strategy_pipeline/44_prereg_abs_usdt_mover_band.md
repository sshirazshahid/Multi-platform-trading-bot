# 44 — Pre-registration: Absolute-USDT mover continuation vs fade (MEASUREMENT)

**Status:** FROZEN before any outcome computation  
**Date:** 2026-07-30  
**sha256_md:** f4b1a1087fd82f1eda1ccd8a9e01027600ae95ad876a1893b5ba4b61de55152a  
**Expectation:** lean-NO_GO for trade use; measurement may support veto/telemetry only

## Hypothesis (null)

Among liquid USDT-M perps whose absolute price move over horizon H₀ falls in **[$5, $200] USDT**, the sign of the move does **not** predict after-cost forward returns at horizons H∈{4h, 12h, 24h} (continuation) better than a coin-flip, and the opposite-sign fade rule likewise fails frozen gates.

## Why this is not a ledger reopen

| Nearby family | Differentiation |
|---------------|-----------------|
| Textbook breakout / TSMOM / RSI | No MA/RSI/channel entry; event = absolute dollar move band only |
| Shadow %-mover shortlist (2026-07-06) | That ranks by **%**; this screens **USDT abs move ∈ [5,200]** mid-priced band |
| AccBand | No MCP geometry; measurement lane only |

## Data (frozen)

| Field | Value |
|-------|-------|
| Universe | Liquid USDT-M perps with quoteVolume ≥ $5M (same floor as broad monitor) |
| Signal | `abs_usdt = \|close[t] − close[t−H₀]\|` with H₀ ∈ {1h, 24h, 7d} evaluated separately |
| Band | abs_usdt ∈ [5, 200] |
| Prices | `data/ohlcv_cache/{BASE}-USDT_1h.parquet` |
| Leakage | Signal uses bars ≤ t; forward return from close[t] → close[t+H] |

## Trade rules (screen accounting only — no live wire)

| Field | Value |
|-------|-------|
| Direction A (continuation) | Sign(close[t]−close[t−H₀]) |
| Direction B (fade) | −Sign(…) |
| Exit | Taker at t+H; H∈{4,12,24} hours |
| Costs | 20 bps RT primary / 40 bps stress + funding 1bp/8h default |
| Overlap | First event; skip until flat |

## Stage-0

Per (H₀ × side-rule × H): triggers ≥ 30 else INSUFFICIENT_DATA (Open ledger; not NO_GO).

## Gates (after Stage-0)

n_OOS≥30, mean net>0, OOS-WR≥0.55, MC P(>0)≥0.95, maxDD p95≤0.25, Holm across cells.

## Explicitly OUT of scope

Live MCP/AccBand wire; RSI/MA overlays; BTC-scale moves (>$200 abs); sub-$5 dust; spot-only path without separate prereg.

## Implement map

| Verdict | Action |
|---------|--------|
| INSUFFICIENT_DATA | Accrue OHLCV + mover shortlist snapshots |
| NO_GO | Ledger measurement/STOP row; keep band as **shadow shortlist filter only** |
| GO | Log-only shadow probe + owner sign-off before any paper OPEN path |
