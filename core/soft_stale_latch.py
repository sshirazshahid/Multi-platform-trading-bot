"""Soft-stale entry latch — block NEW entries, never flatten opens.

Blueprint Phase 1: feed/API soft staleness must refuse OPENs and leave
existing positions alone. Distinct from data/KILL_SWITCH (manual) and from
ENTRY_STALENESS_EXIT (thesis-flip flatten).
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Optional

from loguru import logger

SOFT_STALE_LATCH_PATH = Path("data/soft_stale_entry_latch.json")


def soft_stale_entries_blocked(path: Optional[Path] = None) -> bool:
    """True while the soft-stale latch file exists and active=true."""
    p = path if path is not None else SOFT_STALE_LATCH_PATH
    try:
        if not p.exists():
            return False
        doc = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        # Fail closed on unreadable latch (same posture as kill switch FS errors).
        return True
    if not isinstance(doc, dict):
        return True
    return bool(doc.get("active", True))


def set_soft_stale_latch(
    *,
    reason: str,
    path: Optional[Path] = None,
    detail: Optional[dict[str, Any]] = None,
) -> None:
    p = path if path is not None else SOFT_STALE_LATCH_PATH
    payload = {
        "active": True,
        "reason": str(reason),
        "ts_unix": time.time(),
        "ts_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "detail": detail or {},
        "honesty": (
            "Blocks NEW entries only. Does not flatten open positions. "
            "Cleared when soft-stale conditions resolve."
        ),
    }
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    tmp.replace(p)
    logger.warning(f"[SoftStale] entries blocked — {reason}")


def clear_exchange_outage_latch(
    ex_name: str, path: Optional[Path] = None
) -> bool:
    """Clear the latch iff its reason is exchange_outage:<ex_name>.

    2026-08-12 fix: the exchange-outage latch had a setter (engine monitors,
    5+ API fails) but NO clearer — health_watchdog only clears forward_feeds*
    reasons — so one venue outage (bitget, 894 fails) blocked every OPEN on
    every venue indefinitely and survived restarts. Called from the healthy
    branch of _check_exchange_health. Reason-scoped: never touches latches set
    for other reasons or other venues; unreadable latches stay (fail closed).
    Returns True iff the latch file was removed.
    """
    p = path if path is not None else SOFT_STALE_LATCH_PATH
    try:
        if not p.exists():
            return False
        doc = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return False  # unreadable → leave it latched (kill-switch posture)
    if not isinstance(doc, dict):
        return False
    if str(doc.get("reason") or "") != f"exchange_outage:{ex_name}":
        return False
    return clear_soft_stale_latch(p)


def clear_soft_stale_latch(path: Optional[Path] = None) -> bool:
    """Remove latch if present. Returns True if a file was removed."""
    p = path if path is not None else SOFT_STALE_LATCH_PATH
    try:
        if not p.exists():
            return False
        p.unlink()
        logger.info("[SoftStale] entries resumed — latch cleared")
        return True
    except OSError as exc:
        logger.error(f"[SoftStale] clear failed: {exc}")
        return False
