"""Regression: exchange-outage soft-stale latch must clear on venue recovery.

2026-08-12: monitors._check_exchange_health sets the soft-stale latch with
reason "exchange_outage:<venue>" at 5+ consecutive API fails, but NO code path
ever cleared that reason — health_watchdog only clears "forward_feeds*"
latches. A bitget outage latched 894 fails and then blocked every OPEN on
every venue (1,951/2,003 rejects in 48h), and because the latch is a file it
survived restarts too. The fix clears the latch in the healthy branch of
_check_exchange_health when the latch reason names the recovered venue.
"""
from __future__ import annotations

from pathlib import Path

from core.soft_stale_latch import (
    clear_exchange_outage_latch,
    set_soft_stale_latch,
    soft_stale_entries_blocked,
)


def test_exchange_outage_latch_clears_for_named_venue(tmp_path: Path) -> None:
    latch = tmp_path / "soft_stale_entry_latch.json"
    set_soft_stale_latch(
        reason="exchange_outage:bitget", path=latch,
        detail={"fails": 894, "exchange": "bitget"},
    )
    assert soft_stale_entries_blocked(latch) is True
    # Different venue recovering must NOT clear a bitget-outage latch.
    assert clear_exchange_outage_latch("binance", path=latch) is False
    assert soft_stale_entries_blocked(latch) is True
    # The named venue recovering clears it.
    assert clear_exchange_outage_latch("bitget", path=latch) is True
    assert soft_stale_entries_blocked(latch) is False


def test_exchange_outage_clear_leaves_other_reasons_alone(tmp_path: Path) -> None:
    latch = tmp_path / "soft_stale_entry_latch.json"
    set_soft_stale_latch(reason="forward_feeds_stale", path=latch, detail={})
    assert clear_exchange_outage_latch("bitget", path=latch) is False
    assert soft_stale_entries_blocked(latch) is True


def test_exchange_outage_clear_no_latch_is_noop(tmp_path: Path) -> None:
    latch = tmp_path / "soft_stale_entry_latch.json"
    assert clear_exchange_outage_latch("bitget", path=latch) is False


def test_exchange_outage_clear_unreadable_latch_stays(tmp_path: Path) -> None:
    """Fail closed: an unreadable latch is never deleted blind."""
    latch = tmp_path / "soft_stale_entry_latch.json"
    latch.write_text("{not-json", encoding="utf-8")
    assert clear_exchange_outage_latch("bitget", path=latch) is False
    assert soft_stale_entries_blocked(latch) is True


def test_health_check_recovery_branch_calls_clear() -> None:
    """The engine's healthy branch must route through the venue-scoped clear."""
    import inspect

    from core.engine import monitors

    src = inspect.getsource(monitors._MonitorsMixin._check_exchange_health)
    assert "clear_exchange_outage_latch" in src
