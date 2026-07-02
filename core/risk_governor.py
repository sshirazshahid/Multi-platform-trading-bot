"""core/risk_governor.py — Phase C: formalized risk gate over risk_manager.

``core/risk_manager.py`` already owns dynamic SL/TP, correlation sizing and the
soft daily-loss breaker. This thin governor adds the two GENUINE gaps as pure,
unit-testable functions and a small facade:

  1. ``model_freshness_halt`` — HARD halt when the active model pointer is stale /
     invalid, reusing ``promotion_gate.validate_model_pointer`` (never trade on a
     model whose promotion authorization has expired).
  2. ``per_venue_exposure_breached`` — cap gross notional PER VENUE (venue outage
     / margin-call blast radius), reusing ``risk_manager.exposure_breached`` on the
     venue slice.

No live calls, no order path. Fail-CLOSED on ambiguous inputs (mirrors the A2
audit convention already in ``exposure_breached``).
"""

from __future__ import annotations

from typing import Any

from core.promotion_gate import MAX_MODEL_AGE_DAYS, validate_model_pointer
from core.risk_manager import exposure_breached


def model_freshness_halt(
    pointer: dict | None,
    *,
    market_type: str,
    max_age_days: int = MAX_MODEL_AGE_DAYS,
) -> dict[str, Any]:
    """HARD-halt decision for a model pointer.

    Returns ``{halt, reason, diag}``. ``halt=True`` (fail-closed) when the pointer
    is missing/invalid/stale per ``validate_model_pointer``.
    """
    if not isinstance(pointer, dict):
        return {"halt": True, "reason": "missing model pointer", "diag": {}}
    ok, reason, diag = validate_model_pointer(
        pointer, market_type=market_type, max_age_days=max_age_days
    )
    return {"halt": (not ok), "reason": ("OK" if ok else reason), "diag": diag}


def per_venue_exposure_breached(
    open_positions,
    venue: str,
    new_notional: float,
    equity: float,
    max_pct: float,
) -> bool:
    """Would adding ``new_notional`` on ``venue`` breach that venue's gross cap?

    Only positions whose ``venue``/``exchange`` matches count toward the slice, so
    the cap is per-venue. Fail-closed on bad equity (delegated to
    ``exposure_breached``).
    """
    v = str(venue).lower()
    slice_ = [
        p
        for p in (open_positions or [])
        if str(getattr(p, "venue", "") or getattr(p, "exchange", "")).lower() == v
    ]
    return exposure_breached(slice_, new_notional, equity, max_pct)


class RiskGovernor:
    """Facade bundling the freshness halt + per-venue cap for the entry gate."""

    def __init__(
        self,
        *,
        max_model_age_days: int = MAX_MODEL_AGE_DAYS,
        per_venue_max_pct: float | None = None,
    ):
        self.max_model_age_days = int(max_model_age_days)
        self.per_venue_max_pct = per_venue_max_pct

    def check_model(self, pointer: dict | None, *, market_type: str) -> dict[str, Any]:
        return model_freshness_halt(
            pointer, market_type=market_type, max_age_days=self.max_model_age_days
        )

    def check_venue_exposure(
        self, open_positions, venue: str, new_notional: float, equity: float
    ) -> bool:
        if self.per_venue_max_pct is None or self.per_venue_max_pct <= 0:
            return False
        return per_venue_exposure_breached(
            open_positions, venue, new_notional, equity, self.per_venue_max_pct
        )
