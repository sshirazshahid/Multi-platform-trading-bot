"""Item 4 (data-integrity backlog 2026-07-07): cwd-relative standalone paths.

promotion_loop.ACTIVE_STRATEGIES_PATH and health_watchdog.WAREHOUSE_PATH were
cwd-relative module constants. A standalone entrypoint launched from another cwd
(e.g. System32 via Task Scheduler — the class that broke ShadowResolver on
2026-07-05) then read/wrote the wrong path. Both are now anchored to the repo
root, so the resolved path is correct regardless of the launch directory.
"""
from __future__ import annotations

from pathlib import Path


def test_active_strategies_path_anchored_to_root():
    from core.decision import promotion_loop as pl

    root = Path(pl.__file__).resolve().parents[2]  # core/decision/ -> repo root
    assert pl.ACTIVE_STRATEGIES_PATH.is_absolute()
    assert pl.ACTIVE_STRATEGIES_PATH == root / "data" / "active_strategies.json"


def test_watchdog_warehouse_path_anchored_to_root():
    import core.health_watchdog as hw

    root = Path(hw.__file__).resolve().parents[1]  # core/ -> repo root
    assert hw.WAREHOUSE_PATH.is_absolute()
    assert hw.WAREHOUSE_PATH == root / "data" / "warehouse.sqlite"
