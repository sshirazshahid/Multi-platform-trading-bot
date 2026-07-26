# 03 rev3 — Honesty-Auditor Findings (capital-scaled listing-short GO + binance∩bybit dispersion)

Auditor: honesty-auditor · Date: 2026-07-09
Inputs audited: `02b_rev3_screener_listing_short.md`/`.json` (**GO @7d,30d** — pipeline's FIRST GO),
`02a_rev3_screener_dispersion.md`/`.json` (NO_GO).
Code: `research/screen_listing_short.py` (`run_screen_rev3`,`_decide_rev3`,`apply_concurrency_cap`,
`is_crypto_base`), `research/screen_funding_dispersion.py` (`run_screen_rev3`,
`walk_forward_oos_spread_hold`,`_amortize_holds`); gates `core/{walk_forward,stat_tests,
promotion_gate}.py`, `core/decision/monte_carlo.py`. Prior: `03_rev2_audit_findings.md`.

Default position: each verdict is wrong until it survives attack. Both screens reproduce
byte-for-byte. The GO **survives** — but only under a materially corrected risk figure and a set
of binding conditions, and I formally correct the reported maxDD (~2× understated).

**Reproduction — EXACT.** Re-ran both `--rev3`. Listing: verdict GO; every gate value (n_accepted
71/34/12, acct_mean, WR 0.789/0.794, DSR 0.939/0.857, PBO 0.0714, OOS-WR 0.804/0.792, MC P>0
0.997/0.993, MC maxDD 0.0745/0.0729), universe 92 crypto / 103 all, and excluded-base list match
the JSON with zero mismatches. Dispersion: verdict NO_GO; best pair ZEC +0.4491 bps/0.3782 WR, DSR
0.9998, PBO 0.4209, all 15 per-pair values match.

---

## Candidate B — Post-listing perp short (capital-scaled) → **CONFIRMED_GO, but ONLY as a log-only shadow probe at UNLEVERED 3%-notional, with binding conditions. Reported maxDD is corrected upward ~2×.**

Every one of the eight frozen gates passes under independent recomputation. The GO moves no
capital; it routes to a log-only shadow probe. I confirm it **for that purpose only**. The findings
below are conditions and corrections, not gate-flips — but two of them (B1, B2) are the difference
between a safe probe and an unsafe deployment.

### FINDING B1 — HIGH — the reported MC maxDD (0.073) understates the TRUE intra-hold concurrent drawdown by ~1.4–2.1×. Gate still passes at 1× notional; does NOT flip the GO, but the headline risk number is wrong and the safety margin is ~1.6× not ~3.4×.

The frozen MC (`monte_carlo_trade_sequence`) block-bootstraps the **end-of-hold realized** per-trade
returns and takes peak-to-trough of their cumsum. It never sees (a) positions marking-to-market
*during* their holds, nor (b) up to 4 positions underwater *simultaneously* in calendar time. For a
short on a freshly-listed perp the danger lives precisely in the intra-hold excursion (the pump
before the decay). I reconstructed the actual accepted positions, pulled their 1h OHLCV paths, and
built the true calendar-time concurrent account-MTM curve (STAKE 0.03, forward-filled per bar):

| horizon | screen MC maxDD p95 (realized) | TRUE concurrent MTM maxDD (close) | worst-case (bar-high) | peak concurrent (calendar) |
|---|---|---|---|---|
| 7d  | 0.0745 | **0.1044** | 0.1136 | 4 |
| 30d | 0.0729 | **0.1397** | 0.1521 | 4 |

Worst single-position intra-hold MAE: SOMI pumped to **+290%** above entry (7d), ESP **+289%** (30d)
— i.e. a single 3%-notional short was unrealized −8.7% of account at its worst, and the screen's
realized short_net (−1.27 for SOMI) silently assumes you held through that entire excursion.
**The gate (≤0.25) still passes** on the honest number (0.10–0.15 < 0.25), so the GO is not
falsified — but the true margin to the capital-preservation bound is ~1.6× (30d), not the ~3.4×
the reported 0.073 implies. The screener disclosed this as "caveat 2" but did not quantify it; it is
now quantified. **The promotion gate and shadow probe MUST use the ~0.10–0.15 concurrent-MTM figure,
not 0.073.**

### FINDING B2 — HIGH — the model is unlevered, no-stop-loss, full-collateral, held-to-horizon. At the bot's live 3× tier the concurrent-MTM breaches 0.25; the charter's mandatory 8% SL is absent. The GO is safe ONLY at 1× notional.

The screen sizes each position as `0.03 × short_net` — 3% notional at **1× leverage, fully
collateralized, no SL, held to horizon**. This is internally consistent (a 1× short can absorb a
+290% excursion from the rest of the account without liquidation) but diverges from live reality on
three axes:
- **Leverage.** Scaling B1's concurrent-MTM to the bot's live STANDARD/SCALP **3× tier** gives
  maxDD ≈ 0.31 (7d) / 0.42 (30d) — a clear **breach of 0.25**, and SOMI's −290% intra-hold MAE at
  3× on 3% margin = −26% of account from one position, i.e. **liquidation** long before the modeled
  −1.27 realized exit. *Had the honest concurrent-MTM been computed at the live leverage tier, this
  screen would be UNSAFE.* Its safety is entirely contingent on **unlevered** sizing.
- **Stop-loss.** CLAUDE.md §2 mandates an 8% stop and the live bot applies ATR SLs; the screen holds
  naked through 290% adverse moves. A real 8% SL would truncate every large loser at −8% but may
  also destroy the mean-reversion edge the strategy relies on. The modeled no-SL economics are
  neither charter-compliant nor what the live path would execute.
- **Collateral.** "3% risk" in CLAUDE.md §2 conventionally means capital-at-risk, not notional; the
  screen treats it as notional. At 1× these coincide only because loss is bounded by full collateral.

Not a gate-flip for the log-only probe (which moves no capital), but a **hard promotion condition**:
the probe must be unlevered 3%-notional, and any promotion analysis must re-derive the drawdown at
the actual live leverage/SL before one dollar is risked.

### FINDING B3 — MEDIUM — the 30d GO rests on 34 cap-selected trades, only 4 above the MC floor of 30; 43/77 candidates discarded. Thin. (7d is healthy at 71.)

`apply_concurrency_cap` is outcome-independent (sorts by `(entry_ts, exit_ts, idx)`, releases on
`exit ≤ later entry`, accepts first-in under the 4-cap — **no sorting by return, no skipping
losers**; verified). Representativeness check of accepted vs capped-out realized short_net:

| horizon | accepted | capped-out | full pool |
|---|---|---|---|
| 7d  | n=71 mean +0.124 WR 0.789 | n=6 mean **−0.139** WR 0.667 | n=77 mean +0.104 WR 0.779 |
| 30d | n=34 mean +0.185 WR 0.794 | n=43 mean **+0.286** WR 0.837 | n=77 mean +0.241 WR 0.818 |

At **30d the cap is conservative** — the 43 discarded trades performed *better* than the 34 accepted
(mean +0.286 vs +0.185), so the cap did NOT time-select calm periods; if anything it understates the
edge. At **7d the accepted subset is slightly flattered** (mean +0.124 vs pool +0.104) because the 6
capped-out happened to be losers — but only 6 are dropped and WR barely moves (0.789 vs 0.779).
Net: no cherry-picking, direction of the cap effect is benign-to-conservative. The real caveat is
**sample thinness**: 30d clears the MC evaluability floor (30) by a margin of 4, on a cap-dependent
slice — fragile if two trades reclassify.

### FINDING B4 — MEDIUM — the entire funding-charged sample is a single continuous 2025-06 → 2026-05 listing-wave regime. No cross-regime validation.

The nominal window is 2024-01 → 2026-06, but `backfill_funding_history.py` fetches listing funding
Binance-only **since 2025-06-01**, so every pre-2025-06 listing fails `window_funding_covered` and is
excluded. Accepted entry span is **2025-06-12 … 2026-05-01** (7d: 5/24/22/17/3 per quarter across
2025Q2–2026Q2). The edge (new perps decay after a day-1 pump) is measured in exactly one crypto
regime — a sustained alt-listing wave. A regime where new listings *don't* decay (or a bear tape
where majors fall faster than listings, killing "beats control") is untested. Inherent to the data;
must be a live probe condition, not a resolved concern.

### FINDING B5 — LOW/MEDIUM — survivorship is small at 7d/30d and its dominant direction is conservative; the dangerous (sustained-pump) losers are demonstrably retained in-sample.

The universe is OHLCV-cache-derived; the funding backfill follows it. Delisting-during-hold is
near-nil at short horizons: `excluded_no_price_window` = 0 (7d) / 1 (30d), and among accepted names
**0 (7d) / 1 (30d)** have OHLCV truncating within 3d of exit. The worst short losers — SOMI (−1.27),
ESP (−1.07), AZTEC — are **present** in the sample, so the optimistic worry (missing sustained-pump
names that would have hurt the short) is directly contradicted for currently-tradeable names. The
unquantifiable residual is perps that listed in-window and were delisted-and-purged before caching;
these are dominantly dead tokens (short would have *won*) → their absence is **pessimistic**
(conservative), with a smaller optimistic sliver. Warrants a modest confidence haircut, not a
verdict change. The shadow probe captures forward survivorship natively (it will see delistings live).

### FINDING B6 — LOW — PBO 0.071 across 3 horizons is a decorative CSCV; DSR carries the multiplicity and is robust.

`_pbo_across_horizons` runs CSCV on a (common-listings × 3-horizon) matrix — only 3 "configs" and
≤12 common rows (90d starves it). A 3-column PBO is statistically weak and near-uninformative; it is
the *registered* method so not dishonest, but it should not be read as meaningful multiplicity
control. The real control is DSR, and DSR is **robust**: n_trials sensitivity shows 7d DSR stays
≥0.10 out to n_trials=120 (0.74) and 30d to n_trials=120 (0.54) — i.e. even at 20× the registered
n_trials=6 the DSR gate clears comfortably. The "second registration / n_trials=6" concern does not
flip the verdict.

### FINDING B7 — LOW — execution frictions at day-1 (spread, depth, shortability, borrow) are unmodeled, but the edge is too large to be killed by them; 5bps was NOT softened.

5bps/side slippage is optimistic for brand-new illiquid perps, but the raw edge (+12–18% mean) swamps
it. Stress test — re-running the full accepted-sample gate battery at 5/25/50/**100** bps/side:
at 100 bps/side (20× the modeled cost, 400 bps round-trip) **all gates still pass** (7d: MC P>0
0.993, DSR 0.890, WR 0.775; 30d: MC P>0 0.987, DSR 0.811, WR 0.765). The cost model mirrors
`config.FEE`/`config.SLIPPAGE` exactly and matches rev1/rev2 (not softened). The genuine unmodeled
risk is a **hard** shortability/borrow/position-cap constraint that prevents *opening* the short at
day-1 — this removes trades rather than shaving returns, and the log-only probe is the correct place
to measure it live.

### Disposition B — **CONFIRMED_GO (log-only shadow-probe candidate only), conditioned.**
All eight gates pass under independent recomputation, including the capital-preservation bound after
I correct maxDD from the reported 0.073 to the honest concurrent-MTM 0.10–0.15 (< 0.25 at 1× notional).
DSR is multiplicity-robust, the edge is slippage-robust to 20×, the cap is outcome-independent and
conservative at 30d, and the worst pumpers are retained in-sample. The rev2 full-stake ledger row
STAYS IN FORCE; this GO is the *sized/capped* variant. It is CONFIRMED **strictly** as eligible for a
log-only shadow probe — **NOT** a deploy-capital signal — under the binding conditions in B1/B2/B4.

**Binding conditions the shadow probe MUST satisfy to be promotion-gate-evaluable
(`core/promotion_gate.py`: MIN_DSR≥0.10, MAX_PBO≤0.5, OOS-WR≥0.55, AUC≥0.60):**
1. **Log intra-hold MTM.** Per shadow decision record: symbol, entry_ts/price, exit_ts, per-bar
   unrealized short return (or at least running min/MAE) AND realized after-cost short_net (fees +
   slippage + realized funding). Without the per-bar path the promotion gate cannot recompute the
   true concurrent-MTM drawdown (B1) and would re-inherit the 2× understatement.
2. **Concurrent-MTM monitor.** Compute the calendar-time account-MTM drawdown across all
   simultaneously-open shadow shorts (not the realized-return cumsum). Gate on ≤0.25 using THAT number.
3. **Unlevered 3%-notional only (B2).** No leverage tier; if the live path would apply a tier or the
   mandatory 8% SL, model that as a separate labeled variant — do not silently inherit the naked-1×
   economics.
4. **Capture real day-1 execution (B7).** Log actual shortability/borrow/position-cap availability and
   the realized day-1 spread/slippage, to replace the modeled 5bps at promotion time.
5. **Define & log a discriminating score for AUC (currently unevaluable).** This screen produces NO
   per-decision score — `AUC≥0.60` cannot be computed from it. The probe must emit a score (e.g.
   predicted post-listing decay / funding-adjusted expected short return) and the binary outcome, or
   AUC is un-gradeable and promotion is blocked regardless of the other four gates.
6. **Accumulate ≥30 resolved shadow trades** before running MC/DSR, and carry the family's honest
   sequential n_trials (≥6, growing with each registration).

---

## Candidate A — Cross-venue funding dispersion (binance∩bybit, hold-until-sign-flip) → **CONFIRMED_NO_GO. Ledger row now warranted (was correctly withheld in rev2).**

**Reproduction — exact.** 15 coins (ADA…ZEC), n_trials=15, best-by-mean ZEC binanceL/bybitS OOS
mean +0.4491 bps/settle, DSR 0.9998, PBO 0.4209, **OOS-WR 0.3782 < 0.55 → NO_GO**. MC P(>0)=1.0,
maxDD p95 0.033.

**A1 — the rev2 cost artifact is genuinely fixed; no leakage in the fix.** `walk_forward_oos_spread_hold`
chooses direction from `sign(mean(spread[train]))` and applies it UNSEEN to `spread[test]`
(identical selection rule to the rev1 function). `_amortize_holds` partitions the concatenated
applied-direction timeline into contiguous same-direction runs and charges **one** 42bps 4-leg
round-trip per run, amortized over its settlements — decoupled from `n_splits`, exactly the audit-A1
remedy. Because the train-chosen direction is persistent, each pair holds ≈one position over its
whole OOS window (one round-trip ≈ 0.01 bps/settle amortized), so the OOS mean is essentially gross
carry. rt_cost = 2·5(binance taker) + 2·6(bybit taker) + 4·5(slip) = **42 bps** — verified. Direction
is train-only; position/run boundaries derive from test-fold directions that were chosen on train —
**no look-ahead, no peek.**

**A2 — OOS-WR 0.3782 is honestly per-settlement and the binding failure is real.** `oos_wr =
np.mean(best_oos > 0)` over 4,048 settlements. The positive mean is carried by a fat right tail: only
~38% of individual settlements are on the profitable side. This is exactly the pathology the frozen
OOS-WR≥0.55 floor exists to reject (a mean rescued by a minority of large-differential settlements).
The failure reason is now the honest one (low per-settlement win rate), NOT rev2's refuted
"sign-reversal" claim.

**A3 — selection is honest and the NO_GO is robust.** The frozen rule selects best-by-OOS-mean (ZEC)
and requires it to pass ALL gates; ZEC fails WR. BNB alone clears WR (0.626) but has a lower mean and
is not selected — selecting it would be post-hoc cherry-picking. Even if it passed, BNB's +0.146
bps/settle is ~0.8%/yr on the 2×-notional delta-neutral footprint — economically marginal. DSR
(n_trials=15) is not the binding gate; WR is, so the NO_GO is independent of any DSR/n_trials debate.

### Disposition A — **CONFIRMED_NO_GO.** Zero unresolved findings. Unlike rev2 (where the viable
config was UNTESTED and a refuted row was correctly withheld), rev3 **tested** the viable config and
it is a merit NO_GO with the floor cleared and all six gates run — which the pre-registration itself
says qualifies for a narrowly-scoped ledger row.

**Ledger row warranted (narrowly scoped):**
> `cross-venue funding dispersion — binance∩bybit, hold-until-sign-flip, taker` — NO_GO 2026-07-09.
> Audit-A1-corrected single-round-trip hold model on multi-year binance∩bybit data (15 coins, 2,788–
> 4,832 OOS settlements/pair) yields positive OOS mean carry (best pair ZEC +0.45 bps/settle, DSR
> 0.9998, PBO 0.42, MC P(>0)=1.0, maxDD p95 0.033) BUT per-settlement OOS win-rate **0.378 < the
> frozen 0.55 floor** — the positive mean is a fat-right-tail artifact of a minority of large-
> differential settlements, not a repeatable per-settlement edge. Selection over 15 pairs is
> DSR-penalized (n_trials=15) and still clears DSR; the binding failure is win-rate. Refuted for THIS
> configuration only. Does NOT refute: maker execution, other venue pairs, or alternative hold rules
> (e.g. trade only when |differential| exceeds a threshold — the tail that carries the mean). The
> rev2 "sign non-persistence" reasoning is NOT the operative failure (that was the n_splits cost
> artifact).

---

## Charter / compliance
`git status`: only `research/` screens + the pipeline `_workspace/` artifacts touched. **Zero
`core/` / `config.py` / live-decision-path edits, no commits, no WIDEN-SL, no live-path mutation.**
Costs mirror `config.FEE`/`config.SLIPPAGE` exactly (futures taker 5bps, bybit taker 6bps, slippage
5bps/side); no synthetic data; no threshold softened vs rev1/rev2. The rev3 changes are the sanctioned
re-pre-registrations mandated by `03_rev2_audit_findings.md` Dispositions A & B — legitimate NEW
registrations, not disguised gate-loosening. Verification artifacts are throwaway in
`_workspace/strategy_pipeline/tmp_rev3_audit/` (uncommitted; `data/` is gitignored).

## Debate record (edge-screener vs auditor)
No live edge-screener agent in this single-invocation audit; both positions recorded for the owner.
**Screener (B):** all eight gates pass on the account-scaled/capped sample → GO @7d,30d; maxDD p95
0.073; concurrency-MTM flagged as an approximation (caveat 2). **Auditor (B):** GO CONFIRMED for a
log-only probe ONLY; the honest concurrent-MTM drawdown is 0.10–0.15 (~2× the reported 0.073) —
still < 0.25 at 1× notional so the gate holds, but the margin is ~1.6× not ~3.4×, and the GO is
UNSAFE at any live leverage tier or under the mandatory 8% SL; unlevered-notional + concurrent-MTM
logging + an AUC score are binding promotion conditions. **Screener (A) / Auditor (A) — agree:**
NO_GO on the OOS-WR floor; ledger row warranted for the binance∩bybit hold-until-flip config.
**Both agree: no capital promotes from either candidate today.**
