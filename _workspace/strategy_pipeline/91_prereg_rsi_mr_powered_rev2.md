# Prereg 91 — RSI mean-reversion, powered pooled screen (rev2)

**Status:** FROZEN. Committed BEFORE any outcome statistic is computed.
**Date:** 2026-08-22
**Supersedes:** `90_prereg_rsi_mr_powered.md`
(sha256 `0caf263009fa62d0da9293475b74f77a4b78901b3f0e7d358cabdd69dd847627`).
**Scope of the amendment:** §5's SIZE *estimator* only. §2 (data), §3 (costs),
§4 (the hypothesis), §6 (the null generator) and §7 (verdict rules) are carried
over **unchanged in substance**, and the eight cells are identical.
**Family:** RSI mean-reversion — **REFUTED** (ledger:24). Ceiling unchanged:
**no GO is reachable**; the only permitted output is a measurement,
**"no edge above X bps"**, never "no edge".
**Live trade authorized:** NO. **Promotion path:** NONE.

---

## 0. Why there is a rev2 — and what was NOT seen

Screen 90 ran on 2026-08-22 and its Stage-0 SIZE gate returned **VOID**. Per
prereg 90 §5 the pre-committed consequence is that **no outcome is read**, and
the code honoured it: `main()` printed `VOID: outcome NOT read` and skipped
`evaluate()` entirely. **No real-data statistic from the eight cells has been
computed, printed, or stored.** Everything below rests on surrogate
(edge-free) universes and on data inventory.

That is the discipline working as designed, so this document does not get to
be a quiet re-roll. It records exactly what failed and why.

### 0a. What Stage 0 measured

| Gate | Measured | Bar | Result |
|---|---|---|---|
| POWER | oracle Sharpe **0.671**, MDE 0.1925 R | ≤1.5 interpretable, ≥2.5 fail | **PASS, comfortably** |
| SIZE | false-positive rate **0.0000** (0 of 1,600) | [0.02, 0.08] | **FAIL → VOID** |

**The POWER result is the headline and it survives this amendment.** Against
the prior round's oracle Sharpe of **4.79**, 0.671 is a ~7× improvement — for
the first time in five attempts the instrument can resolve an edge of realistic
size. The two levers (8 cells instead of 43,680; deepest-available history)
did what they were predicted to do.

### 0b. Why SIZE failed — diagnosed on surrogates only

The size gate was estimated with a one-sided **t-test of mean R > 0**. Measured
across 1,600 surrogate cell-tests, that statistic is centred at
**t = −2.467 (sd 1.600, max 0.933)**. It never crosses +1.645, so the
false-positive rate is 0.0000 exactly. Two mechanisms, both quantified:

1. **Cost.** Every surrogate trade pays the frozen round-trip: **0.032–0.038 R
   per trade on 1d and 0.111–0.122 R on 4h.** The 4h cells are cost-dominated —
   12% of a risk unit per round trip before anything else happens.
2. **The surrogate keeps the asset's real drift.** §6 re-centres the null to
   match the original series' mean log return *exactly* — deliberately, so the
   benchmark is not inflated. But that means the null world is not
   zero-expectancy for a **directional** strategy: it is "no edge *beyond the
   drift*". Shorts therefore start behind. Measured surrogate gross R:

   | cell | gross R | predicted from drift × holding ÷ atr_frac |
   |---|---|---|
   | rsi2_long_1d | **+0.0029** | ~0 ✓ optional stopping |
   | rsi2_short_1d | **−0.0960** | −0.055 (mean-based, an underestimate) |
   | rsi14_long_1d | −0.0264 | — |
   | rsi14_short_1d | **−0.2970** | −0.13 |

   The long cells sit at ~0, exactly as the optional-stopping theorem requires
   on a re-centred martingale, which is the evidence that the machinery is
   sound. The short deficit runs ~1.8–2.3× the mean-based prediction because
   holding time is path-correlated (losers are carried to the stop, winners cut
   at the midline), and that convexity is not captured by multiplying by a mean.

And the failure is two-sided: re-centred to zero, the same t-test fires at
**17.9%** against a nominal 5%, because stop asymmetry leaves the per-trade
distribution skewed **−0.75 to −1.97**. The t-test is both mis-*shifted* and
mis-*shaped* here. It was the wrong instrument.

### 0c. What this does NOT change

The t-test was never the screen's decision rule. Prereg 90 §7 already gates
cells on `null_pctile ≥ 95` — the surrogate-referenced test. The t-test
appeared **only** inside the Stage-0 SIZE measurement. So this is a
measurement-instrument defect, not a decision-rule change, and §7 is carried
over verbatim rather than reconsidered.

### 0d. One bug found and fixed before any outcome was read

Short P&L was computed as `entry/exit − 1`. That is the correct P&L divided by
the **exit** price rather than the entry: it understates losses, overstates
gains, and by Jensen's inequality carries a **positive** expected value for
shorts even on a pure martingale — a bias aimed squarely at half the grid.
Corrected to `1 − exit/entry` in both `research/screen_rsi_mr_powered.py` and
`research/sweep_rsi_mr_rank.py`. Every surrogate figure in §0b is post-fix.

Pinned by two new tests: `test_short_pnl_divides_by_the_entry_price_not_the_exit`
and `test_long_and_short_paths_are_mirror_images`, the latter feeding the long
path a series and the short path its multiplicative mirror and asserting the
two select **identical exit bars** — the check that separates "shorts are
genuinely worse on this data" from "the short branch is miscoded". It passes,
so the drift explanation in §0b stands on evidence rather than assertion.

---

## 1. The amendment — Stage 0 SIZE (replaces prereg 90 §5 SIZE only)

**SIZE is measured on the surrogate-referenced percentile test**, which is the
statistic §7 actually decides on:

> A cell "fires" when its observed statistic exceeds the 95th percentile of the
> distribution of that same statistic, computed the same way, on surrogates.

Estimated **leave-one-out**: each of the K surrogate reps is treated in turn as
the observation and scored against the other K−1. Stated up front so it is not
mistaken for a finding: **this returns ≈0.05 by construction.** It is a sanity
assertion that the plumbing is wired correctly — it is not evidence that the
instrument works, and it must not be reported as such.

Pass band unchanged: **[0.02, 0.08]**. Outside it the screen is VOID.

Cost, drift and skew now appear identically on both sides of the comparison and
cancel — which is precisely why this statistic is calibrated where the t-test
was not.

## 2. The evidence that the instrument works — POSITIVE CONTROL (new, binding)

The check that carries real weight, and the one prior rounds got wrong:

1. Take surrogate (edge-free) universes.
2. **Plant a known effect of exactly the MDE magnitude** — add `MDE_R` risk
   units of edge to each entered trade, so the planted effect is by
   construction the smallest the power calculation claims to detect.
3. Run the percentile test.

**Pre-committed bars:**

| Measurement | Required |
|---|---|
| Detection rate on planted universes | **≥ 0.60** (the calculation predicts ~0.80) |
| Detection rate on unplanted twins | **≤ 0.10** |

Failing either → **VOID**, no outcome read. A planted-detection rate below 0.60
would mean the MDE is optimistic and the POWER pass in §0a was not real.

## 3. Everything else — carried over unchanged

§2 data (6 panels; roll-artifact clearance), §3 per-class costs (22/5/4/5/2 bps
with 2× stress and a breakeven-cost column), §4 the eight cells and every frozen
execution rule, §6 the null generator, §7 the verdict table and gate set, §8 the
not-covered list — all as written in `90_prereg_rsi_mr_powered.md` and unchanged
here. The grid is the same eight cells; nothing was added, removed, or re-tuned.

**No parameter in §4 may be edited post-outcome.** This amendment is permitted
only because no outcome has been read.

## 4. Reproduction

```
venv/Scripts/python.exe research/_ohlcv_cache.py --harvest
venv/Scripts/python.exe research/screen_rsi_mr_powered.py
```

The screen verifies this file's sha256 before computing anything and aborts
with `PREREG HASH MISMATCH` if a single byte differs.
