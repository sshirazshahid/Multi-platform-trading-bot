# 36 — Owner override: VPIN full screen (skip Stage-0)

**UTC date:** 2026-07-25  
**Decision:** Explicit owner override of committee REVISE (Stage-0 first).  
**Action:** Run full after-cost screen under frozen `27_prereg_vpin_jump_veto.*` now.

## Binding constraints (unchanged)

- Prereg FROZEN; `sha256_md` = `2b880d1beaefd5f9b16b23997c15214b66489d36b4ac707432524c5d788c4406` (full-file bytes; verified 2026-07-25 before harvest/outcomes).
- θ grid / N / gates / construction: **do not alter**.
- Expectation remains **NO_GO** until measured otherwise.
- No live / MCP path change from this run; integration only on CONFIRMED_GO + owner sign-off.

## Accepted risks (owner)

- Multi-GB aggTrades harvest and possible INSUFFICIENT_DATA if θ never fires or n&lt;30.
- Burns the UTC-day heavy screen slot (C2 / liquidation wait behind).

## Next artifacts

- Harvest → `data/aggtrades_vpin/` (gitignored)
- Screen → `27_screen_vpin_jump_veto.{md,json}`
- Audit → `27_audit_vpin_jump_veto.md`
