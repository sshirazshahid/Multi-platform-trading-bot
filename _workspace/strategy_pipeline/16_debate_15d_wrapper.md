# 16 — Multi-Model Debate Record: 15d Staked-ETH Wrapper Discount Screen

Strategy-evidence-pipeline run 2026-07-16 (record written 2026-07-17).
Candidate: staked-ETH wrapper discount (wBETH/stETH vs ETH spot, ETH-perp hedge) — scout B candidate 3.
Verdict on trial: **NO_GO** (`15d_screen_wrapper.md` / `.json`, screen `research/screen_wrapper_discount.py`).
Debaters: Sonnet 4.6, Opus 4.8, Fable 5 (independent attack sets). Reconciler: Fable (this record).
All three auditors returned `verdict_stands: true` at high confidence. Per the bias rule, agreement
was NOT treated as evidence — every FATAL/MAJOR finding was re-adjudicated against primary artifacts,
and the headline numbers were independently recomputed.

---

## Reconciler's independent verification (read-only, 2026-07-17)

1. **Bit-for-bit recompute** (independent inline implementation, not an import of the screen module):
   WBETH p95 A=21.26 / B=20.85 bps (best A), drift +2.5747%/yr, p99 61.72, max 1,760.61;
   STETH p95 A=25.57 / B=27.89 bps (best B), drift +0.0619%/yr; n=8,762 matched bars each,
   window 2025-05-31 20:00 → 2026-05-31 21:00 UTC; funding mean 8h = 2.8865e-05 over n=1,095
   settlements, credit at D_ref=1h = 0.0361 bps; fixed 4-leg floor = 50.00 bps from `config.FEE`
   (spot_taker 0.001, futures_taker 0.0005) + `config.SLIPPAGE` (5 bps open/close) → floor_min
   49.9639. **Every number matches the JSON exactly.**
2. **Selftest re-run:** `screen_wrapper_discount.py --selftest` → `SELFTEST PASS (7 groups)`.
3. **Fees-only zero-slippage floor** (Opus's clincher, recomputed): 2×10 + 2×5 = 30 bps —
   **both wrappers fail even this** (21.26 and 27.89 < 30). No defensible cost model rescues the candidate.
4. **Venue fee check** (`config.py:94-113`): spot taker identical 0.001 on binance/bybit/bitget;
   bybit/bitget futures taker is 0.0006 vs binance 0.0005. The hedge is pre-registered as
   Binance ETH-perp (funding source `binance_ETH.csv`), so 5 bps is the correct leg; a
   non-Binance hedge would RAISE the floor to ~52 bps and strengthen the fail.
5. **Timestamps + git:** `14_scout_b_spot_2026-07-16.md` created 23:32:47;
   `screen_wrapper_discount.py` 23:41:13; `15d_screen_wrapper.json` 23:41:27;
   `15d_screen_wrapper.md` 23:42:32. All four untracked (`??`) in git; no committed pre-registration.
6. **Scout-file design check:** `14_scout_b_spot_2026-07-16.md` line 75 contains, before any code
   existed: "a near-zero-cost descriptive pass FIRST — compute the 1yr deviation distribution; if
   p95 discount < (2 spot legs + 2 perp legs + expected hedge funding ...), STOP and record NO_GO
   without building the full screen." The binding stop-rule design predates the code on disk.

---

## Per-model attack summaries

### Sonnet 4.6 — verdict stands, high confidence, 3 MINOR
Reproduced the NO_GO bit-for-bit; verified cost model against `config.FEE`; corroborated data
provenance (Oct-2025 flash event real; ETH leg genuinely spot; STETH-not-on-Binance externally
confirmed). Findings: (S1) Estimator A is full-sample OLS (non-causal) and is the headline for
WBETH; (S2) the "top-up cannot plausibly move p95 by 2x" staleness caveat is an assertion, not a
measured sensitivity; (S3) the exact venue of the STETH parquet is unrecorded (traceability gap).

### Opus 4.8 — verdict stands, high confidence, 5 MINOR
Strongest quantitative attack set. Showed both wrappers fail even the fees-only 30 bps floor;
dismantled the maker-fee escape (24 bps floor requires maker fills on median-1h transients,
contradicting touch≠fill; WBETH fails it anyway); exposed the ≥50bps "100% win rate tail strategy"
as tautological (entries ≥50bps by construction; WBETH mean driven entirely by one 1,760 bps flash;
n=9-12 fails frozen gates); flagged the 1h-close granularity as a scope boundary where
re-demanding 1m data would move goalposts in the strategy's favor; noted Estimator A look-ahead is
bounded (~0.4-2 bps vs causal B) and strategy-favorable; noted pre-registration write order is a
trust statement, not git-checkable, but post-hoc fitting is implausible for a rejection.

### Fable 5 — verdict stands, high confidence, 1 MAJOR + 2 MINOR
The only MAJOR of the debate (adjudicated in full below): pre-registration order unverifiable from
artifacts. Also: (F2) the ledger row must be scoped to the screened expression only (CEX spot
round-trip, taker, 1h basis) — WBETH p99/max exceed the floor and resting-limit tail-harvest,
intra-hour dislocations, and on-chain redemption arb were never measured; (F3) SKILL.md
output-convention deviations (selftest inside script instead of `tests/`; no `reports/` copy) —
disclosed, hygiene only.

---

## Adjudications

### MAJOR — Fable 5: "pre-registration written BEFORE any screen code ran" is unverifiable
> **Claim (verbatim):** "The 'pre-registration written BEFORE any screen code ran' claim is
> unverifiable from artifacts: 15d_screen_wrapper.md's on-disk creation time (2026-07-16 23:42:32)
> postdates both the results JSON (23:41:27) and the screen script (23:41:13), and all four
> artifacts are untracked in git with no prior commit or hash of the pre-registration."

**Adjudication: VALID (process defect), NOT verdict-bearing.**
- Evidence re-verified: timestamps confirmed exactly (see verification §5); all four files `??` in
  git. The .md's CreationTime postdating the run means the pre-registration section, as this file,
  physically did not exist on disk before the code ran (or the file was moved/copied, which is
  indistinguishable). The claim in `15d_screen_wrapper.md` line 9 cannot be verified. VALID.
- Why it does not overturn the NO_GO (all three points re-verified against artifacts):
  1. The binding stop-rule design (descriptive pass first, p95 vs 4-leg cost floor, stop early,
     record NO_GO) appears in the scout brief created 23:32:47 — before any code (verification §6).
  2. The 49.96 bps floor is mechanically derived from `config.FEE` + repo slippage convention with
     zero free parameters (verification §1); nothing was tunable post-hoc.
  3. The rejection is over-determined: every discretionary choice (best-of-2 estimators, floor_min,
     funding credit counted) was strategy-FAVORABLE, the margin is ~2x, and both wrappers fail even
     the fees-only 30 bps floor (verification §3). No post-hoc fit could have been needed to reach
     NO_GO; if anything, tampering incentive runs the other way.
- **Binding follow-up (all future screens):** commit or content-hash the pre-registration BEFORE
  the screen runs. For a GO verdict this exact gap would be FATAL. Adopted as pipeline requirement.

### MINOR adjudications (grouped by theme)

**Estimator A look-ahead (Sonnet S1 + Opus finding 4): VALID, bounded, non-verdict-bearing.**
`ols_residuals` (screen script lines 88-93) fits `np.polyfit` over the full sample — non-causal —
and the pre-registered best-of rule picks A for WBETH. Recomputed independently: causal B agrees
within 0.41 bps (WBETH: A 21.26 vs B 20.85) and 2.32 bps (STETH: A 25.57 vs B 27.89); look-ahead
makes deviations cleaner (strategy-favorable) and both estimators fail the floor by ~2x.
**Recorded constraint:** any future revival of this family must use only the causal estimator (B)
or an equivalent live-computable trend — never full-sample fits — in any signal-driving role.

**Staleness sensitivity assertion (Sonnet S2): PARTIALLY VALID.** The "~6.5-week top-up cannot move
p95 by 2x" line is indeed asserted, not computed — Sonnet is right that no sensitivity test exists
in the repo. Reconciler bound (arithmetic, from verified counts): the top-up adds ~1,100 bars
(~11% of sample, ~500 discount-side). For the combined WBETH p95 to reach 50 bps, ≥5% of all
discount bars (~230 of ~4,550) would need magnitude ≥50 bps; today ~1-1.5% do (p99=61.7). That
requires roughly a third of the NEW discount bars to sit at depths currently in the top ~1% of a
full year — i.e., 6.5 weeks of near-continuous record stress. The assertion survives as a
quantified bound. Non-verdict-bearing; re-check optionally after `scripts/backfill_universe_ohlcv.py`.

**STETH venue traceability (Sonnet S3): VALID traceability gap, immaterial to the number.** No
manifest records whether `STETH-USDT_1h.parquet` came from Bybit or Bitget. Spot taker is 0.001 on
all three venues (`config.py:96,104,109`) so the floor is insensitive; Binance non-listing of STETH
spot externally corroborated by Sonnet. Hygiene: future harvests should stamp source venue.

**1h-close granularity scope (Opus finding 1): VALID scope boundary, correctly handled.** The
pre-registration committed to the 1h-close basis and the stop rule fired as written; sub-hourly
fills are a latency race this 10s-polling account loses, and only 12/9 hourly closes per year
reach ≥50 bps. Re-demanding 1m data post-verdict would move goalposts in the strategy's favor.
**Ledger row must read "1h-close basis; sub-hourly untested."**

**Maker-fee escape + tail-strategy mirage (Opus findings 2-3): VALID, verdict-supporting.**
Recomputed: maker-fees-only floor 24 bps is the only model under which anything survives (STETH
27.9) — and it is self-contradictory (maker fills on median-1h transients violate touch≠fill; spot
maker == spot taker so no saving on the fat legs; WBETH fails it regardless). The ≥50bps
"100% WR" tail is tautological (net = depth − 50 ≥ 0 by the entry rule), rests on one 1,760 bps
flash, and at n=9-12 would fail the frozen MC/DSR/PBO gates. Recorded to pre-empt relitigation.

**Ledger-row scoping (Fable F2): VALID — adopted.** WBETH p99 (61.7) and max (1,760.6) exceed the
floor; resting-limit tail-harvest at ≥50bps depths, intra-hour dislocations, and on-chain
redemption arb are distinct, unmeasured expressions. **Adopted ledger wording:** "staked-asset
wrapper discount — CEX spot round-trip expression, taker fills, 1h-close basis, perp-hedged
(wBETH/stETH vs ETH, 2025-05-31→2026-05-31). Sub-hourly, resting-limit tail-harvest, and on-chain
redemption expressions untested — a future proposal for those is NEW, not conversationally
rejected by this row."

**Output-convention deviations (Fable F3): VALID hygiene.** Selftest lives behind `--selftest`
instead of `tests/` (per the orchestrator's one-file restriction, disclosed in the report); no
`reports/` copy. Selftest is substantive (re-run PASS, 7 groups; one asserted case independently
recomputed by the Fable auditor). No validity impact.

---

## Dissents

None material. All three auditors independently concluded `verdict_stands: true` at high
confidence; no auditor's findings conflict with another's on substance. The only severity
disagreement — the pre-registration-order gap rated MINOR by Opus vs MAJOR by Fable — is resolved
in Fable's direction (MAJOR as a process defect; the evidence is identical in both write-ups, the
difference is severity labeling only, and the stricter label carries the binding follow-up).

---

## FINAL STATUS: **CONFIRMED_NO_GO**

The NO_GO verdict stands. Grounds:
1. Headline result reproduced independently, bit-for-bit, from verified on-disk data.
2. The rejection is over-determined: p95 discount (21.26 / 27.89 bps, most strategy-favorable
   estimator) fails the 49.96 bps registered floor by ~2x AND fails the fees-only zero-slippage
   30 bps floor; unmodeled wrapper-book spread and touch≠fill only push the true floor higher.
3. Zero findings overturn or materially weaken the verdict. The single MAJOR (pre-registration
   order unverifiable) is a real process defect but is bounded by the pre-existing scout-file
   design, the zero-free-parameter floor, and the strategy-favorable-throughout construction.
4. Conservative default concurs: even if every unresolved doubt were resolved against the screen,
   the candidate would still not advance.

**Owner-visible follow-ups (binding):**
- Ledger row: write the scoped wording above (pending row → confirmed by this debate).
- Pipeline rule (new, from the MAJOR): pre-registrations must be committed or content-hashed
  BEFORE the screen runs; for GO verdicts an unverifiable pre-registration is FATAL.
- Reopen bar for this family: causal estimator only; sub-hourly/resting-limit/on-chain expressions
  are out of this row's scope and would enter as NEW candidates.
- Optional: after the next cache top-up, re-check p95 against the recorded staleness bound (no
  screen rebuild needed — a one-line percentile recompute).
