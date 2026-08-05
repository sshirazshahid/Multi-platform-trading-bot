# ADR: Blueprint Phase 1 restructure (2026-08-05)

**Status:** Accepted  
**Context:** Deep-research blueprint `docs/research/deep-research_trading_system_blueprint_2026-08-05.md` — ops excellence first; De-Emotion intact.

## Decision

Ship Phase 1 ops/structure only:

1. **Warehouse orphan auto-close** (`reconcile_flat`) when OPEN rows have no `positions.json` match — learning book only; default on under PAPER (`WAREHOUSE_ORPHAN_AUTO_CLOSE`).
2. **Soft-stale entry latch** — block NEW opens on forward-feed / outage soft-stale; do **not** flatten losers on API outage by default (`OUTAGE_FLATTEN_LOSERS_ENABLED=false`).
3. **Hard `max_hold_force_flat`** past AccBand/tier/standard horizons (bypass soft-close grace).
4. **Mission Control telemetry** for `regime_short_bias_latest.json` + `liquidations_status.json` (log-only honesty).
5. **`SCALP_TIER_ENABLED` default false** — no live scalp leverage tier without after-cost GO.

## Non-goals (explicit)

- Live F&G / liquidation SHORT bias (prereg 61 remains ACCRUE_ONLY)
- New after-cost scalp strategy screen
- OOB `flatten_all` on hung heartbeat
- CONTROLLED_LIVE enablement
- Rewriting BotEngine / deleting shadow fleet

## Consequences

- Orphan warehouse rows stop poisoning learning analytics automatically in PAPER.
- Soft outages pause entries instead of panic-closing losers.
- Positions past family max-hold are hard-closed with a clear exit reason.
- Operators see regime/liq telemetry without confusing it for trade authority.
