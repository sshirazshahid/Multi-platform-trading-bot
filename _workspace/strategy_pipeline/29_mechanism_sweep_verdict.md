# 29 — Mechanism sweep VERDICT: microstructure / meta-labeling / agent-frameworks (SATURATED — stop funding)

**Date:** 2026-07-26 | **Runs:** wf_9f027332 (25 claims → 10 confirmed / 2 refuted / 13 unverified) + wf_b65ae5a1 (targeted close-out, 3 agents, adversarial verify)
**Owner directive (verbatim):** "any agent/mcp/sub-agent which can increase/improve the program's prediction, trading & profitability mechanism"
**Verdict: `anything_clears_bar = FALSE`.** Third independent pass on this question. **Record as CLOSED; do not fund further literature search.**

## The one result worth more than the whole literature (ANALYTIC — binding, no evidence bar applies)

**Meta-labeling cannot manufacture edge from a directionally uninformative primary signal.**
If the primary side `s` is independent of features and forward return with `E[s]=0`, then for EVERY measurable filter `m`:
`E[m(x)·s·r] = E[s]·E[m(x)·r] = 0` → net expectancy `= −cost·P(fire) ≤ 0`, strictly negative whenever the filter fires.
⇒ Meta-labeling / triple-barrier filtering / "smarter exits on the same entries" are **permanently closed** for this program unless a primary signal with genuine directional information is found first. This is arithmetic, not an empirical finding — it cannot be reopened by new papers.

## Verified negatives (avenues closed — each prevents future spend)

1. **No 2025+ source clears all four prongs (peer-review · OOS · after-cost · multiplicity) for a tradeable, RETAIL-RECONSTRUCTIBLE crypto order-flow edge.** ~15 sources across three passes, zero survivors.
2. **The closest candidate is not reconstructible by us.** JFM 101047 (Jan 2026) uses signed volume aggregated across **300+ exchanges** — the paper states it "is not available elsewhere"; we have 3 venues. It is also a daily-rebalanced cross-sectional long-short requiring shorts across a large universe, and its headline may be **contemporaneous** (flow *is* price formation ⇒ untradeable by construction) — unconfirmed, paywalled.
3. **Minute-frequency trading of microstructure features is cost-negative** — net Sharpe −31 to −52 (spot) at real Binance VIP-0 costs. ⚠ Scope honestly: this is a **turnover** result (288 round trips/day, 124–204× notional daily turnover), NOT a 4h result. At 4h that is ≤6 round trips/day — two orders of magnitude less drag. **The 4h cadence is the one configuration that study does not test.**
4. **Quarter-hour / clock-boundary imbalance columns: DO NOT BUILD.** The only support is a non-peer-reviewed preprint (arXiv 2607.09426, fails 3 prongs). **Shynkevich, Journal of Futures Markets 2026, 46(5):904-930** (peer-reviewed, same phenomenon) finds those windows **RAISE transaction costs** with no significant adverse price impact — the clock boundaries are the *most expensive minutes to trade*. For a retail-fee $420 bot this is exactly backwards. **Also: do not promote the queued C3 item on this basis.**
5. **CUSUM event-clock columns: DO NOT BUILD.** Source models **zero slippage** (explicitly out of scope) and has **zero multiplicity control across ~5,400 trained models / ~360 configurations**; the headline is the post-hoc winner of that space.
6. **VPIN stays closed** on our own 2026-07-25 CONFIRMED_NO_GO (mean ~0.127, fire% 0.000). Do not record any external VPIN coefficient — see §Source fidelity.
7. **Equity OFI/MLOFI effect sizes must NOT be imported as crypto priors** (different tick/fee structure; largely contemporaneous, not predictive).
8. **LLM-agent / copilot benchmarks are not evidence of edge** — LATTICE is an LLM-judge rubric over static one-shot Q/A pairs: no costs, no time split, no outcomes, no multiplicity.

## Demotions the verifier applied to its own researchers (methodological, worth remembering)

- **Frontiers in Blockchain 2026** carried 3 of 4 CLEARS_BAR grades and **does not clear the bar**: its validation is purged/embargoed **k-fold** where each test block is purged "preceded AND FOLLOWED" — two-sided purging means **training data sits after the test block in calendar time**. That is interleaved k-fold, not a time-consistent forward split. Survives only as a *directionally safe negative* (every weakness biases toward FINDING edge, so its negative cost verdict is conservative). Its positive-direction claims do not survive.
- **"LightGBM overfits catastrophically" → demoted to prudent prior** (one config, one 6.5-month window, no tuning protocol, no multiplicity).
- **Source fidelity caveat (binding):** paywalls forced summarizer extraction which proved unstable on numbers — three reads of the same page returned three different VPIN coefficients, and a load-bearing verbatim quote could not be reproduced. **Record directional findings only from this pass; put NO coefficient from it into the ledger.**

## Statistical corrections to our own reporting (apply immediately)

- ⚠ **Do NOT report "three probes all scored AUC 0.500" as a four-way confirmation.** At n=30 each probe's 95% CI ≈ **[0.29, 0.72]**. Three very wide intervals centred near 0.5 are not three independent confirmations of zero. **Binding statistical fact = the large-sample AUC 0.531 on 270,830 rows**; the probes corroborate **economically** (negative PnL/PF), not statistically.
- **Do not use naive Hanley-McNeil SEs on the 270,830-row set** — 43 symbols with overlapping 4h bars and high cross-sectional crypto correlation mean the effective sample is far smaller than n.

## Store implications (what changes, concretely)

**Plain answer: the store as built is at the wrong CONSTRUCTION to capture any published effect, and partly the wrong horizon.** Published evidence is barbelled — 3s–5min (book imbalance/OFI) → gap → 4–12h (clock-boundary only, now refuted) → daily (300-venue aggregate). A 4h aggregate of single-venue signed flow plus a point-in-time book snapshot has **zero published support at any horizon**.

| Action | Detail |
|---|---|
| **RECLASSIFY** | `book_imbalance`, `spread_bps`, `depth_top20_bid/ask_usd` → move OUT of the alpha feature set INTO **execution-cost + regime gating**. They are single `fetch_order_book` snapshots at bar close (`core/microstructure_store.py:163,190`) and decay in **seconds**. Both researchers reached this independently. |
| **KEEP (as falsification instrument)** | `signed_volume`, `buy_sell_imbalance`, `trade_count`, `large_trade_count`, `notional_total` — the only bar-aggregated columns with hours-plus lineage. Expected first-screen outcome: **NO_GO, written into the prereg up front.** |
| **DO NOT ADD** | quarter-hour boundary columns; CUSUM event-clock columns (both refuted above). |
| **FREE WIN** | Log `which_barrier_first ∈ {tp, sl, vertical}` on every probe outcome. The probes **already are** a triple-barrier structure — the label distribution is currently discarded for no reason. No new feed, no literature justification needed. |

## The first hashed prereg should be an EDGE-CONCENTRATION TEST, not an alpha hunt

Highest-value convergent recommendation across both passes.
**Hypothesis:** is the measured AUC excess (0.531 − 0.500 = 0.031) **uniform** across feature space, or **concentrated** in an identifiable subset?
**Why it matters:** if uniform, no filter of any kind can ever help — and combined with the meta-labeling proof above, that closes the entire "filter/condition the existing signal" family permanently, on our own data. If concentrated, the subset is the only place worth screening.
**Binding lag discipline:** features from bar `t` vs returns from bar `t+1` onward, strictly. Any contemporaneous same-bar coefficient is reported **separately and labelled a diagnostic, never a result** (this repo has already eaten a `backtest_v3` look-ahead bug and a per-fold round-trip cost artifact).
**Model class:** OLS/linear or depth≤3 shallow booster — not an unconstrained LightGBM over 9 columns × 43 symbols.
