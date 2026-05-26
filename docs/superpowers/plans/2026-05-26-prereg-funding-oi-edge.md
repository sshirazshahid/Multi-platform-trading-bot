# PRE-REGISTRATION — Funding-Rate & Open-Interest Edge Search

**Date:** 2026-05-26 (frozen before any result is computed)
**Author:** Claude Code (offline research, bot halted)
**Status:** pre-registered; results appended only AFTER the run

## 1. Context & economic rationale

The prior 443-alpha search (K101 / GTJA-191 / Qlib Alpha158) returned **NO_EDGE**
(IR 0.37 < 0.50, PBO 0.67 > 0.50). That search was **OHLCV-only** — pure
price/volume technicals, the most heavily-arbitraged signal class in the most
liquid market on earth. Finding nothing there is the *expected* result, not a
surprise.

This search tests the **orthogonal, crypto-native data the prior search
structurally could not see**:

- **Perp funding rate** — a real 8h cash flow. Encodes (a) positioning/crowding
  (extreme funding = one side crowded) and (b) a carry premium. Documented
  mechanisms, not data-mining.
- **Open interest** — leverage build-up / flush. OI±price interaction
  distinguishes real trends from squeezes.

If there is no edge here either, that is a *valuable* finding: it says the edge
is not in the data we can cheaply access, and live trading should stay halted.

**Base-rate honesty (stated to check my own confirmation bias):** funding-extreme
reversal (H1) and carry capture (H4) are the *most-published* crypto "edges" and
have been arbitraged for years; the carry is largely a risk premium for fat
tails / squeezes that looks positive in backtest and dies in real drawdowns.
This is not virgin territory — it is a check for *residual* edge after years of
arbitrage. The prior on a robust pass remains **low**. A marginal pass is to be
treated with suspicion, not celebration.

## 2. Frozen acceptance gates (inherited from existing harness — DO NOT change after seeing results)

A hypothesis is an **EDGE CANDIDATE** only if it passes **ALL** of:

| Gate | Threshold | Stage |
|---|---|---|
| Information Ratio `IR = mean(IC)/std(IC)` | ≥ **0.50** | Stage 1 (in-sample) |
| Deflated Sharpe `DSR` (corrected for `n_trials`) | ≥ **0.10** | Stage 2 (OOS) |
| Prob. of Backtest Overfitting `PBO` | ≤ **0.50** | Stage 2 (OOS) |
| Benjamini-Hochberg FDR | pass at **q = 0.05** | across survivors |
| OOS long-short Sharpe **net of costs** | > **0** | Stage 2 (OOS) |

Costs: fee 0.07% × 2 per round-trip + slippage 0.05% per side (same as prior
search). For the carry hypothesis (H4) the funding accrual is **added** to the
return; for all others funding is not part of the price-prediction label.

**Small-sample gate adjustments (settled BEFORE run; ~70d → ~84 OOS 8h bars):**
- `sharpe()` is **per-observation** (the harness p-value uses `SR·√n_obs`), so an
  absolute Sharpe number is scale-ambiguous and is NOT used as a gate. The real
  stage-1 discriminator is **IR ≥ 0.50** — the bar the 443 alphas failed (IR 0.37).
- **PBO partitions 16 → 8** (~10 bars/partition instead of ~5) and PBO is treated
  as a *supporting* check, not primary, because its discrimination power at this N
  is poor.
- **Added promotion constraints** (EXTRA, conservative — a candidate must clear the
  inherited gates AND these to advance): (a) OOS net-of-cost mean long-short return
  **> 0 in BOTH halves** of the OOS window (stability); (b) **DSR ≥ 0.90** as the
  real significance bar (the inherited DSR_MIN 0.10 is reported for
  comparability with the prior search but is too lenient to promote on). If a
  hypothesis clears inherited gates but not (a)/(b), verdict = "insufficient
  evidence / needs more data," not "candidate."

## 3. Universe & data

- **Universe:** the 32 USDT-perp symbols in `data/ohlcv_cache/`, intersected with
  ccxt Binance USD-M availability.
- **Source:** ccxt Binance USD-M (public endpoints), fetched fresh for
  self-consistent alignment (avoids cache/pyarrow coupling).
- **Funding:** ~70 days, 8h cadence (~210 points/symbol).
- **OHLCV:** 4h and 8h.
- **OI:** ~30 days, 1h (Binance history limit) — **secondary, low-confidence**.
- **No look-ahead:** signal at bar `t` uses only funding/OI/price known at or
  before `t`. Label = `close[t+h]/close[t] − 1`. Embargo = horizon bars.

## 4. Pre-registered hypotheses (sign fixed BEFORE results)

| ID | Signal | Sign / direction | Economic rationale |
|---|---|---|---|
| **H1** | `−zscore(funding)` | short highest-funding, long lowest | Extreme funding = crowded positioning → mean-reverting |
| **H2** | funding change / sign-persistence | long persistent-positive | Persistent funding regime = trend continuation |
| **H3** | `−funding · sign(recent_ret)` | divergence | + funding while price falls = longs in denial → more downside |
| **H4** | carry capture (return includes funding accrual) | short high-funding / long neg-funding | Is the carry itself > 0 net of cost, independent of price? |
| **H5** | `sign(Δprice) · ΔOI` | OI/price interaction | OI surge confirms trend / flags squeeze. **~30d only — low power** |

**H4 anti-circularity (critical):** the carry "return" over the holding window
`[t, t+h]` = realized **price** return + the funding payments that **actually
settle inside `(t, t+h]`** × position sign. It must NOT add `funding[t]` (the
selection-time rate) to the return — that would mechanically manufacture the
result from the selection variable. Selection uses `funding[t]` (known at `t`);
PnL uses funding realized *during the hold*. If funding is persistent these
correlate, but that persistence-harvest IS the real edge being tested, not
leakage.

## 5. Horizons & multiple-testing budget

- **Primary:** 8h (one funding period). **Secondary:** 24h (prior comparator).
- Sign is taken from the **pre-registered hypothesis**, not fit to in-sample
  sign (avoids sign-snooping). If the harness is allowed to pick sign, that
  doubles `n_trials` and will be accounted for.
- `n_trials` for DSR = (# signals) × (# horizons) ≈ 5 × 2 = **10** in this family.
  The prior 443 are a **separate, already-reported** family — not pooled, but the
  cumulative search budget is acknowledged in the writeup.

## 6. Protocol

1. Build aligned T×N panels (funding, OI, OHLCV, fwd_ret) via `panel.build_panel`.
2. 60/40 chronological IS/OOS split (`split_panel`, embargo = horizon bars).
3. Stage 1 (IS): `cross_sectional_ic` → `ir`; keep if IR ≥ 0.50.
4. Stage 2 (OOS): `long_short_returns(q=0.20)` → `sharpe` (net cost),
   `dsr_for_returns(n_trials)`, `pbo_over_alphas`, `fdr_bh`.

## 7. Known limitations (stated up front, before results)

- **~70 days is THIN.** 60/40 → ~28d OOS → ~84 non-overlapping 8h periods. DSR/PBO
  power is limited; PBO with 16 partitions on this is borderline. Treat results as
  indicative, not definitive.
- A **pass is a CANDIDATE**, not a live green light. Promotion requires: longer
  history, full walk-forward (not single split), and forward paper validation.
- Funding/OI are Binance-only here; cross-exchange differences not modeled.
- **Binance-only is the WEAKER form of the funding hypothesis.** The cross-sectional
  signal is "which Binance symbol has extreme Binance funding" — already mostly
  arbitraged. The richer signal is **cross-venue funding dispersion** (one venue
  extreme, another not = real positioning imbalance), which needs aligned
  multi-venue history I can't cheaply assemble offline today. A **NO_EDGE here does
  NOT rule out cross-venue funding edges** — it only rules out the single-venue form.
- **Funding timestamp semantics must be verified before trusting the panel:** confirm
  whether ccxt's `fetch_funding_rate_history` timestamp is settlement time or
  interval start, and spot-check one symbol manually, so a bar is never labeled
  with funding info that includes its own return window.
- **H5/OI (~30d) is EXPLORATORY ONLY.** 30 days is too short for DSR/PBO to give a
  trustworthy verdict. A pass routes to "needs more data," never directly to
  candidate-for-paper-forward.

## 8. Decision rule

- **Any hypothesis passes all gates OOS** → promote to extended walk-forward +
  longer history + paper-forward before any CONTROLLED_LIVE re-sign-off.
- **Nothing passes** → verdict **NO_EDGE**; bot stays halted; report honestly.

## 9. Results (run 2026-05-26, `scripts/run_funding_edge_search.py`)

**Data:** 31 Binance USD-M perps × 210 8h-bars (2026-03-17 → 2026-05-26).
Funding timestamps verified all-8h-aligned settlement (no look-ahead). PEPE
skipped (not a Binance perp under this symbol).

**VERDICT: NO_EDGE.** ALL hypotheses/horizons reported (none dropped):

| signal | h | IR_is | passes S1 (≥0.50) | Sharpe_net | mean_net | DSR | p-val | both-halves+ | FDR |
|---|---|---|---|---|---|---|---|---|---|
| H1 reversal | 8h | 0.248 | no | −0.418 | −0.0038 | 0.00 | 1.00 | no | no |
| H2 momentum | 8h | −0.164 | no | −0.794 | −0.0073 | 0.00 | 1.00 | no | no |
| H3 divergence | 8h | −0.025 | no | −0.602 | −0.0051 | 0.00 | 1.00 | no | no |
| H1 reversal | 24h | −0.029 | no | −0.136 | −0.0027 | 0.00 | 0.89 | no | no |
| H2 momentum | 24h | −0.030 | no | −0.260 | −0.0040 | 0.00 | 0.99 | no | no |
| H3 divergence | 24h | 0.032 | no | −0.096 | −0.0017 | 0.00 | 0.81 | no | no |
| H4 carry | 8h | n/a | (not IC) | −0.439 | −0.0038 | 0.00 | 1.00 | no | no |

PBO = 0.057 (8 partitions) — irrelevant, nothing reached stage 2. n_trials(DSR)=7.

**Interpretation (honest):**
- The binding gate is **stage-1 IR ≥ 0.50, which is cost-free**. The best signal
  (H1 reversal) reached only **IR 0.248** — about half the bar, and *weaker than
  the formulaic alphas' 0.37*. Funding has weak-to-no cross-sectional predictive
  power for forward returns here. Cost is not the reason it failed; the signal is.
- Robustness on the strongest signal: even **gross** (cost added back), H1's OOS
  long-short per-bar Sharpe ≈ 0.11 → z ≈ 0.97 (p ≈ 0.17, one-sided) over ~83 OOS
  bars. Not significant gross either. So the conservative full-turnover cost
  assumption is not what produced the negative result.
- **H4 carry is negative net of cost.** Gross carry ≈ +0.1%/8h is real but smaller
  than realistic rebalancing cost and statistically insignificant; the classic
  funding carry premium did not survive here over this window.
- H1's sign is correct (positive, weak) — not a sign error (a flipped sign would
  show strongly negative IR).

**Conclusion (precisely scoped):** The **cross-sectional, 8h-rebalanced,
single-venue** form of funding/carry shows **no exploitable edge** over this
70-day window — consistent with, and slightly weaker than, the OHLCV NO_EDGE.
There is *weak directional alignment* (H1 reversal IR 0.248, correct sign) but it
does not clear the bar — "below threshold," not "nothing there."

This is NOT "the funding data class is exhausted." Untested variants remain:

| # | What's left | Data needed |
|---|---|---|
| 1 | **Time-series funding** (per-symbol vs own trailing history; autocorrelation-aware IC) | same data already fetched |
| 2 | **Multi-day-hold carry** (H4 with 7–30d holds + sparse rebalance to amortize cost) | same data already fetched |
| 3 | **Cross-venue funding dispersion** (one venue extreme, another not) | new: multi-venue aligned funding history |
| 4 | **Options skew / IV-RV** | new: CoinDesk MCP options data |
| 5 | **OI extended history** (H5) | new: >30d OI |

Recommendation stands: **stay halted.** What would move the needle is one of the
five above returning a *gated, OOS-validated* positive — not further tuning of the
live system. Items 1–2 are cheap (same data); 3–5 need new plumbing for a low prior.

## 10. Extended sweep (2026-05-26, `scripts/run_edge_sweep.py`) — items 1,2,3,5

Ran via ccxt across 3 venues (Binance/Bybit/Bitget); ONE consolidated
multiple-testing correction (n_trials=14, DSR-deflated, shared PBO=0.114, BH-FDR).

**VERDICT: NO_EDGE across the full sweep.** Nothing cleared the frozen gates.

- **Cross-venue funding dispersion** (Binance−Bybit / −Bitget): IR 0.153 / −0.160,
  negative net Sharpe. No edge.
- **Time-series funding** (per-symbol z): cross-symbol IC mean −0.043, **t = −2.75**
  (n=31) — a *statistically consistent* weak reversal, but **economically negligible**
  (gross edge ≪ the 0.48%/8h cost); z-fade portfolio Sharpe −1.33.
- **Multi-day carry**: 1d/3d holds negative; **7-day hold is the ONLY positive in the
  whole program — net +0.45%/period, Sharpe +0.11, both OOS halves positive — BUT
  pval 0.19 (not significant) and DSR 0.00 (fails deflation).** A directionally
  sensible, underpowered *lead*, NOT a validated edge.
- **OI (item 5):** did not run — Binance OI history rejects the '8h' period; 30d
  window is low-prior anyway. Deferred.

**Data-feasibility findings (items 4, 6):**
- **BlockchainQuery is NOT exposed** as callable tools. On-chain via CoinDesk =
  *supply metrics only* (circulating/staked/burnt) — useless for short-horizon
  prediction (BTC supply +0.008%/day, pure issuance).
- **Options skew** (CoinDesk/Deribit) needs per-contract instrument enumeration +
  IV; the "BTC" alias is rejected. High-effort, BTC/ETH-only (no cross-section).
  Deferred — not run.

**Overall:** across OHLCV technicals + funding (cross-sectional, time-series,
cross-venue) + carry, there is **no validated edge**. The lone 7-day-carry lead is
too weak and too tail-risky to justify real capital at ~$400. **Stay halted.**
