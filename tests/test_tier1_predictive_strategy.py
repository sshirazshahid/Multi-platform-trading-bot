"""Tier 1 predictive-strategy invariants (2026-05-01).

Two independent components, both grounded in the diminishing-returns
analysis of the futures stack:

  1.1 Entry-Staleness Exit (core/mcp_brain.py::is_entry_invalidated +
      core/order_manager.py monitor loop) — re-checks 4h EMA20/50
      hypothesis on every monitor cycle. Closes when EMAs flip against
      the position with margin.

  1.2 Expectancy Filter (core/warehouse.py::recent_expectancy +
      bot_engine._execute_open) — per-symbol abstain rule. If recent
      realised mean PnL < min_expected_dollar, skip even if
      cell-filter allowed. Self-correcting layer.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from core.warehouse import Warehouse

# ─── Fixture: isolated warehouse per test ────────────────────────────


@pytest.fixture
def wh(tmp_path: Path) -> Warehouse:
    """Per-test Warehouse with fresh thread-local connection."""
    if hasattr(Warehouse._tls, "conn") and Warehouse._tls.conn is not None:
        try:
            Warehouse._tls.conn.close()
        except Exception:
            pass
        Warehouse._tls.conn = None
    return Warehouse(tmp_path / "wh.sqlite")


def _close_trade(wh: Warehouse, *, symbol: str, side: str = "buy",
                 ts_entry: float, realized_pnl: float,
                 exchange: str = "binance") -> None:
    """Insert a CLOSED trade row directly (test helper)."""
    tid = wh.record_trade_open(
        candidate_id=None, ts_entry=ts_entry,
        exchange=exchange, symbol=symbol, market_type="futures",
        side=side, strategy_family="claude_portfolio",
        entry_px=1.0, size=1.0, leverage=2,
    )
    wh.record_trade_close(
        trade_id=tid, ts_exit=ts_entry + 1800,
        exit_px=1.0, realized_pnl=realized_pnl,
        exit_reason="test",
    )


# ─── Component 1.2: Warehouse.recent_expectancy ──────────────────────


def test_recent_expectancy_returns_none_below_min_n(wh: Warehouse):
    """With < min_n trades, return None — caller falls through (don't
    block on insufficient evidence)."""
    import time
    for i, pnl in enumerate([1.0, 2.0]):  # only 2 trades
        _close_trade(wh, symbol="ATOM/USDT:USDT",
                     ts_entry=time.time() - i * 86400, realized_pnl=pnl)
    assert wh.recent_expectancy("ATOM/USDT:USDT", min_n=5) is None


def test_recent_expectancy_computes_mean_correctly(wh: Warehouse):
    import time
    pnls = [1.0, -0.5, 0.8, -0.2, 0.4]  # mean = 0.30, 60% WR
    for i, p in enumerate(pnls):
        _close_trade(wh, symbol="ATOM/USDT:USDT",
                     ts_entry=time.time() - i * 3600, realized_pnl=p)
    out = wh.recent_expectancy("ATOM/USDT:USDT", min_n=5)
    assert out is not None
    assert out["n"] == 5
    assert out["mean"] == pytest.approx(0.30)
    assert out["win_rate"] == pytest.approx(0.6)


def test_recent_expectancy_lookback_excludes_old_rows(wh: Warehouse):
    """Trades older than `days` window are excluded."""
    import time
    now = time.time()
    # Recent: 5 winners
    for i in range(5):
        _close_trade(wh, symbol="ATOM/USDT:USDT",
                     ts_entry=now - i * 3600, realized_pnl=1.0)
    # Old: 3 losers, > 30 days ago
    for i in range(3):
        _close_trade(wh, symbol="ATOM/USDT:USDT",
                     ts_entry=now - 60 * 86400 - i * 3600, realized_pnl=-2.0)
    out = wh.recent_expectancy("ATOM/USDT:USDT", days=30, min_n=5)
    assert out is not None
    assert out["n"] == 5  # only the recent rows
    assert out["mean"] == pytest.approx(1.0)


def test_recent_expectancy_side_filter(wh: Warehouse):
    """When side is specified, only matching trades count."""
    import time
    now = time.time()
    for i in range(5):
        _close_trade(wh, symbol="ATOM/USDT:USDT", side="buy",
                     ts_entry=now - i * 3600, realized_pnl=0.5)
    for i in range(5):
        _close_trade(wh, symbol="ATOM/USDT:USDT", side="sell",
                     ts_entry=now - i * 3600, realized_pnl=-0.5)
    longs = wh.recent_expectancy("ATOM/USDT:USDT", side="buy", min_n=3)
    shorts = wh.recent_expectancy("ATOM/USDT:USDT", side="sell", min_n=3)
    both = wh.recent_expectancy("ATOM/USDT:USDT", min_n=3)
    assert longs["mean"] == pytest.approx(0.5)
    assert shorts["mean"] == pytest.approx(-0.5)
    assert both["mean"] == pytest.approx(0.0)


def test_recent_expectancy_other_symbols_excluded(wh: Warehouse):
    """A query for ATOM must not return ARB rows."""
    import time
    now = time.time()
    for i in range(5):
        _close_trade(wh, symbol="ATOM/USDT:USDT",
                     ts_entry=now - i * 3600, realized_pnl=1.0)
    for i in range(5):
        _close_trade(wh, symbol="ARB/USDT:USDT",
                     ts_entry=now - i * 3600, realized_pnl=-1.0)
    out = wh.recent_expectancy("ATOM/USDT:USDT", min_n=3)
    assert out["mean"] == pytest.approx(1.0)
    assert out["n"] == 5


def test_recent_expectancy_unknown_symbol_returns_none(wh: Warehouse):
    """A symbol with zero closed trades returns None, not crash."""
    out = wh.recent_expectancy("NEWCOIN/USDT:USDT", min_n=5)
    assert out is None


# ─── Component 1.2: filter logic ─────────────────────────────────────


def _expectancy_filter_decision(exp_dict: dict | None, floor: float) -> tuple[bool, str]:
    """Replica of bot_engine._execute_open expectancy block. Returns
    (allow: bool, reason: str)."""
    if exp_dict is None:
        return True, "insufficient_evidence"
    if exp_dict["mean"] < floor:
        return False, f"mean_below_floor({exp_dict['mean']:+.3f}<{floor:.2f})"
    return True, f"mean_ok({exp_dict['mean']:+.3f}>={floor:.2f})"


def test_filter_allows_when_no_evidence():
    allow, _ = _expectancy_filter_decision(None, 0.30)
    assert allow is True


def test_filter_blocks_when_below_floor():
    allow, reason = _expectancy_filter_decision(
        {"n": 10, "mean": 0.10, "sum": 1.0, "win_rate": 0.4}, 0.30,
    )
    assert allow is False
    assert "below_floor" in reason


def test_filter_allows_at_floor_inclusive():
    """mean = floor exactly — must allow (boundary inclusive)."""
    allow, _ = _expectancy_filter_decision(
        {"n": 10, "mean": 0.30, "sum": 3.0, "win_rate": 0.5}, 0.30,
    )
    assert allow is True


def test_filter_allows_above_floor():
    allow, _ = _expectancy_filter_decision(
        {"n": 10, "mean": 0.50, "sum": 5.0, "win_rate": 0.5}, 0.30,
    )
    assert allow is True


def test_filter_disabled_by_default_phase20():
    """Pin the 2026-05-04 Phase 20 disable: EXPECTANCY_FILTER.enabled
    defaulted to False per UNBLOCK_ALL directive after live monitor
    showed 0/N entries firing on otherwise-valid setups (BTC at conf=0.81
    blocked because recent_mean=-$0.467 < $0.05 floor — chicken-and-egg
    lockout with no recovery path).

    Phase 16 adaptive sizing + Phase 18 calibrator handle low-EV setups
    organically (size down by rolling EV + soft confidence multiplier).
    The hard $0.05 floor was a binary triple-veto on top.

    Floor lowered to -$0.50 as catastrophic-only guard if user flips
    enabled=True back. Same value for STAR + non-STAR (the asymmetry
    only made sense with positive floors)."""
    from config import EXPECTANCY_FILTER as ef
    assert ef["enabled"] is False, "Phase 20: filter disabled per UNBLOCK_ALL"
    assert ef["min_expected_dollar"] == -0.50
    assert ef["min_expected_star"] == -0.50
    assert ef["lookback_days"] == 30
    assert ef["min_sample_size"] == 5


def test_star_floor_at_or_below_nonstar_floor():
    """Star floor must remain at-or-below non-STAR floor (relaxed, not
    stricter) — pinned across versions even after the Phase 20 disable."""
    from config import EXPECTANCY_FILTER as ef
    star_floor = ef["min_expected_star"]
    nonstar_floor = ef["min_expected_dollar"]
    assert star_floor <= nonstar_floor


# ─── Component 1.1: is_entry_invalidated ─────────────────────────────


def _staleness_decision(*, side: str, ema20: float, ema50: float,
                        gap_pct: float = 0.15) -> tuple[bool, str]:
    """Replica of MCPBrain.is_entry_invalidated's predicate."""
    if not ema20 or not ema50 or ema50 <= 0:
        return False, "no_ema_data"
    gap = (ema20 - ema50) / ema50 * 100.0
    if side == "buy":
        if gap <= -gap_pct:
            return True, f"4h gap={gap:+.2f}% (long invalidated)"
        return False, f"4h gap={gap:+.2f}% (long still valid)"
    elif side == "sell":
        if gap >= gap_pct:
            return True, f"4h gap={gap:+.2f}% (short invalidated)"
        return False, f"4h gap={gap:+.2f}% (short still valid)"
    return False, f"unknown_side:{side}"


def test_long_with_ema20_above_ema50_not_invalidated():
    """Original entry rationale (ema20 > ema50) still holds."""
    invalidated, _ = _staleness_decision(side="buy", ema20=100.5, ema50=100.0)
    assert not invalidated


def test_long_with_ema20_clearly_below_ema50_invalidated():
    """ema20 = 99.5, ema50 = 100 → gap = -0.5%, > 0.15% threshold → invalidate."""
    invalidated, reason = _staleness_decision(side="buy", ema20=99.5, ema50=100.0)
    assert invalidated
    assert "long invalidated" in reason


def test_long_with_tight_cross_NOT_invalidated():
    """ema20 = 99.95, ema50 = 100 → gap = -0.05%, below 0.15% threshold.
    Whipsaw guard — touching the line isn't a regime change."""
    invalidated, _ = _staleness_decision(side="buy", ema20=99.95, ema50=100.0)
    assert not invalidated


def test_long_at_exact_threshold_invalidated():
    """gap = -0.15% exactly → boundary inclusive, fires."""
    invalidated, _ = _staleness_decision(side="buy", ema20=99.85, ema50=100.0)
    assert invalidated


def test_short_with_ema20_below_ema50_not_invalidated():
    """Short entry rationale (ema20 < ema50) still holds."""
    invalidated, _ = _staleness_decision(side="sell", ema20=99.5, ema50=100.0)
    assert not invalidated


def test_short_with_ema20_above_ema50_invalidated():
    """ema20 = 100.5 → gap = +0.5%, > 0.15% threshold → short invalidated."""
    invalidated, reason = _staleness_decision(side="sell", ema20=100.5, ema50=100.0)
    assert invalidated
    assert "short invalidated" in reason


def test_missing_ema_data_does_not_invalidate():
    """If indicators can't be fetched, the trade keeps its protection
    chain — never close on a missing-data signal."""
    invalidated, reason = _staleness_decision(side="buy", ema20=0, ema50=0)
    assert not invalidated
    assert reason == "no_ema_data"


def test_unknown_side_does_not_invalidate():
    invalidated, reason = _staleness_decision(side="hold", ema20=99, ema50=100)
    assert not invalidated
    assert "unknown_side" in reason


# ─── Production-source structural pin ────────────────────────────────


def test_production_has_expectancy_filter():
    """Pin that bot_engine._execute_open carries the expectancy block."""
    src = Path("core/bot_engine.py").read_text(encoding="utf-8")
    assert "EXPECTANCY_FILTER" in src
    assert "[Expectancy] BLOCKED" in src
    assert "recent_expectancy" in src


def test_production_has_entry_staleness():
    """Pin that order_manager monitor calls is_entry_invalidated."""
    src = Path("core/order_manager.py").read_text(encoding="utf-8")
    assert "ENTRY_STALENESS_EXIT" in src
    assert "is_entry_invalidated" in src
    assert "entry_invalidated" in src   # close reason


def test_production_has_is_entry_invalidated_method():
    """Pin that mcp_brain.py has the method."""
    src = Path("core/mcp_brain.py").read_text(encoding="utf-8")
    assert "def is_entry_invalidated" in src
    assert "ema20" in src
    assert "long invalidated" in src
    assert "short invalidated" in src


def test_config_carries_documented_defaults():
    """Pin defaults from spec."""
    from config import ENTRY_STALENESS_EXIT as es
    assert es["enabled"] is True
    assert es["invalidation_gap_pct"] == 0.15
    assert es["min_hold_minutes"] == 30
