---
name: trading-monitoring
description: 'Design or audit fail-closed observability for this 24x7 crypto futures bot: decision provenance, atomic heartbeat/state, durable alert delivery, health contracts, reconciliation telemetry, shadow-evidence maturity, and external-supervisor signals. Use for logging, dashboards, metrics, alerts, Telegram/email, watchdogs, incident reconstruction, strategy decay, or questions such as "is the bot alive" and "why did this trade happen".'
---

# Trading Monitoring

## Overview

Make every decision and safety transition reconstructable without querying an exchange after the fact. Monitoring is part of the entry gate: stale clocks, missing heartbeats, dead private streams, unknown positions, failed persistence, or unverified alert delivery must become explicit degraded states and pause new exposure.

Keep the health producer and the restart authority in separate failure domains. The bot publishes atomic state; an external supervisor reads it and decides whether the process is healthy. A log line from the same blocked event loop is not proof of liveness.

## When to Use

- Add or review event logging, metrics, dashboards, health endpoints, or daily reports.
- Implement Telegram/email alerts, acknowledgement, retry, cooldown, or escalation behavior.
- Diagnose missing trades, unexplained decisions, stale streams, silent loops, or failed restarts.
- Measure backtest-to-shadow divergence, mature outcomes, protection latency, costs, or edge decay.
- Define the heartbeat contract used by the Windows external supervisor.

Do not use dashboard availability, raw win rate, or "no exception logged" as evidence that trading is healthy.

## Prerequisites

- Work from the repository root with Python 3.9+ and the project environment installed.
- Inspect `core/bot_engine.py`, `core/health_watchdog.py`, `core/warehouse.py`, `core/decision/`, `scripts/live_monitor.py`, and `scripts/launcher_supervisor.py` before changing telemetry contracts.
- Identify all writers and readers for SQLite, JSON/JSONL state, logs, alerts, and `/health`.
- Define UTC event time, receive time, monotonic age, schema version, and mode (`OBSERVATION`, `PAPER`, `CONTROLLED_LIVE`) for each record.
- Keep remote health/dashboard endpoints loopback-only unless a separately authenticated private tunnel is explicitly authorized.
- Read `references/monitoring-contract.md` before changing heartbeat or critical-alert semantics.

## Workflow

1. **Inventory safety decisions.** Trace signal creation, rejection/approval, order intent, submission, acknowledgement, fill, protection, amendment, reconciliation, breaker, pause, flatten, and restart. Assign stable correlation IDs across the chain.
2. **Separate immutable events from current state.** Append decisions and transitions; never rewrite their history. Store compact current state separately. Use one serialized SQLite writer, WAL mode, bounded queues, explicit backpressure, and schema migrations.
3. **Preserve provenance.** Persist UTC event time, local receive time, source timestamp status, operating mode, strategy/model/config/data hashes, feature freshness, gate results, client/exchange IDs, costs, and final outcome. Segregate PAPER and `CONTROLLED_LIVE` metrics in every query.
4. **Publish heartbeat atomically.** Write a complete versioned payload to a same-directory temporary file, flush/sync as appropriate, then replace. Surface write failures; never swallow them. Include PID, boot ID, sequence, UTC timestamp, monotonic loop progress, active mode, last successful cycle, stream/account ages, clock drift, DB write age, protection status, and entry-pause reason.
5. **Expose cached health only.** `/health` may read local cached state but must not perform exchange/network calls. Bind to `127.0.0.1`. Report `healthy`, `degraded`, or `unsafe`; an HTTP 200 alone does not mean safe-to-enter.
6. **Make alerts durable.** Persist a critical alert to an atomic outbox before attempting delivery. Apply bounded network timeouts, retry with backoff/jitter, record provider response/delivery acknowledgement, and set cooldown only after confirmed delivery. Repeat unresolved critical incidents until operator acknowledgement. A failed send must remain pending and visible.
7. **Detect silence and contradictions.** Alert and pause entries on stale heartbeat/event flow, dead private/user stream, clock drift, DB growth stall, unknown positions/orders, missing protection, repeated REST errors, mode mismatch, model/replay mismatch, or supervisor restart. Prevent one broken alert provider from blocking the trading loop.
8. **Monitor economics honestly.** Track after-cost expectancy, payoff distribution, profit factor, drawdown, fee/funding/slippage drag, maker/taker leakage, protection latency, and replay/shadow divergence. Count only deduplicated mature outcomes; require 30-60 continuous shadow days and at least 100 independent mature outcomes for promotion evidence. Raw win rate is contextual, never sufficient.
9. **Exercise failure paths offline.** Test truncated state, disk-full/write failure, locked DB, stale heartbeat, hung loop with live process, dead streams, duplicate events, alert timeout/failure/recovery, process restart, clock jump, and corrupt outbox. Unit tests must not contact live providers or exchanges.
10. **Validate the monitoring manifest.** Populate `references/monitoring-contract.md`, then run:

```bash
python skills/trading-monitoring/scripts/validate_monitoring_contract.py \
  --input reports/monitoring/monitoring-contract.json \
  --output-dir reports/monitoring
```

11. **Gate rollout.** Block entry-capable modes while any mandatory health signal is stale or any critical delivery/reconciliation state is unknown. Return monitoring to healthy only after a fresh successful cycle and explicit recovery evidence, not merely a process restart.

## Output

Produce:

- `reports/monitoring/monitoring-contract-report.json` with deterministic pass/fail results.
- A versioned heartbeat schema and state-transition table.
- An alert matrix listing trigger, severity, persistence, timeout, retry, acknowledgement, cooldown, and operator action.
- Tests and an offline failure-drill record for heartbeat, persistence, and alert delivery.
- A status of `PASS_FOR_REVIEW` or `BLOCKED`; monitoring does not authorize live trading.

Never emit credentials, secret-bearing exception payloads, full account balances, or remote control endpoints without authentication and explicit authorization.

## Resources

- `references/monitoring-contract.md` - heartbeat, health, provenance, and alert-delivery contract.
- `scripts/validate_monitoring_contract.py` - offline contract validator.
- `scripts/tests/test_validate_monitoring_contract.py` - validator regression tests.
- Repository implementation: `core/health_watchdog.py`, `core/warehouse.py`, `core/bot_engine.py`, `scripts/live_monitor.py`, `scripts/launcher_supervisor.py`.
- Repository tests: `tests/test_heartbeat_24x7.py`, `tests/test_health_watchdog.py`, `tests/test_watchdog_edge_alert_latch.py`, and `tests/test_atomic_io.py`.
