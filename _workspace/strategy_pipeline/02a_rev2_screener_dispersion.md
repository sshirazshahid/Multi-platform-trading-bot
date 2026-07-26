# 02a rev2 — Screener Verdict: Candidate A (Cross-venue funding-rate dispersion)

Agent: edge-screener · Date: 2026-07-09 · Supersedes: `02a_screener_dispersion.md` (INSUFFICIENT_DATA)
Data root: `data/funding_history/` (backfilled real funding), fallback `data/funding_carry/`

---

## What changed since rev1 (and what did NOT)

The **frozen pre-registration is unchanged** — hypothesis, universe, cost model, and gate
thresholds are exactly as written in `02a_screener_dispersion.md` (DSR≥0.10, PBO≤0.50,
OOS-WR≥0.55, walk-forward embargo+purge, Monte-Carlo P(total>0)≥0.95 / maxDD p95≤0.25;
floor = ≥60 aligned settlements/coin on ≥2 coins). Nothing was tuned post-hoc.

rev1 was INSUFFICIENT_DATA because the only cross-venue funding on disk was 16 aligned 8h
settlements (5 days) on BTC/ETH. rev2 closes that gap with **real** historical funding:

- `scripts/backfill_funding_history.py` — keyless public ccxt `fetchFundingRateHistory`
  (ccxt 4.5.64), F1 15-coin universe × 3 venues, paginated as far back as each venue serves
  (forward from 2021-01-01, with a **backward `until` fallback** for venues that return empty
  when `since` predates a symbol's listing — this is what recovered Bybit's late-listed coins).
- `research/screen_funding_dispersion.py` now reads `data/funding_history/{venue}_{coin}.csv`
  (falls back to `funding_carry` when absent) and runs the **frozen gate battery on
  walk-forward OOS carry** at n≥floor — see "The amortization trap" below.

---

## Local data coverage (measured, real)

Per venue-symbol: **rows | first_ts (UTC) | last_ts (UTC)**. Full table across all 15 F1 coins.

| coin | binance | bybit | bitget |
|---|---|---|---|
| BTC | 6045 · 2021-01-01 → 2026-07-06 | 6044 · 2021-01-01 → 2026-07-06 | 100 · 2026-06-03 → 2026-07-06 |
| ETH | 6045 · 2021-01-01 → 2026-07-06 | 6044 · 2021-01-01 → 2026-07-06 | 100 · 2026-06-03 → 2026-07-06 |
| BNB | 6045 · 2021-01-01 → 2026-07-06 | 5507 · 2021-06-29 → 2026-07-06 | 100 · 2026-06-03 → 2026-07-06 |
| SOL | 6120 · 2021-01-01 → 2026-07-06 | 5867 · 2021-06-29 → 2026-07-06 | 100 · 2026-06-03 → 2026-07-06 |
| LINK | 6045 · 2021-01-01 → 2026-07-06 | 6044 · 2021-01-01 → 2026-07-06 | 100 · 2026-06-03 → 2026-07-06 |
| TRX | 6045 · 2021-01-01 → 2026-07-06 | 5317 · 2021-09-01 → 2026-07-06 | 100 · 2026-06-03 → 2026-07-06 |
| SUI | 3487 · 2023-05-03 → 2026-07-06 | 3488 · 2023-05-03 → 2026-07-06 | 100 · 2026-06-03 → 2026-07-06 |
| ATOM | 6045 · 2021-01-01 → 2026-07-06 | 5195 · 2021-10-11 → 2026-07-06 | 100 · 2026-06-03 → 2026-07-06 |
| DOGE | 6045 · 2021-01-01 → 2026-07-06 | 5587 · 2021-06-02 → 2026-07-06 | 100 · 2026-06-03 → 2026-07-06 |
| ALGO | 6045 · 2021-01-01 → 2026-07-06 | 5249 · 2021-09-23 → 2026-07-06 | 100 · 2026-06-03 → 2026-07-06 |
| ZEC | 6045 · 2021-01-01 → 2026-07-06 | 5063 · 2021-11-24 → 2026-07-06 | 65 · 2026-06-15 → 2026-07-06 |
| ADA | 6045 · 2021-01-01 → 2026-07-06 | 5816 · 2021-03-18 → 2026-07-06 | 100 · 2026-06-03 → 2026-07-06 |
| XRP | 6045 · 2021-01-01 → 2026-07-06 | 5647 · 2021-05-13 → 2026-07-06 | 100 · 2026-06-03 → 2026-07-06 |
| LTC | 6045 · 2021-01-01 → 2026-07-06 | 6044 · 2021-01-01 → 2026-07-06 | 100 · 2026-06-03 → 2026-07-06 |
| GRT | 6045 · 2021-01-01 → 2026-07-06 | 5026 · 2021-12-06 → 2026-07-06 | 100 · 2026-06-03 → 2026-07-06 |

**Binding constraint (honest):** Bitget's public endpoint serves only ~**100** most-recent
funding records regardless of `since` (confirmed even with ccxt `paginate=True`; a true
venue/ccxt limit, not a harvest bug). Binance and Bybit carry 3–6 k settlements. The
pre-registered `align_cross_venue` inner-joins **all** available venues on the common
settlement grid, so every coin's cross-venue window is Bitget-bound to **100 aligned 8h
settlements (~33 days, 2026-06-03 → 2026-07-06)** — ZEC to 65 (Bitget started 2026-06-15).

All 15 coins clear the pre-registered floor (≥60 settlements/coin on ≥2 coins). Floor cleared
→ **the frozen gates are now EVALUABLE** and the verdict moves off INSUFFICIENT_DATA.

---

## The amortization trap (why the gate battery is decisive, not the headline net)

The pre-registered "after-cost net carry" (`net_return_frac` = Σcarry − ONE 4-leg round-trip,
direction = in-sample mean pick) turns **positive** for a few pairs at large n — e.g. ADA
binance-L/bybit-S nets +3.4 bps (maker). That is **not** an edge: an in-sample direction pick
held statically while amortizing a single round-trip over 100 settlements collects Σ|mean diff|
that grows with n and eventually clears a fixed cost. This is exactly the overfitting front
door. The frozen gates run on **walk-forward OOS carry** (`walk_forward_oos_spread`): direction
chosen on each TRAIN fold, applied UNSEEN to TEST, the full 4-leg round-trip charged **per
fold**. That is what actually decides GO/NO_GO.

---

## Frozen gate battery (walk-forward OOS, best pair after n_trials penalty)

- Variants tried: **45 pair-strategies** (15 coins × 3 venue-pairs) → DSR multiplicity
  `n_trials = 45`; 90 total configs counting the maker/taker sensitivity.
- **Every** pair's OOS mean carry is NEGATIVE. Best pair = GRT binance-L/bitget-S, OOS mean
  **−1.27 bps**, OOS-WR **0.125**, OOS Sharpe −1.29 (n_oos = 80).

| Gate | Threshold | Result | Pass |
|---|---|---|---|
| after-cost net carry > 0 (in-sample, amortized) | > 0 | true (a few pairs) | — (lookahead artifact) |
| **OOS mean carry > 0** | > 0 | **−1.27 bps (best)** | ❌ |
| **DSR** | ≥ 0.10 | **0.00** | ❌ |
| PBO | ≤ 0.50 | 0.0006 | ✅ |
| **OOS-WR** | ≥ 0.55 | **0.125** | ❌ |
| **Monte-Carlo** P(total>0) | ≥ 0.95 | **0.00** | ❌ |
| Monte-Carlo maxDD p95 | ≤ 0.25 | 0.0115 | ✅ (carry is ~1e-4 scale) |

The strategy is on the **wrong side 87.5%** of OOS settlements: the cross-venue funding
differential's sign is not persistent (sign-persistence ~0.49–0.65), so a train-chosen static
direction reverses out-of-sample. DSR 0.0 and MC P(>0) 0.0 are unambiguous. A longer
binance∩bybit-only window would not rescue it — the failure is sign non-persistence, not
sample length; the per-settlement carry (~0.3 bps) is dwarfed by the amortized round-trip
(42 bps ÷ 20-settlement folds ≈ 2.1 bps/settlement) unless the sign persists, and it does not.

---

## VERDICT: NO_GO

The cross-venue funding-dispersion edge does **not** survive walk-forward out-of-sample after
cost. This is a merit NO_GO (the gates ran on a floor-clearing sample and failed), not an
INSUFFICIENT_DATA fail-closed. Qualifies for a `refuted-families-ledger` row.

### JSON verdict
```json
{
  "candidate": "A_cross_venue_funding_dispersion",
  "hypothesis": "Delta-neutral long-low/short-high venue funding differential clears the 4-leg after-cost hurdle out-of-sample",
  "n": {"per_coin_aligned_settlements": {"most": 100, "ZEC": 65}, "coins": 15, "min_floor": 60},
  "sample_days": {"most": 33.0, "ZEC": 21.33, "window": "2026-06-03..2026-07-06 (Bitget-bound)"},
  "coins_cross_venue": ["ADA","ALGO","ATOM","BNB","BTC","DOGE","ETH","GRT","LINK","LTC","SOL","SUI","TRX","XRP","ZEC"],
  "true_variants_tried": 90,
  "n_trials_dsr": 45,
  "after_cost_metrics": {
    "cost_model": "config.FEE per venue + 4x5bps slippage; taker=honest default; round-trip charged per walk-forward fold",
    "in_sample_amortized_net_positive_for_some_pairs": true,
    "in_sample_note": "positive only via direction-lookahead + single-roundtrip amortization (the trap)",
    "best_pair_oos": "GRT binance-L/bitget-S",
    "best_pair_oos_mean_bps": -1.27,
    "best_pair_oos_wr": 0.125,
    "all_pairs_oos_mean_negative": true
  },
  "gates": {
    "OOS_mean_gt_0": false,
    "DSR": 0.0,
    "DSR_pass": false,
    "PBO": 0.0006,
    "PBO_pass": true,
    "OOS_WR": 0.125,
    "OOS_WR_pass": false,
    "monte_carlo": {"p_total_positive": 0.0, "max_drawdown_p95": 0.0115, "passes": false},
    "walk_forward": "embargo+purge, 4 folds, direction on train applied OOS",
    "fail_closed_on_nan": true
  },
  "verdict": "NO_GO",
  "reason": "Floor cleared (15 coins x 100 aligned settlements >= 60). Walk-forward OOS carry is negative for every one of 45 pair-strategies (best -1.27bps, OOS-WR 0.125); DSR 0.0, MC P(>0) 0.0. The in-sample amortized net was positive only via the direction-lookahead/amortization trap. Cross-venue funding-spread sign is not persistent OOS."
}
```

### Honest limitations (for the auditor)
1. **Bitget 100-record cap binds the window to 33 days.** Binance/Bybit have years, but the
   pre-registered all-venue inner join truncates to Bitget's 100. The floor (60) is still
   cleared and the OOS signal is unambiguous (DSR 0.0, OOS-WR 0.125, MC P(>0) 0.0). A
   binance∩bybit-only screen is a *different, unregistered* universe; the sign-persistence
   failure argues it would not flip, but it was not run.
2. **In-sample amortized net > 0 is disclosed as a trap, not an edge** — the walk-forward OOS
   overrides it, exactly as the pre-registration intended ("all frozen gates pass on
   walk-forward OOS").
3. **Static-hold model:** one round-trip per fold assumes no intra-fold rebalancing; a real
   harvester would pay more (re-rolls), making the after-cost result *worse*, never better.

### Artifacts
- Backfill: `scripts/backfill_funding_history.py` (leg=dispersion)
- Screen: `research/screen_funding_dispersion.py` · Tests: `tests/test_screen_funding_dispersion.py` (19 pass)
- Data (git-ignored): `data/funding_history/{binance,bybit,bitget}_{coin}.csv`
- This verdict: `_workspace/strategy_pipeline/02a_rev2_screener_dispersion.md`
