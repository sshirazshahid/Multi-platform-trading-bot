# Monitoring Contract Reference

The manifest describes observable safety properties. It does not prove that an unattended process is healthy at this moment and does not authorize live trading.

## Required shape

```json
{
  "atomic_heartbeat": true,
  "heartbeat_write_failures_visible": true,
  "heartbeat_schema_versioned": true,
  "external_supervisor": true,
  "health_reads_cached_state_only": true,
  "append_only_decision_events": true,
  "decision_provenance_complete": true,
  "operating_modes_segregated": true,
  "persistent_alert_outbox": true,
  "alert_delivery_acknowledged": true,
  "alert_timeout_bounded": true,
  "cooldown_after_confirmed_delivery": true,
  "critical_repeat_until_operator_ack": true,
  "stale_or_unknown_pauses_entries": true,
  "secrets_redacted": true,
  "heartbeat_interval_seconds": 60,
  "stale_heartbeat_seconds": 900,
  "supervisor_poll_seconds": 5,
  "failure_strikes_before_restart": 3
}
```

## Invariants

- Write heartbeat and alert state atomically; a reader sees the old complete document or the new complete document, never a partial file.
- Use UTC wall time for audit and a monotonic clock for local age/timeout decisions.
- Heartbeat freshness must demonstrate loop progress, not only a living PID or health server.
- The 900-second stale budget is the measured bound for synchronous research jobs;
  three consecutive stale polls are required before owned-child recovery. Reducing
  the budget requires first moving or instrumenting those jobs so healthy workers
  are not terminated mid-cycle.
- The external supervisor must not import runtime bot modules or share the bot event loop.
- Persist an alert before delivery; failed delivery stays pending. Do not start cooldown before a provider acknowledgement.
- Unknown/stale state pauses new entries. Exit/protection management remains available when safely possible.
- Preserve mode and strategy/model/config hashes so PAPER evidence cannot contaminate live metrics.

Use stricter thresholds if the runtime contract already requires them.
