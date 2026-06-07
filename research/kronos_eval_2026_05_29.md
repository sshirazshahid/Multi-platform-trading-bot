# Kronos foundation-model evaluation — leakage-safe falsification (2026-05-29)

**Verdict: NO_EDGE after costs for the scalp application. Phase-3 gate FAILED → Kronos was NOT
wired into the live or shadow path. No capital risked.**

This is a rigorous, leakage-safe negative result on a state-of-the-art model — not a bug, not a
tuning gap. It is consistent with every prior finding (443-alpha sweep, funding/carry sweep, scalp
bracket replay): the bot's entry signal space has no tradeable edge at retail scalp costs.

## What was tested

[Kronos](https://github.com/shiyu-coder/Kronos) (arXiv 2508.02739) — an MIT, decoder-only
autoregressive Transformer + OHLCV tokenizer, pretrained on 12B K-lines from 45 exchanges (crypto
included). We used **Kronos-mini** (4.1M params, Tokenizer-2k, ctx 2048) via the vendored MIT model
code (`vendor/kronos/`) and HF weights (`models/kronos/`, gitignored).

Directional signal per decision bar = `exp_ret` = (mean predicted close at horizon / entry close − 1),
side = `sign(exp_ret)`. Adapter: `core/kronos_forecaster.py`.

## Leakage control (the constraint that makes the result meaningful)

Kronos's training cutoff is **undisclosed** and crypto is in its corpus, so backtesting on bars it may
have memorized would be falsely optimistic. Paper/weights are from **Aug 2025**, so **all evaluation
was restricted to bars ≥ 2025-09-01** (asserted in every probe):
- 15m cache: 2026-04-10 → 2026-05-25 (2157 bars, 100% post-cutoff, shallow single regime).
- 1h cache post-cutoff tail: 2025-09-01 → 2026-05-25 (~6401 bars, ~9 months) — the deeper OOS set.

## Phase 0 — feasibility (PASS)

Kronos-mini loads on the local GPU (NVIDIA GTX 1660 Ti, CUDA); warm inference ≈ **130 ms**
(`scripts/kronos_smoke.py`). Budget tractable (~10k inferences ≈ 22 min). Compute was never the
blocker.

## Phase 2 — directional IC pre-screen (WEAK, not zero)

8 liquid symbols (BTC/ETH/SOL/BNB/XRP/DOGE/LINK/ADA), 1200 inferences/horizon, post-cutoff only
(`scripts/kronos_ic_probe.py`):

| tf  | h | hit% | hit p | Spearman IC | IC p  | mean signed R |
|-----|---|------|-------|-------------|-------|---------------|
| 1h  | 1 | 52.3 | 0.119 | 0.076       | 0.009 | +0.052%       |
| 1h  | 4 | 50.4 | 0.773 | 0.046       | 0.109 | +0.076%       |
| 15m | 1 | 51.2 | 0.419 | 0.063       | 0.030 | +0.010%       |
| 15m | 4 | 49.8 | 0.908 | 0.055       | 0.056 | +0.003%       |

There is a **faint, partially-significant** directional signal (IC ≈ 0.05–0.08). But the **mean signed
return per trade (≤ 0.076%) is already smaller than the round-trip taker cost (~0.12% + slippage)** at
every horizon → foreshadows a negative after-cost result.

## Phase 3 — after-cost bracket falsification (FAIL → the decisive gate)

Reused the look-ahead-free `bracket_outcome` from `scripts/scalp_replay_backtest.py` (SL 0.8% / TP
1.3%, entry at t+1 open, pessimistic double-wick), entries driven by Kronos direction, conviction
threshold swept, 3 OOS time-folds, taker + charitable-maker costs
(`scripts/kronos_bracket_backtest.py`, n=1600/tf). EV = mean net return per trade after costs; the
"inverted" column flips every side (falsification-completeness — see below):

| tf  | conviction | n    | EV taker | EV maker | EV inverted (taker) |
|-----|-----------|------|----------|----------|---------------------|
| 1h  | all       | 1600 | −0.28%   | −0.15%   | −0.13%              |
| 1h  | \|er\|≥0.6% | ~400 | −0.44%   | −0.34%   | +0.04% (noise, small n) |
| 15m | all       | 1600 | −0.30%   | −0.14%   | −0.19%              |
| 15m | \|er\|≥0.6% | ~28  | −0.58%   | −0.36%   | +0.06% (noise, small n) |

(Figures are approximate: Kronos sampling is stochastic at T=1.0, so values shift by a few bps
between runs — sub-0.1% effects are at noise level, which itself argues against any stable edge.)

- **Negative EV at every threshold, both timeframes, both cost models, and all 3 OOS folds.** The gate
  ("net EV > 0 after taker AND positive in all 3 folds") fails outright.
- **No exploitable edge in EITHER direction.** At threshold 0 the signal AND its inversion are both
  net-negative after taker costs (1h: −0.28% / −0.13%; 15m: −0.30% / −0.19%). Gross EV ≈ 0 and costs
  sink both sides — so "just invert the signal" does **not** work. The faint +0.04–0.06% on the
  inverted high-conviction subset is small-n noise, far below tradeable, and unstable across runs.
- **Conviction filtering does not rescue EV** (it gets more negative for the signal). The weak Phase-2
  IC does not translate into the asymmetric 0.8/1.3 bracket.
- (`WR` printed by the script is the net-win rate *after costs*, not a TP-hit rate — the gate is EV,
  not WR vs a baseline.)

**GATE FAILED.** Per the approved plan, Phase 4 (shadow logging + promotion-gate hand-off) was
therefore **not executed**. Kronos is not in any decision path.

## Why (the structural reason, not a fix-it)

A near-zero entry edge (Phase-2 IC ~0.05–0.08, gross signed return ≤0.076% < ~0.12% round-trip cost)
cannot win an asymmetric 0.8/1.3 bracket: to win you need +1.3% before −0.8%, and a tiny directional
tilt is swamped by path volatility — then retail fees finish it, symmetrically (both long-bias and
short-bias entries lose). This restates the prior insight: **exits/sizing/conviction cannot
manufacture EV from a near-zero entry edge.** See `research/scalp_edge_finding_2026_05_28.md`.

## Honest scope / what remains formally untested

- **Larger Kronos (small/base/large):** unlikely to turn a 32% bracket WR into >49% given the mini's
  IC is already tiny and the paper claims accuracy (RankIC), not after-cost profit. Not pursued.
- **Cross-sectional ranking** (the paper's actual strength — rank many assets, long top / short
  bottom, longer horizon, market-neutral): untested here, and **impractical at ~$1300** (many
  simultaneous tiny positions vs min-notional + per-trade fees). Different strategy from the user's
  scalp ask.
- This evaluates the **scalp** use the user requested. For that use, on this account size and fee
  tier, Kronos is not tradeable.

## Reusable artifacts (kept; eval-only, NOT wired to the bot)

- `core/kronos_forecaster.py` — clean lazy adapter (torch guarded inside; bot runtime never imports it).
- `scripts/kronos_smoke.py`, `scripts/kronos_ic_probe.py`, `scripts/kronos_bracket_backtest.py` —
  reusable to screen any future forecast idea BEFORE risking capital.
- `requirements-kronos.txt` (separate from bot runtime), `vendor/kronos/` (MIT), gitignored
  `models/kronos/` weights.

## Bottom line

We tested the SOTA open foundation model for candlesticks, on leakage-safe data, with the bot's real
cost model, and it does not clear breakeven for scalping. The result reinforces the standing posture:
no validated edge → capital preservation, no scalper shipped.

---

## ADDENDUM 2026-06-07 — bigger model (Kronos-BASE, 102M) directional IC re-test

Owner asked to "adapt EVERYTHING from Kronos" — fair objection that the May-29 run used only
**Kronos-mini (4.1M)**. Downloaded **Kronos-base (102M, 25× mini)** + Tokenizer-base, ran the SAME
leakage-safe Phase-2 IC probe (`scripts/kronos_ic_probe.py`, env-override `KRONOS_MDL_DIR`/`KRONOS_TOK_DIR`
in `core/kronos_forecaster.py`), 1h post-cutoff (≥2025-09-01), 1200 inferences, 8 symbols.

| tf | h | hit% | hit_p | Spearman IC | IC_p | mean signed R% |
|----|---|------|-------|-------------|------|----------------|
| 1h | 1 | 50.50 | 0.729 | 0.0429 | 0.138 | **0.0145%** |
| 1h | 2 | 51.67 | 0.248 | 0.0301 | 0.297 | **0.0216%** |
| 1h | 4 | 50.75 | 0.603 | 0.0035 | 0.903 | **0.0149%** |

**Verdict: NO_EDGE, decisively — bigger is NOT better here.** Gross per-trade move 0.015–0.022% is
**6–8× below the ~0.12% round-trip cost** (pre-committed kill threshold). Hit-rate is a coin-flip
(no p<0.05), and the IC is **non-significant AND lower than the mini's** (base 0.043 vs mini 0.076 at
h=1). Scaling 25× the parameters did not raise the per-bar *directional* magnitude — consistent with
the paper's gains being in cross-sectional RankIC, not single-asset after-cost direction. Phase-3
(after-cost bracket) was NOT run: the gross-vs-cost wall already fails before costs are even applied,
so it cannot clear. Kronos remains NOT in any decision path; nothing was removed/disabled from the
live bot. Structural reason unchanged: forecasting candles well ≠ exploitable directional edge net of
retail cost — the efficient-market result, now confirmed at 25× scale. Base weights kept under
`models/kronos/Kronos-base` (gitignored). **Do not re-litigate Kronos** (mini AND base both falsified).
