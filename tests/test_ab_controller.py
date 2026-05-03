"""Task C.1 — A/B traffic-split controller."""
from __future__ import annotations

import json
import random


def test_route_returns_live_when_no_split_file(tmp_path):
    from core.agents.ab_controller import route
    p = tmp_path / "ab.json"
    assert route(123, path=p) == "live"


def test_route_returns_live_when_pct_zero(tmp_path):
    from core.agents.ab_controller import route, save_split, SplitState
    p = tmp_path / "ab.json"
    save_split(SplitState(pct_to_shadow=0.0, set_at=0), path=p)
    for cid in range(50):
        assert route(cid, path=p) == "live"


def test_route_distributes_by_pct(tmp_path):
    from core.agents.ab_controller import route, save_split, SplitState
    p = tmp_path / "ab.json"
    save_split(SplitState(pct_to_shadow=0.50, set_at=0), path=p)
    n_shadow = sum(1 for cid in range(1000) if route(cid, path=p) == "shadow")
    # Tolerance ±10% on 1000 samples at 50% mean
    assert 400 <= n_shadow <= 600


def test_route_deterministic_per_candidate(tmp_path):
    from core.agents.ab_controller import route, save_split, SplitState
    p = tmp_path / "ab.json"
    save_split(SplitState(pct_to_shadow=0.30, set_at=0), path=p)
    for cid in [1, 7, 42, 100]:
        first = route(cid, path=p)
        for _ in range(5):
            assert route(cid, path=p) == first


def test_auto_ramp_advances_on_continuous_promote(tmp_path):
    from core.agents.ab_controller import (
        auto_ramp, save_split, SplitState, RAMP_STAGES,
    )
    p = tmp_path / "ab.json"
    save_split(SplitState(pct_to_shadow=0.0, set_at=0), path=p)
    state = auto_ramp(["PROMOTE"] * 7, path=p, now_ts=10**9)
    assert state.pct_to_shadow == RAMP_STAGES[1]


def test_auto_ramp_no_advance_on_mixed_verdict(tmp_path):
    from core.agents.ab_controller import auto_ramp, save_split, SplitState
    p = tmp_path / "ab.json"
    save_split(SplitState(pct_to_shadow=0.0, set_at=0), path=p)
    state = auto_ramp(["PROMOTE", "HOLD", "PROMOTE"], path=p, now_ts=10**9)
    assert state.pct_to_shadow == 0.0


def test_auto_ramp_respects_dwell(tmp_path):
    from core.agents.ab_controller import (
        auto_ramp, save_split, SplitState, RAMP_DWELL_DAYS,
    )
    p = tmp_path / "ab.json"
    now = 10**9
    save_split(SplitState(pct_to_shadow=0.10,
                          set_at=now, last_ramp_at=now), path=p)
    # Same day → no ramp
    state = auto_ramp(["PROMOTE"] * 5, path=p, now_ts=now + 60)
    assert state.pct_to_shadow == 0.10
    # 2 days later → ramp advances
    state = auto_ramp(["PROMOTE"] * 5, path=p,
                      now_ts=now + RAMP_DWELL_DAYS * 86400 + 60)
    assert state.pct_to_shadow == 0.25


def test_rollback_drops_to_zero_on_bad_wr(tmp_path):
    from core.agents.ab_controller import (
        rollback_if_unhealthy, save_split, SplitState,
    )
    p = tmp_path / "ab.json"
    save_split(SplitState(pct_to_shadow=0.50, set_at=0), path=p)
    state = rollback_if_unhealthy(rolling_wr=0.20, path=p)
    assert state.pct_to_shadow == 0.0
    assert state.rollback_active is True


def test_rollback_no_op_when_healthy(tmp_path):
    from core.agents.ab_controller import (
        rollback_if_unhealthy, save_split, SplitState,
    )
    p = tmp_path / "ab.json"
    save_split(SplitState(pct_to_shadow=0.50, set_at=0), path=p)
    state = rollback_if_unhealthy(rolling_wr=0.50, path=p)
    assert state.pct_to_shadow == 0.50
    assert state.rollback_active is False
