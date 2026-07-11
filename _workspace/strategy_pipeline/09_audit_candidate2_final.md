# 09 — Final Adversarial Audit: `pre_unlock_short_capital_scaled` (08b GO)

Auditor: honesty-auditor | 2026-07-11 | Inputs: 08b (.md/.json), 08d, 07 scout brief,
`research/screen_preunlock_short.py`, `research/screen_listing_short.py` (reused helpers),
`scripts/backfill_unlock_calendar.py`, `data/unlock_calendar/`, `data/funding_history/`,
`_workspace/tmp_phase2_run/{result_08b,diag_08b}.json`.
Probe code: `_workspace/tmp_phase2_audit/audit_08b_fragility.py` (throwaway; full screen re-run
+ subset gate recomputation). No live code touched.

## VERDICT: **CONFIRMED — log-only shadow probe ONLY, W1+W2 arms, W3 excluded, 6 binding conditions**

The GO survives all 7 attacks as registered. No leakage mechanism that manufactures the result,
no cost softening, no gate loosening, exact reproduction. But the sample is FRAGILE in ways the
owner must see undiluted (findings F1–F3 below): the sign of the edge is robust; the *gate-clearing*
is not robust to single-symbol perturbations, and all profit comes from one regime. That fragility
is exactly what a log-only probe resolves — it does NOT justify live capital and it does NOT
justify skipping the frozen promotion gate later.

## Reproduction (independent re-run, 2026-07-11)

`research/screen_preunlock_short.py` re-executed → **byte-identical gate table** to
`result_08b.json` (verdict GO; W1/W2/W3 n=32/36/41; DSR 0.8468/0.9985/0.9749; PBO 0.4517;
AUC 0.6927/0.6823/0.5907; OOS-WR 0.875/0.75/0.719; MC-p95-DD 0.0616/0.0351/0.0159; realized-MTM
0.0726/0.0261/0.0123). Deterministic, reproducible.

**Frozen-assumption diff (source swap only):** every constant in the executed script matches the
frozen 08b text — ratio ≥0.10, window 2023-06→2026-06, W1/W2/W3 = −28d/−14d/T→T+3d, 3%/12%
(MAX_CONCURRENT=4), n_trials=3, MIN_N=30, gates 0.10/0.5/0.55/0.60/0.95/0.25, fees from
`config.FEE` (5/6/6 bps) + 5 bps slip/side, AUC constants 0.20 and 10. Only the calendar
transport changed (paywalled api.llama.fi → keyless datasets bucket, per 08d C2-a). CONFIRMED
untouched.

**End-to-end spot-check (ARB 2024-03-16 W1):** entry 2024-02-17 00:00 close 2.0137, exit
2024-03-16 01:00 close 1.9030, gross short +5.50%, realized funding +4.53% (short RECEIVED —
March-2024 long froth; real prints, plausible magnitude), costs −0.20% → net +9.82% = screen's
number. Math verified at the row level.

## The 7 attacks

### 1. Leakage / look-ahead — SURVIVES (one bounded caveat, one unverifiable-but-self-resolving)
- **Calendar knowability:** cliff timestamps are contractual vesting-schedule outputs
  (second-precision, deterministic), published at TGE — knowable at T−28d in principle.
  UNVERIFIABLE locally: the bucket snapshot is as-of 2026-07-11; retroactive schedule revisions
  (delayed unlocks) or late DefiLlama coverage of 2023 events cannot be excluded without
  historical snapshots. Bounded (cliffs of this size rarely move) and the forward probe
  self-resolves it (uses the as-of-entry calendar). → F5, MEDIUM, carried.
- **Perp-existence-at-event-time:** verified NOT leaked. Venue matching uses current
  `load_markets`, BUT `window_funding_covered` requires funding prints bracketing the hold —
  a perp that didn't exist at event time has no prints and the event is excluded. Checked:
  `binance_SUI.csv` spans 2023-05-03→2026-07-11, covering the earliest W1 entry (2023-06-04).
- **AUC score frozen pre-outcome:** code (`AUC_RATIO_SCALE=0.20`, `AUC_FUNDING_W=10.0`) matches
  the addendum §6 exactly. Corroborating evidence it was NOT outcome-tuned: on the 2025+ subset
  the score is ANTI-predictive (AUC 0.246 W1 / 0.459 W2) — a peeked score would discriminate
  everywhere. (This cuts the other way too — see F4.)
- **Funding in the score:** `funding_print_nearest(entry)` can select the settlement up to ~4–8h
  AFTER entry — a bounded look-ahead in the SCORE input only (not PnL; venue-displayed predicted
  funding closely tracks it). → F7, LOW.
- **Funding in PnL:** realized prints summed over [entry, exit) — realized-in-hindsight is the
  CORRECT convention for a cost actually paid. Not leakage.

### 2. Multiplicity — SURVIVES (recomputed under harsher counting)
Visible trial history = exactly the 3 registered windows (the 07-08 "post-unlock" idea became W3
and is counted; no abandoned local parameterizations found in 07/08b — thresholds/offsets came
from the external anchor, not local search). The real multiplicity risk is the anchor itself
(unlocks.app tried windows/thresholds on 236 events that overlap this sample — the hypothesis is
not independent of the test data). Stress-tested: **W1 DSR = 0.658 at n_trials=10, 0.551 at
n_trials=20; W2 = 0.983/0.959** — the DSR≥0.10 gate holds even charging ~20 implicit trials.
PBO 0.452 passes but is within 0.05 of the 0.5 ceiling — noted, not failed.

### 3. Cost realism — SURVIVES
Venue taker (5/6/6 bps) + 5 bps slip per side charged both legs; realized funding
settlement-aligned to the short, both signs (W1 mean −1.50%, 59% of events funding-negative —
the crowded-short-pays effect IS in the numbers; ARB shows the receive side). Spot-as-perp proxy
divergence is asserted, not quantified — but funding-arb keeps perp≈spot within ~±0.5%/leg
against a +9.8%/event mean; cannot flip the sign. Carried caveat (frozen convention, stated).

### 4. Small-N fragility — THE REAL FINDINGS (recomputed, `audit_08b_fragility.py`)

| W1 subset | n | mean | WR | DSR(3) | MC P>0 | verdict on frozen battery |
|---|---|---|---|---|---|---|
| full | 32 | +9.8% | 0.750 | 0.847 | 0.959 | PASS (as registered) |
| worst loss (BASED) removed | 31 | improves | improves | improves | improves | PASS — verdict does NOT flip |
| no SUI (−7 events) | 25 | +10.6% | 0.800 | 0.799 | 0.957 | **n<30 floor → INSUFFICIENT_DATA** |
| no GUN (−4 events) | 28 | +7.7% | 0.714 | 0.709 | **0.903** | **FAILS MC P>0 (and n<30)** |
| drop top-3 winners | 29 | +5.9% | 0.724 | 0.635 | **0.792** | **FAILS MC P>0** |
| one event per symbol | 19 | +8.8% | 0.789 | 0.632 | **0.892** | FAILS (n, MC) |
| 2023 events only | 8 | **−6.6%** | **0.375** | 0.058 | 0.234 | strategy LOST in 2023 |
| 2025+ events only | 22 | +14.6% | 0.864 | 0.916 | 0.999 | all the profit lives here |

W2 is materially more robust: no-SUI n=29 mean +12.7% DSR 0.9999; one-per-symbol n=22 mean
+15.3% DSR 0.9999; drop-top3 mean +6.8% but MC P>0 0.936 (marginal fail) and OOS-WR 0.278
(fail). W2 2023-only: −6.3%, WR 0.25.

- **F1 (HIGH): the n≥30 floor is met only via monthly-cliff pseudo-replication.** SUI's 7 events
  (2023) and GUN's 4 (2026) are near-contiguous 28d holds on the same token — economically ~2
  continuous multi-month shorts, not 11 independent bets. Unique symbols: 19 (W1) / 22 (W2).
  DSR/MC treat them as i.i.d.; independence is violated. Removing SUI alone drops BOTH windows
  below the frozen floor (25/29).
- **F2 (HIGH): single-regime profit.** Every dollar is from 2025-26 (downtape); 2023 was NET
  NEGATIVE in both windows (and in 2023 the control-adjusted drift also failed on several events
  — APT 2023-11 did not underperform its pumping control). Forward, a bull tape can make this
  probe bleed even if the idiosyncratic effect persists. Mitigant: sign is cross-sectionally
  consistent — 16/19 (W1) and 18/22 (W2) symbols have positive mean net.
- **F3 (MEDIUM): W1's gate pass is single-perturbation-fragile** (table above). W2 is the arm
  that survives perturbation; W1 should be treated as secondary evidence, not headline.
- Explicit answer to the audit question: the verdict does NOT flip on removing the single worst
  loss (it improves) and does NOT flip sign on removing SUI (it improves) — but the frozen gate
  battery would NOT have passed on no-GUN or drop-top-3-winners W1 subsets, and the n-floor
  fails on any SUI removal. A fragile n=32 is hereby NOT certified as robust.

### 5. Charter / sizing compliance — SURVIVES with one honest gap
- Unlevered ENFORCED in the math: account return = 0.03 × net_ret, additive curve, no leverage
  multiplier anywhere; 4-position chronological cap = 12% gross. Leverage 1x < 2.5x clamp. ✓
- 3%/12% match CLAUDE.md §2 figures, with a semantic note: charter 3% is a RISK cap; a 3%
  unlevered short's loss is bounded by 3% only up to a +100% adverse move (worst observed −61.5%
  → −1.85% account; tail beyond 2× is unbounded and unobserved).
- **F6 (MEDIUM): the 8% Stop-Loss Guardian is NOT modeled.** The screened edge holds through
  +61% adverse (BASED W1). A charter-faithful implementation stops at −8% — which would have
  TRUNCATED the three big losses (favorable) but might also stop winners on interim pumps
  (unquantifiable from event-level artifacts). The screened PnL is therefore NOT the
  charter-compliant PnL. Binding condition 3 resolves this in the probe.

### 6. Realized-MTM 0.073 > MC-p95 0.062 — EXPLAINED, not an anomaly, but reframe the headline
Mechanical: MC bootstraps exit-to-exit event returns (no intra-hold marks, resampled ordering);
the realized curve marks daily with concurrency overlap and intra-hold adverse excursion. MC p95
is therefore NOT a tail bound on concurrent-MTM drawdown and must not be quoted as one. The
honest number is the realized 0.073 — itself a single path on daily 00:00 marks (intraday wicks
unmarked), so the true tail is larger by an unknown amount. Stress bound: 4 concurrent × 3% ×
BASED-scale (~60%) co-adverse ≈ 7.2% instantaneous + sequencing → plausibly ~0.11–0.15 in a bad
draw — still under the 0.25 gate with real headroom. Safe as log-only framing; per-bar MTM
logging (binding condition 2, rev3 precedent) produces the real distribution. → F10, LOW.

### 7. Survivorship — claim holds for the MEAN, NOT for the TAIL
- Mean direction verified: missing delisted/dead tokens (current `load_markets` matching +
  DefiLlama dropping dead protocols) predominantly removes collapsed tokens = missed SHORT
  WINNERS → edge understated. "Conservative for shorts" is correct for the point estimate.
- NOT conservative for risk: (a) squeeze-then-delist events (short blown through, instrument
  gone) are invisible — the loss tail is right-censored; (b) forced closure/ADL during a
  delisting is an execution risk no backtest row carries. The 08b caveat is accepted only with
  this two-sided restatement. → folded into F6/F10 risk framing.

## Additional findings
- **F4 (MEDIUM): the frozen AUC score is likely non-discriminating forward.** Full-sample AUC
  0.69/0.68 is driven by 2023-vs-later separation; on 2025+ it is 0.246 (W1) / 0.459 (W2) —
  at-or-below chance in the current regime. Expect the shadow probe's frozen score to FAIL the
  AUC≥0.60 promotion gate. That is a legitimate NO-PROMOTE outcome. The score must NOT be
  re-tuned after outcomes accumulate (that would be the exact peeking this pipeline exists to
  prevent); a new score requires a new pre-registration.
- **F8 (LOW, favorable):** SUI circ calibration is 0.768 (documented/index) — SUI/APT marginal
  ratios (0.10–0.14) are overstated ~1.3×, i.e., some events may truly be sub-threshold. Those
  marginal events were mostly LOSSES; the miscalibration diluted rather than manufactured the
  edge. Denominator caveat confirmed real and material, direction favorable.
- **F9 (LOW):** `diag_08b.json` is labeled "accepted-event dump" but contains all 34 W1
  candidates including the 2 capped-out (BABY 2026-04-10, DOLO 2026-04-24). Stats are correctly
  computed over the 32 accepted (verified by rebuild); label is wrong.

## Binding conditions for shadow-integrator (all six required; violation voids this CONFIRMED)
1. **LOG-ONLY.** No order path, no sizing hooks into live decisions. Promotion only via the
   frozen gate (`core/promotion_gate.py` thresholds) on ≥30 RESOLVED forward events per arm +
   owner sign-off. Given ~1–3 qualifying events/month historically, expect this to take months
   — that is the design, not a defect.
2. **Per-bar intra-hold MTM logged** (rev3 precedent) — resolves F10 and the AUC-computability
   requirement; per-event rows must record entry/exit ts+px, venue, realized funding prints,
   ratio, and the frozen score at entry.
3. **8%-SL counterfactual logged** alongside raw hold-to-T outcome (resolves F6): flag the first
   bar where adverse MTM ≥8% and record both PnLs.
4. **Both W1 and W2 arms logged separately; W3 NOT implemented** (failed its gate). Promotion
   assessed per-arm; W2 is the robustness-primary arm (F3).
5. **Calendar as-of-entry**: the probe must snapshot the unlock event (ts, tokens, ratio, source
   fetch time) at signal time — this closes F5 forward; retroactive calendar edits must not
   rewrite logged signals.
6. **Ledger row** for this registration: family = event-driven unlock-short; verdict
   CONFIRMED-GO (log-only); fragility profile F1/F2 recorded so a future bull-tape bleed is not
   re-litigated as a surprise.

## Owner's one-line takeaway
The edge is real-looking and honestly screened, but 100% of the profit comes from 2025-26
bear-tape events and the sample's n=32 is really ~19 independent bets — treat the probe as a
hypothesis still on trial, not a validated income stream.
