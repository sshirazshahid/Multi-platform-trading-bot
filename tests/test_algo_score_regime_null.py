"""Algo score must be NULL (not 0.0) for regime-rejected coins (2026-05-24).

Bug: mcp_brain.py:1851-1854 wrote `algo_score = float(_s) if _s is not
None else None`. _score_coin's `_zero` return for regime exits has
`score=0` (an int, not None), so `algo_score` became 0.0 in the
warehouse. That conflates "regime rejected" with "genuinely scored 0"
and poisons per-score-band WR analytics — the same diagnostic the
75-84 anti-monotonicity work depends on.

Fix: gate the score on `layers_ok > 0` (genuine evaluation past regime
filters). Historical mcp_score=0 rows are not migrated — fix-forward.
"""
from __future__ import annotations

import re
from pathlib import Path


def test_algo_score_only_kept_when_layers_ok_positive():
    """Structural pin: the score write must gate on layers_ok > 0."""
    src = (Path(__file__).resolve().parents[1]
           / "core" / "mcp_brain.py").read_text(encoding="utf-8")
    # Find the algo_score assignment site (analyze_portfolio's Claude blend).
    m = re.search(
        r"_s\s*=\s*algo\.get\(\"score\"\)[\s\S]{0,800}?algo_score\s*=\s*[\s\S]+?\n",
        src,
    )
    assert m is not None, (
        "algo_score assignment block not found in mcp_brain.py — "
        "structural test cannot verify; update if the block moved."
    )
    block = m.group(0)
    assert "layers_ok" in block, (
        "algo_score must be gated on layers_ok>0 so regime-rejected "
        "scores (layers_ok=0, score=0) write NULL to the warehouse, "
        "not 0.0."
    )


def test_score_coin_zero_return_shape_unchanged():
    """Sanity: _score_coin's regime-exit return must keep score=0 + layers_ok=0.

    If this changes, the structural fix above silently stops working
    because the gate predicate becomes wrong."""
    src = (Path(__file__).resolve().parents[1]
           / "core" / "mcp_brain.py").read_text(encoding="utf-8")
    m = re.search(
        r'_zero\s*=\s*\{\s*"score":\s*0,\s*"side":\s*"buy",\s*"layers_ok":\s*0',
        src,
    )
    assert m is not None, (
        "_score_coin's _zero return shape changed; update the "
        "layers_ok>0 gate at the algo_score assignment site to match."
    )
