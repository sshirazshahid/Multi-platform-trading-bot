# Prereg 90 — RSI mean-reversion, powered pooled screen

**Status:** FROZEN. Committed BEFORE any outcome statistic is computed.
**Date:** 2026-08-22
**Family:** RSI mean-reversion — **REFUTED**
(`.claude/skills/refuted-families-ledger/SKILL.md:24`, "5 coins × 3yr screen,
NO_EDGE, 2026-06").
**Ceiling, fixed in advance:** this screen **cannot produce a GO** and cannot
reopen the family. The reopen bar (peer-reviewed 2025+ evidence quoted verbatim
that overturns the refutation) is **not met**. Its only permitted output is a
*measurement*: the smallest per-trade edge this repo's best data can detect,
and a verdict phrased **"no edge above X bps"** — never "no edge".
**Live trade authorized:** NO. **Promotion path:** NONE.

---

## 1. Why this screen exists

Four prior sweeps ran ~1.2M backtests and returned zero survivors. The fourth
run measured *why* and the answer was not "no edge":

- Runs 1–3 used a block bootstrap that **retained 67.2% of a planted signal**
  inside its own resampled blocks and **inflated the benchmark by 56%** — the
  null contained the alternative.
- The replacement null (per-segment sign randomisation) **passed a positive
  control**: 80% detection vs 7.5% on edge-free twins, false-alarm rate 0.0475
  against a nominal 5%.
- With the fixed null, EURUSD 4h Stage-0 returned **NOT RESOLVABLE**: MDE
  4.92 bps/bar = **oracle Sharpe 4.79**, against a pre-committed bar of
  ≤1.5 interpretable / ≥2.5 fail.

So the four "zero survivors" results were never evidence of no edge. They were
evidence the instrument could not see. The measured conclusion was: *"the lever
for resolvability is more bars or a coarser hypothesis, not a different null."*

**This screen pulls both levers at once.** 8 cells instead of 43,680, and the
deepest data the repo can reach.

## 2. Data — frozen inventory (surveyed 2026-08-22, before this document)

Provenance note: the design below was fixed **after** a data-availability
survey (bar counts, date ranges, gap diagnostics) and **before** any outcome
statistic. Inventory is not outcome; nothing is burned.

| Panel | Instrument | Source | 4h bars | 1d bars | Daily span |
|---|---|---|---|---|---|
| BTC | BTC-USDT perp | `data/ohlcv_cache` (venue) | 7,094 | 1,183 | 2023-05-26 → 2026-08-20 |
| ETH | ETH-USDT perp | `data/ohlcv_cache` (venue) | 7,085 | 1,181 | 2023-05-26 → 2026-08-18 |
| SPY | AMEX:SPY | `data/tv_cache` (TradingView) | 2,120 | 8,447 | 1993-01-29 → 2026-08-20 |
| GOLD | COMEX:GC1! | `data/tv_cache` | 2,618 | 12,989 | 1975-01-01 → 2026-08-20 |
| CRUDE | NYMEX:CL1! | `data/tv_cache` | 2,618 | 10,908 | 1983-03-29 → 2026-08-20 |
| EURUSD | FX:EURUSD | `data/tv_cache` | 2,629 | 14,300 | 1971-01-03 → 2026-08-20 |

Harvest: `venv/Scripts/python.exe research/_ohlcv_cache.py --harvest`
(via `scripts/tv_client.py`, the public anonymous chart websocket — deep
history without transiting an LLM context). `data/` is gitignored; the repo is
PUBLIC and no artifact is committed.

### 2a. Roll-artifact clearance (blocking, run before this freeze)

TradingView's `GC1!`/`CL1!` continuous series splice contracts, and
`fetch_ohlcv` passes `"adjustment": "splits"` — an equity flag that does
nothing for futures rolls. A roll discontinuity reads to an RSI as an extreme
excursion that mean-reversion then "reverts into": the single most likely
generator of a spurious winner. **Measured:**

| Panel | daily bars | gaps >5σ | gaps >10σ | largest gap | day-of-month of >5σ gaps |
|---|---|---|---|---|---|
| SPY | 8,447 | 8 | 0 | 10.45% | 13, 17, 24, 9 |
| GOLD | 12,989 | 12 | 0 | 9.55% | 16, 6, 1, 19 |
| CRUDE | 10,908 | 11 | 0 | 23.44% | 17, 23, 4, 13 |
| EURUSD | 14,300 | 12 | 1 | 6.19% | 6, 16, 19, 11 |

No clustering on roll dates. Every largest gap is a dated macro event:
CRUDE 1991-01-16 (Desert Storm, −23.4%), CRUDE 2020-03-08 (OPEC+ price war,
−20.4%), SPY 2020-03-16 (COVID limit-down, −10.4%), SPY 2001-09-17 (post-9/11
reopen, −8.2%), EURUSD 1973-02-11 (Bretton Woods collapse, +6.2%). CRUDE has
**zero** calendar holes >7 days and its April-2020 window holds 25 rows with
min close $11.57 — the continuous series had already rolled to June, so no
negative print and no hole. **Panels cleared for use.**

## 3. Frozen cost model — per asset class

A single 22 bps crypto-perp cost applied to SPY would kill the tradfi panels
for a reason that has nothing to do with edge. Costs are frozen **per class**,
computed at each panel's **median historical price** (not today's — SPY's
median close is $136.63 against $762.60 today; today's price understates
historical cost ~5×).

| Panel | Arithmetic | RT cost (frozen) |
|---|---|---|
| BTC, ETH | `core.cost_model.round_trip_cost('bybit')` = 12 bps fee + 10 bps slippage | **22.0 bps** |
| SPY | 1¢ spread on median $136.63 = 0.73 bps; + commission + 1 bp slippage | **5.0 bps** |
| GOLD | $0.10 tick on median $425.30 = 2.35 bps; + $2.50/side + 1 bp slippage | **4.0 bps** |
| CRUDE | $0.01 tick on median $38.08 = 2.63 bps; + $2.50/side + 1.5 bps slippage | **5.0 bps** |
| EURUSD | 1 pip on median 1.1100 = 0.90 bps; + 0.6 bp slippage | **2.0 bps** |

**Cost stress:** every cell is evaluated at 1× and **2×** these values. A cell
whose sign flips under 2× is reported as cost-fragile.
**Assumption-free companion:** each cell additionally reports its **breakeven
round-trip cost** — the RT cost at which its edge reaches zero — so the verdict
does not depend on believing this table.
**Known limitation, stated up front:** SPY pre-2001 traded in eighths/sixteenths
(12.5¢/6.25¢ ticks), and 1970s–80s FX and futures spreads were far wider than
these constants. The frozen table is therefore **optimistic for the early
history** of SPY/GOLD/CRUDE/EURUSD. This biases toward finding edge, not away.

## 4. The hypothesis — 8 cells, pooled across all 6 panels

One hypothesis, evaluated across six markets. **N = 8**, not 43,680: fewer
cells is the necessary lever, and cross-market breadth (not bar count) supplies
the power.

**Swept axes — the only three:**

| Axis | Values | Count |
|---|---|---|
| `rsi_len` | 2, 14 | 2 |
| `side` | long-oversold, short-overbought | 2 |
| `timeframe` | 4h, 1d | 2 |

**2 × 2 × 2 = 8 cells.** Everything else is FROZEN at published canonical
values and is **not** swept:

| Parameter | Frozen value | Provenance |
|---|---|---|
| Entry threshold | RSI(2): 10 / 90.  RSI(14): 30 / 70 | Connors RSI(2); Wilder RSI(14) |
| Exit rule | RSI closes back through 50 (midline) | canonical |
| Protective stop | 3.0 × ATR(14) from entry | wide, deliberately un-tuned |
| Max hold | 10 bars | Connors' canonical bound |
| Entry fill | **next bar's OPEN** after the signal bar closes | no same-bar fill |
| Exit fill (RSI/time) | that bar's CLOSE | — |
| Exit fill (stop) | long: `min(open, stop)`; short: `max(open, stop)` | **gap-through fills at the OPEN, never at the stop price** |
| RSI implementation | `utils.indicators.rsi` (Wilder, `ewm(com=n-1)`) | the audited canonical |
| ATR implementation | `utils.indicators.atr` | the audited canonical |

Three other `rsi()` copies exist in this repo and **disagree on the zero-loss
branch** (NaN / 50.0 / 100.0) — a silent-divergence trap that bites hardest at
RSI(2), where zero-loss windows are common. Only `utils.indicators.rsi` is
used, and a parity test pins it.

**Pooling and the test statistic.** Crude's daily σ is 2.47% against EURUSD's
0.60%; pooling raw returns would let crude dominate. Each trade's net return is
therefore expressed in **risk units**, `R = net_return / atr_frac_at_entry`,
which also makes the 3-ATR stop a common −3R floor across panels. A cell's
trades from all six panels are merged in chronological order into one series.
Headline reported in bps for interpretability; **gates decided on R**.

## 5. Stage 0 — read BEFORE any outcome

Both gates are computed and printed **before** the real-data statistic exists.

**SIZE.** The screen's decision rule is run on stressed no-edge synthetic
panels (sign-randomised real data, zero drift by construction). Measured
false-positive rate must land in **[0.02, 0.08]** against nominal α = 0.05.
Outside that band the screen is **VOID** and no outcome is read.

**POWER.** Minimum detectable effect at 80% power, two-sided, Bonferroni over
the 8 cells (α' = 0.05/8 = 0.00625, z = 2.734; z₀.₈₀ = 0.8416):

```
MDE_R        = (2.734 + 0.8416) × σ_R / √n
oracle_Sharpe = (MDE_R / σ_R) × √(trades per year)
```

Pre-committed bar, unchanged from the prior round:
**oracle Sharpe ≤ 1.5 → interpretable; ≥ 2.5 → UNDERPOWERED, outcome not read.**
Between 1.5 and 2.5 the cell is reported as BORDERLINE and its outcome is read
but explicitly labelled weak.

## 6. Null hypothesis

**Per-segment sign randomisation with exact re-centering.** The design that
passed the positive control (80% detection / 7.5% on edge-free twins / FPR
0.0475). Per panel:

1. Split the log-return series into contiguous segments (length 20 bars).
2. Flip the sign of every return in a segment with probability 0.5, i.i.d.
   across segments — destroys directional structure, preserves the volatility
   clustering and fat tails that make naive nulls too easy to beat.
3. Re-centre **exactly** so the surrogate's mean log-return equals the
   original's — the benchmark must not be inflated by a drift the real series
   has and the surrogate lacks.
4. Rebuild bars by cumulating; when a segment is flipped, reflect intrabar
   geometry with **high and low swapped** about the bar's open — a flipped
   down-bar's excursion must become an up-bar's excursion, or every stop test
   is biased.

`null_pctile` for a cell = the percentile of its observed statistic in the
distribution of the **same cell** computed on surrogates. Bar: **≥ 95**.

## 7. Verdict rules — fixed before outcomes

| Condition | Verdict |
|---|---|
| Stage-0 SIZE outside [0.02, 0.08] | `VOID` — no outcome read |
| Stage-0 POWER oracle Sharpe ≥ 2.5 | `UNDERPOWERED` — no outcome read |
| Stage-0 passes, no cell clears | `NO_EDGE_ABOVE_X_BPS`, X = measured MDE in bps |
| Stage-0 passes, a cell clears every gate | `MEASURED_POSITIVE` — **still NO_GO**; family refuted, reopen bar unmet |

Gates a cell must clear to be called "clears": pooled mean R > 0 after cost,
`null_pctile ≥ 95`, OOS-WR ≥ 0.55 (`core.promotion_gate.MIN_OOS_WR`),
DSR ≥ 0.10 (`MIN_DSR`) with `n_trials = 8`, PBO ≤ 0.5 (`MAX_PBO`) computed by
`core.stat_tests.trial_pnl_matrix` over **all 8 cells** (winners and abandoned
alike), and `core.decision.monte_carlo` capital preservation
(P(total>0) ≥ 0.95, maxDD p95 ≤ 0.25).

`MEASURED_POSITIVE` is not a promotion and not a reopen. It would be a
statement that a positive result was measured and that the ledger's standing
refutation is unchanged.

## 8. What is explicitly NOT covered

Any RSI variant outside the eight frozen cells; any threshold, exit, stop or
hold value other than those in §4; any asset outside the six panels; maker-only
execution; intraday timeframes below 4h. Each would require a **new** hashed
prereg. No parameter in §4 may be edited post-outcome — a re-tune after seeing
results burns this document and the run with it.

## 9. Reproduction

```
venv/Scripts/python.exe research/_ohlcv_cache.py --harvest
venv/Scripts/python.exe research/screen_rsi_mr_powered.py
```

The screen verifies this file's sha256 before computing anything and aborts
with `PREREG HASH MISMATCH` if a single byte differs.
