# 02b rev2 — Screener Verdict: Candidate B (Post-listing perp short)

Agent: edge-screener · Date: 2026-07-09 · Supersedes: `02b_screener_listing_short.md` (INSUFFICIENT_DATA)
Data: local only (no synthetic). Funding backfilled real from Binance public endpoint.

---

## What changed since rev1 (and what did NOT)

The **frozen pre-registration is unchanged** — hypothesis (short day-1 close, exit at H∈{7,30,90}d),
mandatory funding-coverage gate, cost model (`config.FEE.futures_taker` 5bps + `config.SLIPPAGE`
5bps per side = 20bps round-trip), and frozen gates (DSR≥0.10, PBO≤0.5, OOS-WR≥0.55, walk-forward
embargo+purge, Monte-Carlo P(>0)≥0.95 / maxDD p95≤0.25). Nothing tuned post-hoc.

rev1 was INSUFFICIENT_DATA because **0/103** genuine listings had local funding, so the
pre-declared killer cost (a short PAYS negative funding) was unmeasurable. rev2 closes exactly
that gap, and also fixes the audit's **MEDIUM finding** (the screen never read the harvested store):

- `scripts/backfill_funding_history.py --leg listing` — keyless public ccxt
  `fetchFundingRateHistory`, Binance-only, the **screen's own** genuine listing bases
  (`genuine_listing_bases()`, not a hardcoded list), since 2025-06-01.
- `research/screen_listing_short.py` now **reads `data/funding_history/binance_{BASE}.csv`**,
  charges realized funding to the short leg per held settlement, and runs the frozen gate
  battery on the funding-charged sample. `has_coverage`/`window_funding_covered` consult the
  harvested store (audit MEDIUM finding resolved).

---

## Data coverage (measured, real)

| quantity | value |
|---|---|
| total `_1h` symbols | 525 |
| backfill-cluster timestamps (excluded, counted) | 8 ts → 419 symbols |
| genuine listings (idiosyncratic first candle, window, ≥30d) | 103 |
| genuine bases WITH Binance funding history (backfilled) | **92** |
| genuine bases NOT on Binance USDT-M (excluded, counted) | 11 (`BFUSD, BLEND, ELIZAOS, ELON, ES, NVDAX, RLUSD, SCOR, U, USDS, WHITEWHALE`) |
| Binance funding rows / base | min 269 · median 1632 · max 4144 |
| funding-charged listings USED (price+funding window spanned) | **88** (7d/30d), **75** (90d) |
| listings excluded — funding window not spanned (counted, never guessed) | 14–16 per horizon |

Coverage went from **0/103 → 88/103** funding-charged. The funding-charged sample is now large
enough to run every frozen gate. Verdict moves off INSUFFICIENT_DATA.

---

## The killer cost, now MEASURED

New-listing funding on the short is **systematically adverse but not fatal to the mean**:

| horizon | n | funding_sum mean (short) | funding_sum median | listings with NEGATIVE funding |
|---|---|---|---|---|
| 7d  | 88 | **−2.61%** | −0.66% | 69.3% |
| 30d | 88 | **−7.36%** | −2.06% | 67.0% |
| 90d | 75 | **−11.97%** | −3.72% | 69.3% |

~69% of listings carry net-negative funding over the hold → the short **pays** it, exactly the
pre-declared killer. It drags the 30d short by ~7.4 points and the 90d by ~12 points — real, and
it is now charged. But the post-listing price decline is larger, so the funding-charged mean short
return stays **positive** at every horizon.

---

## Frozen gate battery (funding-charged sample)

| gate | threshold | 7d | 30d | 90d |
|---|---|---|---|---|
| n (funding-charged) | ≥30 (MC floor) | 88 ✅ | 88 ✅ | 75 ✅ |
| short net mean (after ALL cost incl funding) | > 0 | **+8.4%** ✅ | **+21.4%** ✅ | **+30.8%** ✅ |
| win rate | ≥ 0.55 | 0.750 ✅ | 0.807 ✅ | 0.787 ✅ |
| beats control (equal-weight majors) | underperf > 0 | +9.5% ✅ | +19.3% ✅ | +15.9% ✅ |
| DSR (Pr[SR>0], n_trials=3) | ≥ 0.10 | 0.917 ✅ | 0.9994 ✅ | 0.9997 ✅ |
| PBO (CSCV across horizons) | ≤ 0.50 | 0.094 ✅ | 0.094 ✅ | 0.094 ✅ |
| OOS-WR (walk-forward, embargo+purge) | ≥ 0.55 | 0.824 ✅ | 0.882 ✅ | **NaN → fail-closed** ❌ |
| **Monte-Carlo P(total>0)** | ≥ 0.95 | 0.988 ✅ | 1.000 ✅ | 1.000 ✅ |
| **Monte-Carlo maxDD p95** | ≤ 0.25 | **3.30** ❌ | **2.32** ❌ | **2.47** ❌ |
| **MC capital-preservation gate** | pass | **FAIL** ❌ | **FAIL** ❌ | **FAIL** ❌ |

The edge is **statistically robust** — high positive mean, WR ~75–81%, DSR ≈ 1, PBO 0.094,
beats the majors basket, OOS-WR 0.82–0.88 (7d/30d) — yet it **fails the frozen
capital-preservation Monte-Carlo drawdown gate at every horizon**. The block-bootstrap p95
peak-to-trough on an equal-notional-per-listing short book is **230–330% of per-trade stake**:
a minority of listings rip upward against the short (short losses of −50% to −150%+), and a
cluster of them produces a multi-unit equity drawdown. Under the owner's capital-preservation
objective (and a ~$420 book), that tail is disqualifying — which is precisely what the MC gate
exists to catch. (90d additionally cannot form a purged walk-forward OOS-WR: the clustered
2025–26 listing dates make 90d hold-windows overlap so heavily that the time-based purge empties
every train fold → NaN → fail-closed.)

---

## VERDICT: NO_GO

The funding-charged, after-ALL-cost post-listing short has a real, robust price/funding edge that
**beats the control and passes DSR/PBO/WR/OOS-WR (7d,30d)**, but it **fails the frozen
capital-preservation Monte-Carlo drawdown bound (maxDD p95 2.3–3.3 ≫ 0.25) at all three
horizons**. NaN on any required gate fails closed. This is a merit NO_GO, not INSUFFICIENT_DATA —
the killer cost was measured and the gates ran on n=75–88. Qualifies for a `refuted-families-ledger`
row (refuted for *this* equal-notional, unhedged sizing under the capital-preservation gate).

### JSON verdict
```json
{
  "candidate": "B_post_listing_perp_short",
  "hypothesis": "Short newly-listed USDT perp at day-1 close for H in {7,30,90}d; post-listing decay nets a positive after-ALL-cost (incl realized funding) return and beats the market.",
  "n": {
    "total_1h_symbols": 525,
    "excluded_backfill_cluster": 419,
    "genuine_listings": 103,
    "funding_covered_used": {"7d": 88, "30d": 88, "90d": 75},
    "not_on_binance_excluded": 11
  },
  "after_cost_metrics": {
    "cost_model": "20bps round-trip (config.FEE.futures_taker 5bps + config.SLIPPAGE 5bps x2 sides) + realized funding charged to the short leg from data/funding_history",
    "funding_sum_mean_short": {"7d": -0.0261, "30d": -0.0736, "90d": -0.1197},
    "funding_negative_rate": {"7d": 0.693, "30d": 0.670, "90d": 0.693},
    "short_net_mean": {"7d": 0.0837, "30d": 0.2138, "90d": 0.3082},
    "win_rate": {"7d": 0.750, "30d": 0.807, "90d": 0.787},
    "beats_control_underperf_mean": {"7d": 0.0951, "30d": 0.1927, "90d": 0.1589}
  },
  "gates": {
    "DSR": {"7d": 0.917, "30d": 0.9994, "90d": 0.9997, "threshold": 0.10, "pass": true},
    "PBO_across_horizons": {"value": 0.094, "threshold": 0.50, "pass": true},
    "OOS_WR_walk_forward": {"7d": 0.824, "30d": 0.882, "90d": "NaN(fail-closed)", "threshold": 0.55},
    "monte_carlo": {
      "p_total_positive": {"7d": 0.988, "30d": 1.0, "90d": 1.0},
      "max_drawdown_p95": {"7d": 3.30, "30d": 2.32, "90d": 2.47},
      "threshold_maxDD_p95": 0.25,
      "passes": {"7d": false, "30d": false, "90d": false}
    },
    "fail_closed_on_nan": true
  },
  "verdict": "NO_GO",
  "blocking_reason": "Robust price+funding edge (mean +8.4%/+21.4%/+30.8%, WR 75-81%, DSR~1, PBO 0.094, beats control) but FAILS the frozen capital-preservation Monte-Carlo drawdown gate (maxDD p95 2.3-3.3 >> 0.25) at every horizon; 90d OOS-WR also fail-closed (NaN from purge on overlapping windows). Killer funding cost measured: ~69% of listings pay negative funding, dragging the short -2.6% to -12%.",
  "artifacts": {
    "backfill": "scripts/backfill_funding_history.py (leg=listing)",
    "screen": "research/screen_listing_short.py",
    "tests": "tests/test_screen_listing_short.py (16 pass)",
    "verdict": "_workspace/strategy_pipeline/02b_rev2_screener_listing_short.md"
  }
}
```

### Honest caveats (for the auditor)
1. **MC maxDD units.** The gate is applied to the strategy's per-listing return series (a "trade"
   = one listing short, unit notional). At a capital-scaled per-trade fraction (e.g. CLAUDE.md's
   3% max) the *capital* drawdown would be far smaller — but rescaling is a modeling choice **not
   in the frozen pre-registration**, which fed the gate the strategy's per-trade returns. I did
   **not** rescale to manufacture a pass; the frozen gate as written FAILS. A capital-scaled,
   position-capped re-pre-registration is the honest way to revisit this, not a reinterpretation.
2. **Survivorship.** The OHLCV cache holds only currently-listed perps; listings delisted after
   listing are absent (direction ambiguous, likely optimistic for the short — undisclosed names
   were often the ones that mooned or died).
3. **Execution realism.** 5bps slippage is optimistic for brand-new illiquid perps; day-1
   shortability/borrow limits and position caps are unmodeled — all cut against the diagnostic,
   never for it. The MC drawdown finding is therefore a floor on the tail risk, not a ceiling.
4. **11 not-on-Binance and 14–16 window-uncovered listings remain EXCLUDED and COUNTED** — never
   charged a guessed funding number. To fold in Bybit/Bitget-only listings, extend the backfill's
   listing leg to those venues (separate change; would enlarge n, not change the MC drawdown
   character which is intrinsic to per-listing dispersion).
```
