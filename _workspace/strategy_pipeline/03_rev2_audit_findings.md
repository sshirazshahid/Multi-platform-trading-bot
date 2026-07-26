# 03 rev2 — Honesty-Auditor Findings (funding-history backfill re-run)

Auditor: honesty-auditor · Date: 2026-07-09
Inputs audited: `02a_rev2_screener_dispersion.md` (NO_GO), `02b_rev2_screener_listing_short.md` (NO_GO)
Code: `research/screen_funding_dispersion.py`, `research/screen_listing_short.py`,
`scripts/backfill_funding_history.py`; gates `core/{walk_forward,stat_tests}.py`,
`core/decision/monte_carlo.py`. Prior audits: `03_audit_dispersion.md`, `03_audit_listing-short.md`.

Default position: each verdict is wrong until it survives attack. Both were reproduced
byte-for-byte; the data is real (ccxt-verified). One verdict survives clean; one reaches the
right *action* on refuted *reasoning*.

---

## Candidate A — Cross-venue funding dispersion → **UNSAFE (reason below); action = do-not-promote is correct, family-refutation is NOT**

**Reproduction:** exact. 15 coins × 100 aligned settlements (ZEC 65), 45 pairs, n_trials=45,
best pair GRT binanceL/bitgetS OOS −1.2745 bps, OOS-WR 0.125, DSR 0.0, PBO 0.0006,
MC P(>0) 0.0, maxDD p95 0.0115 → NO_GO. Tests 19/19 pass.

**Data integrity — CLEAN.** `align_cross_venue` returns exactly the 3-way settlement-timestamp
intersection = Bitget's 100 rows (ZEC 65); all gaps a uniform 28800 s (8h grid); zero duplicate
ts per venue. Raw ccxt spot-check (ccxt 4.5.64): bitget GRT / binance BTC / bybit ADA each 9/9
overlapping points **exact-match** live exchange data. Bitget 100-record cap confirmed real. No
synthetic data. Backfill dedupes on ts (`write_history_csv`, keep=last) — no double-count.

**Look-ahead — CLEAN in the direction the screen claims.** Walk-forward picks direction from the
sign of the TRAIN-fold mean and applies it UNSEEN to TEST (`walk_forward_oos_spread`). No
timestamp leakage; realized per-settlement rates, not averages.

### FINDING A1 — CRITICAL — the decisive OOS cost is a cross-validation artifact, and the verdict's stated reason is empirically false. **Changes the verdict's character + ledger disposition.**

The walk-forward charges `rt_cost / len(te)` per test settlement → **exactly one 4-leg round-trip
per fold**. Pooled over `n_splits` folds the OOS series therefore carries **`n_splits × rt_cost`**
of cost. Proven three ways:

- Fold directions are **constant** for every pair I checked — GRT/XRP/ALGO/ADA best pairs all
  yield `fold_dirs = [1, 1, 1, 1]`. The train-chosen sign **never flips out-of-sample.** The
  verdict's headline — *"the cross-venue funding differential's sign is not persistent … a
  train-chosen static direction reverses out-of-sample"* and *"a longer window would not rescue
  it — the failure is sign non-persistence"* — is **contradicted by the screen's own data.**
- Because direction never flips, a real delta-neutral carry harvester opens **once** and holds,
  paying **1** round-trip, not 4. Under a single round-trip the *selected best pair is
  OOS-POSITIVE*: GRT binanceL/bitgetS gross +66 bps − 42 bps = **+24.0 bps** OOS
  (per-settle +0.30 bps); GRT binanceL/bybitS **+15.4 bps**. The reported −1.2745 bps/settle is
  exactly `(66 − 4×42)/80`.
- The total OOS cost scales linearly with the CV hyperparameter: 42 / 168 / 336 bps at
  n_splits = 1 / 4 / 8. A faithful cost model cannot depend on the fold count. Here it does — so
  the NO_GO is manufactured by charging 4 phantom re-entries the strategy never makes.

At **zero cost**, 27/45 pairs have positive OOS mean and GRT/XRP show zero-cost OOS-WR 0.74–0.86.
The differential's sign is *persistent*; the strategy's viability hinges purely on amortizing one
round-trip over a long enough hold — the exact thing the 33-day Bitget-bound window and the
per-fold model prevent.

**Why this does NOT overturn to GO (the do-not-promote action is still correct):** +24 bps is
(a) the in-sample-selected best of 45 pairs (textbook selection bias; a properly-costed single-RT
series still owes DSR its n_trials=45 penalty), (b) one coin over ~27 days, (c) ≈3.3% APR on
notional / ~1.6% on the 2×-notional delta-neutral footprint — economically marginal. No capital
should move on it.

**But the verdict must not be recorded as a merit refutation of the family.** The configuration
that could actually carry an edge — **binance∩bybit, multi-year, hold-until-sign-flips (single
round-trip)** — was never tested (Bitget's cap truncated the registered all-venue window to 33
days). The screener dismissed that follow-up on a false premise. The honest label for the family
is **UNTESTED for its viable configuration**, not refuted.

### FINDING A2 — MEDIUM — universe expanded 2→15 coins vs the frozen 02a pre-registration; legitimate, not drift.
02a's registered universe was "every coin with funding on ≥2 venues over a common aligned
window"; rev1 only had BTC/ETH on disk. The backfill widened coverage to the F1 15-coin set — a
data-availability change consistent with the registered rule, not a post-hoc universe swap. Cost
model, gates, and floor (≥60/coin, ≥2 coins) are byte-identical to 02a. No threshold softened.

### FINDING A3 — LOW — n_trials=45 (not 90) fed to DSR is correct.
OOS is computed taker-only (one series/pair); maker is an in-sample sensitivity never selected
on. Selection was over 45 OOS series → n_trials=45. Since all 45 are OOS-negative under the
(flawed) per-fold cost, DSR 0.0 is robust regardless.

**Disposition A:** Do **not** promote (correct). **Do not** write a "cross-venue funding
dispersion — REFUTED" ledger row: the family is not refuted on merit; its economically-plausible
long-hold configuration is untested and shows persistent-sign, single-round-trip OOS-positive
carry on GRT/XRP. **Required follow-up (legitimate NEW pre-registration, not post-hoc
shopping):** binance∩bybit multi-year dispersion screen, hold-until-sign-flip (cost = actual
round-trips incurred, decoupled from n_splits), DSR-penalized across the pair universe.

---

## Candidate B — Post-listing perp short → **CONFIRMED_NO_GO (clean)**

**Reproduction:** exact. 88/88/75 funding-charged listings; short net mean +8.4/+21.4/+30.8%;
WR 0.75/0.807/0.787; funding_sum mean −2.6/−7.4/−12.0% (neg-rate ~69%); DSR 0.917/0.9994/0.9997;
PBO 0.094; OOS-WR 0.824/0.882/NaN; **MC maxDD p95 3.30/2.32/2.47 ≫ 0.25 → FAIL at every
horizon**. Tests 16/16 pass.

**Funding genuinely charged — VERIFIED.** Per-settlement realized rates summed over the actual
[entry_bar, exit_bar) window, sign-correct: SOMI short PAYS −6.5% (and lost 67.8% as it mooned —
the worst drawdown driver); PUMP RECEIVES +1.1%. Not guessed/averaged. The pre-declared killer
cost is real and now measured.

**Look-ahead — CLEAN.** Entry = day-1 close (`first_ts+24h`), exit = `entry+H·24h`, both real
bars; +12h data-gap tolerance on exit; control basket is contemporaneous close-to-close on the
same window. Funding window keyed to the actual fill bars, strictly inside the hold.

**Fail-closed — HONEST.** 90d OOS-WR NaN (clustered 2025–26 listing dates make 90d holds overlap
so the time-based purge empties train folds) → `np.isfinite` check makes it False → fails closed.
Correct.

**MC gate applied as written — CORRECT, not gate-loosened.** The frozen gate is fed the
strategy's per-listing (unit-notional) return series; p95 peak-to-trough of the cumsum equity is
2.3–3.3 stakes. That is the capital-preservation gate doing its job on an equal-notional,
unhedged short book: a cluster of listings ripping up (SOMI −127%, AVNT −75%, AZTEC −64%, ESP,
SENT, PIEVERSE, KITE) drives a multi-stake drawdown. The screener explicitly did **not** rescale
to a 3%-per-trade fraction to manufacture a pass — I confirm rescaling the gate input post-hoc
would be gate-loosening; a capital-scaled/position-capped variant is a **new pre-registration**,
which is the honest path. I take the same position.

### FINDING B1 — LOW — equity/commodity + junk symbols sit in the gate-eligible sample, but do NOT flip the verdict.
The funding-charged sample includes 10 tokenized-equity/commodity perps (AAPL, AMZN, CL, COIN,
COPPER, MSFT, MSTR, TSLA, XAG, XAU) and one scrape-junk base (`币安人生`). These behave unlike
crypto-hype decay. **Robustness check:** crypto-only (equities + junk removed) leaves MC maxDD
p95 at **3.56 / 2.37 / 2.48** (7/30/90d) — still ≫ 0.25, and the mean/WR *improve*
(+10.4%/+24.1%/+35.2%, WR 0.78/0.82/0.84). The tail risk is intrinsic to crypto listing
dispersion, exactly as the screener's caveat #4 claims. Contamination is a data-hygiene nit, not
a verdict-changer. (Recommend filtering the 11 equity/commodity perps and non-ASCII junk in any
re-pre-registration.)

### FINDING B2 — LOW — survivorship correctly disclosed, direction of bias confirmed.
The OHLCV cache holds only currently-listed perps; delisted-after-listing names are absent.
Disclosed. Bias is optimistic for the short (dead/mooned names missing) — cuts against the
diagnostic, never rescues the drawdown. No undisclosed leakage.

**Disposition B:** **CONFIRMED_NO_GO.** Zero unresolved findings that could flip it. Ledger row
warranted but **narrowly scoped**: refuted only for *equal-notional, unhedged, full-stake*
sizing under the capital-preservation MC gate — NOT the underlying price/funding signal, which is
robust (mean +8–31%, WR 75–81%, DSR≈1, PBO 0.094, beats control, OOS-WR 0.82–0.88).

---

## Ledger rows

**A (dispersion): NO ledger row.** Not refuted on merit — the NO_GO is an artifact of an
n_splits-scaled cost charge on a provably constant-direction hold; the viable long-hold
binance∩bybit config is untested and OOS-positive on the best pairs. A "refuted" row here would
falsely foreclose a live follow-up.

**B (listing-short): one narrowly-scoped row —**
> `post-listing perp short (equal-notional, unhedged, full-stake)` — NO_GO 2026-07-09. Real
> after-all-cost price+funding edge (mean +8–31%, WR 75–81%, DSR≈1, PBO 0.094, beats majors
> basket, OOS-WR 0.82–0.88 @7/30d) but FAILS the frozen capital-preservation MC drawdown gate
> (maxDD p95 2.3–3.3 stakes ≫ 0.25) at all horizons; 90d OOS-WR fail-closed (purge-empty folds).
> Funding killer measured (~69% pay negative, −2.6% to −12%). Refuted for THIS sizing only; the
> price/funding signal is NOT refuted. Re-open via a position-capped/capital-scaled
> pre-registration.

## New pipeline candidates to queue
1. **Listing-short, capital-scaled / position-capped** (e.g. ≤3% notional/listing, concurrent-
   position cap, equity/junk-filtered universe) — re-pre-register and re-run the frozen MC on the
   capital-scaled equity curve. The signal is strong; only the unhedged full-stake tail fails.
   **Legitimate NEW pre-registration, not a disguised gate-loosening.**
2. **Dispersion, binance∩bybit multi-year, hold-until-sign-flip** — cost = round-trips actually
   incurred (decoupled from CV fold count), DSR-penalized across the pair universe. This is the
   configuration the rev2 screen could not evaluate and wrongly dismissed.

## Charter / costs
`git status`: only `research/` screens + their tests modified (tracked); `backfill_funding_history.py`
untracked. **Zero `core/` / `config.py` / live-path edits, no commits, no WIDEN-SL.** Costs mirror
`config.FEE`/`config.SLIPPAGE` exactly (futures taker 5bps, maker 2bps, bybit taker 6bps, slippage
5bps/side). No synthetic data anywhere. (Note: a `requirements_freeze_pre_upgrade_2026-07-09.txt`
indicates a ccxt→4.5.64 upgrade accompanied this backfill; funding values verified against that
version.)

## Debate record (edge-screener position vs auditor)
No live edge-screener agent in this single-invocation audit; per protocol both positions are
recorded for the owner. **Screener (A):** sign non-persistent → NO_GO on merit → ledger row.
**Auditor (A):** sign is persistent ([1,1,1,1] across folds); NO_GO is driven by an
n_splits-scaled cost artifact; best pair single-round-trip OOS-positive; no ledger row, run the
long-hold follow-up. **Both agree:** no capital promotes from either candidate today.
