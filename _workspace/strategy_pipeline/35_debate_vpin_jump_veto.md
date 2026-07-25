# 35 — Debate: VPIN jump-risk veto

**Date:** 2026-07-25 (orchestrator; Fable Task usage-capped mid-session — debate written in-loop from bull/bear artifacts)  
**Inputs:** `35_research_brief_vpin_jump_veto.md`, `35_bull_vpin_jump_veto.md`, `35_bear_vpin_jump_veto.md`, `27_prereg_vpin_jump_veto.*`  
**Screen outcomes:** none (not invented)

## SideScores

| Side | Evidence quality | Confidence claimed | Strongest card | Weakest card |
|------|------------------|--------------------|----------------|--------------|
| Bull (68) | High on process/bleed; Moderate on mechanism | Proceed with screen | Frozen hashed prereg + negative-selection precedent + queue binding | Relayed single peer-reviewed jump paper; P(GO) admitted low (~15–25%) |
| Bear (85) | High on local screens + warehouse n | Park / cheap pre-check first | −0.24R substrate + 0/16 positive-selection + 2/99 BTC/ETH trades | May undervalue process value of closing a frozen prereg cleanly |

**Weighing:** Bear’s structural gate arithmetic and substrate count are higher-quality *local* facts than Bull’s mechanism optimism. Bull correctly notes information value and queue order. Neither side claims a live install.

## UnsupportedClaims

- Bull inference that VPIN is a “sharper clock” than ADX/vol that will stratify ΔEV — **untested**; plausible but not evidenced after costs.
- Any implied GO probability above “low” — prereg expectation is NO_GO; pipeline base rate is sparse GO.
- Bear’s “foregone conclusion” if read as “never run” — overstated; a **feasibility pre-check** can falsify the starvation/miscalibration risks cheaply.

## KeyUnknowns

1. Raw VPIN distribution on Binance BTC/ETH vs frozen θ grid (fire rate).  
2. Whether live AccBand BTC/ETH n can reach ≥30 skipped+kept in horizon, or whether replay is required (and at what disk/time cost).  
3. Arm-count semantics (pooled vs per-arm n≥30) — must be pinned in writing before outcomes if screen proceeds.  
4. Incremental ΔEV vs a baseline that currently may run with BAND_REGIME_FILTER off (owner flow posture) — overlap with ADX/vol unknown.

## Recommendation

**REVISE**

Do **not** burn the full heavy harvest/screen slot blind. Do **not** REJECT the family (ADJACENT veto expression remains screen-eligible; directional VPIN stays STOP).

**Authorized next action (HumanAction):** Stage-0 feasibility only, then branch:

1. **Stage-0 (cheap, same UTC day if capacity remains):** harvest a *small* pre-declared aggTrades slice; compute raw VPIN; report θ-crossing rates + projected n; pin substrate (live vs replay) and n semantics in a one-page addendum that does **not** alter frozen θ/gates/construction.  
2. **If Stage-0 shows both arms can reach n≥30 within a defined window** → escalate to full `strategy-evidence-pipeline` screen under frozen `27_*` (APPROVE-for-screen).  
3. **If Stage-0 fails** → close queue item as INSUFFICIENT_DATA / park; free slot for C2 accrual / liquidation-cascade prep; no live path change.

APPROVE-for-screen without Stage-0 is rejected by this debate (bear R2/R3/R7 dominate bull’s schedule pressure).

## ConditionsForRevise

- Stage-0 passes → upgrade to APPROVE (screen only).  
- Owner explicitly reorders queue or re-values WR-protection vetoes → revisit.  
- Substrate expectancy ≥0 after costs → materially stronger case (bear InvalidateIf 4).

## LedgerCheck

- Directional VPIN: STOP (adverse anchor).  
- Veto overlay: ADJACENT; no REFUTED row blocks the screen.  
- Does not reopen formulaic alphas, QH-imbalance, or band positive-selection.  
- Mass chart-sweep / pullback installs: out of scope (ledger STOP 2026-07-25 reopen test).

## Sources

Bull/bear files §Sources; prereg `27_*`; brief `35_research_brief_*`; ledger skill.
