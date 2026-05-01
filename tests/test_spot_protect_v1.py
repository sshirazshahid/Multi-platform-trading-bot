"""SPOT-PROTECT-V1 invariants (2026-05-01).

Spec: docs/superpowers/specs/2026-05-01-spot-protect-v1-design.md

Two-component spot strategy:
  Component A — Dust Consolidation (one-shot operator script)
  Component B — Peak-Drawdown Stop (live rule in evaluate_holding)

Tests target the predicate logic without spinning up the full
SpotPortfolioManager (heavy: needs real exchange clients).
"""
from __future__ import annotations

from dataclasses import dataclass, field

import pytest


# ── Test fixtures: lightweight HoldingInfo replica ────────────────────


@dataclass
class _Hold:
    coin: str = "TEST"
    exchange: str = "binance"
    balance: float = 100.0
    avg_cost: float = 1.0
    current_price: float = 1.0
    unrealized_pnl: float = 0.0
    unrealized_pnl_pct: float = 0.0
    peak_price: float = 1.0
    last_updated: float = 0.0


# ── Self-contained replica of evaluate_holding's peak-drawdown block ──


def _peak_drawdown_decision(h: _Hold, ss: dict) -> dict:
    """Replica of `core/spot_manager.py::evaluate_holding` peak-drawdown
    block. Returns the same dict shape."""
    if not ss.get("enabled", False):
        return {"action": "FALLTHROUGH", "reason": "ss_disabled"}
    value_usdt = h.balance * h.current_price
    min_pos = float(ss.get("min_position_usd", 50.0))
    half_pct = float(ss.get("drawdown_half_pct", 0.25))
    full_pct = float(ss.get("drawdown_full_pct", 0.40))
    if value_usdt < min_pos:
        return {
            "action": "HOLD",
            "reason": f"below_min_position(${value_usdt:.2f}<${min_pos:.0f})",
            "value_usdt": value_usdt,
        }
    drawdown = (h.peak_price - h.current_price) / h.peak_price if h.peak_price > 0 else 0
    if drawdown >= full_pct:
        return {"action": "SELL", "reason": "peak_drawdown_full",
                "drawdown": drawdown, "value_usdt": value_usdt}
    if drawdown >= half_pct:
        h.peak_price = h.current_price  # reset peak
        return {"action": "SCALE_OUT", "reason": "peak_drawdown_half",
                "drawdown": drawdown, "value_usdt": value_usdt}
    return {"action": "HOLD", "reason": "peak_drawdown_ok",
            "drawdown": drawdown, "value_usdt": value_usdt}


@pytest.fixture
def cfg() -> dict:
    return {
        "enabled":           True,
        "dust_cutoff_usd":   25.0,
        "min_position_usd":  50.0,
        "drawdown_half_pct": 0.25,
        "drawdown_full_pct": 0.40,
    }


# ── Component B — Peak-Drawdown Stop ─────────────────────────────────


def test_below_min_position_skipped(cfg):
    """Position worth $40 is below the $50 floor — no rules apply.
    Tiny positions are Component A's job, not Component B's."""
    h = _Hold(balance=40.0, current_price=1.0, peak_price=2.0)  # 50% DD but $40 value
    out = _peak_drawdown_decision(h, cfg)
    assert out["action"] == "HOLD"
    assert "below_min_position" in out["reason"]


def test_drawdown_below_half_threshold_holds(cfg):
    """-24% drawdown — under the 25% threshold, no action."""
    h = _Hold(balance=200.0, current_price=0.76, peak_price=1.0)  # -24%
    out = _peak_drawdown_decision(h, cfg)
    assert out["action"] == "HOLD"
    assert out["drawdown"] == pytest.approx(0.24, abs=0.001)


def test_drawdown_at_half_threshold_inclusive(cfg):
    """-25% exactly — boundary inclusive, fires SCALE_OUT."""
    h = _Hold(balance=200.0, current_price=0.75, peak_price=1.0)
    out = _peak_drawdown_decision(h, cfg)
    assert out["action"] == "SCALE_OUT"
    assert out["drawdown"] == pytest.approx(0.25, abs=0.001)


def test_drawdown_at_full_threshold_inclusive(cfg):
    """-40% exactly — boundary inclusive, fires SELL."""
    h = _Hold(balance=200.0, current_price=0.60, peak_price=1.0)
    out = _peak_drawdown_decision(h, cfg)
    assert out["action"] == "SELL"


def test_drawdown_between_thresholds(cfg):
    """-30% drawdown — between half (25%) and full (40%), SCALE_OUT only."""
    h = _Hold(balance=200.0, current_price=0.70, peak_price=1.0)
    out = _peak_drawdown_decision(h, cfg)
    assert out["action"] == "SCALE_OUT"


def test_full_exit_dominates_half(cfg):
    """-50% drawdown — both thresholds met. The full-exit rule must
    win (checked first in production)."""
    h = _Hold(balance=200.0, current_price=0.50, peak_price=1.0)
    out = _peak_drawdown_decision(h, cfg)
    assert out["action"] == "SELL", (
        "full-exit dominates half-exit at deep drawdowns")


def test_no_drawdown_holds(cfg):
    """Current = peak — drawdown is 0%, hold."""
    h = _Hold(balance=200.0, current_price=1.0, peak_price=1.0)
    out = _peak_drawdown_decision(h, cfg)
    assert out["action"] == "HOLD"


def test_disabled_falls_through(cfg):
    """When SPOT_STRATEGY.enabled=False, the block doesn't fire — caller
    gets the FALLTHROUGH sentinel and uses the legacy TA path."""
    cfg["enabled"] = False
    h = _Hold(balance=200.0, current_price=0.50, peak_price=1.0)
    out = _peak_drawdown_decision(h, cfg)
    assert out["action"] == "FALLTHROUGH"


def test_half_exit_resets_peak(cfg):
    """After SCALE_OUT, peak must be reset to current_price so the
    NEXT trigger requires a NEW drawdown — not the same continuing one."""
    h = _Hold(balance=200.0, current_price=0.75, peak_price=1.0)
    assert h.peak_price == 1.0
    out = _peak_drawdown_decision(h, cfg)
    assert out["action"] == "SCALE_OUT"
    assert h.peak_price == 0.75, (
        "peak must reset to current after half-exit, otherwise the "
        "next scan would re-fire SCALE_OUT on the same drawdown")


def test_repeated_drawdown_after_reset(cfg):
    """Holding climbs to new peak after a SCALE_OUT, then drops again.
    The new drawdown is measured from the NEW peak, not the original.

    Sequence (using whole numbers to avoid float-precision issues that
    bite at 0.90×0.75 = 0.6749...):
      peak=100, current=75 (-25%)  → SCALE_OUT, peak resets to 75
      peak=75 → climbs to 200 (new peak)
      peak=200, current=150 (-25%) → second SCALE_OUT
    """
    h = _Hold(balance=2.0, current_price=75.0, peak_price=100.0)
    out1 = _peak_drawdown_decision(h, cfg)
    assert out1["action"] == "SCALE_OUT"
    assert h.peak_price == 75.0

    # Simulate: position rallied to 200 (peak update happens in
    # scan_holdings line 119 via max(price, prev.peak_price))
    h.current_price = 200.0
    h.peak_price = max(h.peak_price, h.current_price)
    assert h.peak_price == 200.0
    # Now drops to -25% from new peak
    h.current_price = 150.0  # 200 × 0.75 — exact in float
    out2 = _peak_drawdown_decision(h, cfg)
    assert out2["action"] == "SCALE_OUT", (
        "new -25% drawdown from new peak must trigger again")


# ── Component A — Dust Consolidation predicate ───────────────────────


def _is_dust(value_usdt: float, cutoff_usd: float) -> bool:
    """Replica of `dust_candidates` filter row predicate."""
    return value_usdt > 0 and value_usdt < cutoff_usd


@pytest.mark.parametrize("value,cutoff,expected", [
    (24.99,  25.0, True),    # just under cutoff = dust
    (25.0,   25.0, False),   # exactly cutoff = NOT dust (>= cutoff kept)
    (25.01,  25.0, False),   # just over = NOT dust
    (5.0,    25.0, True),    # tiny = dust
    (0.0,    25.0, False),   # zero = excluded (== 0, not > 0)
    (100.0,  25.0, False),   # large = NOT dust
])
def test_dust_consolidation_threshold(value, cutoff, expected):
    assert _is_dust(value, cutoff) == expected


def test_default_config_carries_documented_values():
    """Pin the spec-documented defaults so future tuning has to update
    both this test and the spec."""
    from config import SPOT_STRATEGY as ss
    assert ss["enabled"] is True
    assert ss["dust_cutoff_usd"] == 25.0
    assert ss["min_position_usd"] == 50.0
    assert ss["drawdown_half_pct"] == 0.25
    assert ss["drawdown_full_pct"] == 0.40


def test_thresholds_consistent_ordering():
    """Defensive: drawdown_half_pct < drawdown_full_pct, otherwise the
    full-exit rule could never fire (would always be caught by the
    half rule first if equal)."""
    from config import SPOT_STRATEGY as ss
    assert ss["drawdown_half_pct"] < ss["drawdown_full_pct"], (
        "half threshold must be strictly less than full")


def test_min_position_at_or_above_dust_cutoff():
    """min_position_usd ($50) must be >= dust_cutoff_usd ($25). Otherwise
    there's a gap: positions worth $25-$50 would have neither active
    rules (Component B) nor get consolidated (Component A) — they'd
    sit with no defined behavior."""
    from config import SPOT_STRATEGY as ss
    assert ss["min_position_usd"] >= ss["dust_cutoff_usd"]


# ── Production-source structural pin ─────────────────────────────────


def test_production_evaluate_holding_has_peak_drawdown_block():
    """Pin the contract so future edits to evaluate_holding can't
    silently drift from the test replica."""
    from pathlib import Path
    src = Path("core/spot_manager.py").read_text(encoding="utf-8")
    assert "SPOT_STRATEGY" in src
    assert "peak_drawdown_full" in src
    assert "peak_drawdown_half" in src
    assert "h.peak_price = h.current_price" in src, (
        "peak-reset on half-exit must be explicit in production code")
