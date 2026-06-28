"""PAPER-only self-improvement latch — the single chokepoint for autonomous writes.

Every autonomous mutation / promotion WRITE must pass through
``assert_paper_self_improve`` before touching disk. It enforces BOTH:

  1. The immutable safety kernel (``core.selfmod.kernel_guard.assert_kernel_safe``):
     the loop may never write ``config.py``, ``live_gate.py``, ``promotion_gate.py``,
     ``data/ab_split.json``, ``.env*``, or any other protected / runtime-control path.
  2. A hard PAPER posture: ``OPERATING_MODE == "PAPER"``. A mode flip silently disarms
     the loop — there is no code path here that can act on a live account.

This is the literal implementation of "the autonomy is bounded by the paper/live
latch": real money stays behind ``live_gate.py`` + the kernel, untouched by this module.
"""
from __future__ import annotations

import os

from core.selfmod.kernel_guard import assert_kernel_safe


class SelfImproveLatchError(RuntimeError):
    """Raised when an autonomous write is attempted outside the PAPER + kernel-safe envelope."""


def _operating_mode() -> str:
    """Read OPERATING_MODE at call time (mirrors the test pattern that patches
    ``sys.modules['config']``). Fail-closed to a non-PAPER sentinel if unavailable."""
    try:
        import config

        return str(getattr(config, "OPERATING_MODE", "")).upper()
    except Exception:
        return "UNKNOWN"


def self_improve_enabled() -> bool:
    """True only when the loop is enabled AND we are in PAPER.

    Default-on in PAPER (spec WS4 default) via ``SELF_IMPROVE_ENABLED`` (env, not a
    config-file edit — config.py is kernel-immutable), but hard-gated to PAPER so a
    mode flip disarms it without any further wiring."""
    flag = os.getenv("SELF_IMPROVE_ENABLED", "true").strip().lower() in ("1", "true", "yes", "on")
    return flag and _operating_mode() == "PAPER"


def assert_paper_self_improve(write_paths=None) -> None:
    """Gate an autonomous write. Raise ``SelfImproveLatchError`` unless ALL hold:

      1. ``OPERATING_MODE == "PAPER"`` — never act on a live account.
      2. ``assert_kernel_safe(write_paths)`` is ok — never write an immutable / denied path.

    ``write_paths``: repo-relative paths the caller is about to write. Pass an empty
    list (or omit) to assert only the PAPER posture (e.g. a read-only evaluation step)."""
    mode = _operating_mode()
    if mode != "PAPER":
        raise SelfImproveLatchError(
            f"self-improve refused: OPERATING_MODE={mode!r} (must be PAPER). The "
            "autonomous loop is paper-latched and may never act on a live account."
        )
    paths = [p for p in (write_paths or []) if str(p).strip()]
    if paths:
        ok, violations = assert_kernel_safe(paths)
        if not ok:
            raise SelfImproveLatchError(
                "self-improve refused: write touches the IMMUTABLE SAFETY KERNEL / "
                f"runtime-control state: {violations}"
            )
