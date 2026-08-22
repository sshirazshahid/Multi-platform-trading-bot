# RSI mean-reversion: the ranked table, and the answer

**2026-08-22** · Lane 1 `research/sweep_rsi_mr_rank.py` · Lane 2
`research/screen_rsi_mr_powered.py` under prereg 91
(`5916c04b7bcc235850be51ff88fc729f48e8337008c30b77fbfacb71ba325da7`).

---

## The short version

You asked five times for a big RSI sweep across SPY, GOLD, CRUDE, FOREX and
CRYPTO, ranked by Sharpe, drawdown, win rate and profit factor, to find what
beats buy-and-hold. It ran: **903,168 backtests**. The winner looks superb —
**annualised Sharpe 9.56, 84.5% win rate, profit factor 14.07, a 2.8% maximum
drawdown, against buy-and-hold's Sharpe of 1.20.**

It is noise, and this time that is not an opinion. Two independent
demonstrations, then a real measurement:

1. That winner sits at the **80th percentile** of what the *identical search*
   produces on randomised noise. Nothing reached the 95th. On SPY 4h the
   noise's best (3.16) **beat the real data's best (1.30) by 2.4×**.
2. Across the 22 panels, the best Sharpe found is **inversely proportional to
   how much data the panel has** (corr −0.304). Same grid, same rules: give it
   more history and the edge evaporates — EURUSD 22.8×, CRUDE 4.2×, GOLD 3.1×,
   SPY 2.8×.
3. The separate pre-registered test that *can* resolve returns:
   **no edge above 18.8 bps per trade.**

That third line is the thing four previous rounds could not produce.

---

## Why this round is different from the last four

Four sweeps had already run — ~1.2 million backtests, zero survivors. Those
results were worth nothing, and the fourth round finally measured why: its
minimum detectable effect was **oracle Sharpe 4.79**. It could only have seen
an edge so enormous that someone who already knew the answer would earn a
Sharpe near 5. Nothing real is that big, so "zero survivors" meant "the
instrument is blind", not "there is nothing there".

Two levers fix that, and this round pulled both.

**More data.** `scripts/tv_client.py` — already in the repo, written for exactly
this problem — pulls deep history straight to parquet without transiting an LLM
context. It reached far past the 5,000-bar cap that throttled every prior run:

| | bars | back to |
|---|---|---|
| EURUSD daily | 14,300 | **1971** |
| GOLD (COMEX:GC1!) daily | 12,989 | **1975** |
| CRUDE (NYMEX:CL1!) daily | 10,908 | **1983** |
| SPY daily | 8,447 | **1993** |
| BTC / ETH hourly | 28,377 / 28,339 | 2023 |

**A coarser hypothesis.** Lane 2 tests **8 pre-registered cells**, not 43,680.

Result: minimum detectable effect fell from **oracle Sharpe 4.79 to 0.671** — a
**7× improvement**, and the first time in five attempts the question was
answerable at all.

---

## Lane 1 — the ranked table you asked for

37,632 variations × 6 assets × 4 timeframes = **903,168 backtests**; 624,288
produced ≥30 trades. RSI length {2,3,4,5,7,10,14,21} × entry threshold
{5…35} × exit threshold {40…70} × stop {1–3 ATR, none} × max hold {2…48} ×
both directions.

**Top 10 by annualised Sharpe:**

| # | panel | tf | rsi | entry | side | exit | stop | hold | **annSh** | total% | WR | PF | maxDD | **expo%** | **n** | B&H Sh |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | EURUSD | 4h | 21 | 35 | long | 50 | none | 48 | **9.56** | 67.4 | 0.845 | 14.07 | 0.028 | 110.7 | **148** | 1.20 |
| 2 | EURUSD | 4h | 10 | 35 | long | 60 | none | 48 | 9.13 | 110.1 | 0.765 | 4.48 | 0.090 | 285.4 | 361 | 1.20 |
| 3 | EURUSD | 4h | 14 | 35 | long | 55 | none | 48 | 8.56 | 85.6 | 0.773 | 5.67 | 0.080 | 196.7 | 242 | 1.20 |
| 4 | EURUSD | 4h | 10 | 30 | long | 60 | none | 48 | 8.27 | 74.1 | 0.793 | 6.60 | 0.050 | 152.1 | 193 | 1.20 |
| 5 | EURUSD | 4h | 7 | 35 | long | 65 | none | 48 | 8.21 | 117.7 | 0.714 | 3.08 | 0.103 | 360.2 | 504 | 1.20 |
| 6 | EURUSD | 4h | 21 | 30 | long | 50 | none | 48 | 7.89 | 29.1 | **0.976** | **99.9** | **0.000** | 31.4 | **42** | 1.20 |
| 7 | EURUSD | 4h | 7 | 30 | long | 65 | none | 48 | 7.39 | 85.2 | 0.731 | 3.59 | 0.070 | 227.9 | 316 | 1.20 |
| 8 | EURUSD | 4h | 14 | 35 | long | 50 | none | 48 | 7.16 | 64.9 | 0.756 | 4.41 | 0.098 | 140.9 | 242 | 1.20 |
| 9 | SPY | 4h | 7 | 35 | long | 70 | none | 48 | 7.11 | **526.4** | 0.854 | 7.91 | 0.455 | 288.4 | 295 | 2.53 |
| 10 | EURUSD | 4h | 5 | 35 | long | 70 | none | 48 | 7.00 | 111.0 | 0.700 | 2.34 | 0.132 | 414.5 | 656 | 1.20 |

**43,624 of 624,288 combos (7.0%) beat buy-and-hold on both Sharpe and total
return.** Their median exposure is 57.7% of bars.

### Why not one row of that is evidence

**The arithmetic of searching.** E[max Sharpe over N trials] ≈ √(2 ln N). At
N = 37,632 per panel that is **4.59 standard errors** of pure noise. A search
this wide *cannot fail* to produce a spectacular winner. Ranking is not a way
to find edge; ranking **is** the overfitting. The peer-reviewed reopen test on
this exact methodology (IJFE 10.1002/ijfe.2863) ranked ~7,851 variants, took
the top 15, added proper multiplicity control and genuine out-of-sample — and
**14 of 15 failed**.

**The two columns a normal leaderboard omits.**

`n` — row 6 shows a 97.6% win rate, an effectively infinite profit factor and a
**zero** drawdown. It has **42 trades**: 41 wins and 1 loss. Row 1's Sharpe of
9.56 rests on **148 trades**. These are small samples wearing a strategy's
clothes.

`null_pctile` — the decisive one. The same 37,632-cell search was re-run on 20
**sign-randomised surrogates** of each panel: series with the direction
destroyed but volatility clustering, fat tails and drift preserved exactly.
The bar any row must clear is the *best* the search finds on noise, because
every row competed in the same search.

| panel | real best | null median | null p95 | **null max** | **percentile of real** |
|---|---|---|---|---|---|
| EURUSD 4h | 1.5568 | 0.9952 | 2.0316 | 2.3103 | **80** |
| SPY 4h | 1.2996 | 1.0192 | 1.8488 | **3.1649** | **70** |
| SPY 1h | 0.8131 | 0.7663 | 1.6203 | 1.7523 | **60** |
| CRUDE 4h | 0.9868 | **0.9569** | 1.2418 | 1.4227 | **50** |

Nothing reaches 95. CRUDE 4h's best real result sits at the null **median** —
a coin flip. And on SPY 4h, noise's best beat the real data's best by 2.4×.

### The sweep refutes itself, without needing the null

The panel summary alone settles it. Best Sharpe found, against how much
history the panel holds:

| panel depth | panels | mean best annSharpe |
|---|---|---|
| < 3,000 bars | 10 | **4.09** |
| 3,000–11,000 bars | 8 | 3.67 |
| > 11,000 bars | 4 | **1.45** |

corr(log bars, best Sharpe) = **−0.304**. Per asset — identical grid, identical
rules, only the depth differs:

| asset | 4h (shallow) | 1d (deep) | ratio |
|---|---|---|---|
| SPY | 7.11 (2,120 bars) | 2.51 (8,447) | 2.8× |
| GOLD | 5.48 (2,618) | 1.76 (12,989) | 3.1× |
| CRUDE | 6.00 (2,618) | 1.44 (10,908) | 4.2× |
| EURUSD | **9.56** (2,629) | **0.42** (14,300) | **22.8×** |

A real edge does not shrink when you give it more data to prove itself on; it
gets easier to see. This one is inversely proportional to sample size — the
signature of a search fitting noise. **The 9.56 Sharpe and the 0.42 Sharpe are
the same strategy family on the same instrument.** Only the amount of history
it had to hide in changed.

No leaderboard file was written, deliberately: a ranked artifact outlives the
caveats attached to it.

---

## Lane 2 — the test that could actually answer

8 cells (RSI {2,14} × {long,short} × {4h,1d}), every other parameter frozen at
published canonical values, pooled across all six markets, hashed before any
outcome existed.

### Stage 0, read before the outcome

| gate | measured | bar | verdict |
|---|---|---|---|
| SIZE | false-positive rate **0.0537** | [0.02, 0.08] | PASS |
| **POSITIVE CONTROL** | **0.835** detection of a planted MDE-size effect | ≥ 0.60 | **PASS** |
| POWER | oracle Sharpe **0.671**, MDE 0.194 R | ≤ 1.5 | PASS |

The positive control is the one that matters: plant an effect of exactly the
minimum-detectable size into edge-free universes and confirm the test finds it.
It did, 83.5% of the time, against a predicted ~80%. **The instrument works** —
which is what makes a negative result mean something.

### The outcome

**Verdict: `NO_EDGE_ABOVE_18.8_BPS` per trade.** 0 of 8 cells clear; 0 of 8
have a positive risk-adjusted mean; best null percentile 92, against a 95 bar.

| cell | n | mean R | mean bps | WR | PF | OOS-WR | null% | DSR | breakeven cost × |
|---|---|---|---|---|---|---|---|---|---|
| rsi2_long_1d | 2,569 | −0.041 | **+5.48** | **0.629** | 0.919 | 0.637 | 28 | 0.001 | −0.23 |
| rsi2_long_4h | 1,331 | −0.070 | −11.28 | 0.651 | 0.848 | 0.593 | **92** | 0.000 | +0.41 |
| rsi2_short_1d | 2,943 | −0.137 | −8.19 | 0.600 | 0.758 | 0.619 | 27 | 0.000 | −2.95 |
| rsi2_short_4h | 1,473 | −0.186 | −17.64 | 0.587 | 0.673 | 0.636 | 58 | 0.000 | −0.53 |
| rsi14_long_4h | 265 | −0.199 | −26.41 | 0.525 | 0.799 | 0.475 | 26 | 0.001 | −0.80 |
| rsi14_long_1d | 524 | −0.219 | +4.46 | 0.494 | 0.804 | 0.532 | 5 | 0.000 | −5.94 |
| rsi14_short_4h | 410 | −0.381 | −40.83 | 0.427 | 0.651 | 0.496 | 51 | 0.000 | −2.26 |
| rsi14_short_1d | 786 | −0.509 | −50.31 | 0.420 | 0.618 | 0.432 | 0 | 0.000 | −11.82 |

PBO 0.314 over the full 8-cell grid (winners and abandoned cells alike).

**Three things worth your attention:**

- **The win rate is genuinely high and it does not help.** RSI(2) longs win
  **62.9%** and **65.1%** of the time — and every profit factor is below 1.0.
  The wins are smaller than the losses. That is the same geometry this repo has
  hit before, and no win rate rescues it.
- **`rsi2_long_4h` is the only near miss** (null percentile 92) and its
  breakeven-cost multiple is **+0.41**: gross profit covers only 41% of the
  round-trip cost. It would need costs cut by **59%** to reach zero, and it is
  a crypto-4h cell paying 22 bps. Not reachable.
- **`rsi2_long_1d` earns +5.48 bps per trade yet is negative risk-adjusted**
  (−0.041 R). Losses concentrate in the low-volatility trades. A dollar-average
  that looks positive can still be a losing risk profile.

---

## What was fixed on the way

Three defects, all caught before any outcome was read:

**A short-side P&L bug.** Short return was `entry/exit − 1` — the correct P&L
divided by the **exit** price instead of the entry. It understates losses,
overstates gains, and by Jensen's inequality books a *positive* expected value
for shorts on a pure martingale: a bias aimed at half the grid. Now
`1 − exit/entry`, pinned by a mirror test that feeds the long path a series and
the short path its multiplicative mirror and asserts identical exit bars.

**An indicator warm-up artefact.** `pandas.ewm(adjust=False)` seeds with the
first observation rather than converging from zero, so opening bars carry an
RSI up to **45 points** away from the converged recursion. Five time constants
are now discarded before any signal is taken.

**A void run, honoured.** The first Lane 2 execution failed its own SIZE gate
(false-positive rate **0.0000** of 1,600 — a t-test against zero can never fire
when the surrogate null sits at t = −2.467, because of cost plus the drift the
null deliberately preserves). The pre-committed consequence is that **no
outcome is read**, and the code did exactly that. Prereg 91 then replaced only
the SIZE *estimator*, leaving hypothesis, null and verdict rules untouched.

**Roll artifacts, checked and cleared.** GC1!/CL1! are spliced continuous
contracts and `fetch_ohlcv` passes an equity-only "splits" adjustment, so a
roll discontinuity would read to an RSI as an extreme excursion to revert into.
Measured 11–12 gaps >5σ per panel, **zero clustering on roll dates**, and every
outlier is a dated macro event: Desert Storm (−23.4%), the OPEC+ price war
(−20.4%), COVID limit-down (−10.4%), the post-9/11 reopen (−8.2%), the Bretton
Woods collapse (+6.2%).

---

## Limits of this result

Stated so they are not discovered later as gotchas:

- The `null_pctile ≥ 95` bar is **per cell**. Across 8 cells the family-wise
  false-positive rate is 33.7%; DSR (n_trials=8) and PBO are the multiplicity
  controls layered on top of it, not `null_pctile` alone.
- Lane 1 allows **overlapping signals**, so one excursion can be counted more
  than once and its effective sample is smaller than `n` suggests. The null
  runs through the identical construction, so the comparison is like-for-like,
  but the raw `n` column is optimistic. Lane 2 enforces strict non-overlap.
- The frozen cost table is **optimistic for the early history**: SPY traded in
  eighths/sixteenths before 2001, and 1970s–80s FX and futures spreads were far
  wider than the constants used. That biases toward finding edge, not away.
- Lane 1's null used 20 surrogate reps on 4 panels — enough to place a result
  at the 50th–80th percentile, not enough to distinguish the 96th from the 99th.
  It did not need to be: nothing came close.
- The verdict covers this family, these six instruments, at 4h and 1d.
  Sub-4h timeframes, maker-only execution, and any other entry/exit geometry are
  untested and would need their own pre-registration.

---

## "Then wire them together"

Nothing survived, so there is nothing to wire, and combining null results
compounds costs without adding edge. **No code path was changed, no gate was
touched, no threshold was moved.** Everything here is research-only.

The standing ledger verdict is unchanged — RSI mean-reversion stays refuted —
but it now carries a number it did not have before: **no edge above 18.8 bps
per trade**, measured on an instrument proven to detect an effect of that size
83.5% of the time.

That is the difference between a result and a shrug. It is also the honest
answer to "find the strategy that beats buy and hold": **on this family, across
these five asset classes, at this resolution — there isn't one.**
