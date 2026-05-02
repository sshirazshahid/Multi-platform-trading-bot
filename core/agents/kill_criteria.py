"""KillCriteriaMonitor — auto-disable shadow on bad stats.

Triggers (any → disable shadow + notify):
  - shadow fee burn > 2x live (over min_decisions window)
  - shadow WR < 30% (over min_decisions window)
  - shadow drawdown halts > 3 in 7 days
  - shadow agent crashes (handled by thread watchdog)
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable, Optional


@dataclass
class KillVerdict:
    triggered: bool
    reason: str = ""

    def __bool__(self) -> bool:
        return self.triggered


@dataclass
class KillThresholds:
    fee_burn_x: float = 2.0
    wr_floor: float = 0.30
    min_decisions: int = 100
    max_halts_7d: int = 3


class KillCriteriaMonitor:

    def __init__(
        self,
        warehouse=None,
        thresholds: Optional[KillThresholds] = None,
        state_path: Optional[Path] = None,
    ):
        self.name = "KillCriteriaMonitor"
        self._wh = warehouse
        self._th = thresholds or KillThresholds()
        self._state_path = Path(state_path) if state_path \
            else Path("data/shadow_kill_state.json")

    def evaluate(
        self,
        shadow_pnls: Iterable[float],
        live_fee_burn_per_trade: float,
        shadow_fee_burn_per_trade: float,
        halts_in_7d: int,
    ) -> KillVerdict:
        pnls = list(shadow_pnls)
        n = len(pnls)
        if n < self._th.min_decisions:
            return KillVerdict(False, f"insufficient_decisions:{n}")

        wins = sum(1 for x in pnls if x > 0)
        wr = wins / n if n else 0.0

        if wr < self._th.wr_floor:
            return KillVerdict(True, f"wr_below_floor:{wr:.2f}<{self._th.wr_floor}")

        if live_fee_burn_per_trade > 0 and \
                shadow_fee_burn_per_trade > self._th.fee_burn_x * live_fee_burn_per_trade:
            return KillVerdict(True,
                f"fee_burn_high:{shadow_fee_burn_per_trade:.4f}>{self._th.fee_burn_x}x{live_fee_burn_per_trade:.4f}")

        if halts_in_7d > self._th.max_halts_7d:
            return KillVerdict(True, f"halts_7d:{halts_in_7d}>{self._th.max_halts_7d}")

        return KillVerdict(False, "ok")

    def disable_shadow_runtime(self, reason: str) -> None:
        """Persist kill-state so the runtime flag picks it up next cycle."""
        self._state_path.parent.mkdir(parents=True, exist_ok=True)
        self._state_path.write_text(json.dumps({
            "killed": True,
            "reason": reason,
            "thresholds": asdict(self._th),
        }, indent=2), encoding="utf-8")
