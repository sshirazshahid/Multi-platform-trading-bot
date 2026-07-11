# 08c — Screen: Quarterly-Futures Basis Leg-Swap for F1

Screener: edge-screener | Phase 2 of strategy-evidence-pipeline | 2026-07-11
Candidate 3 of 07_scout_candidates_2026-07-11.md. Research paths only; no live code touched.

---

## PRE-REGISTRATION (frozen 2026-07-11, written BEFORE the feasibility probe — never edited after)

### Order of operations (owner-mandated)
Capital feasibility is checked FIRST. A signal that cannot be sized inside the charter is not
screened — screening it would manufacture an unactionable "edge" and waste a multiplicity slot.

### Hypothesis (screened ONLY if feasibility passes)
When the annualized basis on Binance dated futures (BTC/ETH quarterlies) exceeds the
trailing/expected funding APR of the equivalent F1 perp position, expressing the carry short
leg via the quarterly (long spot + short dated future, hold to expiry) yields higher net carry
than the floating perp leg, after identical registered costs.

### Feasibility protocol (frozen)
- Account sizes evaluated: **$420** (owner's current, per user_trading_profile) and **$1000**
  (upper bound named in the Phase-2 tasking).
- Charter caps (CLAUDE.md Hard Portfolio Risk Layers): **3% per single trade**, **12% total
  open exposure**, exposure measured **gross-notional** (established convention:
  "R2 is gross-notional by design", 2026-06-15 compliance pass).
  - $420 → per-trade cap $12.60; gross-exposure cap $50.40.
  - $1000 → per-trade cap $30.00; gross-exposure cap $120.00.
- Instrument check (keyless ccxt market metadata, Binance): do USDT-margined quarterly/dated
  futures exist for the F1 coins, and what are the exchange minimums per leg
  (min amount × price, min notional/cost filter) for (a) the dated-future short leg and
  (b) the spot long leg?
- **INFEASIBLE rule (frozen):** if the minimum single-leg notional exceeds the 3% per-trade cap,
  OR the minimum combined two-leg gross notional exceeds the 12% exposure cap, at BOTH $420 and
  $1000, the verdict is INFEASIBLE-AT-CURRENT-CAPITAL and NO screen is run. Leverage does not
  rescue exposure: the 12% cap is gross-notional, and margining down does not shrink notional.
- Only if feasible at some charter-compliant size: a full screen pre-registration
  (basis-vs-funding net-carry comparison, walk-forward, frozen gates DSR≥0.10 / PBO≤0.5 /
  OOS-WR≥0.55, MC P(total>0)≥0.95 & maxDD p95≤0.25) would be written BEFORE any basis data
  is pulled. Note also the scout's data caveat: only ~2–4 expired quarterly cycles are
  retrievable — small-N would have to be stated and would likely bind before the gates do.

---

## FEASIBILITY PROBE (executed after freeze; keyless ccxt, Binance, 2026-07-11)

Raw probe output (binanceusdm dated futures + spot minimums):

```
USDT-margined dated futures on binanceusdm: 4
BTC/USDT:USDT-260925  expiry=2026-09-25 px=64691.8  amount.min=0.001  cost.min=5.0 -> min leg ~= 64.69 USDT
BTC/USDT:USDT-261225  expiry=2026-12-25 px=65310.3  amount.min=0.001  cost.min=5.0 -> min leg ~= 65.31 USDT
ETH/USDT:USDT-260925  expiry=2026-09-25 px=1805.77  amount.min=0.001  cost.min=5.0 -> min leg ~= 5.00 USDT
ETH/USDT:USDT-261225  expiry=2026-12-25 px=1821.73  amount.min=0.001  cost.min=5.0 -> min leg ~= 5.00 USDT
SPOT BTC/USDT  px=64203.71 amount.min=1e-05  cost.min=5.0 -> min leg ~= 5.00 USDT
SPOT ETH/USDT  px=1800.74  amount.min=0.0001 cost.min=5.0 -> min leg ~= 5.00 USDT
```

Only BTC and ETH have USDT-margined quarterlies (4 contracts total). No other F1 coin has one.
ETH quarterly minimum viable order = 0.003 ETH (amount step 0.001; 0.003 × ~$1,806 = $5.42
clears the $5 cost floor).

### Feasibility arithmetic vs frozen caps

| Pair (both legs matched) | Min gross notional | vs $420 caps (trade $12.60 / gross $50.40) | vs $1000 caps ($30 / $120) | Feasible? |
|---|---|---|---|---|
| BTC: spot + BTC quarterly | $64.69 + $64.69 = **$129.39** | leg = 5.1× per-trade cap; gross = 2.6× exposure cap | leg = 2.2× per-trade cap; gross = 1.08× exposure cap | **NO — INFEASIBLE at both sizes** |
| ETH: spot + ETH quarterly | $5.42 + $5.42 = **$10.83** | leg $5.42 ≤ $12.60 ✓; gross $10.83 ≤ $50.40 ✓ (2.6% of equity) | ✓ | **YES (minimum size only; step granularity $1.81)** |

The scout's "~100 USDT per leg → ~48% of account" estimate is CORRECTED by the probe: it holds
in spirit for BTC (leg minimum $64.69, driven by amount.min = 0.001 BTC, not by a notional
filter) but NOT for ETH ($5.42/leg). Leverage does not rescue BTC: the 12% cap is
gross-notional by charter convention.

**Feasibility verdict: BTC leg-swap INFEASIBLE-AT-CURRENT-CAPITAL ($420 and $1000).
ETH leg-swap FEASIBLE at/near exchange-minimum size within both charter caps.**

### Screen status for the feasible (ETH) variant

Per the frozen protocol, the ETH screen requires historical quarterly-futures prices to
compute the dated basis vs realized perp funding. Local check:
- `data/ohlcv_cache/` contains NO dated-future series (only spot `-USDT_` and 14
  commodity/equity perp `-USDTUSDT_` files).
- The scout's brief already flagged this: "NOT cached locally — quarterly-futures OHLCV must
  be pulled fresh."
- This Phase-2 pass is bound to the local cache + the funding top-up only (hard rule);
  no new acquisition was performed.

## VERDICT: INSUFFICIENT_DATA (ETH variant) / INFEASIBLE-AT-CURRENT-CAPITAL (BTC variant)

**Exact blocking gap (ETH):** no local historical series for Binance ETH dated-future prices;
the basis leg of basis-vs-funding cannot be computed from cache.

**Exact harvest command to unblock:** new script `scripts/backfill_quarterly_basis.py` hitting
keyless Binance `GET /fapi/v1/continuousKlines?pair=ETHUSDT&contractType=CURRENT_QUARTER`
(and `NEXT_QUARTER`), interval=1h, full available history → write
`data/ohlcv_cache/ETH-USDT_qtrcont_1h.parquet` (+ same for BTC only if capital ever supports
it). Caveats to carry into the future pre-registration, stated now:
- Only a limited number of expired quarterly cycles are retrievable — small-N must be stated,
  and n may bind before the frozen gates do.
- Quarterly-leg spread/slippage must be measured from the live book at screen time, NOT
  assumed equal to the perp's 5 bps (dated books are thinner).
- Binance charges a delivery/settlement fee on positions held to expiry — must enter the cost
  model explicitly.
- At the charter-compliant ETH size (~$10.8 gross), the screen decides edge-existence per the
  PAPER research policy; per-result dollar smallness is not a verdict criterion.

No screen was run; no gate table exists. The feasibility half is final; the ETH screen half
awaits the quarterly backfill.

---

## ETH SCREEN PRE-REGISTRATION (frozen 2026-07-11, written BEFORE any quarterly-basis data was pulled — per the frozen protocol above)

Audit 08d confirmed the unblock endpoint (continuousKlines history to 2021-02-04, ~22 cycles,
finding C3-a). BTC stays INFEASIBLE — not re-probed. Everything below is frozen before the
backfill runs.

### Hypothesis (as frozen above)
When annualized ETH dated-futures basis at entry exceeds the expected perp funding APR,
long-spot + short-quarterly held to expiry yields higher net carry than the perp leg over the
identical window, after registered costs.

### Event universe
Decision points: 1st of each calendar month 00:00 UTC (first 1h bar ≥ that ts), 2021-03-01 →
2026-06-01, where the Binance ETHUSDT CURRENT_QUARTER contract has ≥ 14 days to expiry.
Expiries = last Friday of Mar/Jun/Sep/Dec 08:00 UTC (Binance spec). Hold = entry → expiry.
Entries within one quarter share an expiry (overlapping holds) — stated; walk-forward purge
drops train events whose hold overlaps the test fold's earliest entry; MC on the overlapping
series is a stated limitation. PRIMARY sample = events where the conditional gate holds:
APR_basis(entry) > APR_funding(entry). Unconditional Δ reported as diagnostic only.
- APR_basis = [(Q − S)/S] × 365d/days_to_expiry, Q = current-quarter continuous 1h close,
  S = spot 1h close, both at the entry bar (no-lookahead).
- APR_funding = mean of the last 21 realized binance ETH funding prints strictly BEFORE
  entry × 3 × 365.

### Primary metric (per event; returns on one unit of short-leg notional, same window)
Δ = net_basis_carry − net_perp_carry
- net_basis_carry = basis_frac(entry) − basis_frac(exit) − cost_qtr, exit = last 1h bar
  before expiry (residual basis MEASURED, not assumed zero; delivery cash-settles at the
  settlement price — the last-bar proxy is a stated small divergence).
- net_perp_carry = Σ realized binance ETH funding prints in [entry, expiry) − 0.0050
  (F1's frozen 50 bps round-trip model; ONE round trip for the whole window — the
  perp-favorable treatment, stated).
- cost_qtr = spot RT fee (2 × 10 bps) + quarterly entry taker (5 bps, config.FEE) +
  delivery settlement fee (5 bps — Binance USDT-margined quarterly spec: settlement fee =
  taker fee, cash settlement, no exit crossing) + 3 crossings × 5 bps slippage (spot in,
  quarterly in, spot out; delivery = no crossing) = **45 bps = 0.0045**.
- Quarterly-leg slippage: live book measured at screen time (frozen requirement):
  ETHUSDT_260925 spread 0.61 bps, ETHUSDT_261225 spread 1.76 bps, top-5 depth ≥ 66× the
  $5.42 clip (probe 2026-07-11). Registered slippage = max(repo 5 bps convention, measured
  half-spread) = **5 bps** — the never-softer side.

### Frozen gates (unchanged from the protocol above)
- DSR ≥ 0.10 on the conditional Δ series, n_trials = 1 (single arm, family's first
  registration); PBO ≤ 0.5 via CSCV on the per-event [basis_net, perp_net] 2-column matrix;
  OOS-WR ≥ 0.55 (WalkForward n_splits=4, embargo=1, purge as above; share of OOS events with
  Δ > 0); MC (`monte_carlo_trade_sequence`) on the chronological conditional Δ series:
  P(total>0) ≥ 0.95 AND maxDD p95 ≤ 0.25, min_trades = 30. NaN fails closed.
- Small-N statement (frozen above): if the conditional sample has n < 30, the gates are not
  evaluable → INSUFFICIENT_DATA (n binds before the gates, exactly as pre-registered), not a
  merit NO_GO and not an excuse to widen the event grid.

### What NO_GO looks like (declared in advance)
Conditional sample evaluable (n ≥ 30) and any single frozen gate fails; or the Δ sign is
unstable across walk-forward folds. A positive mean Δ failing any gate is NO_GO.

### Data pulled AFTER this freeze
`scripts/backfill_quarterly_basis.py`: (a) continuousKlines ETHUSDT CURRENT_QUARTER (and
NEXT_QUARTER, stored for completeness; the screen uses CURRENT_QUARTER only) 1h from
2021-02-04 → `data/ohlcv_cache/ETH-USDT_qtrcont_1h.parquet` (+ `_qtrnext_`); (b) ETH spot 1h
extension 2021-02-04 → now (local cache starts 2023-05-26) via keyless api.binance.com
klines → `data/ohlcv_cache/ETH-USDT_qtrspot_1h.parquet` (separate file — the shared cache
file's first-ts is a listing-date heuristic elsewhere and is not touched). Funding:
`data/funding_history/binance_ETH.csv` (already local, 2021-01 →).

No result below this line existed when this registration was written.

---

## EXECUTED RESULTS (2026-07-11, screen run AFTER the registration above)

Data landed: `scripts/backfill_quarterly_basis.py` → ETH-USDT_qtrcont_1h.parquet (47,598
bars, 2021-02-04 → 2026-07-11), ETH-USDT_qtrspot_1h.parquet (47,591 bars, same span),
ETH-USDT_qtrnext_1h.parquet (39,200 bars, stored, unused by the screen). Funding:
`binance_ETH.csv` (local). Screen: `research/screen_basis_swap_eth.py`.

Sample: 64 monthly decision points, ALL resolved (0 exclusions). Conditional sample
(APR_basis > APR_funding at entry) n = **31** ≥ 30 — evaluable; small-N statement stands
(31 is the bare minimum).

Unconditional diagnostic: Δ mean −7.3 bps/event (WR 54.7%) — swapping legs blindly LOSES;
mean APR_basis 7.8% vs mean APR_funding 8.4%.

### Gate table (conditional sample, frozen thresholds)

| Gate | Threshold | Value | Pass |
|---|---|---|---|
| mean Δ > 0 | >0 | +9.2 bps/event (median +17.7) | ✅ |
| Win rate | ≥0.55 | 0.581 | ✅ |
| OOS-WR (walk-forward, embargo+purge) | ≥0.55 | 0.625 | ✅ |
| DSR (n_trials=1) | ≥0.10 | 0.463 | ✅ |
| PBO (basis vs perp CSCV) | ≤0.5 | 0.000 | ✅ |
| MC P(total>0) | ≥0.95 | **0.683** | ❌ |
| MC maxDD p95 | ≤0.25 | 0.114 | ✅ |

## VERDICT: **NO_GO** (ETH variant) / INFEASIBLE-AT-CURRENT-CAPITAL (BTC variant, unchanged)

The conditional leg-swap signal points the right way but is far too noisy to clear the
capital-preservation probability gate: a 31-event bootstrap leaves a 32% chance the swap
nets ≤ 0. Per the frozen decision tree, a positive mean failing any single gate is NO_GO —
not "promising". The feasibility half (BTC infeasible / ETH feasible-at-minimum) is final
and audit-confirmed; no re-probe was performed.
