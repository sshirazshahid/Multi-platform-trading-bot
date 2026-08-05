# 55 — Integration report: MTSI (Micro Two-Sided Inventory)

**Date:** 2026-08-01  
**Verdict:** CONFIRMED_NO_GO (expectation met)  
**Shadow probe:** **NOT wired** (plan: only on CONFIRMED_GO)

## Shipped

| Item | Status |
|------|--------|
| Deep research `55_deep_research_mtsi_micro_mm_2026-08-01.md` | done |
| Hashed prereg `55_prereg_mtsi_inventory.{md,json}` sha256 `5262e070…` | done |
| Skill `.claude/skills/strategy-research-wiring/SKILL.md` | done |
| Sim `research/sim_mtsi_inventory.py` | done |
| Challenges `research/challenge_mtsi.py` | done |
| Screen `55_screen_mtsi_inventory.*` — 0/6 cells GO | done |
| Mission Control `/api/mtsi` + deck panel | done |
| Ledger row (refuted-families-ledger) | done |
| AccBand κ `52_*` | deferred (not primary) |

## Explicitly not shipped

- `MtsiProbeAgent` / `_PROBE_SPECS` registration
- AccBand allowlist reopen / CONTROLLED_LIVE
- `ENABLE_DCA` / `ENABLE_REBALANCE`
- Raising the $1 inventory cap

## Allowlist

`APPROVED_PAPER_STRATEGIES=F1` unchanged by this work.

## Honesty

Many micro maker clips under CEX fees + adverse-selection haircut do **not** clear after-cost gates on the frozen synthetic path. Inventory control ≠ edge.
