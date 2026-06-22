"""Immutable safety-kernel manifest (audit 2026-06-22, red-team verdict).

Defines the files + runtime-state control files that an autonomous self-modifier
(and any code it authors) must NEVER edit or write. core/selfmod/kernel_guard.py
enforces this fail-closed. Editing THIS manifest or the guard is itself a kernel
violation (core/selfmod/ is self-protected below).

Granularity note: core/order_manager.py is FILE-level protected for Phase 0 — it
mixes the fail-closed SL kernel with exit logic, so conservative-first denies the
whole file (a self-modifier improves other files; order_manager edits stay human).
A later refinement may narrow to AST-symbol protection.
"""
from __future__ import annotations

# Files the self-modifier may NEVER edit. Repo-relative POSIX paths; a trailing
# "/" denotes a directory subtree (every file under it is protected).
PROTECTED_PATHS: frozenset = frozenset({
    "config.py",                          # OPERATING_MODE/DRY_RUN derivation, leverage caps, breakers, latch
    "main.py",                            # crash-restart supervisor / _classify_exit
    "core/live_gate.py",                  # CONTROLLED_LIVE double-latch
    "core/risk_manager.py",               # SL / leverage / daily-loss enforcement
    "core/order_manager.py",              # fail-closed SL, reconcilers, _finalize_close
    "core/promotion_gate.py",             # the honest gate + its floors (immutable)
    "core/btc_vol_pause.py",              # BTC-vol circuit breaker on new entries
    "core/selfmod/",                      # the guard + this manifest protect themselves
    "docs/CONTROLLED_LIVE_CHECKLIST.md",  # SUP must never self-sign the live checklist
})

# Secret / latch files matched by PREFIX (the SUP must never write any .env*).
PROTECTED_PREFIXES: tuple = (".env",)

# Runtime control files the LIVE engine READS as inputs — writing them changes
# behavior with ZERO code diff (the data-channel bypass the red-team flagged:
# ab_controller.route()/auto_ramp can escalate shadow routing to 100% off
# data/ab_split.json). The SUP must be barred from WRITING any of these.
RUNTIME_STATE_DENY: frozenset = frozenset({
    "data/ab_split.json",                 # ab_controller route + auto_ramp -> 100%
    "data/self_heal_state.json",          # Phase-1 healer circuit-breaker budget
    "data/selfmod_state.json",            # SUP breaker / change-count state
    "data/selfmod_disabled",              # SUP self-disable latch
    "data/selfmod_kill",                  # SUP kill-switch
    "data/risk_state.json",               # daily-loss / drawdown rail state
    "data/review_required.json",          # loss-streak review latch
    "data/auto_mutations.json",           # leverage-cap mutation state
})
