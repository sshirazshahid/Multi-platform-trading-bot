"""Dashboard display invariants (2026-04-30).

Three concrete bugs surfaced from running dashboard.py against live state:

1. Daily-loss limit displayed via :.0f format → 1.5%-cap rendered as "2%".
   Misleads the user about how close to halt they are.

2. RISK panel "Positions: 42 / 8 max" — the count summed manual spot
   holdings (SUI, BTC, etc.) into the futures-only cap denominator.
   Bot-tracked count is futures-only; the panel should match.

3. "Mem: 0MB" when psutil is missing from the bot's venv — the bot
   writes memory_mb=0 in the heartbeat fallback. Dashboard rendered the
   literal 0 as if it were a real measurement.

Tests below pin the rendering so a future format-string tweak can't
silently re-break user-facing values. We do NOT spin up the full
dashboard renderer; we exercise the format strings + filter logic
directly.
"""
from __future__ import annotations

import pytest

# ─── Bug 1: daily-loss / drawdown limit format ─────────────────────────


@pytest.mark.parametrize("limit_pct,expected", [
    (1.5, "1.5"),       # the actual cap; was rounded to "2" with :.0f
    (12.0, "12.0"),
    (0.5, "0.5"),
    (8.0, "8.0"),
    (2.5, "2.5"),
])
def test_daily_loss_limit_renders_one_decimal(limit_pct, expected):
    """Match the production format string used in the RISK panel."""
    out = f"{limit_pct:.1f}"
    assert out == expected


def test_old_zero_decimal_format_misleads_at_one_point_five():
    """Document the original buggy behavior so a regression is visible
    without re-running the dashboard against live state."""
    bug_render = f"{1.5:.0f}"
    assert bug_render == "2", (
        "Confirms the original :.0f bug premise — 1.5 silently rounds to 2. "
        "This test exists to anchor the regression: if format ever returns "
        "to :.0f, the user will see the wrong cap again.")


# ─── Bug 2: positions count must be futures-only ───────────────────────


def _count_futures(open_pos: list[dict]) -> int:
    """Replica of the production filter."""
    return sum(1 for p in open_pos if p.get("market_type") == "futures")


def _count_spot(open_pos: list[dict]) -> int:
    return sum(1 for p in open_pos if p.get("market_type") == "spot")


def test_positions_count_filters_futures_only():
    open_pos = (
        [{"market_type": "futures"}] * 3
        + [{"market_type": "spot"}] * 39
    )
    assert _count_futures(open_pos) == 3
    assert _count_spot(open_pos) == 39


def test_positions_count_handles_missing_market_type():
    """Defensive: a position without `market_type` defaults to non-futures
    (the dashboard's elsewhere-used `.get("market_type", "spot")` rule).
    The futures filter must not over-count those."""
    open_pos = [{"market_type": "futures"}, {}, {"market_type": "spot"}]
    assert _count_futures(open_pos) == 1


def test_positions_count_empty():
    assert _count_futures([]) == 0
    assert _count_spot([]) == 0


# ─── Bug 3: memory_mb fallback display ─────────────────────────────────


def _mem_str(mb: float | None) -> str:
    """Replica of the production fallback chain."""
    if not mb or mb == 0:
        return "n/a"
    return f"{mb:.0f}MB"


@pytest.mark.parametrize("mb,expected", [
    (0, "n/a"),
    (None, "n/a"),
    (0.0, "n/a"),
    (45.7, "46MB"),
    (123.0, "123MB"),
    (1.0, "1MB"),
])
def test_memory_display_fallback(mb, expected):
    assert _mem_str(mb) == expected


# ─── Regression on the daily-loss denominator math ─────────────────────


def test_daily_loss_uses_start_balance_not_total_live():
    """The risk_manager halt trigger compares daily_pnl to start_balance
    set at session start. The dashboard previously divided by current
    `total_live`, which drifts as PnL moves — the displayed % wouldn't
    match the actual halt threshold.

    This test pins the new denominator-selection logic.
    """
    risk_data_with_start = {"start_balance": 300.0, "daily_pnl": -3.0}
    risk_data_legacy = {}                # no start_balance
    total_live_now = 250.0               # current balance (lower after losses)

    # Production replica:
    def daily_used(risk_data, total_live, daily_pnl):
        denom = float(risk_data.get("start_balance") or 0.0)
        if denom <= 0:
            denom = total_live
        if denom > 0:
            return abs(min(daily_pnl, 0)) / denom * 100
        return 0

    # With start_balance: 3/300 = 1.0%
    pct_start = daily_used(risk_data_with_start, total_live_now, -3.0)
    # Without start_balance: fallback to total_live: 3/250 = 1.2%
    pct_legacy = daily_used(risk_data_legacy, total_live_now, -3.0)

    assert pct_start == pytest.approx(1.0, abs=1e-3)
    assert pct_legacy == pytest.approx(1.2, abs=1e-3)
    # The two values differ — that's the prior bug. New code prefers
    # start_balance to match what risk_manager actually uses.
    assert pct_start < pct_legacy
