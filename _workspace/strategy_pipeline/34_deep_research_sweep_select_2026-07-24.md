# Sweep-and-Select ("thousands of versions, keep survivors"): Research Verdict
*Generated: 2026-07-24 (19:22 UTC) | Sources: local measured + 4 external | Confidence: High*

Owner request: pullback spec (close>MA20 ∧ RSI14>55, RSI>70/close<MA20 exits, 1.5×ATR stop,
SMA50>SMA200 filter, 1% risk) → "create thousands of versions… remove weak systems… test every
version… implement the strongest systems survived. use the /advisor and refine."

## Verdict

1. **The strategy spec: STOP (unchanged).** Same family deep-researched 24h ago
   ([`29_deep_research_pullback_ma20_rsi_2026-07-23.md`](29_deep_research_pullback_ma20_rsi_2026-07-23.md),
   14 sources) — ledger-refuted parents, adverse peer-reviewed anchor, probe already running.
   This spec is the probe arm `pullback_ma20_rsi14_4h_v1` with a LEVEL entry instead of the
   probe's CROSS entry (level = strictly higher turnover = worse cost death).
2. **The methodology: already executed here, to its endpoint — and it failed exactly as the
   statistics predict.** See §2. Mass-variant selection on the same market is not run again.

## 1. Forward status of the owner's spec (measured, log-only probe)

`data/promotion_funnel.json` @ 2026-07-24T18:40Z, lane `pullback_ma20_4h`:
**12/30 resolved, 0 wins, WR 0.000** (was 7/30 WR 0.0 on 07-23). Expectation NO-PROMOTE stands.

## 2. The natural experiment: this exact method already ran on this bot

The 2026-07-19 paper_bundle_test WAS "thousands of versions, same market, filter, keep
survivors": a **432-config sweep** over MR variants on our own multi-venue data. Survivors
(cfg365 candidate, cfg226 tracker) were wired as log-only probes with frozen gates. Forward
result as of today:

| Lane | Resolved | Forward WR | Gate result |
|------|----------|------------|-------------|
| `rsi2_4h_cfg226` | 37/30 (floor hit) | **0.649 — inside the owner's 59–67% band** | **GATE_BLOCKED, fail-closed**: net after-cost PnL **−$31.15**, expectancy **−0.84/trade**, PF **0.62**, AUC **0.50** (score = coin flip). oos_wr (0.649≥0.55), n, DSR all PASSED — profit gates killed it |
| `zfade_4h_cfg365` | 24/30 | 0.667 | Gate test in ~1.8 days (eta) |

This is the full arc of the proposed method, measured forward: **a sweep survivor with an
in-band win rate that loses money after costs.** The bundle's own OOS already predicted this
("cfg226 in/near band but net NEGATIVE OOS" — CLAUDE.md 2026-07-19 row); the forward funnel has
now confirmed it out-of-sample of the sweep itself. The frozen gate did its job.

## 3. Why "thousands of versions, same market" manufactures winners from noise

Expected MAXIMUM Sharpe among N variants with ZERO true edge (Bailey & López de Prado,
[Deflated Sharpe Ratio](https://www.davidhbailey.com/dhbpapers/deflated-sharpe.pdf); formula
E[max SR] ≈ σ_SR[(1−γ)Φ⁻¹(1−1/N) + γΦ⁻¹(1−1/(Ne))], computed 2026-07-24):

| N variants | σ_SR=0.3 | σ_SR=0.5 | σ_SR=0.8 |
|-----------:|---------:|---------:|---------:|
| 100   | 0.76 | 1.27 | 2.02 |
| 432   | 0.90 | 1.50 | 2.41 |
| 1,000 | 0.98 | 1.63 | 2.60 |
| 5,000 | 1.11 | 1.84 | 2.95 |

At 1,000 variants the best backtest is expected to show Sharpe ≈1.6 **from pure noise**.
Bonferroni at N=1,000 requires per-test |t| ≈ 4.06. Two structural points:

- **The proposed filters (drawdown, consistency, trade count, R:R, PF stability) are computed
  on the same data used for selection — they are part of the selection operator, not a control
  for it.** Only data the selection never touched (true OOS / forward) counts.
- **n_trials for DSR must count every variant GENERATED, including discarded ones.**
  Pre-filtering weak systems does not shrink the multiplicity penalty
  ([PBO, SSRN 2326253](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2326253)).

External corroboration: [AutoQuant (arXiv 2512.22476)](https://arxiv.org/pdf/2512.22476) — an
execution-constrained two-stage selection framework still reports "substantial residual
overfitting risk" on its own CSCV/PBO diagnostics. Cost anchor:
[arXiv 2606.00060](https://arxiv.org/abs/2606.00060) — BTC hourly momentum +31.16% gross →
−45.93% net at 10 bps, 27-fold walk-forward, Holm-corrected.

## 4. "/advisor"

No `/advisor` skill exists in this environment (checked). The harness's adversarial instrument
is the **honesty-auditor** (verifies screens/pipelines); the bot's `claude_advisor.py` is
advisory-only and holds no decision authority. "Refine the strategies" on a refuted family =
re-litigating the ledger → refused. Refinement happens only as NEW pre-registered constructs
through the pipeline (prereg hash → screen → audit → shadow → frozen gate → owner sign-off).

## 5. What runs instead (already queued, binding order in `30_edge_queue`)

1. VPIN jump-risk veto screen — UTC day 2026-07-25 (prereg `27_*` frozen).
2. `zfade_4h_cfg365` + `tsmom_20d_1h` (29/30) hit their frozen gates within ~2 days — the
   honest continuation of the owner's "keep what survives" intent, with correct statistics.
3. C2 gamma-expiry forward harvest accruing (prereg `33_*` frozen).

## Sources

1. `data/promotion_funnel.json` (2026-07-24T18:40Z) — cfg226 GATE_BLOCKED detail; pullback 12/30 WR 0.0
2. [`29_deep_research_pullback_ma20_rsi_2026-07-23.md`](29_deep_research_pullback_ma20_rsi_2026-07-23.md) — 14-source STOP on this spec
3. CLAUDE.md 2026-07-19 bundle-MR row — 432-config sweep provenance; cfg226 kept to measure band-vs-profit tension
4. [Bailey & López de Prado — Deflated Sharpe Ratio](https://www.davidhbailey.com/dhbpapers/deflated-sharpe.pdf) — E[max SR] under selection
5. [Bailey, Borwein, López de Prado, Zhu — Probability of Backtest Overfitting (SSRN 2326253)](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2326253) — CSCV/PBO
6. [AutoQuant, arXiv 2512.22476](https://arxiv.org/pdf/2512.22476) — residual overfitting after two-stage selection
7. [arXiv 2606.00060](https://arxiv.org/abs/2606.00060) — momentum cost death, Holm-corrected

## Methodology

Refuted-family portion answered from the ledger + 24h-old deep-research (no re-run — harness
rule). New analysis: funnel forward read, 432-sweep endpoint audit, E[max SR] computation
(N ∈ {100…10,000}, σ_SR ∈ {0.3,0.5,0.8}). Firecrawl/Exa MCP unavailable; the 2026-07-23/24
web sweeps (delta = null) cover the external literature within 24h.
