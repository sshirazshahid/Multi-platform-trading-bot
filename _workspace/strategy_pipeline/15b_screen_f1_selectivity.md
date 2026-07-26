# 15b — Funding-percentile persistence selectivity for F1 (edge-screener, 2026-07-16)

Phase-2 screen of scout A candidate 2 (`14_scout_a_futures_2026-07-16.md`).
Status: **PRE-REGISTERED — frozen before any outcome code ran.** Results are appended
below the marked line only after this section was written to disk.

## 1. Pre-registration (frozen 2026-07-16, before results)

### Hypothesis
Conditioning F1 delta-neutral carry ENTRIES on the coin's current funding sitting in a
high percentile of its OWN trailing funding distribution, with a short persistence
requirement, and adding an EXIT on funding decaying below its trailing median, yields
higher after-cost net carry **per settlement of deployed capital** than the incumbent
Rev-5 `f1_entry_gate` selection on the same window, universe, venues, and cost model —
without gutting the absolute harvest.

### NULL hypothesis (the incumbent — not zero)
The incumbent is the funding-observable portion of the live Rev-5 gate
(`research/funding_carry_lab.f1_entry_gate` + the audited replay convention of
`scripts/f1_replay_historical.py`):

- Entry at settlement index i requires ALL of:
  - `rates[i] > 0` (current funding quote positive),
  - trailing 21 settlements available and `mean(rates[i-21:i]) > 0`,
  - projection `min(rates[i], trailing_21_mean) * 21 − cost ≥ max(15 bps, 3 × cost)`
    (the live gate's `f1_net_expected_edge_bps` with `hold_settlements=21`),
  - `f1_net_funding_lower_bound_bps(trailing_21, hold=21, cost) > 0`
    (the live gate's moving-block-bootstrap 95% lower bound, seed 0 — frozen code).
- Exit after any of (audited replay convention, shared by BOTH arms):
  - 2 consecutive negative (PAY) settlements (`MAX_CONSEC_NEG_SETTLEMENTS=2`),
  - trailing-21 funding mean ≤ 0 (regime flip),
  - `F1_MAX_HOLD_SETTLEMENTS = 42` reached.
- Funding accrual: entry before settlement i (per the replay convention the episode
  collects `rates[i]` first); realized funding accrues each held settlement; one
  round-trip cost charged per episode at entry. No overlapping episodes per
  venue-symbol; scanning resumes after exit.

### Variants (frozen — exactly these two; no other configuration will be run)
Variant entries are the incumbent entry conditions AND the selectivity overlay;
variant exits are the incumbent exits PLUS the decay exit. Multiplicity m=2
(Bonferroni); DSR `n_trials=2`.

- **V1 (PRIMARY):** percentile window 90d = 270 settlements (`rates[i-270:i]`,
  fail-closed skip when < 180 available). Threshold `thr = P75` of that window.
  Entry requires `rates[i] ≥ thr` AND `rates[i-1] ≥ thr` AND `rates[i-2] ≥ thr`
  (3 consecutive settlements = 24h in the top quartile). Decay exit: at held
  settlement j, if `rates[j] < median(rates[j-90:j])` (30d) for 2 consecutive held
  settlements → exit after settlement j (funding at j still collected).
- **V2 (robustness only):** percentile window 30d = 90 settlements (fail-closed
  skip when < 60 available), threshold P75, persistence 2 settlements
  (`rates[i] ≥ thr` AND `rates[i-1] ≥ thr`). Same decay exit as V1.

All trailing statistics (percentile, median, trailing mean) use windows ending at
index i EXCLUSIVE (`rates[..:i]`) — strictly before the decision settlement; the
"current quote" `rates[i]` is the realized rate of the settlement being entered into,
the same approximation the audited incumbent replay uses, applied IDENTICALLY to both
arms so it cannot bias the delta.

### Universe / sample / data
- Universe: `F1_EXPANDED_UNIVERSE_2026_07_05` (15 coins, frozen 2026-07-05,
  owner-approved; includes the 0-entry coins BTC/SUI by design).
- Venues: binance AND bybit, each simulated independently (venue-stability requirement).
- Data: `data/funding_history/{venue}_{BASE}.csv`, realized settlements, verified
  current to 1784217600 = 2026-07-16 16:00 UTC on all 30 venue-symbol series
  (earliest series start 2021-01-01; per-symbol starts vary and are reported).
- No synthetic data; missing series are named, never imputed.

### Cost model (frozen — identical to the audited 08a screen, same lane)
- Round-trip per episode: binance 50 bps (2×10 bps spot taker + 2×5 bps perp taker +
  4×5 bps slippage), bybit 52 bps. Charged once per episode at entry.
- Funding: realized per-settlement rates from the CSVs, credited to the short-perp
  leg (positive = collect, negative = PAY — charged in full).
- Basis PnL assumed 0 (delta-neutral; the documented replay assumption, shared
  identically by both arms). Execution terms the CSVs cannot see (leg spreads, depth,
  contango, time-to-funding, fillability) are held identical across arms — the delta
  can only come from funding selection, never from assumed fill quality.
- Stress row: 2× cost (F1_STRESS_COST_MULT) reported as a diagnostic for both arms.

### Endpoints (frozen)
- **PRIMARY: after-cost net carry per settlement-deployed** = (Σ episode net, fraction
  of notional) / (Σ settlements held), pooled across venues+symbols. This is the
  capital-efficiency claim in the scout brief.
- Secondary: total after-cost net bps; episodes; mean bps/episode; per-episode WR;
  mean hold length.

### Gates (frozen — never loosened) and verdict rule
GO requires ALL of the following, evaluated on **V1** (V2 is robustness reporting
only; "only V2 passes" = NO_GO for the registered claim):

1. **Sample floors:** pooled incumbent episodes ≥ 60 (else INSUFFICIENT_DATA — the
   comparison window cannot express the null); pooled V1 episodes ≥ 30 (if incumbent
   ≥ 60 but V1 < 30 → NO_GO: selectivity too extreme to validate, fail-closed);
   ≥ 10 V1 episodes per venue for the venue-stability check to be evaluable
   (fewer → that check fails, fail-closed).
2. **Frozen gate battery on V1 episode returns (pooled):**
   DSR ≥ 0.10 (`deflated_sharpe`, n_trials=2, sr_var=1/n);
   PBO ≤ 0.5 (CSCV over the per-settlement portfolio-grid matrix with columns
   [incumbent, V1, V2] on the common settlement grid);
   OOS-WR ≥ 0.55 (per-episode wins, 5 anchored walk-forward folds, embargo 1,
   time purge of train episodes still open at test start);
   Monte Carlo on the episode sequence: P(total>0) ≥ 0.95 AND maxDD p95 ≤ 0.25
   (block bootstrap, min 30 trades — `core/decision/monte_carlo.py` defaults).
3. **Delta vs the incumbent NULL:**
   pooled primary-endpoint delta (V1 − incumbent) > 0;
   episode-level bootstrap (1000 resamples, seed 7) of the delta with Bonferroni
   m=2 → the 2.5th percentile of the delta distribution must be > 0;
   **fold sign stability:** delta of the primary endpoint positive in ALL 5
   calendar folds (the settlement-timing NO_GO precedent is binding: a small mean
   improvement with unstable fold signs is a NO_GO);
   **venue sign stability:** delta positive on binance AND bybit separately.
4. **Harvest guard:** V1 total after-cost net ≥ 0.75 × incumbent total after-cost
   net, AND V1 total net > 0 (efficiency gained by gutting the absolute harvest is
   not a GO).

**NO_GO looks like:** delta ≤ 0 pooled; or fold/venue sign instability; or any
frozen-gate failure on V1; or the harvest guard failing; or V1 too thin (< 30
episodes) while the incumbent has ≥ 60. INSUFFICIENT_DATA is reserved for the
incumbent itself failing its 60-episode floor or for missing/stale local data
(exact backfill command named).

### Multiplicity accounting (true trial count)
Variants tried in this screen: 2 (V1, V2) — no other thresholds, windows, or
persistence values were or will be evaluated against outcomes. DSR n_trials=2;
delta CI Bonferroni m=2. No sweep.

### Ledger distinctness (asserted at registration)
Distinct from all three refuted carry-adjacent rows: settlement-window timing
(intra-window entry OFFSETS — this screen selects across settlements, never moves
the intra-window entry point); cross-venue dispersion hold-until-flip (venue-PAIR
spread expression — this is single-venue F1 as-is); quarterly-basis leg-swap
(instrument substitution — this keeps spot+perp). Funding here is the harvested
cash flow, never a directional price signal.

### Code / protocol
New code ONLY in `research/screen_f1_percentile_selectivity.py` (+ tests in
`tests/test_screen_f1_percentile_selectivity.py`). Reuses audited helpers from
`research/screen_listing_short.py` (`_dsr_prob`, `_pbo_across_horizons`,
`_monte_carlo`, `load_funding_history`) and frozen lab primitives
(`f1_net_expected_edge_bps`, `f1_net_funding_lower_bound_bps`). No core/ or config
changes. Runner: `venv/Scripts/python.exe`.

---
<!-- RESULTS APPENDED BELOW THIS LINE ONLY AFTER THE ABOVE WAS FROZEN -->

## 2. Results (run 2026-07-16 23:49 local; screen `research/screen_f1_percentile_selectivity.py`, 16/16 unit tests green)

**Data:** 30/30 venue-symbol series loaded, zero missing, zero stale (all tails =
1784217600 = 2026-07-16 16:00 UTC). 0 unresolved data-end episodes in any arm.

### VERDICT: **NO_GO**
Failed pre-registered gates: `delta_ci_lb>0_bonferroni_m2`, `fold_sign_stable_all5`,
`harvest_guard`. Full JSON: `15b_screen_f1_selectivity.json`.

### Incumbent vs variants (pooled, after cost, resolved episodes)

| arm | n | total net (bps·notional) | mean bps/episode | WR/episode | mean hold (settles) | **eff bps/settlement (PRIMARY)** |
|---|---|---|---|---|---|---|
| incumbent (NULL) | 95 | **33,097** | 348.4 | 0.989 | 40.5 | 8.60 |
| V1 (primary) | 46 | 6,964 | 151.4 | 0.826 | 14.1 | **10.76** |
| V2 (robustness) | 103 | 19,633 | 190.6 | 0.874 | 15.2 | 12.53 |

### Per-gate numbers (V1)

| gate | value | floor | pass |
|---|---|---|---|
| DSR (n_trials=2) | 1.000 | ≥ 0.10 | ✓ |
| PBO (grid CSCV [inc,V1,V2]) | 0.000 | ≤ 0.5 | ✓ |
| OOS-WR (episodes, 5-fold WF) | 0.771 | ≥ 0.55 | ✓ |
| MC P(total>0) | 1.000 | ≥ 0.95 | ✓ |
| MC maxDD p95 | 0.0055 | ≤ 0.25 | ✓ |
| pooled delta (V1−inc) | +2.16 bps/settle | > 0 | ✓ |
| **bootstrap delta CI-LB (Bonferroni m=2, 2.5th pctl)** | **−0.18 bps** | > 0 | **✗** |
| **fold sign stability (5 calendar folds)** | **[+, −, ∅, ∅, +]** | all + | **✗** |
| venue sign stability | binance +3.12 / bybit +0.38 | both > 0 | ✓ |
| **harvest guard (V1 total ≥ 0.75× inc)** | **0.21×** (6,964 vs 33,097) | ≥ 0.75× | **✗** |

∅ = calendar folds 3–4 (mid-sample, ~2022–2024 funding compression) contain ZERO
episodes in either arm — fail-closed per pre-registration. Note the verdict does not
hinge on the null-handling choice: fold 2 is sign-NEGATIVE outright.

### 2× cost stress (diagnostic)
| arm | n | total net bps | eff bps/settle | WR |
|---|---|---|---|---|
| incumbent | 18 | +5,708 | 8.32 | 0.889 |
| V1 | 5 | **−49 (NEGATIVE)** | −1.37 | 0.40 |
| V2 | 17 | +2,321 | 11.66 | 0.824 |

The stress row is a second, independent indictment: V1's short episodes (mean 14.1
settlements vs the incumbent's 40.5) amortize the round-trip cost poorly — under 2×
cost the variant flips negative while the incumbent stays robustly positive.

### Honest reading
1. **The frozen gate battery on V1's OWN returns passes everything** (DSR 1.0, PBO
   0.0, OOS-WR 0.77, MC pass) — because F1 carry itself is profitable. That was never
   the claim on trial. The claim was beating the incumbent, and it fails: the +2.16
   bps/settlement efficiency delta is not distinguishable from zero (CI-LB −0.18),
   is sign-unstable across calendar folds (the settlement-timing NO_GO precedent
   pattern, binding per pre-registration), and is bought by giving up **79% of the
   absolute harvest**.
2. **The scout's mechanism claim is refuted in DIRECTION**: "fewer, longer, richer
   episodes" — episodes came out fewer (46 vs 95) but SHORTER (14.1 vs 40.5
   settlements) and per-episode POORER (151 vs 348 bps). The trailing-median decay
   exit dominates the behavior change (42/46 V1 exits) and truncates exactly the
   long profitable holds the incumbent rides; the incumbent's own exit stack
   (2-consec-negative + regime flip + net-edge recheck) already handles decay well.
3. V2 (robustness arm) looks better pooled (+3.93 bps/settle, 103 episodes) but
   still fails the harvest guard (0.59× < 0.75×) and is robustness-only per the
   frozen design — "only V2 passes" was pre-registered as NO_GO regardless.
4. Episode overlap across symbols is not capital-constrained in this replay (same
   convention as the audited incumbent baseline, shared by both arms).

### What would change the verdict (all require a NEW pre-registration, not a re-run)
- An **entry-only selectivity variant WITHOUT the decay exit** — the harvest-guard
  and stress failures trace to the exit overlay, not the percentile entry. Untested
  here by design (frozen variants included the exit); would need fresh registration.
- Evidence that the efficiency delta survives in mid-sample regimes: today the
  2022–2024 funding-compression folds contain zero qualifying episodes, so fold
  stability is structurally unattainable for ANY selectivity overlay on this gate —
  a longer archive or a compressed-regime entry pathway would be needed.
- An owner-level reframing making per-deployed-settlement efficiency the binding
  objective over total harvest (capital genuinely scarce across concurrent uses) —
  but even then the Bonferroni CI-LB and fold-sign failures stand on their own.

**Ledger action (post-audit):** add row "F1 percentile+persistence selectivity
(P75 trailing 270/90-settle windows + trailing-median decay exit)" as refuted —
pending honesty-auditor confirmation.
