---
name: windows-bot-deployment
description: Audit and deploy this repository's 24x7 Windows bot with Task Scheduler, the canonical external heartbeat supervisor, singleton ownership, bounded restart backoff, atomic state/alerts, secure credentials, and fail-closed operating-mode gates. Use for startup-at-boot, unattended PAPER/OBSERVATION runs, restart or hang recovery, clock/power/network hardening, scheduled-task changes, or incidents such as "the bot stopped overnight".
---

# Windows Bot Deployment

## Overview

Run two independent layers: Windows Task Scheduler owns the canonical launcher, and the launcher owns one bot worker while monitoring its atomic heartbeat. This detects both crashes and a living-but-hung process. Task Scheduler restores the supervisor after reboot or supervisor failure; the supervisor applies bounded restart backoff and never creates duplicate workers.

The repository's scheduled launcher is deliberately limited to `PAPER` and `OBSERVATION`. It refuses `CONTROLLED_LIVE`. Do not bypass that refusal, edit `.env` to arm live mode, or create an alternate scheduled live command. Live deployment remains blocked until its separate audited procedure, 1.0x/one-position/0.1%-risk/2%-gross gates, 30-60 days plus 100 independent mature shadow outcomes, and owner sign-off all pass.

## When to Use

- Install, inspect, repair, or remove the repository's Windows scheduled task.
- Diagnose crash loops, stale heartbeats, hung event loops, duplicate instances, or reboot recovery.
- Harden power, time sync, disk, network, credentials, logging, alerting, and graceful shutdown.
- Review `TradingBot.bat`, `scripts/launcher_supervisor.py`, or `scripts/install_24x7_task.ps1`.
- Prove an unattended PAPER/OBSERVATION collector can operate continuously and safely.

Do not use this skill to authorize real orders or work around a fail-closed launcher check.

## Prerequisites

- Work from the repository root on Windows 11 with PowerShell 5.1+ and Python 3.9+.
- Confirm `venv\Scripts\python.exe`, `.env`, `scripts/launcher_supervisor.py`, `scripts/install_24x7_task.ps1`, and writable `data/` and `logs/` paths exist.
- Verify `.env` reports `OPERATING_MODE=PAPER` or `OBSERVATION` and `CONTROLLED_LIVE_ENABLED=false` without printing secret values.
- Run the full offline test suite and targeted launcher/heartbeat tests before registration.
- Obtain explicit user approval before registering, replacing, starting, stopping, or deleting a Windows scheduled task, changing power/time policy, or modifying ACLs.
- Read `references/deployment-contract.md` before installing or changing the task.

## Workflow

1. **Audit the canonical path.** Require Task Scheduler action -> `venv\Scripts\python.exe scripts\launcher_supervisor.py run --restart` -> one owned `main.py` child. Reject direct `main.py`, nested batch restart loops, multiple supervisors, shell-embedded secrets, or an unanchored working directory.
2. **Verify fail-closed mode.** Confirm both the installer and launcher independently accept only PAPER/OBSERVATION, force or require the live latch off, check duplicate processes conservatively, and return a nonzero code when safety cannot be verified. Never weaken these checks to improve availability.
3. **Validate the task definition.** Require an AtStartup trigger, StartWhenAvailable, unlimited execution time, IgnoreNew multiple-instance policy, limited privilege, explicit repository working directory, and Task Scheduler restart of the supervisor. The task must run under a dedicated/known identity with `.env` read access but no interactive secret arguments.
4. **Validate external hang detection.** The launcher must use `Popen`, own the worker PID/handle, poll an atomic heartbeat, allow a bounded startup grace period, require multiple stale strikes, terminate only its owned child, escalate to kill after a grace timeout, and return a distinct nonzero hung-worker code for restart policy.
5. **Validate heartbeat and alerts.** Require versioned heartbeat state written atomically and write failures surfaced. Persist watchdog alerts to an atomic outbox before bounded delivery; retry failures and mark cooldown only after delivery acknowledgement. A failed alert must never masquerade as delivered.
6. **Harden the host.** Prevent sleep/hibernation on AC power, configure controlled update/reboot windows, keep Windows Time running and measure exchange-server drift, preserve at least 2 GB free disk, rotate logs, use a UPS for PC and network, and prefer wired networking. Clock drift or stale state pauses entries; uptime never outranks trading safety.
7. **Protect credentials.** Keep withdrawals disabled at exchanges, avoid command-line/environment dumps, restrict `.env` ACLs to the service identity, owner/administrators, and SYSTEM as appropriate, and verify access after any atomic rewrite. Never echo or copy secret contents into reports.
8. **Test failure modes offline.** Mock worker processes and clocks. Cover nonzero exit/backoff, stable-run backoff reset, stale/missing/corrupt heartbeat, startup grace, PID mismatch, duplicate launcher/worker, graceful terminate, forced kill, alert failure/retry, and `CONTROLLED_LIVE` refusal. Do not contact exchanges.
9. **Validate the deployment manifest.** Populate `references/deployment-contract.md`, then run:

```bash
python skills/windows-bot-deployment/scripts/validate_deployment_contract.py \
  --input reports/deployment/deployment-contract.json \
  --output-dir reports/deployment
powershell -NoProfile -File scripts/install_24x7_task.ps1 -WhatIf
```

10. **Inspect before mutation.** Review the `-WhatIf` action and current scheduled-task definition. With explicit approval, register using `scripts/install_24x7_task.ps1`; do not hand-create a divergent task.
11. **Prove recovery.** In PAPER/OBSERVATION, restart the worker, simulate a stale heartbeat, and reboot during a planned window. Verify one supervisor, one worker, fresh heartbeat progress, atomic alert record/delivery, and no live writes.
12. **Operate with evidence.** Review daily restart counts, heartbeat gaps, clock drift, disk, alert backlog, and strategy shadow maturity. Repeated restarts are incidents to diagnose, not success. Keep scheduled `CONTROLLED_LIVE` blocked until an authorized implementation explicitly replaces this contract.

## Output

Produce:

- `reports/deployment/deployment-contract-report.json` with deterministic pass/fail checks.
- The inspected Task Scheduler action, trigger, settings, principal, working directory, and current safe mode, with secrets redacted.
- Test results and a reboot/hang-recovery drill record.
- A status of `PASS_FOR_PAPER_24X7_REVIEW` or `BLOCKED`; never claim live approval.

If task registration or host policy was changed with approval, state exactly what changed, how to disable it safely, and whether it is recoverable.

## Resources

- `references/deployment-contract.md` - canonical scheduler/supervisor manifest and invariants.
- `scripts/validate_deployment_contract.py` - offline manifest validator.
- `scripts/tests/test_validate_deployment_contract.py` - validator regression tests.
- Repository deployment: `scripts/install_24x7_task.ps1`, `scripts/launcher_supervisor.py`, `TradingBot.bat`, `scripts/start_all.ps1`.
- Repository tests: `tests/test_launcher_safety.py`, `tests/test_heartbeat_24x7.py`, `tests/test_watchdog_crash_recovery.py`, and `tests/test_entrypoint_paths_anchored.py`.
