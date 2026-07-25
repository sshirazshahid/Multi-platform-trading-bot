# 27 — Integration report: VPIN jump-risk veto

**Date:** 2026-07-25  
**Candidate:** `vpin_jump_veto_v1`  
**Screen verdict:** NO_GO (auditor CONFIRM)  
**Action:** **NO-OP** — no shadow probe, no MCP/live path change.

## Why no integrate

Frozen gates require OOS ΔEV>0 under multiplicity. All four θ cells: fire_rate=0, ΔR=0, MC P(>0)=0 on kept arm. Nothing to promote.

## Artifacts

| Artifact | Path |
|----------|------|
| Override | `36_owner_override_vpin_full_screen.md` |
| Harvest | `data/aggtrades_vpin/` (gitignored) + `scripts/harvest_aggtrades_vpin.py` |
| Screen | `27_screen_vpin_jump_veto.{md,json}` |
| Audit | `27_audit_vpin_jump_veto.md` |
| Tests | `tests/test_screen_vpin_jump_veto.py` (4✓) |

## Queue

VPIN closed. Next: C2 Deribit gamma-expiry accrual / liquidation-cascade prep per `30_edge_queue` / `32_*`.
