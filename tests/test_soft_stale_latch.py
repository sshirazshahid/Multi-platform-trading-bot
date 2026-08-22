"""Tests for soft-stale entry latch (blueprint Phase 1)."""
from __future__ import annotations

from pathlib import Path

from core.soft_stale_latch import (
    clear_soft_stale_latch,
    set_soft_stale_latch,
    soft_stale_entries_blocked,
)


def test_soft_stale_round_trip(tmp_path: Path) -> None:
    latch = tmp_path / "soft_stale_entry_latch.json"
    assert soft_stale_entries_blocked(latch) is False
    set_soft_stale_latch(reason="forward_feeds_stale", path=latch, detail={"feeds": ["l2"]})
    assert soft_stale_entries_blocked(latch) is True
    assert clear_soft_stale_latch(latch) is True
    assert soft_stale_entries_blocked(latch) is False


def test_corrupt_latch_fails_closed(tmp_path: Path) -> None:
    latch = tmp_path / "soft_stale_entry_latch.json"
    latch.write_text("{not-json", encoding="utf-8")
    assert soft_stale_entries_blocked(latch) is True


def test_execute_open_soft_stale_exception_fails_closed() -> None:
    """Import/runtime failure of the latch check must refuse the OPEN.

    2026-08-18 hunter HIGH: ``except Exception`` used to log
    ``entry check skipped`` and fall through — a broken latch module would
    admit new exposure. Kill-switch posture is fail-closed.
    """
    from tests.bot_engine_source import bot_engine_implementation_source

    src = bot_engine_implementation_source(method="_execute_open")
    idx = src.index("soft_stale_entries_blocked")
    # Window widened 700→1600 (2026-08-22): the branch now carries the incident
    # comment explaining WHY it must log (a scored ETH proposal vanished between
    # the OPEN line and "Cycle complete: 0/1 actions executed", with nothing in
    # between), pushing the asserted tokens to +1213/+1253/+1353. The assertions
    # and the check order they pin are unchanged.
    block = src[idx : idx + 1600]
    assert "entry check skipped" not in block
    assert "soft_stale_entry_block" in block
    assert "return False" in block
    assert "failed closed" in block
