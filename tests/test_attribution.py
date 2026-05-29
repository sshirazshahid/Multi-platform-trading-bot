"""Phase 1.1: per-trade alpha attribution invariants.

Decomposition: realized_pnl = alpha - spread - slippage - funding - fees
where
  alpha = mid-to-mid PnL (pure direction edge)
  spread = bid-ask crossing cost (positive = paid)
  slippage = sim-modeled vs mid divergence (paper only; 0 for live)
  funding = perpetual funding paid (positive = cost)
  fees = entry + exit trading fees

Tests cover both sides, size scaling, the invariant, and warehouse persistence.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load_attribution():
    spec = importlib.util.spec_from_file_location(
        "attribution_test_copy", ROOT / "core" / "attribution.py",
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["attribution_test_copy"] = mod
    spec.loader.exec_module(mod)
    return mod


def _load_warehouse():
    spec = importlib.util.spec_from_file_location(
        "warehouse_attr_test", ROOT / "core" / "warehouse.py",
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["warehouse_attr_test"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def attr():
    return _load_attribution()


@pytest.fixture
def wh(tmp_path):
    return _load_warehouse().Warehouse(path=tmp_path / "attr.sqlite")


def test_closed_form_long(attr):
    """Plan example: long 1 unit, entry mid 100 fill 100.10, exit mid 102
    fill 101.95, funding 0.20, fees 0.06."""
    t = attr.Trade(
        side="long", size=1.0,
        entry_mid=100.0, entry_fill=100.10,
        exit_mid=102.0, exit_fill=101.95,
        funding_paid=0.20, fees=0.06)
    row = attr.attribute(t)
    assert row.alpha == pytest.approx(2.00, abs=1e-9)
    assert row.spread == pytest.approx(0.15, abs=1e-9)
    assert row.funding == pytest.approx(0.20, abs=1e-9)
    assert row.fees == pytest.approx(0.06, abs=1e-9)
    assert row.slippage == pytest.approx(0.0, abs=1e-9)
    # Invariant
    assert row.realized_pnl == pytest.approx(
        row.alpha - row.spread - row.slippage - row.funding - row.fees, abs=1e-9)
    assert row.realized_pnl == pytest.approx(1.59, abs=1e-9)


def test_closed_form_short(attr):
    """Short 1 unit: sells at entry_fill=99.90 (below mid 100, paid spread),
    buys at exit_fill=98.05 (above mid 98, paid spread)."""
    t = attr.Trade(
        side="short", size=1.0,
        entry_mid=100.0, entry_fill=99.90,
        exit_mid=98.0, exit_fill=98.05,
        funding_paid=-0.10,  # short receives funding
        fees=0.06)
    row = attr.attribute(t)
    # short alpha when price falls 100→98: gain = 2 per unit
    assert row.alpha == pytest.approx(2.00, abs=1e-9)
    # spread = (entry_mid - entry_fill) + (exit_fill - exit_mid) = 0.10 + 0.05 = 0.15
    assert row.spread == pytest.approx(0.15, abs=1e-9)
    assert row.funding == pytest.approx(-0.10, abs=1e-9)  # received
    assert row.realized_pnl == pytest.approx(
        row.alpha - row.spread - row.slippage - row.funding - row.fees, abs=1e-9)
    # 2 - 0.15 - 0 - (-0.10) - 0.06 = 1.89
    assert row.realized_pnl == pytest.approx(1.89, abs=1e-9)


def test_size_scales_alpha_and_spread(attr):
    t = attr.Trade(
        side="long", size=10.0,
        entry_mid=100.0, entry_fill=100.10,
        exit_mid=102.0, exit_fill=101.95,
        funding_paid=2.00, fees=0.60)
    row = attr.attribute(t)
    assert row.alpha == pytest.approx(20.00, abs=1e-9)
    assert row.spread == pytest.approx(1.50, abs=1e-9)
    # invariant
    assert row.realized_pnl == pytest.approx(
        row.alpha - row.spread - row.slippage - row.funding - row.fees, abs=1e-9)


def test_losing_long(attr):
    """Long with falling price: alpha negative, costs still positive."""
    t = attr.Trade(
        side="long", size=1.0,
        entry_mid=100.0, entry_fill=100.05,
        exit_mid=98.0, exit_fill=97.95,
        funding_paid=0.10, fees=0.04)
    row = attr.attribute(t)
    assert row.alpha == pytest.approx(-2.00, abs=1e-9)
    assert row.spread == pytest.approx(0.10, abs=1e-9)
    # realized = -2 - 0.10 - 0 - 0.10 - 0.04 = -2.24
    assert row.realized_pnl == pytest.approx(-2.24, abs=1e-9)


def test_zero_spread_when_fill_equals_mid(attr):
    t = attr.Trade(
        side="long", size=1.0,
        entry_mid=100.0, entry_fill=100.0,
        exit_mid=102.0, exit_fill=102.0,
        funding_paid=0.0, fees=0.0)
    row = attr.attribute(t)
    assert row.spread == pytest.approx(0.0, abs=1e-9)
    assert row.alpha == pytest.approx(2.00, abs=1e-9)
    assert row.realized_pnl == pytest.approx(2.00, abs=1e-9)


def test_paper_trade_with_slippage(attr):
    """Paper trade where SimExecutionModel inserted 0.05 of mid-vs-fill
    slippage on top of spread."""
    t = attr.Trade(
        side="long", size=1.0,
        entry_mid=100.0, entry_fill=100.10,
        exit_mid=102.0, exit_fill=101.95,
        funding_paid=0.20, fees=0.06,
        slippage_modeled=0.05)
    row = attr.attribute(t)
    assert row.slippage == pytest.approx(0.05, abs=1e-9)
    assert row.realized_pnl == pytest.approx(
        2.00 - 0.15 - 0.05 - 0.20 - 0.06, abs=1e-9)


def test_unknown_side_raises(attr):
    t = attr.Trade(
        side="upward", size=1.0,
        entry_mid=100.0, entry_fill=100.0,
        exit_mid=100.0, exit_fill=100.0)
    with pytest.raises(ValueError):
        attr.attribute(t)


def test_record_persists_to_warehouse(attr, wh):
    """record() computes attribution AND writes to warehouse.attribution row."""
    t = attr.Trade(
        side="long", size=1.0,
        entry_mid=100.0, entry_fill=100.10,
        exit_mid=102.0, exit_fill=101.95,
        funding_paid=0.20, fees=0.06)
    row = attr.record(wh, trade_id=42, trade=t)
    assert row.alpha == pytest.approx(2.00, abs=1e-9)
    rows = wh.query("SELECT * FROM attribution WHERE trade_id=?", (42,))
    assert len(rows) == 1
    db = rows[0]
    assert db["alpha"] == pytest.approx(2.00, abs=1e-9)
    assert db["spread"] == pytest.approx(0.15, abs=1e-9)
    assert db["funding"] == pytest.approx(0.20, abs=1e-9)
    assert db["fees"] == pytest.approx(0.06, abs=1e-9)
    assert db["realized_pnl"] == pytest.approx(1.59, abs=1e-9)
    assert db["attributed_at"] is not None


def test_record_replaces_on_re_run(attr, wh):
    """Re-running attribution on the same trade_id replaces the row
    (use case: re-attribute after a slippage-snapshot fix)."""
    t1 = attr.Trade(
        side="long", size=1.0,
        entry_mid=100.0, entry_fill=100.0,
        exit_mid=101.0, exit_fill=101.0)
    attr.record(wh, trade_id=7, trade=t1)
    t2 = attr.Trade(
        side="long", size=1.0,
        entry_mid=100.0, entry_fill=100.10,  # differs
        exit_mid=101.0, exit_fill=100.95,    # differs
        fees=0.04)
    attr.record(wh, trade_id=7, trade=t2)
    rows = wh.query("SELECT * FROM attribution WHERE trade_id=?", (7,))
    assert len(rows) == 1, "PK on trade_id; re-attribution must replace, not duplicate"
    assert rows[0]["spread"] == pytest.approx(0.15, abs=1e-9)
    assert rows[0]["fees"] == pytest.approx(0.04, abs=1e-9)
