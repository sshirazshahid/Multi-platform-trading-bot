# 02a rev3 — Screener Verdict: Candidate A (Cross-venue funding-rate dispersion)

Agent: edge-screener · Date: 2026-07-09 · Supersedes: `02a_rev2_screener_dispersion.md` (NO_GO)
Mandated by: `03_rev2_audit_findings.md` Finding A1 (per-fold round-trip cost artifact) +
Disposition A "Required follow-up (legitimate NEW pre-registration)".
Data root: `data/funding_history/` (backfilled real funding — REUSED, no new harvest).

---

## ⚠ PRE-REGISTRATION (written and frozen BEFORE any rev3 result was computed)

Everything in this section is fixed in advance. Results are appended in a separate
"RESULTS" section BELOW this line and this text is never edited after the fact. A failed
gate fails — no post-hoc threshold tuning.

### Why a rev3 at all (the audited defect being fixed)
The rev2 walk-forward charged `rt_cost / len(te)` per test settlement, i.e. **one 4-leg
round-trip per CV fold**. Pooled over `n_splits` folds the OOS carry therefore carried
`n_splits × rt_cost` — a cost that scales with a cross-validation hyperparameter, not with
the strategy's actual trading. The auditor proved the train-chosen direction is constant
across folds (`fold_dirs = [1,1,1,1]`), so a real delta-neutral harvester opens **once** and
holds, paying **one** round-trip. rev3 replaces the per-fold cost with the audited-correct
**hold-until-sign-flip** model. This is a cost-model correction, NOT a gate loosening.

### Hypothesis (unchanged from rev1/rev2)
A delta-neutral cross-venue pair — LONG the low-funding venue, SHORT the high-funding venue —
harvests the per-settlement funding differential and, after the full 4-leg round-trip + slippage,
clears zero out-of-sample. Direction is chosen only from past (train) data.

### Universe (CHANGED — the registered fix)
- **binance ∩ bybit settlements only.** Bitget (and its ~100-record public cap) is DROPPED.
- All coins with a funding-history CSV on **both** binance and bybit on the common 8h
  settlement grid. The inner-join is over these two venues only.
- Floor (frozen from rev1): ≥ 60 aligned settlements/coin on ≥ 2 coins. Below floor →
  INSUFFICIENT_DATA, fail-closed.

### Sample period
Whatever the binance∩bybit inner-join yields per coin from the EXISTING CSVs — multi-year
(2021-… where both venues carry the coin). No new data harvested.

### Execution & cost model (CHANGED — hold-until-sign-flip)
- Walk-forward, purged + embargoed (`core.walk_forward.WalkForward`, anchored, n_splits=4,
  embargo=1). Direction for each TRAIN fold = sign of the train-fold mean differential,
  applied UNSEEN to that fold's TEST settlements (no look-ahead).
- The concatenated OOS timeline is partitioned into **contiguous same-direction runs =
  held positions**. A position opens at the first test settlement and again ONLY when the
  applied direction FLIPS versus the previously-held direction.
- **ONE 4-leg round-trip is charged per held position**, amortized evenly over that
  position's actual settlements. Total OOS cost = (number of positions) × rt_cost — decoupled
  from `n_splits`. When direction never flips, total cost = exactly ONE round-trip.
- Cost numbers FROZEN from rev1 (unchanged): per-fill fees mirror `config.FEE`
  (binance taker 5bps / bybit taker 6bps), slippage 5bps on each of the 4 fills →
  taker round-trip = 2·5 + 2·6 + 4·5 = **42 bps**. Taker is the honest default; the OOS
  series is taker-only (one series per pair).

### Gates (FROZEN from rev1 — all must pass for GO)
1. OOS mean net carry > 0 (best pair, after the hold-model cost)
2. DSR ≥ 0.10, with **n_trials = number of coin-pair strategies tested** (= #coins, one
   venue-pair per coin under binance∩bybit)
3. PBO ≤ 0.50 (CSCV across the pair strategies)
4. OOS-WR ≥ 0.55 (per-settlement, pooled walk-forward)
5. Monte-Carlo P(total > 0) ≥ 0.95
6. Monte-Carlo maxDD p95 ≤ 0.25
Purged/embargoed walk-forward throughout. **Fail closed on anything uncomputable (NaN).**

### What NO_GO looks like (declared in advance)
Any one of: best-pair OOS mean ≤ 0; DSR < 0.10; PBO > 0.50; OOS-WR < 0.55; MC P(>0) < 0.95;
MC maxDD p95 > 0.25; or any required gate NaN/uncomputable. Selection of the best pair is
penalized through DSR's n_trials. A merit NO_GO here (floor cleared, gates ran) qualifies for
a `refuted-families-ledger` row for the binance∩bybit hold-until-flip configuration.

### What GO looks like (declared in advance)
ALL six gates pass on the best pair after the n_trials penalty. GO does not move capital — it
routes the candidate to `honesty-auditor` then a log-only shadow probe.

---

<!-- RESULTS APPENDED BELOW THIS LINE — pre-registration above is frozen and unedited -->

## RESULTS (computed after the pre-registration above was frozen)

### Config echo
- Universe: **binance∩bybit only**, 15 coins, one venue-pair each → **15 coin-pair
  strategies** (n_trials = 15).
- Coverage (aligned settlements/coin, multi-year): 3,487 (SUI) → 6,044 (BTC/ETH/LINK/LTC),
  ~1,162–2,014 days each. All 15 clear the 60-settlement floor → gates EVALUABLE.
- Cost: hold-until-sign-flip, ONE 42 bps 4-leg round-trip per held position, amortized over
  its settlements (decoupled from n_splits). OOS series 2,788–4,832 settlements/pair.
- **Position count / avg hold:** the train-chosen direction is highly persistent across
  folds, so each pair holds essentially ONE position over its whole OOS window (≈ 1
  round-trip per pair; avg hold ≈ the full 2,788–4,832-settlement OOS span). This is exactly
  the audit-A1 behaviour: cost no longer scales with fold count.

### Gate-by-gate (best pair = ZEC binanceL/bybitS, selected by OOS mean; n_oos = 4,048)

| Gate | Threshold | Result | Pass |
|---|---|---|---|
| OOS mean net carry > 0 | > 0 | **+0.4491 bps/settle** | ✅ |
| DSR (n_trials = 15) | ≥ 0.10 | **0.9998** | ✅ |
| PBO (CSCV across 15 pairs) | ≤ 0.50 | 0.4209 | ✅ |
| **OOS-WR (per-settlement)** | ≥ 0.55 | **0.3782** | ❌ |
| Monte-Carlo P(total > 0) | ≥ 0.95 | 1.000 | ✅ |
| Monte-Carlo maxDD p95 | ≤ 0.25 | 0.0326 | ✅ |

Selected pair fails **one** frozen gate: OOS win rate 0.378 < 0.55.

### What the corrected model shows (and how it differs from rev2's stated reason)
The audit was right that rev2's "sign non-persistent → reverses OOS" headline was an artifact
of the per-fold cost. Under the corrected hold-model on multi-year binance∩bybit data the
differential's sign IS persistent enough to produce a **positive** OOS mean carry on most
pairs (best +0.45 bps/settle, DSR ≈ 1.0, MC P(>0) = 1.0, MC maxDD tiny). **BUT** the signal is
low-win-rate / fat-tailed: only ~38% of individual settlements are on the profitable side —
the positive mean is carried by a minority of large-differential settlements. Under the frozen
**OOS-WR ≥ 0.55** floor (which exists precisely to reject a mean rescued by a few outliers),
this is a **NO_GO**. The failure reason is now the honest one (low per-settlement win rate),
not the refuted "sign reversal" claim.

Per-pair OOS (mean bps / WR): ZEC +0.449/0.378 · SUI +0.257/0.526 · XRP +0.197/0.460 ·
TRX +0.173/0.444 · GRT +0.152/0.375 · SOL +0.153/0.470 · BNB +0.146/**0.626** ·
DOGE +0.130/0.417 · LTC +0.055/0.403 · LINK +0.027/0.353 · ETH +0.011/0.431 ·
BTC −0.008/0.426 (ADA/ALGO/ATOM similar). Note BNB alone clears WR 0.626 but is not the
best-by-mean pair; the frozen rule selects the best OOS mean (ZEC) and it must pass all gates.

## VERDICT: NO_GO

Merit NO_GO (floor cleared, all six gates ran). The binance∩bybit hold-until-sign-flip
configuration produces a positive-mean, DSR≈1, low-drawdown OOS carry, but its per-settlement
OOS win rate (0.38) fails the frozen OOS-WR ≥ 0.55 floor. Selection over 15 pairs is DSR-
penalised (n_trials = 15) and still passes DSR; the binding failure is win-rate. No capital
moves. This is the configuration the auditor said was UNTESTED — it is now tested and is a
NO_GO on the win-rate gate, not the refuted sign-persistence reasoning. Submit to
`honesty-auditor` for adversarial review.

### JSON verdict
See `02a_rev3_screener_dispersion.json`.

