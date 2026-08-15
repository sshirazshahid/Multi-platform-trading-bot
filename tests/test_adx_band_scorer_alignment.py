"""Scorer must not propose entries the band-regime veto will reject.

Implements prereg 77 (_workspace/strategy_pipeline/
77_prereg_adx_band_scorer_alignment.md, sha256 12149d6e2efe..., commit
0793bdf, hashed BEFORE this change).

THE CONFLICT (measured 2026-08-15, see 76_adx_gate_conflict):
  * the scorer REQUIRES trend - rejects adx_4h < 15 as chop, needs adx >= 20
  * the band-regime veto REJECTS adx_4h > 30 as a toxic regime
So the scorer's best candidates landed exactly where the veto fires: of 2,531
candidates that PASSED scoring in 24h, 65.4% had ADX > 30 and were vetoed at
execution (1,743 of 1,786 post-approval blocks), producing 0 opens in 38h.

The fix gives the scorer the veto's own upper bound so it stops proposing
doomed entries. The veto is NOT touched, and 30 is not a new number - it is
the pre-registered bucket edge from screen 13_band_conditional.

HONESTY (prereg §3, restated so no reader mistakes flow for edge): the 20-30
band is itself significantly NEGATIVE (mean -0.1551/trade, 95% CI
[-0.2232, -0.0871]). This change restores FLOW, not EDGE.

Run: venv/Scripts/python.exe -m pytest tests/test_adx_band_scorer_alignment.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.scoring.entry_score import score_coin  # noqa: E402


class _Coord:
    """Minimal data-coordinator stub; the regime gate returns before use."""

    def __getattr__(self, _name):
        return lambda *a, **k: {}


def _ei(adx_4h: float) -> dict:
    """Indicator bundle that passes every gate EXCEPT the regime check."""
    tf = {"adx": adx_4h, "bb_width": 2.5, "atr_pct": 1.0,
          "avg_body_20": 1.0, "last_body": 1.0, "ema_fast": 100.0,
          "ema_slow": 100.0, "rsi": 55.0, "close": 100.0}
    return {"4h": dict(tf), "1h": dict(tf), "15m": dict(tf)}


def _score(adx_4h: float) -> dict:
    return score_coin(data_coordinator=_Coord(), coin="BTC",
                      data={"funding": {}, "orderbook": {}}, ei=_ei(adx_4h))


# ── the fix ──────────────────────────────────────────────────────────────
def test_adx_above_30_is_rejected_by_the_scorer():
    """THE change: the scorer must stop proposing veto-doomed entries."""
    r = _score(34.0)
    assert r["score"] == 0, "adx_4h>30 must not produce a scored candidate"
    assert "30" in r["reason"], r["reason"]


def test_adx_at_exactly_30_is_not_rejected_by_the_upper_bound():
    """Boundary: the veto fires on > 30, so 30 itself must pass the scorer.
    An off-by-one here would silently discard a whole band of live setups."""
    reason = _score(30.0).get("reason") or ""
    assert ">30" not in reason, f"adx=30 must not hit the upper bound: {reason!r}"


# ── preserved behaviour ──────────────────────────────────────────────────
def test_chop_rejection_below_15_is_unchanged():
    r = _score(11.0)
    assert r["score"] == 0 and "chop" in r["reason"]


def test_mid_band_is_not_rejected_by_the_regime_gate():
    """20-30 is the surviving window; the regime gate must let it through.

    A surviving candidate reaches full scoring, so `reason` becomes a scoring
    BREAKDOWN (which legitimately mentions adx). The discriminator is the
    regime rejection's own shape: score==0 with a regime_* reason."""
    for adx in (20.0, 25.0, 29.9):
        r = _score(adx)
        reason = r.get("reason") or ""
        assert not (r["score"] == 0 and "regime" in reason), (
            f"adx={adx} must survive the regime gate, got {reason!r}")


def test_rejection_reason_is_diagnosable():
    """The reason must name the threshold so an operator can tell this apart
    from the chop rejection in the skip histogram."""
    r = _score(45.0)
    assert "adx" in r["reason"].lower() and "30" in r["reason"]
