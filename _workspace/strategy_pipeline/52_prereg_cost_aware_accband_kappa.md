# 52 — Pre-registration: Cost-aware AccBand admit filter (κ × stressed RT)

**Status:** FROZEN before any outcome computation  
**Date:** 2026-07-31  
**Source:** Deep-research Candidate A (`51_deep_research_futures_strategies_2026-07-31.md`)  
  — arXiv 2606.00060 cost-aware execution filter (trade only when signal clears costs)  
**Class:** Veto / admit overlay on MCP directional AccBand PAPER — NOT a new entry family  
**Expectation:** **NO_GO** (prior: AccBand dual-goal `30_*` CONFIRMED_NO_GO; cost filter may cut bleed but is unlikely to clear joint profit+WR gates)  
**Owner posture:** F1-only (`APPROVED_PAPER_STRATEGIES=F1`) remains in force until funding clears; this prereg does **not** reopen AccBand opens.

## 0. Why this candidate (and not B/C)

| ID | Family | Why deferred |
|----|--------|--------------|
| A (this) | Cost-aware low-turnover admit filter | Local AccBand closed trades + planned TP already exist; maps directly to 2606.00060 |
| B | Perp pairs @ 8 bps | Needs pair-construction harvest + new codepath; higher burn risk |
| C | Cascade-fade worst-case fills | Already frozen as `41_prereg_liq_cascade.md` — do not re-prereg |

## 1. Null hypothesis

Among historical MCP directional AccBand PAPER outcomes (geometry cohort), applying a **stricter** admit rule  
`planned_tp_pct ≥ κ × stressed_round_trip_cost_pct` with κ > 1  
does **not** improve after-cost expectancy versus the unfiltered AccBand baseline under the joint gates in §5.

Falsification requires ≥1 κ cell clearing **all** §5 gates after multiplicity control.  
Motivation (literature only — not an outcome): hourly ML sign strategies collapse under ~10 bps unless a cost-magnitude filter slashes turnover ([arXiv 2606.00060](https://arxiv.org/html/2606.00060v1)).

## 2. Relationship to existing EconGate (binding)

- `paper_fallback` already refuses brackets with geometric breakeven_wr ≥ 1.0 (TP cannot clear stressed RT). That is **κ ≈ 1** in spirit (winner must be a net winner on costs).
- This screen tests **κ ∈ {1.5, 2.0, 2.5, 3.0}** — stricter clearance multiples on the **same** stressed cost definition.
- It does **not** replace F1. It does **not** loosen `BAND_REGIME_FILTER`, EntryFloor, or funding thresholds.

## 3. Data (frozen — local only)

| Field | Value |
|-------|-------|
| Source | `data/warehouse.sqlite` table `trades` (CLOSED, mode PAPER) |
| Cohort filter | `strategy_family` ∈ {`algo_det`, `algo`, `claude`, `systematic_v3_1`} **and** AccBand geometry markers available (see §4) |
| Price path | Not required for primary gates (use warehouse `realized_pnl`, `r_multiple`, `exit_reason`) |
| Leakage | Offline filter uses only fields known at entry: planned TP%, SL%, fees/slip config constants. No future bars. |
| No fetch | No exchange backfill for this screen |

**Stressed RT cost (frozen constant for all cells):**  
`C_stress = 0.00315` (31.5 bps) — the AccBand/`paper_fallback` default cited in `config.py` AccBand notes.  
Do **not** re-estimate C_stress from the evaluation cohort (that would bake in outcomes).

## 4. Signal / filter construction (frozen)

For each closed trade row eligible under §3:

1. **planned_tp_pct** (prefer in order; first finite wins):  
   - `|target_px − entry_px| / entry_px` when `target_px` and `entry_px` present and > 0  
   - else `|r_multiple|`-implied TP from entry geometry if `entry_stop_px` present:  
     `tp_pct = (|entry_px − entry_stop_px| / entry_px) × tp_frac` with tp_frac from side  
     (`ACCURACY_TP_FRAC_BUY=0.45` long / `ACCURACY_TP_FRAC_SELL=0.35` short / else 0.50)  
   - else row **excluded** from both baseline and treatment (not a skip — dropped from n)
2. **Admit(κ):** `planned_tp_pct ≥ κ × C_stress`
3. **Baseline:** all eligible rows (no κ filter)  
4. **Treatment arm κ:** eligible rows with Admit(κ)

**Forbidden predictors:** RSI/MA/MACD/SuperTrend, funding sign, OI, liquidation USD, ML forecast files not already in warehouse at entry, any post-exit feature.

## 5. Cells, multiplicity, gates

- **Cells:** κ ∈ `{1.5, 2.0, 2.5, 3.0}` → **m = 4 FIXED**. Bonferroni α = 0.05/4 = 0.0125 if a bootstrap mean>0 test is run; point gates below are mandatory regardless.
- **Min n per treatment cell:** ≥ 80 closed outcomes after Admit(κ), else that cell = `INSUFFICIENT_DATA` (does not shrink m).
- **Primary metric:** after-cost mean `realized_pnl` (USD) and mean `r_multiple` on the treatment subset.
- **Joint GO gates (ALL required for a cell):**
  1. `mean(realized_pnl) > 0`
  2. `mean(r_multiple) > 0`
  3. `profit_factor = gross_win_pnl / abs(gross_loss_pnl) > 1.0`
  4. `win_rate ∈ [0.59, 0.67]` (owner AccBand band — do not widen)
  5. ΔEV vs baseline: `mean(r_multiple)_treatment − mean(r_multiple)_baseline > 0`  
     (filter must beat keeping all AccBand fills, not only look good in isolation)
  6. Optional bootstrap: P(mean R_treatment > 0) ≥ 0.95 after Bonferroni — informational if compute-heavy; point gates 1–5 remain binding

## 6. Verdict rules

- **GO:** ≥1 κ clears all §5 gates **and** an adjacent κ (if any) is same-sign on ΔEV (anti threshold-mining). Still requires frozen promotion gate + owner sign-off before any allowlist reopen.
- **NO_GO:** no κ clears; or WR-in-band without positive expectancy; or ΔEV ≤ 0 vs baseline.
- **INSUFFICIENT_DATA:** all cells n<80 after filter.

## 7. Non-goals / forbidden during screen

- Reopening `APPROVED_PAPER_STRATEGIES` to include `mcp_registry`/`algo_det` from this prereg alone  
- Loosening F1 funding/contango thresholds  
- Changing EntryFloor, band filter, or AccBand fracs mid-screen  
- Adding cells after peeking  
- CONTROLLED_LIVE  
- Claiming GO from literature 2606.00060 numbers (different market, model, costs)

## 8. Run protocol (after hash lock only)

1. Confirm sha256 of this markdown matches companion JSON.  
2. Implement offline screen `research/screen_cost_aware_accband_kappa.py` (TDD) — **no live path**.  
3. Write `52_screen_cost_aware_accband_kappa.{md,json}` only after hash lock.  
4. Honesty-auditor / dual-model both-agree before any `.env` change.  
5. Even on surprise GO: AccBand reopen is owner-signed and separate from F1.

## 9. Artifacts

- This file: `52_prereg_cost_aware_accband_kappa.md`  
- Companion: `52_prereg_cost_aware_accband_kappa.json` (records sha256)  
- Screen (later): `52_screen_cost_aware_accband_kappa.*`  
- Parent research: `51_deep_research_futures_strategies_2026-07-31.md`
