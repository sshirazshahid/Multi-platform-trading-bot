# PREREG 70 — Hourly L2 book-imbalance → forward drift (15 symbols)

**Status:** PRE-REGISTERED, hashed BEFORE any outcome computation. Any edit
after the hash is a NEW pre-registration.

**Date:** 2026-08-13. Owner directive: "Focus on making profitable trades.
Any coin any market any time."

## Novelty vs ledger
The refuted row "Quarter-hour clock-boundary opening order-imbalance"
(2026-07-23) covers **taker TRADE imbalance at 15-min clock boundaries** on
BTC/ETH. This screen tests a DIFFERENT signal — top-of-book **L2 depth
imbalance** aggregated hourly across 15 symbols — harvested locally since
2026-06-20 (`data/l2_history.jsonl`, ~25k obs) and never screened. Adjacent,
not covered; the adverse prior from that row is acknowledged and the
**expected outcome is NO_GO**.

## Hypothesis
H1: hours with strong book imbalance (signed) predict same-signed forward
returns at 1h/4h horizons, positive after taker costs. H0 (default): no.

## Data (frozen)
- Signal: `data/l2_history.jsonl` — fields `hour` (epoch s), `symbol`,
  `imbalance` ∈ [−1,1], `n_polls`. Keep rows with `n_polls ≥ 30`.
- Prices: `data/ohlcv_cache/<SYM>-USDT_1h.parquet` (`ts`,`close`), the same
  cache screen 41 used. Symbols missing a price file are skipped and NAMED.
- Span: full harvested window (2026-06-20 → present, ~53d).

## Event definition (frozen)
- Event at hour t when `|imbalance_t| ≥ θ`, θ ∈ {0.2, 0.3} (Stage-0 verified:
  2,338 / 1,020 raw triggers).
- **De-overlap:** after an event fires for a symbol, no new event for that
  symbol until the horizon expires (non-overlapping holds, first-fire wins).
- Direction: sign(imbalance) — long if bids heavier, short if asks heavier.
- Entry: close[t]; Exit: close[t+H], H ∈ {1, 4} hours. No SL/TP inside.

## Costs (frozen)
Taker round trip: 2 × (6 bps fee + 5 bps slip) = **22 bps** subtracted per
event (matches repo resolver constants; conservative vs screen-41's 30/60
grid because holds are shorter).

## Cells and multiplicity (frozen)
θ(2) × H(2) × {pooled ALL-15, majors-only BTC/ETH} (2) = **8 cells**.
Holm correction over m=8 at α=0.05. Bootstrap CI clusters by symbol-day
(1,000 resamples).

## Gates (frozen — all must pass jointly in a cell)
n ≥ 100 events; after-cost mean > 0; Holm-adjusted p < 0.05; OOS-WR ≥ 0.55
under a 70/30 chronological split; MC P(total>0) ≥ 0.95.

## Decision rule
Any cell passing ALL gates → CONFIRMED-candidate → adversarial audit before
anything else; a GO **never** changes runtime config — shadow probe only,
after audit + owner sign-off. Otherwise **NO_GO** → ledger row.

## Expected outcome
**NO_GO** — the adjacent trade-imbalance family showed no gross alpha even at
zero cost, and 53 days is one regime. Stated in advance; either outcome
accepted.
