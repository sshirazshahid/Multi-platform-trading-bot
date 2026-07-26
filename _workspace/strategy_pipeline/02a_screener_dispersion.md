# 02a — Screener Verdict: Candidate A (Cross-venue funding-rate dispersion)

Agent: edge-screener · Date: 2026-07-09 · Data root: `data/funding_carry/`, `data/derivs_history.jsonl`, `data/funding_cache/`

---

## PRE-REGISTRATION (written before any screen code ran)

**Hypothesis.** For a given coin, structural per-settlement funding-rate differences
between two venues can be harvested delta-neutral: LONG the perp on the low-funding
venue, SHORT the perp on the high-funding venue, holding across settlements to collect
the signed funding differential. Net edge exists only if the accumulated differential
clears the amortized FOUR-leg round-trip cost (2 opens + 2 closes) plus slippage plus
any funding paid by the leg that pays.

**Universe (pre-registered).** Every coin with funding history on ≥2 of our three venues
(binance, bybit, bitget) over a common, settlement-aligned window. The ONLY dataset
carrying per-venue funding for the same coin is `data/funding_carry/{venue}_{coin}.csv`
(inventory determines the real universe; single-venue datasets below are ineligible for a
cross-venue screen and are declared as such).

**Sample period.** Whatever common aligned window the cross-venue files actually cover
(measured, not assumed).

**Cost model (charge ALL of it — pre-registered).**
- Fees from `config.FEE` per venue, per fill: futures maker binance 2.0bps / bybit 1.0bps /
  bitget 2.0bps; futures taker binance 5.0bps / bybit 6.0bps / bitget 6.0bps.
- Round trip = 4 fills: open-long-L, open-short-H, close-long-L, close-short-H →
  `RT = 2·fee_L + 2·fee_H + 4·slippage`.
- Slippage from `config.SLIPPAGE`: 5.0bps per open/close fill (charged on all 4 fills).
- Honest DEFAULT = taker/taker both legs (repo baseline, `F1_EXECUTION_MODE=taker`).
  Best-case maker/maker reported only as a sensitivity ("touch ≠ fill" — maker fills are an
  UNVERIFIED optimistic assumption).
- Funding is the signal itself: per settlement the SHORT leg receives `rate_H`, the LONG
  leg pays `rate_L`; net collected = `rate_H − rate_L`. When the differential flips against
  the held direction, the pair PAYS. Charged from realized per-settlement rates, not averages.

**Frozen gate thresholds (from `core/promotion_gate.py` — never loosened).**
DSR ≥ 0.10 · PBO ≤ 0.50 · OOS-WR ≥ 0.55 (+ walk-forward with embargo+purge; Monte Carlo
P(total>0) ≥ 0.95, maxDD p95 ≤ 0.25). NaN fails closed.

**Pre-registered minimum sample to EVALUATE the gates.** The frozen gates require a
walk-forward (train/test folds with embargo+purge) and a Monte Carlo block bootstrap. I
pre-register a floor of **≥ 60 aligned common settlements per coin (≈ 20 days) on ≥ 2 coins**
as the minimum at which those gates are computable in good faith. Below that floor the
statistical gates are declared NOT EVALUABLE and fail closed → verdict INSUFFICIENT_DATA.
Computing a DSR/PBO on a handful of points would be the overfitting front door this repo
has spent months closing; I will not do it.

**What NO_GO looks like (pre-registered).** Sample ≥ floor AND (after-cost net carry ≤ 0
across the venue-pairs) OR any frozen gate fails (DSR < 0.10, or PBO > 0.50, or OOS-WR <
0.55, or MC P(total>0) < 0.95). Best-case maker fees still net-negative = NO_GO.

**What INSUFFICIENT_DATA looks like (pre-registered).** Cross-venue overlap below the floor
(too few aligned settlements and/or < 2 coins and/or a single short window). Report the
exact harvest command to extend coverage; never synthesize data.

**What GO looks like (pre-registered).** Sample ≥ floor AND after-cost net carry > 0 on the
honest taker cost model AND all frozen gates pass on walk-forward OOS.

---

## RESULTS

**VERDICT: INSUFFICIENT_DATA** (frozen gates NOT EVALUABLE — fail closed).
Secondary, supporting-only finding: on the tiny sample that exists, the edge is
after-cost negative in every configuration.

### Local data coverage (measured, honest)
| Dataset | Cross-venue? | Coins | Venues | Window | Verdict for this screen |
|---|---|---|---|---|---|
| `data/funding_carry/{venue}_{coin}.csv` | **YES** | BTC, ETH only | binance, bybit, bitget | **16 aligned 8h settlements each = exactly 5.0 days** (2026-07-04 → 2026-07-09) | The ONLY eligible dataset |
| `data/derivs_history.jsonl` | **NO** | 8 (BTC,ETH,SOL,BNB,XRP,DOGE,LINK,ADA) | single (no venue/exchange field; one `funding` value/snapshot) | 40.8 days | Ineligible — single-venue |
| `data/funding_cache/*.parquet` | **NO** | 31 | single (cols = `ts,rate` only) | ~3 years | Ineligible — single-venue |
| `data/funding_oi/*.csv` | **NO** | 5 (BNB,BTC,ETH,SOL,XRP) | single (Binance) | 2019+ | Ineligible — single-venue |

The cross-venue overlap is **2 coins × 16 settlements = 32 observations over a single
5-day window** — well below the pre-registered floor (≥60 settlements/coin, ≥2 coins).
The derivs harvester carries only ONE funding number per coin per hour (verified: no
`venue`/`exchange`/`source` key across all 5,563 rows), so it does NOT widen the
cross-venue picture despite its longer history. This is the honest bottleneck.

### After-cost screen on the available 5-day sample (supporting evidence only)
Cost model: `config.FEE` per venue + 5bps slippage × 4 fills. Direction chosen
in-sample (long the lower-mean-funding venue) — an optimistic lookahead; it still loses.

| coin | long / short | mean\|diff\|/8h | carry/8h | gross (5d) | RT taker | RT maker | **net taker** | **net maker** | APR maker | breakeven hold |
|---|---|---|---|---|---|---|---|---|---|---|
| BTC | bybit / binance | 0.38bps | 0.24bps | 3.9bps | 42bps | 26bps | **−38.1bps** | **−22.1bps** | −16.2% | 108 settles (36d) |
| BTC | bitget / binance | 0.81bps | 0.61bps | 9.7bps | 42bps | 28bps | **−32.3bps** | **−18.3bps** | −13.4% | 46 settles (15d) |
| BTC | bitget / bybit | 0.77bps | 0.36bps | 5.8bps | 44bps | 26bps | **−38.1bps** | **−20.1bps** | −14.7% | 71 settles (24d) |
| ETH | bybit / binance | 0.32bps | 0.07bps | 1.1bps | 42bps | 26bps | **−40.9bps** | **−24.9bps** | −18.2% | 372 settles (124d) |
| ETH | bitget / binance | 0.46bps | 0.19bps | 3.0bps | 42bps | 28bps | **−39.0bps** | **−25.0bps** | −18.2% | 148 settles (49d) |
| ETH | bitget / bybit | 0.49bps | 0.12bps | 1.9bps | 44bps | 26bps | **−42.1bps** | **−24.1bps** | −17.6% | 217 settles (72d) |

**Reading:** the per-settlement funding differential (0.07–0.61 bps/8h) is one to two
orders of magnitude smaller than the amortized 4-leg round-trip hurdle (26–44 bps).
Best case anywhere — BTC bitget-long/binance-short at maker fees — is still **−18.3 bps**
net, and would need to hold ~46 settlements (~15 days) just to break even on cost while the
differential itself mean-reverts and flips (sign-persistence 0.53–0.73 over the 5-day window).
All 12 configurations (6 venue-pairs × 2 fee models) are net-negative. The 2× notional
delta-neutral capital footprint at a $420 book makes the fee drag structurally worse, not better.

### Frozen gate status
| Gate | Threshold | Result |
|---|---|---|
| DSR | ≥ 0.10 | **NOT EVALUABLE** (n=16 < floor; computing it would be dishonest) → fail closed |
| PBO | ≤ 0.50 | **NOT EVALUABLE** → fail closed |
| OOS-WR | ≥ 0.55 | **NOT EVALUABLE** (no walk-forward folds possible at n=16) → fail closed |
| Walk-forward (embargo+purge) | required | **NOT EVALUABLE** — 16 points cannot form train/test folds with embargo |
| Monte Carlo (P(total>0)≥0.95, maxDD p95≤0.25) | required | **NOT EVALUABLE** — block bootstrap needs a meaningful sample |

True variant count reported: **12** (6 aligned venue-pairs × 2 fee models). DSR/PBO would
have to be penalized against these 12 plus the in-sample direction pick — but the sample is
too small to compute them at all; they fail closed rather than being fabricated.

### To convert this to a screenable verdict — exact harvest command (no synthetic data)
```
venv\Scripts\python.exe scripts\harvest_funding_carry.py
```
Single-pass appender (1 row per venue/coin/settlement). Schedule HOURLY:
```
schtasks /Create /TN TradingBot-FundingCarryHarvest /SC HOURLY /TR ^
  "cmd /c cd /d D:\Downloads\Trading_Bot && venv\Scripts\python.exe scripts\harvest_funding_carry.py"
```
Accumulate to the ≥60-settlement floor (~20+ calendar days) before re-screening BTC/ETH.
To broaden the cross-venue universe beyond BTC/ETH, the `COINS` tuple in
`scripts/harvest_funding_carry.py` must be extended (separate change, out of this screen's scope).

### JSON verdict
```json
{
  "candidate": "A_cross_venue_funding_dispersion",
  "hypothesis": "Delta-neutral long-low/short-high venue funding differential clears the 4-leg after-cost hurdle",
  "n": {"BTC": 16, "ETH": 16},
  "sample_days": {"BTC": 5.0, "ETH": 5.0},
  "coins_cross_venue": ["BTC", "ETH"],
  "single_venue_datasets_ineligible": ["derivs_history.jsonl", "funding_cache/*.parquet", "funding_oi/*.csv"],
  "true_variants_tried": 12,
  "after_cost_metrics": {
    "cost_model": "config.FEE per venue + 4x5bps slippage; taker=honest default, maker=best-case sensitivity",
    "best_config": "BTC long bitget / short binance",
    "best_config_net_bps_maker": -18.3,
    "best_config_net_bps_taker": -32.3,
    "all_negative_after_cost": true,
    "mean_carry_per_8h_bps_range": [0.07, 0.61],
    "roundtrip_hurdle_bps_range": [26, 44]
  },
  "gates": {
    "DSR": "NOT_EVALUABLE (n<floor)",
    "PBO": "NOT_EVALUABLE (n<floor)",
    "OOS_WR": "NOT_EVALUABLE (n<floor)",
    "walk_forward": "NOT_EVALUABLE (n<floor)",
    "monte_carlo": "NOT_EVALUABLE (n<floor)",
    "fail_closed": true
  },
  "verdict": "INSUFFICIENT_DATA",
  "reason": "Cross-venue funding exists on only 2 coins (BTC, ETH) over 16 aligned 8h settlements = 5.0 days, below the pre-registered floor of >=60 settlements/coin. Frozen gates fail closed. Supporting-only: all 12 configs are net-negative after cost even at best-case maker fees with an in-sample direction pick.",
  "harvest_to_extend": "venv\\Scripts\\python.exe scripts\\harvest_funding_carry.py (schedule HOURLY; reach >=60 settlements/coin ~20+ days before re-screening; extend COINS tuple to broaden beyond BTC/ETH)"
}
```

### Artifacts
- Screen script: `research/screen_funding_dispersion.py`
- Tests (12 passing): `tests/test_screen_funding_dispersion.py`
- This verdict: `_workspace/strategy_pipeline/02a_screener_dispersion.md`

### For the honesty-auditor
- Challenge 1 (sample): 32 observations, one 5-day window, 2 coins — is INSUFFICIENT_DATA the
  correct call vs forcing a NO_GO? I claim yes: NO_GO implies the gates ran and failed on merit;
  here they are not computable, so the honest label is INSUFFICIENT_DATA with the harvest path.
- Challenge 2 (cost model): slippage charged 4×5bps + full per-venue taker as the honest default.
  If you argue maker-first is realistic, the maker sensitivity is shown and is STILL −18 to −25 bps.
- Challenge 3 (direction lookahead): direction was chosen in-sample (optimistic). Removing the
  lookahead can only make the after-cost result worse, never better.
- Challenge 4 (derivs_history): I claim it is single-venue. Evidence: 5,563 rows, zero
  `venue`/`exchange`/`source` keys, one scalar `funding` per coin/snapshot. If you can point to a
  per-venue funding field I missed, the universe widens to 8 coins over 40 days and I re-screen.
