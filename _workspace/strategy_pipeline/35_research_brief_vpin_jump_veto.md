# 35 — Investment Committee Research Brief: VPIN jump-risk veto

**Date:** 2026-07-25 (local) / committee session  
**Candidate:** `vpin_jump_veto_v1` (queue #1)  
**Market:** Binance USDT-M BTCUSDT + ETHUSDT  
**Objective:** Committee packaging before after-cost screen (NOT directional entry; NOT live install)  
**Novelty vs ledger:** ADJACENT — veto overlay on AccBand lane; **directional VPIN is STOP** (adverse −15.6 bps net 2026 practitioner WF). Formulaic alphas / QH-imbalance REFUTED — does not reopen them.

## FACT (sourced)

1. Prereg FROZEN+hashed: `_workspace/strategy_pipeline/27_prereg_vpin_jump_veto.{md,json}` — expectation **NO_GO**; treatment = veto AccBand OPEN when VPIN_t > θ ∈ {0.55,0.60,0.65,0.70}; primary metric after-cost ΔEV vs no-veto baseline; n_trials=4.
2. Mechanism literature: VPIN as jump/volatility predictor (RIBAF Jan 2026) — toxicity/jump risk, not directional alpha (`32_deep_research_futures_2026-07-24.md` §VPIN / prior `24_*`).
3. AccBand dual-goal CONFIRMED_NO_GO (`30_*`): geometry WR ≠ after-cost profit; expectancy ≈ −0.24R class. Band-regime veto already ships (ADX/vol) as WR-protection, not edge.
4. Screen **not yet run** — harvest path `data/aggtrades_vpin/` required; queue scheduled heavy stage for UTC day 2026-07-25.
5. Binding rails: PAPER, SIGNAL_SOURCE=mcp, live OFF; promotion needs frozen gate + owner sign-off.

## INFERENCE

- Best honest use is a **conditioning/veto** study mirroring BAND_REGIME_FILTER — may cut bleed if ΔEV>0; may be pure churn if only WR rises while EV falls.
- Prior remains NO_GO until OOS ΔEV clears multiplicity-controlled gates.

## Missing data (blocking for screen, not for debate)

- Continuous aggTrades harvest for VPIN buckets (not yet produced for this prereg).
- Screen outcomes — must not be invented.

## Committee scope

Bull/bear argue whether to **proceed with the pre-registered screen** (harvest → after-cost ΔEV) vs **park/defer**. APPROVE ≠ install veto live.
