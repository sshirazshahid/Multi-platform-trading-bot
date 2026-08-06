# 52 — Screen result: Cost-aware AccBand admit filter (κ × stressed RT)

**Run:** 2026-08-06T04:15:59Z (loop iteration, session d0257d33)
**Prereg:** `52_prereg_cost_aware_accband_kappa.md` — sha256 verified at run time
(`8d94bd24583e5a2c93017d0d0eb5ac7b4a16d6d704437bb3c5d89f3f8fe6dae6`), frozen 2026-07-31, committed f43ba74 BEFORE this run.
**Screen code:** `research/screen_cost_aware_accband_kappa.py` (offline, read-only warehouse, no live path; 15 TDD tests in `tests/test_screen_cost_aware_accband_kappa.py`, all green).
**Raw artifact:** `52_screen_cost_aware_accband_kappa.json`

## Arithmetic verdict: **NO_GO** (matches prereg expectation)

## Cohort

- `data/warehouse.sqlite` `trades`, mode=PAPER, status=CLOSED, families {algo_det, algo, claude, systematic_v3_1}
- 1,186 rows fetched → 2 excluded (no stop geometry) → **n = 1,184 baseline**
- Exit window: epoch 1781985878 → 1785979423 (≈ 2026-06-21 → 2026-08-06 UTC)
- **Schema note (honest deviation record):** the trades table has NO `target_px` column, so §4's first-preference branch is vacuous for every row; all planned-TP values come from the frozen second preference (`|entry−stop|/entry × tp_frac`, buy 0.45 / sell 0.35 / else 0.50). This is within the frozen spec's stated fallback order, not a protocol change.

## Results

| Cell | n | WR | mean PnL (USD) | mean R | PF | ΔEV vs base (R) | Gates passed |
|---|---|---|---|---|---|---|---|
| baseline | 1,184 | 0.330 | −0.266 | −0.150 | 0.300 | — | — |
| κ=1.5 | 439 | 0.301 | −0.323 | −0.133 | 0.216 | +0.0164 | 1/5 (ΔEV only) |
| κ=2.0 | 425 | 0.306 | −0.325 | −0.132 | 0.214 | +0.0180 | 1/5 (ΔEV only) |
| κ=2.5 | 2 | — | — | — | — | — | INSUFFICIENT (n<80) |
| κ=3.0 | 1 | — | — | — | — | — | INSUFFICIENT (n<80) |

## Reading

- **No κ cell comes near the joint gates.** κ=1.5/2.0 fail 4/5: after-cost mean PnL, mean R, and PF all negative; WR ~0.30 far below the 0.59–0.67 band.
- The only passing check is ΔEV>0 (+0.016–0.018 R vs baseline) — the cost filter trims bleed slightly per trade but **cannot manufacture positive expectancy** (consistent with the standing "exits/filters can't manufacture EV" finding and the prereg's NO_GO prior).
- Mean per-trade PnL is *more* negative in treated cells (−0.32 vs −0.27 USD) while mean R is slightly less negative — treated trades are larger-SL rows; the filter selects wider-geometry trades, not better ones.
- κ≥2.5 is effectively empty (3 rows of 1,184): AccBand geometry almost never plans a TP ≥ 0.79%, so higher clearance multiples are untestable on this cohort — INSUFFICIENT_DATA at cell level, disclosed, m stays 4.

## Status: verdict stage PENDING-CODEX

Per protocol 19 both-agree rule, closure (CONFIRMED_NO_GO + scoped ledger row) requires the Codex leg. Codex probe today: still usage-limited (resets 2026-08-08 09:31 local). Fable leg position: **NO_GO — confirm and close with a sizing-scoped ledger row** ("cost-aware κ admit overlay on AccBand directional PAPER: no κ cell clears joint gates; ΔEV marginally positive but expectancy stays negative — filter is bleed-trim, not edge"). No .env change, no live-path change, no allowlist change (prereg §7 respected).
