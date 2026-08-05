---
name: strategy-research-wiring
description: Wire deep-research strategy ideas into Trading_Bot via evidence gates only — hashed prereg, after-cost screen, adversarial challenge, log-only shadow. Primary doctrine MTSI (many micro clips, ≤$1 inventory, never one big trade). Use when owner asks to build/wire strategies from research, simulate SPOT+FUTURES challenges, or add maker inventory MM. Never CONTROLLED_LIVE from narrative.
---

# Strategy Research Wiring

## Doctrine (binding)

1. Build Up & Down at different times (inventory/time drives live side).
2. Keep both sides balanced; **gross inventory ≤ $1 USD** for MTSI v1.
3. Small directional tilt only when a pre-registered fair-value residual says a side is undervalued.
4. **Edge can never be one big trade** — capture small inefficiencies thousands of times with systematic maker-first execution.

## Hard stops

- Never enable CONTROLLED_LIVE or reopen allowlists from research narrative alone.
- Never enable `ENABLE_DCA` / `ENABLE_REBALANCE` as a substitute for inventory MM.
- Never use MCP AccBand score as MTSI fair-value tilt (different family; AccBand ≈ −0.24R geometry).
- Never claim co-located HFT profitability on Binance/Bybit majors without rebate + latency evidence.
- Hash prereg **before** computing outcomes. Fail closed on hash mismatch.

## Workflow

1. **Ledger check** — refuted families without reopen bar → answer and stop.
2. **Hashed prereg** — write `_workspace/strategy_pipeline/{NN}_prereg_*.md` + `.json` with sha256 of MD.
3. **Stage-0** — if fill/trigger count < prereg minimum for every cell → `INSUFFICIENT_DATA`.
4. **After-cost screen** — run `research/sim_mtsi_inventory.py` (or family-specific screen); SPOT = fees only; FUTURES = fees + funding.
5. **Challenge** — fee understatement, adverse selection, look-ahead, `$1` invariant, “one big trade” ban.
6. **Audit** — dual-model / honesty-auditor when available.
7. **Shadow only on CONFIRMED_GO** — `_PROBE_SPECS` log-only; promotion = frozen gate ≥30 + owner sign-off.
8. **Monitor** — Mission Control `/api/mtsi` inventory + clip PnL histogram (geometry/inventory ≠ edge).

## Refuse list (from 53/54 research)

Grid-as-edge, DCA-as-edge, auto-optimizer live, VPIN reopen without new prereg, LOXM-as-alpha, options without venue data, AccBand reopen from κ narrative alone.

## Artifacts

| Item | Path |
|------|------|
| MTSI research | `_workspace/strategy_pipeline/55_deep_research_mtsi_micro_mm_2026-08-01.md` |
| MTSI prereg | `_workspace/strategy_pipeline/55_prereg_mtsi_inventory.{md,json}` |
| Sim | `research/sim_mtsi_inventory.py` |
| Challenges | `research/challenge_mtsi.py` |
| Deferred AccBand κ | `52_prereg_cost_aware_accband_kappa.*` |

## Relation

- Investment committee packages debate → hands screens here / to strategy-evidence-pipeline agents.
- F1 carry remains the only ledger profit family when funding clears; MTSI does not replace F1.
