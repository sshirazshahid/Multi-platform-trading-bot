# 41 — Integration note (ACCRUE_ONLY implement)

*UTC day 2026-07-29 | Dual-model both-agree + ai-reviewer APPROVE*

## Matched verdict

**`ACCRUE_ONLY`** — prereg `sha256_md=13ee84e40f2604b660d763082f2681200532c1f6bd55cbbb21f4c9491085afaf`

## Harvest verify (done)

| Check | Result |
|-------|--------|
| `start_all.ps1` includes `liq-harvester` | Yes (`scripts\harvest_liquidations.py`) |
| Process live | Yes — single logical instance (venv launcher PID + child CPython PID is normal Windows venv spawn, not dual writers; process lock blocks a true second instance) |
| Restart this iteration | Killed stale orphans; restarted via venv; confirmed second `python -I harvest_liquidations.py` prints `another harvester instance is already running; exiting` |
| `data/liquidations_status.json` | `connected=true` after restart; status refreshes on flush cadence (~120s); in-memory `total_events` resets on process restart (expected) |
| JSONL | `data/liquidations_history.jsonl` preserved (~46k rows); hour rows append on completed-hour flush |
| WS URL | `/market/ws/!forceOrder@arr` (post-2026-04-23 path); wired in `scripts/start_all.ps1` as `liq-harvester` |

### Undercount (binding — do not “fix” post-hoc)

Binance forceOrder stream delivers at most the latest liquidation per symbol per ~1000ms. Hourly `long_usd`/`short_usd` therefore **understate** true cascade notional. Screens under this hash must treat Θ thresholds as noisy lower bounds, not complete cascade size. No Tardis backfill without a **new** prereg.

## Explicitly not built

- No shadow probe agent
- No MCP / order-path change
- No after-cost outcomes under this hash today
- No FIT-alt pooling

## Queue for next heavy UTC day

Majors-only (`BTC`,`ETH`) Stage-0 → after-cost screen citing hash `13ee84e40f2604b6…`, 30/60 bps grid, Holm multiplicity. Requires fresh ai-reviewer pass at that stage. FIT remains fail-closed cell-by-cell.

## Edge-queue touch

Item “Liquidation-cascade / OI-flush reversion” moves from “accrue before prereg” → **prereg FROZEN + harvest green + screen queued**. C2 / F1 / unlock clocks unchanged.

## Parked next loop day

S1: `tsmom_20d_1h` GATE_BLOCKED (42/30, OOS-WR 0.33, AUC 0.5, −EV).
