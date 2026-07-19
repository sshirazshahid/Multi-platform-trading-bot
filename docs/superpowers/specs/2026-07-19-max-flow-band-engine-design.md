# Max-Flow Band Engine — Design + Implementation Plan (2026-07-19)

**Owner-approved** (AskUserQuestion chain, 2026-07-19): Max-flow firehose variant with **score threshold 50** (owner amendment from 40), SL-cooldown disabled, band geometry re-enabled, clean cohort epoch. PAPER-only. Goal served: "maintain WR + profitability ratio 63–67% daily" — this makes the WR half measurable daily; the profitability half is reported as measured.

## Design

1. **Flow**: directional (mcp) lane entry score floor 65→**50** via env knob; SL-cooldown gate disabled via env flag. Expected ≥30 closed outcomes/UTC-day (the daily maturity floor).
2. **WR mechanism**: re-enable the `ACCURACY_TARGET_MODE` runtime chokepoint (owner explicitly approves reversing the parallel session's disable). `.env` already carries `ACCURACY_TARGET_MODE=true`, fracs buy 0.35 / sell 0.30 → WR lands in-band by TP/SL construction.
3. **Cohort integrity**: new paper profile `MAX_FLOW_BAND`; heartbeat `paper_trading_profile` + `paper_profile_started_at` give the clean epoch; goal report's profile lane scopes automatically. Gate-set + fracs frozen here; any mid-cohort change = new epoch, journaled.
4. **Untouched**: universe filter, band regime filter, daily-loss breaker (20%), ATR SL, exposure caps, leverage clamps, PAPER/live latches, tokenized-stock blocks, CONTROLLED_LIVE path.
5. **Rollback**: manual — revert the `.env` block + supervisor restart (env-inheritance rule: supervisor, not main.py). Breaker remains the automatic backstop. Goal loop reports (never auto-reverts) if mature-day WR sits outside 55–75% for 3 consecutive days.
6. **Binding honesty box**: the WR band is delivered by geometry, not edge. At threshold 50 entries are lower-conviction; expectancy expected ≤ historical ≈ −0.24R and reported honestly. No gate-set can manufacture `net_after_cost_pnl>0`; that half of the goal remains edge-gated. Paper bleed = accepted research cost per standing owner directive.

## Implementation tasks (TDD, verified anchors 2026-07-19)

**T1 — Score floor env knob.** `core/mcp_brain.py` (docstring l.32: "score >= 66 AND 6+/10 layers; scalp >= 65") — locate the actual constants/comparisons; introduce `MCP_ENTRY_MIN_SCORE` env (default preserving current 66/65 behavior when unset) in `config.py`, honor it in the gate(s). Test: env=50 admits a score-55 candidate that default rejects; default unchanged.
**T2 — SL-cooldown flag.** `core/risk_manager.py:549 is_sl_cooldown_active` (consumed `core/bot_engine.py:2997`): add `SL_COOLDOWN_ENABLED` env (default true); when false return (False, "sl_cooldown_disabled_by_profile"). Test both states.
**T3 — Geometry re-enable.** `core/bot_engine.py:3707-3718` chokepoint calls `mcp_brain._apply_accuracy_target`; find where runtime forces it off despite `config.ACCURACY_TARGET_MODE["requested_enabled"]=True` (check config.py's ACCURACY_TARGET_MODE dict for a derived/forced `enabled` and any bot_engine/order_manager guard; also order_manager.py:5004 time-exit suppression). Restore honoring of `requested_enabled`. Test: with env true + fracs 0.35/0.30, computed TP compresses vs SL per `_apply_accuracy_target`; with env false, unchanged.
**T4 — Profile.** `core/entry_policy.py:47-64`: add `MAX_FLOW_BAND` ModeProfile (copy AGGRESSIVE_RESEARCH params) + env selection; heartbeat writes `paper_trading_profile="MAX_FLOW_BAND"` + fresh `paper_profile_started_at` on boot under it. Test profile resolution + validity set.
**T5 — Env block + restart + verify.** Append to `.env`: `MCP_ENTRY_MIN_SCORE=50`, `SL_COOLDOWN_ENABLED=false`, `PAPER_TRADING_PROFILE=MAX_FLOW_BAND` (exact var name per T4 implementation). Kill launcher_supervisor tree; clear `data/risk_incident_latch.json` if present; relaunch detached (PowerShell Start-Process); verify IN-PROCESS boot log lines print the new threshold, cooldown-off, geometry-on, and profile name (add one boot log line per knob if absent — in-process verification rule).
**T6 — Suite + journal.** Full pytest on touched test files + siblings green; journal the activation (epoch timestamp, frozen gate-set, honesty box reference).

Rules: TDD red→green per task; `git add` only named files (tree carries unrelated owner modifications); commit messages end with the Claude Fable co-author trailer; never touch live/CONTROLLED_LIVE semantics.
