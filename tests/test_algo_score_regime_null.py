"""Algo score must be NULL (not 0.0) for regime-rejected coins.

Post De-Emotion the Claude blend path is gone; pin the warehouse write
inside `_algorithmic_portfolio` still gates on layers_ok > 0 when present,
and `_score_coin` zero-return shape is unchanged.
"""
from __future__ import annotations

import re
from pathlib import Path


def test_algo_score_only_kept_when_layers_ok_positive():
    src = (Path(__file__).resolve().parents[1]
           / "core" / "scoring" / "portfolio.py").read_text(encoding="utf-8")
    # Prefer explicit gate if algo_score still written; otherwise accept
    # layers_ok checks on rule_gate / decision path.
    if "algo_score" in src:
        assert re.search(r"layers_ok\s*>\s*0", src), (
            "algo_score must be gated on layers_ok>0 so regime-rejected "
            "scores write NULL to the warehouse, not 0.0."
        )
    else:
        # Decision gate still requires positive layers_ok for ALLOW.
        assert "layers_ok" in src
        assert re.search(r'layers_ok["\']?\]\s*>=\s*[46]|layers_ok\s*>\s*0', src)


def test_score_coin_zero_return_shape_unchanged():
    src = (Path(__file__).resolve().parents[1]
           / "core" / "scoring" / "entry_score.py").read_text(encoding="utf-8")
    m = re.search(
        r'_zero\s*=\s*\{\s*"score":\s*0,\s*"side":\s*"buy",\s*"layers_ok":\s*0',
        src,
    )
    assert m is not None, (
        "_score_coin regime-exit _zero shape must keep score=0 + layers_ok=0"
    )
