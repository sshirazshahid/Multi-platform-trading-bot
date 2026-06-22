"""Self-modification safety layer (audit 2026-06-22).

Phase 0 of the autonomous self-healing/self-upgrading build: the IMMUTABLE
SAFETY KERNEL guard. No autonomous actor lives here yet — this package only
DEFINES (kernel_manifest) and ENFORCES (kernel_guard, fail-closed) what an
eventual self-modifier (and any code it authors) may never edit or write.

Phased plan (red-team verdict 2026-06-22):
  Phase 0  this package — kernel manifest + guard + tests (NO actor).
  Phase 1  core/self_healer.py — detect + bounded auto-recovery (default-OFF, PAPER).
  Phase 2  scripts/run_self_modify_loop.py — PROPOSE-ONLY (opens PRs; human merges).
  Phase 3  auto-apply — explicitly armed only, never auto-armed; deferred/optional
           on a money bot per the red-team. main->CONTROLLED_LIVE stays human-only.
"""
