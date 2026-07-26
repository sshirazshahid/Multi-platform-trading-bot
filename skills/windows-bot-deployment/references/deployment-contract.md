# Windows Deployment Contract

This contract validates the current PAPER/OBSERVATION unattended path. It intentionally rejects scheduled `CONTROLLED_LIVE`.

## Required shape

```json
{
  "operating_mode": "PAPER",
  "controlled_live_enabled": false,
  "controlled_live_refused_by_launcher": true,
  "canonical_supervisor_action": true,
  "repository_working_directory": true,
  "at_startup_trigger": true,
  "start_when_available": true,
  "execution_time_limit_disabled": true,
  "multiple_instances_ignore_new": true,
  "limited_run_level": true,
  "secrets_absent_from_command_line": true,
  "singleton_lock": true,
  "supervisor_owns_worker": true,
  "external_heartbeat_monitoring": true,
  "atomic_heartbeat": true,
  "heartbeat_write_failures_visible": true,
  "bounded_restart_backoff": true,
  "owned_child_only_termination": true,
  "persistent_alert_outbox": true,
  "alert_delivery_acknowledged": true,
  "graceful_shutdown_then_kill": true,
  "task_restart_count": 999,
  "task_restart_interval_seconds": 60,
  "heartbeat_max_age_seconds": 900,
  "heartbeat_poll_seconds": 5,
  "failure_strikes_before_restart": 3,
  "startup_grace_seconds": 600,
  "worker_stop_grace_seconds": 15,
  "max_restart_backoff_seconds": 300
}
```

## Invariants

- `operating_mode` is PAPER or OBSERVATION and the live latch is false.
- Task Scheduler owns exactly one supervisor; the supervisor owns exactly one worker.
- Heartbeat age proves loop progress. A PID-only or HTTP-200-only check is insufficient.
- The 900-second age is a measured synchronous-job allowance, followed by three
  five-second confirmation strikes. It is bounded and must not be silently raised.
- The supervisor terminates only the child handle it created, never a process found by a broad name match.
- Task and supervisor restart policies have bounded delay to avoid a hot crash loop.
- State and alert outbox updates are atomic. Failed alert delivery remains pending.
- All paths are anchored to the repository; Task Scheduler's default System32 directory is never relied on.
- A pass means ready for an approved PAPER/OBSERVATION recovery drill, not live trading.
