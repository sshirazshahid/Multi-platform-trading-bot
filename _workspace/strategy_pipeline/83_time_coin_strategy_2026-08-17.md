# 83 — Profitable time / coins / strategies: two are noise, one is real

**Type:** MEASUREMENT with multiplicity control. Read-only. **No config
changed.**

**Trigger:** owner — "Profitable time. Profitable coins. Profitable
Strategies."

Three questions, three different answers. Two are known ledger traps
(`hour-of-day / seasonality sweet spots` NO_EDGE 2026-06-02; the standing
no-symptom-blacklist rule), so both were tested with a **label-shuffle
permutation test** rather than reported at face value.

## 1. Profitable TIME — NOISE (p = 0.21)

n=2,547 closed trades, bucketed by UTC entry hour (n>=30 buckets):

| | hour | n | mean |
|---|---|---|---|
| best | **18:00** | 106 | **+28.3 bps** |
| 2nd | 16:00 | 92 | -6.1 bps |
| worst | 00:00 | 132 | -116.8 bps |

**Only 1 of 24 hours is positive.** Shuffle test: randomly relabel every
trade's hour 2,000 times and take the best-of-24 bucket each time. The
observed +28.3 bps is matched or beaten in **420/2,000 shuffles → p = 0.21**.

**Verdict: no tradeable hour.** Selecting the best of 24 buckets from noise
produces exactly this. Confirms the 2026-06-02 seasonality row.

## 2. Profitable COINS — NOISE (p = 0.37)

19 coins with n>=30:

| | coin | n | mean |
|---|---|---|---|
| best | **DOGE** | 104 | **+15.7 bps** |
| 2nd | XRP | 164 | -3.4 bps |
| worst | ALGO | 97 | -104.4 bps |

Same shuffle test over 19 symbols: **734/2,000 → p = 0.37.**

**Verdict: no tradeable coin.** DOGE is the winner of a 19-way lottery, not a
signal. Acting on it would also invert the no-symptom-blacklist rule
(whitelisting on noise).

## 3. Profitable STRATEGY — ONE REAL CANDIDATE

Resolved shadow evidence, all lanes with n>=25:

| lane | n | WR | mean | t | verdict |
|---|---|---|---|---|---|
| **zfade_4h_cfg365** | **157** | **72.6%** | **+0.5999** | **+1.60** | not yet sig |
| rsi2_4h_cfg226 | 273 | 69.6% | -0.0817 | -0.26 | not sig |
| shadow_v1 (MCP live path) | 92,408 | 34.7% | -0.7679 | **-19.72** | **negative-significant** |
| tsmom_20d_1h | 88 | 25.0% | -1.2044 | -3.72 | negative-significant |
| pullback_ma20_rsi14_4h | 46 | 13.0% | -2.8786 | -4.82 | negative-significant |
| tsmom_20d_4h | 31 | 12.9% | -3.2371 | -3.61 | negative-significant |

**zfade is the only lane with a positive mean.** It is a 4h z-score fade
(z20 ±1.5, EMA200 trend side, TP 1.0xATR / SL 2.4xATR), log-only since July.

Robustness (checked 2026-08-16 at n=154): remove the single best trade →
still +61.38 total; remove best 3 → +43.11; median trade +2.20; 49 distinct
symbols, max 7 per symbol; first-half mean +0.223 → second-half +0.795;
OOS-WR 76.6% vs the 55% gate floor.

**Trajectory (real, small):** n=154 → 157 and t=1.35 → **1.60** within hours.

## 4. Why the answers differ

Time and coin were **searched over** (24 and 19 candidates) — multiplicity
makes the best one meaningless without correction, and correction kills both.

zfade was **pre-registered before outcomes** (bundle prereg, frozen score
never re-tuned) and is measured on its own resolved events. That is the whole
methodological difference, and it is why one survives and two do not.

## 5. Verdict and what NOT to do

- **Do not** trade an hour filter or a coin whitelist — measured noise at
  p=0.21 / p=0.37.
- **Do not** promote zfade. t=1.60, CI still includes zero; its ledger row
  records it as a **1-of-432 sweep survivor** that FAILED the bundle's G2
  gate (WR above the 63-67 band) — plausible-unconfirmed, expectation
  NO-PROMOTE.
- **Do not** tune zfade. Post-hoc tuning is precisely what destroys a
  1-of-432 survivor.
- **Do** let it accrue. Roughly n≈300 is needed for the CI to clear zero —
  weeks, not days.

One-line answer: **the profitable thing is a strategy, not a time or a coin —
and it is not proven yet.**
