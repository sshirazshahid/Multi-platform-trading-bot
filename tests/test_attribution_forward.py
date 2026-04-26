"""Phase 2 / Task A: forward-attribution wiring.

Verifies the new code path that runs *every time a position closes*:
  - Position carries entry_mid / exit_mid / funding_paid
  - _mid_from_ticker extracts (bid+ask)/2 robustly from a ccxt ticker
  - Building an attribution.Trade from a closed Position satisfies the
    invariant `realized_pnl == alpha - spread - slippage - funding - fees`

These tests do NOT spin up exchanges. They construct a Position, mutate it
to mimic the close path, and run attribution.attribute on the result.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _load(name: str, relpath: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relpath)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def attr():
    return _load("attribution_fwd_test", "core/attribution.py")


def test_mid_from_ticker_normal():
    """Helper extracts (bid+ask)/2 from a healthy ticker."""
    from core.order_manager import _mid_from_ticker
    assert _mid_from_ticker({"bid": 100.0, "ask": 102.0}) == pytest.approx(101.0)


def test_mid_from_ticker_missing_returns_zero():
    """When bid or ask are missing/zero, return 0.0 — attribution will skip."""
    from core.order_manager import _mid_from_ticker
    assert _mid_from_ticker({"bid": 0, "ask": 100}) == 0.0
    assert _mid_from_ticker({"bid": 100, "ask": 0}) == 0.0
    assert _mid_from_ticker({"last": 100}) == 0.0
    assert _mid_from_ticker({}) == 0.0


def test_mid_from_ticker_garbage_returns_zero():
    from core.order_manager import _mid_from_ticker
    assert _mid_from_ticker({"bid": "n/a", "ask": "n/a"}) == 0.0
    assert _mid_from_ticker({"bid": None, "ask": None}) == 0.0


def test_position_carries_mid_and_funding_fields():
    """Position dataclass must accept the new fields with sane defaults."""
    from core.position_tracker import Position
    p = Position(
        id="x", exchange="binance", symbol="BTC/USDT:USDT", side="buy",
        market_type="futures", strategy="systematic_v3_1",
        entry_price=100.0, size=1.0, stop_loss=99.0, take_profit=102.0,
    )
    assert p.entry_mid == 0.0
    assert p.exit_mid == 0.0
    assert p.funding_paid == 0.0

    p2 = Position(
        id="y", exchange="binance", symbol="BTC/USDT:USDT", side="buy",
        market_type="futures", strategy="systematic_v3_1",
        entry_price=100.0, size=1.0, stop_loss=99.0, take_profit=102.0,
        entry_mid=100.05, exit_mid=101.95, funding_paid=0.20,
    )
    assert p2.entry_mid == pytest.approx(100.05)
    assert p2.exit_mid == pytest.approx(101.95)
    assert p2.funding_paid == pytest.approx(0.20)


def test_round_trip_invariant_long_paper(attr):
    """Build a closed Position the way _finalize_close would see it, build
    an attribution.Trade from it, and check the invariant holds."""
    from core.position_tracker import Position
    p = Position(
        id="z", exchange="binance", symbol="BTC/USDT:USDT", side="buy",
        market_type="futures", strategy="systematic_v3_1",
        entry_price=100.10, size=1.0,
        stop_loss=99.0, take_profit=102.5,
        leverage=1,
        entry_mid=100.0, exit_mid=102.0, funding_paid=0.20,
    )
    p.close(exit_price=101.95, reason="take_profit")

    t = attr.Trade(
        side=p.side, size=p.size,
        entry_mid=p.entry_mid, entry_fill=p.entry_price,
        exit_mid=p.exit_mid, exit_fill=p.exit_price,
        funding_paid=p.funding_paid,
        fees=p.total_fees,
        slippage_modeled=0.0,
    )
    row = attr.attribute(t)

    # Invariant from attribution.attribute itself
    assert row.realized_pnl == pytest.approx(
        row.alpha - row.spread - row.slippage - row.funding - row.fees,
        abs=1e-9)

    # Sanity: alpha is mid-to-mid (102 - 100) = 2.00 on size 1
    assert row.alpha == pytest.approx(2.00, abs=1e-9)
    # spread = (entry_fill - entry_mid) + (exit_mid - exit_fill)
    #        = (100.10 - 100) + (102 - 101.95) = 0.15
    assert row.spread == pytest.approx(0.15, abs=1e-9)
    assert row.funding == pytest.approx(0.20, abs=1e-9)


def test_round_trip_invariant_short(attr):
    """Mirror test on the short side: shorts sell below mid (entry) and
    buy above mid (exit) — both cost spread."""
    from core.position_tracker import Position
    p = Position(
        id="s", exchange="bybit", symbol="ETH/USDT:USDT", side="sell",
        market_type="futures", strategy="MultiTF",
        entry_price=99.90, size=2.0,
        stop_loss=101.0, take_profit=98.0,
        leverage=2,
        entry_mid=100.0, exit_mid=98.0, funding_paid=-0.05,
    )
    p.close(exit_price=98.05, reason="take_profit")

    t = attr.Trade(
        side=p.side, size=p.size,
        entry_mid=p.entry_mid, entry_fill=p.entry_price,
        exit_mid=p.exit_mid, exit_fill=p.exit_price,
        funding_paid=p.funding_paid,
        fees=p.total_fees,
    )
    row = attr.attribute(t)

    # Invariant
    assert row.realized_pnl == pytest.approx(
        row.alpha - row.spread - row.slippage - row.funding - row.fees,
        abs=1e-9)

    # alpha for short over a 2-point drop on size 2 = +4.00
    assert row.alpha == pytest.approx(4.00, abs=1e-9)
    # spread = (entry_mid - entry_fill) + (exit_fill - exit_mid) per unit
    #        = (100 - 99.90) + (98.05 - 98) = 0.15 per unit × size 2 = 0.30
    assert row.spread == pytest.approx(0.30, abs=1e-9)


def test_attribution_skipped_when_mid_zero(attr):
    """The wire-up in _finalize_close must skip rows where either mid is 0
    (ghost-sync, pre-Task-A positions). attribute() itself does not enforce
    this — it's the wiring's job. We document the policy here as a smoke
    test on the predicate."""
    from core.position_tracker import Position
    p = Position(
        id="g", exchange="bitget", symbol="SOL/USDT:USDT", side="buy",
        market_type="futures", strategy="claude_portfolio",
        entry_price=20.0, size=5.0,
        stop_loss=19.0, take_profit=22.0,
        # entry_mid and exit_mid unset (ghost-sync close)
    )
    p.close(exit_price=20.5, reason="ghost_force_close")

    has_mids = (
        float(getattr(p, "entry_mid", 0.0) or 0.0) > 0.0
        and float(getattr(p, "exit_mid", 0.0) or 0.0) > 0.0
    )
    assert not has_mids
