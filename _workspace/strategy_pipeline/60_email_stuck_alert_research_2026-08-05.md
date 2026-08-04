# Email / Stuck-Alert Defects: Research Report
*Generated: 2026-08-05 | Sources: local bot surface + 5 external | Confidence: High (local), Medium (external patterns)*

## Executive Summary

Two distinct email paths were annoying the operator: (1) **Daily Intel Synthesis** — a Windows scheduled task explicitly passed `--email` to an advisory research script; (2) **stuck_open_positions** — the in-process health watchdog emailed when warehouse trades stayed `OPEN` >24h, even when those rows still matched live `positions.json` holds under tier-geometry (up to ~72h). Industry monitoring practice agrees: age alone is not orphanhood; orphans are **cross-system custody gaps**. Fixes: drop `--email` from the schtask (durable bat wrapper), and alert only on warehouse OPEN rows with no matching tracker open (edge-triggered).

## 1. Local email / alert surface

| Path | Trigger | Channel | Intended? |
|------|---------|---------|-----------|
| `TradingBot-IntelSynthesis` → `run_intel_synthesis.py --email` | Daily ~06:00 | SMTP via script | No — advisory report should be file-only by default |
| `HealthWatchdog._check_stuck_open_positions` | Every ~1 min tick; cooldown 1h | `EmailNotifier.alert` | Yes for true orphans; No for live holds |
| `BotEngine._daily_summary` | 00:00 UTC | `notifier.daily_summary` | Separate “daily PnL” email — not the synthesis complaint |
| `TradingBot-MarketIntel` | ~4h | File only (`run_market_intel.bat`) | Correct pattern to copy |
| `core/engine/gate_health.py` §5b | Log warnings | Logger only | Same false-positive logic as watchdog (twin bug) |

Live evidence (2026-08-05): 4 warehouse OPEN rows matched 4 tracker opens exactly; BNB age ~25.6h → would trip age-only stuck alert while still intentionally held.

## 2. Industry pattern: orphan ≠ long hold

- Position-level orphan detection is a **custody gap**: open in one ledger, absent from the managing process’s healthy registry — not “older than N hours” ([Jeremy Knox](https://www.jeremyknox.ai/blog/position-level-orphan-detection-why-heartbeat-monitoring-isnt-enough/)).
- Bot monitoring runbooks treat exchange↔internal mismatches as distinct alerts from process-down, with actionable detail ([NexusFi](https://nexusfi.com/a/automation/trading-bot-monitoring); [rift RUNBOOK](https://github.com/Nexstone/rift/blob/main/docs/RUNBOOK_ALGO_MONITORING.md)).
- Soft vs hard thresholds: noisy alerts get muted by operators — false stuck emails erode trust in real custody alerts ([Finnovic NOP monitoring](https://www.finnovic.com/blog/real-time-exposure-monitoring.html)).

**Inference (labeled):** For this PAPER bot, `positions.json` is the authoritative live registry; warehouse `status='OPEN'` without a matching (exchange, symbol, side) open is the correct orphan signal. Exchange API reconciliation would be a stronger next step for CONTROLLED_LIVE, out of scope for this defect.

## 3. Defect classification (orch-fix-defect)

- **Size:** small (watchdog + gate_health + schtask/bat; no new contract).
- **Root cause A:** schtask Action included `--email` (opt-in flag used as default).
- **Root cause B:** stuck check used age threshold as proxy for “close bookkeeping failed,” colliding with `TIER_GEOMETRY_TIME_EXIT_HOLD`.
- **Phases:** light research → TDD (regression tests for hold-silent / orphan-alert / edge) → review → Gate 2 commit.

## Key Takeaways

- Synthesis email: stop at source (no `--email` in scheduled runner); keep CLI flag for manual opt-in.
- Stuck email: reconcile warehouse vs `positions.json`; edge-trigger sticky conditions.
- Align GateHealth log finding with the same orphan helper so ops logs don’t contradict email silence.
- Restart supervisor so in-process watchdog loads the new code; schtask change is already live for tomorrow’s 06:00 run.

## Sources

1. Local: `core/health_watchdog.py`, `scripts/run_intel_synthesis.py`, `core/engine/lifecycle.py`, `utils/notifier.py`, schtask `TradingBot-IntelSynthesis`, live warehouse/`positions.json` sample.
2. [Position-Level Orphan Detection](https://www.jeremyknox.ai/blog/position-level-orphan-detection-why-heartbeat-monitoring-isnt-enough/) — custody-gap definition.
3. [Trading Bot Monitoring (NexusFi)](https://nexusfi.com/a/automation/trading-bot-monitoring) — reconcile internal vs broker; actionable alerts.
4. [rift RUNBOOK_ALGO_MONITORING](https://github.com/Nexstone/rift/blob/main/docs/RUNBOOK_ALGO_MONITORING.md) — orphan on restart / FLAT vs exchange open.
5. [NOP Monitoring (Finnovic)](https://www.finnovic.com/blog/real-time-exposure-monitoring.html) — soft/hard limits; false-alert fatigue.
6. [Risk & Liquidation Engine notes](https://dev.to/sandrawilliam/what-i-learned-building-a-risk-liquidation-engine-for-a-trading-platform-1154) — idempotent state (context only).

## Methodology

Searched web for orphan/stuck position monitoring patterns (no Firecrawl/Exa MCP in this environment — used WebSearch). Mapped all local SMTP/`notifier.alert` / `--email` call sites. Sub-questions: what sends the emails; is age a valid stuck proxy; what industry defines as orphan; what durable fix prevents schtask regression.
