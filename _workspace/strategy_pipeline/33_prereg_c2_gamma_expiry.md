# 33 — Pre-registration: C2 gamma-expiry reversal (BTC perp, daily Deribit expiry)

**Status:** FROZEN before any outcome computation  
**Date:** 2026-07-24  
**Candidate:** C2 from `18_final_pair_verdicts.json` (2026-07-22 dual-model adjudication:
INSUFFICIENT_DATA; sanctioned reopen = "chain archive or ≥30 forward events" — this prereg
implements that path; the 08:00-reversal substitute WITHOUT options conditioning remains
forbidden per the adjudication)  
**Evidence trigger:** Weiss et al., *Finance Research Letters* 107 (Sep 2026) 110340 —
peer-reviewed intraday BTC reversal around daily Deribit expirations, concentrated on
ATM-OI>p90 days, strongest under negative cumulative gamma exposure
([doi:10.1016/j.frl.2026.110340](https://doi.org/10.1016/j.frl.2026.110340))  
**Expectation:** lean-NO_GO (reversal magnitude vs 10–28 bps round-trip is the likely kill)

## Hypothesis (null)

Fading the pre-expiry BTC move into the daily 08:00 UTC Deribit expiry does **not** produce
positive after-cost expectancy on Binance BTCUSDT perp, incrementally and unconditionally, on
forward data collected after this prereg's hash — including on the paper's conditioned cell
(ATM-OI>p90 ∧ GEX<0).

## Data (frozen)

| Field | Value |
|-------|-------|
| Signal data | Self-archived Deribit BTC option chain snapshots, `data/deribit_chain_snapshots/` (07:30 + 19:30 UTC daily, started 2026-07-24 — ALL outcome data is post-prereg forward) |
| Price data | Binance USDT-M BTCUSDT 1m OHLCV (ccxt) |
| Funding | Binance BTCUSDT 08:00 UTC settlement from funding history (the hold window crosses it — always charged) |
| Event | Every UTC day with a valid 07:30 snapshot AND 1m bars covering 07:00–09:30 |

## Signal construction (frozen — do not change after hash)

1. **Snapshot:** the 07:30 UTC snapshot of the full BTC option chain (all expiries).
2. **Underlying S:** median `underlying_price` across chain rows in that snapshot.
3. **ATM OI (today's expiry):** sum of `open_interest` over instruments expiring at TODAY's
   08:00 UTC with strike within ±2.5% of S.
4. **ATM-OI percentile:** rank of today's ATM OI within the trailing 60 daily 07:30 snapshots
   (minimum 30 history days before the conditioned cell is computable; expanding until 60).
5. **Net GEX (full chain):** Σ over all chain rows of
   `gamma_B76(mark_iv, K, T, S) × open_interest × (+1 if call, −1 if put)`, Black-76 gamma,
   T from instrument expiry vs snapshot time, r=0. Sign is the signal; units irrelevant.
6. **Conditioned day:** ATM-OI percentile > 0.90 AND net GEX < 0.
7. **Pre-expiry move:** log(close_0750 / close_0700) from 1m closes. Skip event if
   |move| < 10 bps (noise floor) or if any required bar/snapshot is missing (fail-closed skip,
   logged).

## Trade rules (frozen)

- **Entry:** 07:50 UTC taker at the 07:50 1m close.
- **Direction — fade (primary):** short if pre-expiry move > 0, long if < 0.
  **Follow (multiplicity control arm):** the opposite.
- **Exit:** 09:30 UTC taker at the 09:30 1m close (100-min bounded hold; covers the paper's
  ~90-min post-expiry reversal window). No SL/TP inside the window — the horizon is the risk
  bound, per event-study convention; per-event loss is capped by the 100-min BTC move.
- **Sizing (accounting):** 1R = notional stake; results in R units and bps on stake.
- **One event per day; no overlap possible.**

## Variants (joint multiplicity — n_trials = 4)

{conditioned, unconditioned} × {fade, follow}. Holm correction on after-cost mean > 0 tests.
Contrarian/follow cells are controls — a follow-only pass is NOT promotable.

## Costs (charge all, per side)

- Taker fee: `config.FEE["binance"]["futures_taker"]` (5 bps default).
- Slippage: 5 bps open + 5 bps close.
- Funding: BTCUSDT 08:00 settlement at position side, always in-window.
- Stress leg (reported): 1.5× fee + 2× slippage must keep the passing cell's mean > 0.

## Gates (frozen — never loosen)

| Gate | Threshold |
|------|-----------|
| MIN_DSR (n_trials=4) | ≥ 0.10 |
| MAX_PBO | ≤ 0.5 |
| OOS-WR | ≥ 0.55 |
| MC P(total>0) | ≥ 0.95 |
| MC maxDD p95 | ≤ 0.25 |
| Min n per cell | ≥ 30 else INSUFFICIENT_DATA |
| Fold stability | first-half vs second-half mean same-sign (all data is forward; halves are the fold diagnostic) |

## Verdict rules

- **GO:** conditioned-fade passes ALL gates AND unconditioned-fade mean is same-sign
  (anti-conditioning-mining) AND stress leg holds.
- **NO_GO:** any gate failure on conditioned-fade, or follow-only pass, or stress-leg flip.
- **INSUFFICIENT_DATA:** n < 30 in the conditioned cell (expected for ~3–4 months) — the
  unconditioned cells (n ≥ 30 in ~4–6 weeks) may be reported early but do NOT authorize a GO.

## Adjacency disclosure (does not reopen)

- Hour-of-day seasonality is REFUTED (2026-06-02): this candidate is event-anchored to the
  option-expiry mechanism with options-state conditioning, not clock-hour mining. If the
  conditioned and unconditioned cells are statistically indistinguishable, the construct
  collapses toward refuted seasonality → NO_GO by the anti-conditioning-mining rule.
- Quarter-hour imbalance is REFUTED (2026-07-23): different construct (order-flow at QH
  boundaries); no shared machinery.

## Non-goals

- No live install, no shadow probe, no MCP/live-path change from this prereg alone.
- Any GO → log-only shadow probe first → frozen `core/promotion_gate.py` + owner sign-off.
- Directional VPIN, max-pain pinning, standalone DVOL/skew remain refused.

## Artifacts

- This file + `33_prereg_c2_gamma_expiry.json` (sha256 of this markdown recorded before any
  outcome computation)
- Harvester: `scripts/harvest_deribit_chain_snapshots.py` (schtasks `TradingBot_DeribitChainSnap_AM/_PM`)
- Screen outputs (later): `33_screen_c2_gamma_expiry.{md,json}`
