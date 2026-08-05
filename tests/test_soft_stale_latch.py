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
