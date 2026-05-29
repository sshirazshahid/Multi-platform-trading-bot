"""RiskAgent — central veto over agent proposals.

Reads runtime risk state (halts, blacklist, allowed hours) and approves
or rejects each Proposal. Returns (approved: bool, veto_reason: str).
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .base_agent import Proposal


@dataclass
class VetoResult:
    approved: bool
    reason: str = ""

    def __bool__(self) -> bool:
        return self.approved


class RiskAgent:
    """Central veto. Same name pattern as agents but isn't BaseAgent
    (it doesn't propose — it reviews)."""

    def __init__(self, risk_state_path: Optional[Path] = None):
        self.name = "RiskAgent"
        self.risk_state_path = Path(risk_state_path) if risk_state_path \
            else Path("data/risk_state.json")

    def review(self, proposal: Proposal,
               *,
               blacklist: Optional[set] = None,
               allowed_hours: Optional[set] = None,
               now_ts: Optional[int] = None) -> VetoResult:
        # 1. Halt check (Spec §12 + drawdown)
        state = self._load_state()
        if state.get("is_halted"):
            return VetoResult(False, f"halt_active:{state.get('halt_reason', '?')}")

        # 2. Blacklist
        bl = blacklist if blacklist is not None else self._cfg_blacklist()
        if bl and proposal.symbol in bl:
            return VetoResult(False, f"blacklist:{proposal.symbol}")

        # 3. Allowed hours
        if allowed_hours is not None:
            ts = now_ts if now_ts is not None else proposal.ts
            hour = datetime.fromtimestamp(ts, tz=timezone.utc).hour
            if hour not in allowed_hours:
                return VetoResult(False, f"hour_blocked:{hour}")

        # 4. Sanity
        if proposal.side == "buy" and not (proposal.sl < proposal.entry < proposal.tp):
            return VetoResult(False, "invalid_long_levels")
        if proposal.side == "sell" and not (proposal.sl > proposal.entry > proposal.tp):
            return VetoResult(False, "invalid_short_levels")

        return VetoResult(True, "")

    def _load_state(self) -> dict:
        try:
            return json.loads(self.risk_state_path.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def _cfg_blacklist(self) -> set:
        try:
            from config import BLACKLIST_HARD
            return set(BLACKLIST_HARD)
        except Exception:
            return set()
