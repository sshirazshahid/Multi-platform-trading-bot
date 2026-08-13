# PREREG 71 — Options-skew shock (Δrr25) → forward perp drift (BTC/ETH)

**Status:** PRE-REGISTERED, hashed BEFORE any outcome computation. Any edit
after the hash is a NEW pre-registration.

**Date:** 2026-08-13. Owner directive: "Focus on making profitable trades.
Any coin any market any time."

## Novelty vs ledger
No ledger row covers options risk-reversal skew. Gamma-expiry (C2) was
INSUFFICIENT_DATA on a different mechanism (dealer positioning at expiry);
this tests hourly **shocks in the 25-delta risk reversal** from the locally
harvested Deribit feed (`data/skew_history.jsonl`, since 2026-06-20) —
never screened. Mechanism: a sharp rr25 drop = puts bid = hedging-flow /
sentiment shock; H1 says it precedes same-signed perp drift.

## Hypothesis
H1: sign(Δrr25) predicts the sign of forward BTC/ETH perp returns at 4h/12h,
positive after taker costs. H0 (default): no.

## Data (frozen)
- Signal: `data/skew_history.jsonl` — `hour` (epoch s), `currency` ∈
  {BTC, ETH}, `rr25` (pts), `n_polls`. Keep `n_polls ≥ 6`. Δrr25 defined only
  on consecutive hours (gap ⇒ no event).
- Prices: `data/ohlcv_cache/{BTC,ETH}-USDT_1h.parquet` (`ts`,`close`).

## Event definition (frozen)
- Event at hour t when `|rr25_t − rr25_{t−1}| ≥ θ`, θ ∈ {0.5, 1.0} pts
  (Stage-0: 251/68 BTC, 261/59 ETH raw triggers).
- Direction: sign(Δrr25) — long on skew-up (calls bid), short on skew-down.
- De-overlap per currency: no new event until the horizon expires.
- Entry close[t]; exit close[t+H], H ∈ {4, 12}. No SL/TP inside.

## Costs (frozen)
22 bps taker round trip per event (repo resolver constants).

## Cells and multiplicity (frozen)
θ(2) × H(2) × currency(2, tested separately) = **8 cells**; Holm m=8, α=0.05.
Bootstrap CI clustered by calendar day (1,000 resamples). ALSO run (reported,
not gated) the contrarian direction −sign(Δrr25) as a sanity column; if the
contrarian side "passes," that is evidence of sign-instability, not a GO —
contrarian results can never authorize anything under this prereg.

## Gates (frozen — all jointly per cell)
n ≥ 30 (event-scarce feed, 53d); after-cost mean > 0; Holm p < 0.05;
OOS-WR ≥ 0.55 (70/30 chrono split); MC P(total>0) ≥ 0.95.

## Decision rule
PASS cell → adversarial audit required before anything else; GO never changes
runtime config — shadow probe only after audit + owner sign-off. Else
**NO_GO** → ledger row.

## Expected outcome
**NO_GO** — 53 days is one regime, n is small at θ=1.0, and options-flow
signals typically decay inside minutes, not hours. Stated in advance.
