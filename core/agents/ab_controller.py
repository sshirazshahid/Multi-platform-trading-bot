"""A/B traffic-split controller for Phase C live cutover.

Reads `data/ab_split.json` (written by promotion_gate.write_split_flag).
Routes candidates between live and shadow based on a percentage.

NB: Phase C is GATED. This module ships the infrastructure but does NOT
self-activate. Activation requires:
  1. multi_agent_promotion_gate.evaluate_multi_agent_shadow() == PROMOTE
  2. User explicit advance (operator step — see docs/CUTOVER.md)
  3. write_split_flag(0.10) called manually
Without all three, route() returns "live" 100% of the time.

Auto-ramp & auto-rollback are implemented but stay dormant until step 3.
"""
from __future__ import annotations

import json
import random
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


SPLIT_PATH = Path("data/ab_split.json")
RAMP_STAGES = [0.0, 0.10, 0.25, 0.50, 1.00]
RAMP_DWELL_DAYS = 1  # one ramp step per 24h of continuous PROMOTE


@dataclass
class SplitState:
    pct_to_shadow: float
    set_at: int
    last_ramp_at: int = 0
    rollback_active: bool = False


def load_split(path: Path = SPLIT_PATH) -> SplitState:
    if not path.exists():
        return SplitState(pct_to_shadow=0.0, set_at=0)
    try:
        j = json.loads(path.read_text())
        return SplitState(
            pct_to_shadow=float(j.get("pct_to_shadow", 0.0)),
            set_at=int(j.get("set_at", 0)),
            last_ramp_at=int(j.get("last_ramp_at", 0)),
            rollback_active=bool(j.get("rollback_active", False)),
        )
    except Exception:
        return SplitState(pct_to_shadow=0.0, set_at=0)


def save_split(state: SplitState, path: Path = SPLIT_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "pct_to_shadow": max(0.0, min(1.0, state.pct_to_shadow)),
        "set_at": state.set_at,
        "last_ramp_at": state.last_ramp_at,
        "rollback_active": state.rollback_active,
    }, indent=2), encoding="utf-8")


def route(candidate_id: int, *, path: Path = SPLIT_PATH,
          rng: Optional[random.Random] = None) -> str:
    """Returns "shadow" or "live" for the given candidate.

    Deterministic per candidate_id (so the same candidate always routes
    the same way during a tick) — uses candidate_id as RNG seed.
    """
    state = load_split(path)
    if state.pct_to_shadow <= 0.0:
        return "live"
    r = rng if rng is not None else random.Random(candidate_id)
    return "shadow" if r.random() < state.pct_to_shadow else "live"


def auto_ramp(promotion_evaluations_last_n: list[str],
              path: Path = SPLIT_PATH,
              now_ts: Optional[int] = None) -> SplitState:
    """If the last N daily evaluations are all PROMOTE and dwell time has
    elapsed, advance to the next ramp stage. Otherwise no-op.

    promotion_evaluations_last_n: list of MAVerdict.value strings
        ordered newest-last. Caller decides N (e.g., last 7 days).
    """
    state = load_split(path)
    now = now_ts if now_ts is not None else int(time.time())
    if not promotion_evaluations_last_n or \
            not all(v == "PROMOTE" for v in promotion_evaluations_last_n):
        return state
    if state.rollback_active:
        return state
    if now - state.last_ramp_at < RAMP_DWELL_DAYS * 86400:
        return state
    cur_idx = next((i for i, p in enumerate(RAMP_STAGES)
                    if abs(p - state.pct_to_shadow) < 1e-6), 0)
    if cur_idx + 1 >= len(RAMP_STAGES):
        return state
    state.pct_to_shadow = RAMP_STAGES[cur_idx + 1]
    state.last_ramp_at = now
    state.set_at = now
    save_split(state, path)
    return state


def rollback_if_unhealthy(
    rolling_wr: float,
    path: Path = SPLIT_PATH,
    now_ts: Optional[int] = None,
    wr_floor: float = 0.35,
) -> SplitState:
    """Drop split to 0% if rolling WR breaches the floor."""
    state = load_split(path)
    if rolling_wr < wr_floor and state.pct_to_shadow > 0:
        state.pct_to_shadow = 0.0
        state.rollback_active = True
        state.set_at = now_ts if now_ts is not None else int(time.time())
        save_split(state, path)
    return state
