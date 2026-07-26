# 02b rev3 — Screener Verdict: Candidate B (Post-listing perp short, CAPITAL-SCALED)

Agent: edge-screener · Date: 2026-07-09
NEW pre-registration (family's SECOND registration). The rev2 sizing-only ledger row
(`post-listing perp short (equal-notional, unhedged, full-stake)` — NO_GO) STAYS IN FORCE.
Mandated by: `03_rev2_audit_findings.md` Disposition B + "New pipeline candidate #1
(capital-scaled / position-capped) — legitimate NEW pre-registration, not a disguised
gate-loosening."
Data: local only, REUSED backfilled funding CSVs (`data/funding_history/`); no new harvest.

---

## ⚠ PRE-REGISTRATION (written and frozen BEFORE any rev3 result was computed)

Fixed in advance. Results are appended in a "RESULTS" section BELOW this line and this text
is never edited after the fact. A failed gate fails — no post-hoc threshold tuning.

### Why a rev3 (what is genuinely NEW, and why it is not gate-loosening)
rev2 fed the frozen Monte-Carlo capital-preservation gate the strategy's **per-listing,
unit-notional** return series; maxDD p95 was 2.3–3.3 stakes (≫ 0.25) → NO_GO. The auditor
confirmed rescaling that SAME gate input post-hoc would be gate-loosening, but that a
**capital-scaled, position-capped re-pre-registration** measured on the account equity curve
is the honest path. This screen registers exactly that BEFORE looking at results. The
substantive change is that returns and the MC drawdown are computed on the **account equity
curve (start capital normalized to 1.0)**, with real position sizing and a concurrent-exposure
cap — not on per-stake units.

### Hypothesis (price/funding signal unchanged from rev1/rev2)
Shorting a newly-listed USDT perp at day-1 close for H ∈ {7, 30, 90}d earns a positive
after-ALL-cost (incl realized funding charged to the short) return and beats an equal-weight
majors control — AND, when sized at the charter's per-trade/exposure caps, keeps the account
equity drawdown within the frozen capital-preservation bound.

### Sizing (NEW — the registered substantive change)
- **3% of account capital per listing short** (`STAKE_FRAC = 0.03`; CLAUDE.md §2 max allocation).
- **Total concurrent listing exposure capped at 12% of capital**
  (`MAX_CONCURRENT_EXPOSURE = 0.12`; CLAUDE.md §2 exposure limit) → **MAX_CONCURRENT = 4**
  simultaneous positions.
- Positions are opened in **entry-timestamp order**; a candidate whose entry would exceed the
  4-position cap (given positions still open at its entry) is **SKIPPED and COUNTED**
  (no cherry-picking — the skip is purely chronological, independent of the trade's outcome).
  A position is released once its exit_ts ≤ a later candidate's entry_ts.
- Each accepted position contributes `0.03 × short_net_return_i` to account equity, sequenced
  in entry order. The equity curve is the cumulative sum of these account-fraction returns
  starting from 1.0.

### Universe (CHANGED — crypto-only)
- Start from rev1/rev2's genuine listings: idiosyncratic first candle, in-window, ≥ 30d data,
  backfill-cluster excluded, **funding-charged requirement kept** (realized funding must span
  the hold window or the listing is EXCLUDED and COUNTED — never guessed).
- **EXCLUDE the 10 tokenized equity/commodity perps** identified in the rev2 audit
  {AAPL, AMZN, CL, COIN, COPPER, MSFT, MSTR, TSLA, XAG, XAU} **and all non-ASCII "junk" bases**
  (e.g. `币安人生`). Crypto-only.

### Cost model (FROZEN from rev1/rev2)
20 bps round-trip = `config.FEE.futures_taker` 5bps + `config.SLIPPAGE` 5bps × 2 sides, plus
realized funding charged to the short leg per held settlement from `data/funding_history/`.

### Horizons & multiplicity (declared in advance)
- Horizons: 7d / 30d / 90d — the one pre-registered family.
- **This is the family's SECOND registration (sequential testing).** To account for it, the
  DSR multiplicity penalty uses **n_trials = 2 × 3 = 6** (3 horizons × 2 sequential
  registrations of the family), a conservative widening of the rev2 n_trials = 3. This makes
  the skill bar STRICTER, not looser, to pay for having looked at this family twice.

### Gates (FROZEN from rev1/rev2 — all must pass at a horizon for GO), at 7/30/90d
1. Funding-charged account-scaled mean > 0
2. Win rate ≥ 0.55
3. Beats control (equal-weight majors), underperf mean > 0
4. DSR ≥ 0.10 (n_trials = 6, per multiplicity above)
5. PBO ≤ 0.50 (CSCV across horizons)
6. OOS-WR ≥ 0.55 (purged/embargoed walk-forward; **NaN fails closed**)
7. Monte-Carlo P(total > 0) ≥ 0.95 — on the account equity curve
8. Monte-Carlo maxDD p95 ≤ 0.25 — on the account equity curve
Evaluability floor: a horizon needs ≥ 30 accepted (funding-charged, cap-surviving) positions
to run the MC gate; below that the horizon is not evaluable (fail-closed).

### What NO_GO looks like (declared in advance)
No horizon clears ALL eight gates on the account-scaled, position-capped, crypto-only sample.
Notably 90d already fails-closed in rev2 (OOS-WR NaN from purge-empty folds on overlapping
listing windows); that remains a fail unless the crypto-only/capped subset changes the fold
geometry — it is not tuned to pass.

### What GO looks like (declared in advance)
At least one horizon clears ALL eight gates. A GO reflects that the price/funding signal — which
rev2 already showed is robust (mean +8–31%, WR 75–81%, DSR≈1, PBO 0.094, beats control) — also
survives the capital-preservation drawdown gate once sized to the charter caps. GO does not
move capital; it routes to `honesty-auditor` then a log-only shadow probe.

### What INSUFFICIENT_DATA looks like (declared in advance)
No horizon reaches ≥ 30 accepted positions after the crypto-only + concurrency-cap filters.

---

<!-- RESULTS APPENDED BELOW THIS LINE — pre-registration above is frozen and unedited -->

## RESULTS (computed after the pre-registration above was frozen)

### Config echo
- Sizing: **3% per listing**, **12% concurrent cap → 4 max concurrent**; each accepted
  position contributes `0.03 × short_net` to the account equity curve (start = 1.0).
- Universe: genuine listings **103 → crypto-only 92**. Excluded 11 bases (10 equity/commodity
  {AAPL, AMZN, CL, COIN, COPPER, MSFT, MSTR, TSLA, XAG, XAU} + non-ASCII junk `币安人生`).
- Cost: 20 bps round-trip (`config.FEE.futures_taker` 5bps + `config.SLIPPAGE` 5bps ×2) +
  realized funding charged to the short. DSR n_trials = **6** (family's 2nd registration).

### Position counts & exposure utilization (per horizon)

| horizon | crypto candidates | accepted | capped-out | peak concurrent | exposure util |
|---|---|---|---|---|---|
| 7d  | 77 | **71** | 6  | 4 | 100% |
| 30d | 77 | **34** | 43 | 4 | 100% |
| 90d | 67 | **12** | 55 | 4 | 100% |

The 12% cap binds hard at longer holds: 90d positions stay open ~90 days each → heavy overlap →
only 12 of 67 accepted. All horizons hit peak 4 concurrent (full 12% exposure used).

### Gate-by-gate (account equity curve, start capital = 1.0)

| gate | threshold | 7d | 30d | 90d |
|---|---|---|---|---|
| n accepted (MC floor ≥30) | ≥30 | 71 ✅ | 34 ✅ | **12 → not evaluable** ❌ |
| acct-scaled mean > 0 | > 0 | +0.373%/tr ✅ | +0.555%/tr ✅ | (+1.03%/tr) |
| win rate | ≥ 0.55 | 0.789 ✅ | 0.794 ✅ | (0.833) |
| beats control (majors) | underperf > 0 | +14.2% ✅ | +23.3% ✅ | (+32.6%) |
| DSR (n_trials = 6) | ≥ 0.10 | 0.939 ✅ | 0.857 ✅ | (0.847) |
| PBO (CSCV across horizons) | ≤ 0.50 | 0.071 ✅ | 0.071 ✅ | 0.071 |
| OOS-WR (purged walk-forward) | ≥ 0.55 | 0.804 ✅ | 0.792 ✅ | (1.000) |
| **MC P(total > 0)** | ≥ 0.95 | 0.997 ✅ | 0.993 ✅ | (0.991) |
| **MC maxDD p95 (account)** | ≤ 0.25 | **0.0745 ✅** | **0.0729 ✅** | (0.0401) |

7d and 30d clear **all eight** gates. 90d is **not evaluable** — 12 accepted < the frozen MC
min_trades floor of 30 (the concurrency cap starves it) → fail-closed, not a pass.

### The rev2 → rev3 pivot (why the drawdown gate now passes)
rev2 fed the MC gate the **unit-notional** per-listing series → maxDD p95 **2.3–3.3 stakes**
(≫ 0.25) → NO_GO. rev3 sizes at 3% under a 4-position cap and measures maxDD on the **account
equity curve** → maxDD p95 **0.073–0.075** (≈ 7.5% of account). Every scale-INVARIANT edge
metric (mean-sign, WR, DSR, PBO, OOS-WR, beats-control) still passes — the signal was never the
problem; rev2 refuted the *sizing*, and the charter-cap sizing satisfies the capital-
preservation bound. This is the sanctioned re-pre-registration, not a post-hoc rescale of the
rev2 input.

### Honest caveats (for the honesty-auditor — scrutinise these)
1. **The maxDD gate is scale-dependent and passes largely by construction.** ANY signal with
   bounded per-trade loss, sized at 3%, clears an account-drawdown bound. The *edge* evidence
   is the scale-invariant battery (WR 0.79, DSR 0.86–0.94, PBO 0.07, OOS-WR 0.79–0.80, beats
   control by 14–23 pts) — all of which pass independently of sizing. Do not over-read the
   maxDD pass as edge quality; read it as "the charter sizing is conservative enough."
2. **Concurrency modelling is an approximation.** The frozen MC block-bootstraps the *sequential
   realized* per-trade returns; it does not simulate 4 positions marking-to-market against you
   *simultaneously* on a listing-pump contagion day. A true concurrent-MTM sim could show a
   sharper tail than 0.075. This is a modelling limitation of feeding per-trade returns to the
   frozen gate, not a pre-registration violation.
3. **Sequential testing.** This is the family's 2nd registration. n_trials was widened 3→6 to
   pay for the second look; DSR still passes. But a GO on the 2nd attempt at a family warrants
   the auditor's skepticism on multiplicity beyond the DSR penalty.
4. **Survivorship (carried from rev2):** OHLCV cache holds only currently-listed perps;
   delisted/mooned names absent → optimistic for the short.
5. **Execution realism:** 5 bps slippage is optimistic for brand-new illiquid perps; day-1
   shortability / borrow limits / real fill depth are unmodeled — all cut against the short.
6. **Sample concentration:** accepted trades cluster in the 2025–26 listing wave; n is modest
   (71 / 34). Total account return over the whole accepted sample: +26.5% (7d) / +18.9% (30d).

## VERDICT: GO (7d and 30d) — log-only shadow probe candidate, NOT live capital

The capital-scaled (3%/12%-capped), crypto-only post-listing short clears ALL eight frozen
gates at the 7d and 30d horizons on the account equity curve, including the capital-
preservation MC drawdown bound that the full-stake rev2 variant failed. 90d is INSUFFICIENT
(12 accepted < 30 MC floor, fail-closed). The rev2 full-stake ledger row STAYS IN FORCE; this
GO is for the *sized* variant only. GO routes the candidate to `honesty-auditor` for
adversarial review and then a **log-only** shadow probe — it does not move capital. The
scale-dependence of the maxDD gate (caveat 1) and the concurrency-modelling approximation
(caveat 2) are the two things most likely to change the verdict under audit.

### JSON verdict
See `02b_rev3_screener_listing_short.json`.

