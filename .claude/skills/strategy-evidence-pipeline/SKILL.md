---
name: strategy-evidence-pipeline
description: Orchestrates the full strategy workflow for this trading bot — research candidates, screen them after-cost on local data, adversarially audit, and integrate survivors as log-only shadow probes. USE THIS whenever the owner asks to research/find/apply/implement trading strategies or patterns, "update the program" with strategies, screen or backtest an edge idea, or re-run/update/refine/improve a previous strategy screen or pipeline run. Simple factual strategy questions are answered directly from the refuted-families-ledger skill instead of launching this pipeline.
---

# Strategy Evidence Pipeline (Orchestrator)

**Execution mode: HYBRID.** Phase 1 (research): parallel sub-agents — results-only handoff, no team overhead. Phase 2 (screen + audit): generator–verifier pair — team mode with SendMessage debate when available, otherwise sequential sub-agents with one written rebuttal round. Phase 3 (integration): single sub-agent. Per harness policy, every Agent call sets `model: "opus"`. Agent definitions: `.claude/agents/{strategy-scout, edge-screener, honesty-auditor, shadow-integrator}.md`.

Why this pipeline exists: this system refuted ~2,400+ pattern tests the hard way. The pipeline makes "apply a strategy" mean *earn evidence* — screen → shadow → frozen gate — so no idea, however exciting, reaches live capital on narrative alone.

## Phase 0: Context check (initial / follow-up / partial)
- `_workspace/strategy_pipeline/` exists + partial request ("re-screen X", "fix the audit finding") → partial re-run: invoke only the affected agent; downstream artifacts marked stale.
- Exists + a new research question → move workspace to `_workspace_prev/`, fresh run.
- Missing → initial run.
- Always read `reports/deep-research_*.md` and the refuted-families-ledger first — recent research seeds Phase 1 and settled questions are not re-searched.

## Phase 1: Scout — `strategy-scout` (1–3 parallel sub-agents by sub-question)
Output: `01_scout_candidates.md`. Every candidate carries a novelty-vs-ledger verdict; REFUTED candidates stop here with the ledger row cited.

## Phase 2: Screen + Audit — `edge-screener` ↔ `honesty-auditor`
Screener pre-registers and runs after-cost screens (`after-cost-screening` skill) → `02_screener_verdicts.md`. Auditor attacks each verdict incrementally (leakage/costs/multiplicity/charter) → `03_audit_findings.md`. Debate until resolution; deadlock = NO_GO with both positions recorded. Only **CONFIRMED GO** proceeds.

## Phase 3: Integrate — `shadow-integrator`
CONFIRMED GO only → log-only shadow probe per `shadow-probe-integration` skill → `04_integration_report.md`. No CONFIRMED candidates → explicit no-op report (a valid, honest outcome).

## Phase 4: Close out
Report verdicts to the owner (after-cost numbers, gates, next actions). Add NO_GO rows to `refuted-families-ledger`. Append a row to the CLAUDE.md harness change log. Keep `_workspace/` for audit trail.

## Data protocol
File-based handoff: `_workspace/strategy_pipeline/{NN}_{agent}_{artifact}.md` + JSON verdicts (shape in `after-cost-screening`). Final artifacts in `reports/`. Team mode adds SendMessage (screener↔auditor debate) and TaskCreate (phase tracking). Sub-agent mode returns summaries; files remain authoritative.

## Error handling
- Agent failure → one retry → proceed with the gap named in the final report; never silently drop a candidate.
- API session limits → degrade to sequential main-loop execution of the same steps (this fallback ran successfully on 2026-07-08).
- Conflicting evidence → keep both with sources, downgrade confidence; never delete.
- INSUFFICIENT_DATA verdicts must carry the exact harvest command as the next action.

## Test scenarios
- **Normal:** "screen the cross-venue funding dispersion idea" → Phase 0 finds the 2026-07-08 deep-research report → Scout condenses the brief → Screener pre-registers and runs on local funding history → Auditor confirms → integrator adds a log-only dispersion probe; owner reads shadow_vs_live after a soak.
- **Error:** unlock-short candidate needs an unlock calendar not present locally → Screener returns INSUFFICIENT_DATA + the harvest command; pipeline reports it as the blocking next step; nothing integrates.
