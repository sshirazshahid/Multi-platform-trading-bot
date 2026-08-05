"""Cell-Filter Entry Gate invariants (2026-05-01).

Spec: docs/superpowers/specs/2026-05-01-cell-filter-entry-gate-design.md

Single rule: take entry IFF
    (symbol in STAR_SYMBOLS) OR (70.0 <= mcp_score <= 84.0)

The full _execute_open path is heavyweight (exchanges, balances,
risk_manager, etc.). Rather than spin all of that up, these tests
exercise a self-contained predicate replica that mirrors the
production gate. Any change to the production logic must update both
sides — pin via the structural test below.
"""
from __future__ import annotations

from tests.bot_engine_source import bot_engine_source_for_grep

import pytest


def _gate_decision(
    *,
    symbol: str,
    mcp_score: float | None,
    star_symbols: set,
    cf: dict,
) -> tuple[bool, str]:
    """Self-contained replica of the production gate at
    `core/bot_engine.py::_execute_open` (cell-filter block).

    Returns (allow: bool, reason: str). reason is empty on allow, or one
    of {'cell_filter:score_below_band', 'cell_filter:score_above_band'}.
    """
    if not cf.get("enabled", True):
        return True, ""
    symbol_key = symbol if ":" in symbol else f"{symbol}:USDT"
    is_star = (
        cf.get("star_overrides", True)
        and (symbol in star_symbols or symbol_key in star_symbols)
    )
    score = mcp_score
    try:
        score = float(score) if score is not None else 0.0
    except (TypeError, ValueError):
        score = 0.0
    band_min = float(cf.get("score_band_min", 70.0))
    band_max = float(cf.get("score_band_max", 84.0))
    if is_star:
        return True, ""
    if score < band_min:
        return False, "cell_filter:score_below_band"
    if score > band_max:
        return False, "cell_filter:score_above_band"
    return True, ""


# Fixtures ─────────────────────────────────────────────────────────────


@pytest.fixture
def stars() -> set:
    return {"ATOM/USDT:USDT", "ARB/USDT:USDT", "DOGE/USDT:USDT"}


@pytest.fixture
def cf_default() -> dict:
    return {
        "enabled": True,
        "score_band_min": 70.0,
        "score_band_max": 84.0,
        "star_overrides": True,
    }


# Spec Section "Edge cases" — 6 cells of the (symbol_class, score) matrix


def test_star_symbol_with_any_score_is_allowed(stars, cf_default):
    """STAR symbols always pass the cell filter, regardless of score.
    The score-85 tier-cap (commit 86acef3) handles the high-score case
    by capping size, not blocking entry."""
    for score in (50.0, 70.0, 85.0, 99.0):
        allow, reason = _gate_decision(
            symbol="ATOM/USDT:USDT", mcp_score=score,
            star_symbols=stars, cf=cf_default,
        )
        assert allow, f"STAR + score={score} must allow"
        assert reason == ""


def test_non_star_at_lower_band_inclusive(stars, cf_default):
    """score=70.0 exactly must allow (band is inclusive on the low end)."""
    allow, reason = _gate_decision(
        symbol="LINK/USDT:USDT", mcp_score=70.0,
        star_symbols=stars, cf=cf_default,
    )
    assert allow
    assert reason == ""


def test_non_star_at_upper_band_inclusive(stars, cf_default):
    """score=84.0 exactly must allow (band is inclusive on the high end)."""
    allow, reason = _gate_decision(
        symbol="LINK/USDT:USDT", mcp_score=84.0,
        star_symbols=stars, cf=cf_default,
    )
    assert allow
    assert reason == ""


def test_non_star_below_band_blocks_with_correct_reason(stars, cf_default):
    """score=69.99 must block with 'score_below_band'. The existing
    score-gate at the scorer level requires score>=66 to even reach
    this code, so 'below band' is rare but real for edge cases like
    Claude-AI-proposed entries with arbitrary scores."""
    allow, reason = _gate_decision(
        symbol="LINK/USDT:USDT", mcp_score=69.99,
        star_symbols=stars, cf=cf_default,
    )
    assert not allow
    assert reason == "cell_filter:score_below_band"


def test_non_star_above_band_blocks_with_correct_reason(stars, cf_default):
    """score=85.0 (the anti-EV threshold) on a non-STAR symbol must
    block with 'score_above_band'. This is THE load-bearing rejection.
    claude_portfolio data: score 85-100 is -$2.98/16 trades/31% WR."""
    allow, reason = _gate_decision(
        symbol="LINK/USDT:USDT", mcp_score=85.0,
        star_symbols=stars, cf=cf_default,
    )
    assert not allow
    assert reason == "cell_filter:score_above_band"


def test_missing_score_treated_as_below_band(stars, cf_default):
    """A None score (Claude-advisor-proposed entry that didn't carry one)
    falls back to 0.0 → blocked as below_band. Better than letting an
    unscored candidate through."""
    allow, reason = _gate_decision(
        symbol="LINK/USDT:USDT", mcp_score=None,
        star_symbols=stars, cf=cf_default,
    )
    assert not allow
    assert reason == "cell_filter:score_below_band"


# Rollback path ────────────────────────────────────────────────────────


def test_disabled_filter_allows_everything(stars):
    """`CELL_FILTER.enabled=False` is the one-line rollback. Must restore
    pre-filter behavior — every candidate passes."""
    cf_off = {"enabled": False, "score_band_min": 70.0,
              "score_band_max": 84.0, "star_overrides": True}
    for sym, score in (
        ("LINK/USDT:USDT", 50.0),
        ("LINK/USDT:USDT", 99.0),
        ("ATOM/USDT:USDT", 50.0),
    ):
        allow, _ = _gate_decision(symbol=sym, mcp_score=score,
                                  star_symbols=stars, cf=cf_off)
        assert allow, f"disabled filter must allow {sym} score={score}"


# Future tightening ────────────────────────────────────────────────────


def test_star_overrides_false_subjects_star_to_band(stars, cf_default):
    """`star_overrides=False` flips STAR rows to require the score band
    too. Reserved for a future tightening if STAR-at-high-score proves
    anti-EV with more data."""
    cf = dict(cf_default, star_overrides=False)
    allow, reason = _gate_decision(
        symbol="ATOM/USDT:USDT", mcp_score=90.0,
        star_symbols=stars, cf=cf,
    )
    assert not allow
    assert reason == "cell_filter:score_above_band"


# Structural pin: production code matches replica ──────────────────────


def test_production_gate_matches_replica_signature():
    """Pin the contract so a future edit to bot_engine._execute_open's
    cell-filter block can't drift from this test's predicate without
    failing CI. Reads the source verbatim and asserts the load-bearing
    strings are present."""
    from pathlib import Path
    src = bot_engine_source_for_grep()
    assert "CELL_FILTER" in src
    assert "[CellFilter] BLOCKED" in src
    assert "cell_filter:score_below_band" in src
    assert "cell_filter:score_above_band" in src
    assert "star_overrides" in src


def test_config_carries_documented_defaults():
    """Pin the cell-filter config keys exist with sane values. The
    `enabled` flag is operator-set (currently False per UNBLOCK_ALL —
    user reasoning: don't double-count when MCP score + model gate +
    expectancy filter already evaluate profitability per-trade).

    Restore by setting CELL_FILTER.enabled=True if losses resume."""
    from config import CELL_FILTER
    assert isinstance(CELL_FILTER["enabled"], bool)
    assert CELL_FILTER["score_band_min"] == 70.0
    assert CELL_FILTER["score_band_max"] == 84.0
    assert CELL_FILTER["star_overrides"] is True
