# PREREG 68 — TP-geometry replay on realized MFE/MAE (PAPER cohort)

**Status:** PRE-REGISTERED. Written and hashed BEFORE any outcome was
computed. Any edit after the hash is a NEW pre-registration, not a tweak.

**Date:** 2026-08-12
**Author:** Claude (agent), under the owner's standing profitability goal.
**Edge-queue row:** 6 (TP-exit geometry) — previously LOCKED pending SP1
n>=30; this replay is measurement-only and places no trade.

## 1. Question

Does any fixed take-profit geometry, applied to the SAME realized entries,
produce positive after-cost expectancy?

The current AccBand geometry books TP at `frac x SL_distance`
(frac buy=0.45 / sell=0.35) against a ~0.80% ATR-derived stop. The
2026-08-12 diagnostic established that gross PnL is negative pre-fee
(30d: -37.19 over 279 trades), which means **the expected answer is NO**.
This screen exists to falsify the remaining hypothesis that the entries are
directionally fine and only the exit geometry is wrong.

## 2. Hypothesis (H1) and null (H0)

- **H0 (default, retained unless falsified):** no TP multiple produces
  positive after-cost expectancy. The entries carry no directional edge.
- **H1:** there exists a TP multiple `k` (TP = k x SL_distance) whose
  after-cost mean PnL per trade is > 0 with a 95% CI excluding zero, and
  which survives the multiplicity correction in section 6.

## 3. Data (frozen before outcomes)

- Source: `data/warehouse.sqlite`, table `trades`, opened `mode=ro`.
- Population: `status='CLOSED' AND mfe IS NOT NULL AND mae IS NOT NULL AND
  entry_stop_px > 0 AND entry_px > 0`.
- Count at pre-registration time: **275 trades**. (A population size,
  counted before any outcome — not a result.)
- `mfe`/`mae` are FRACTIONS of entry price: mfe >= 0 is the best favorable
  excursion, mae >= 0 the worst adverse excursion.

## 4. Replay model (frozen)

For each trade, with `sl_frac = |entry_px - entry_stop_px| / entry_px`:

```
tp_frac = k * sl_frac
hit_sl  = (mae >= sl_frac)
hit_tp  = (mfe >= tp_frac)
```

Exit rule, **SL-first** (the conservative tie-break used everywhere in this
repo — a bar touching both is booked as the stop):

```
if hit_sl:    outcome = -sl_frac      # stop
elif hit_tp:  outcome = +tp_frac      # target
else:         outcome =  0.0          # time exit, marked flat
```

**KNOWN LIMITATION, stated before results (binding):** MFE/MAE are
path-agnostic — they record the extremes but not their ORDER. A trade whose
MAE preceded its MFE is indistinguishable from the reverse. SL-first makes
this conservative for tight TPs but **optimistic for wide TPs**, because a
trade that ran to a large MFE only after nearly stopping out is booked as a
clean win. Therefore: a POSITIVE result at high `k` is NOT decision-grade
and must be re-run on per-bar OHLCV before any use. A NEGATIVE result is
sound — the optimism runs against H0, so failing to find edge under a
model tilted in H1's favour is strong evidence.

The time-exit is booked at **0.0**, not at the realized close. This is
deliberate: the realized close embeds the CURRENT geometry's exit, which is
the thing being replaced.

## 5. Costs (frozen, applied identically to every arm)

- Fee: **6.0 bps per side** = 12 bps round trip (repo `FEE_BPS_PER_SIDE`).
- Slippage: **5 bps** open + **5 bps** close; **10 bps** when the exit is the
  stop (fast-move fills are worse) — the `shadow_resolver` constants.
- Total cost is subtracted from every arm's gross, in fraction units.

## 6. Grid and multiplicity (frozen)

- `k in {0.35, 0.45, 0.6, 0.8, 1.0, 1.25, 1.5, 2.0, 2.5, 3.0}` -> **m = 10**.
- Bonferroni: an arm passes only at **alpha = 0.05 / 10 = 0.005**
  (two-sided), i.e. its mean must exceed **2.807** standard errors.
- Reporting every arm is mandatory. Selecting the best arm post-hoc without
  the correction is exactly the failure this section prevents.

## 7. Decision rule (frozen)

- **GO** requires ALL of: after-cost mean PnL/trade > 0; the Bonferroni-
  corrected CI excludes zero; **n_effective >= 100**; and the arm is not the
  grid edge (`k = 3.0`) — an edge-of-grid winner means the optimum lies
  outside the tested range and is not a result.
- Anything else is **NO_GO**, recorded to the refuted-families ledger.
- A GO authorizes **only** a follow-on per-bar OHLCV replay (per §4), never
  a live or paper config change. No result of this screen may alter
  `ACC_BAND_*`, `MCP_ENTRY_MIN_SCORE`, or any runtime flag.

## 8. Expected outcome

**NO_GO.** Gross is already negative before fees, and a replay cannot create
directional edge the entries do not have. Predicting the result in advance
is the point: a screen whose outcome is acceptable either way.
