"""Causal harmonic-pattern detector.

The detector confirms swing pivots only after ``right`` bars have elapsed.
Signals therefore appear at the confirmation bar, not at the historical pivot
bar. Pattern ratios are intentionally broad; this is a candidate detector, not
a promotion gate.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class SwingPoint:
    idx: int
    confirmed_at: int
    kind: str  # "H" | "L"
    price: float


@dataclass(frozen=True)
class HarmonicPattern:
    direction: int  # +1 bullish, -1 bearish
    name: str
    points: tuple[SwingPoint, SwingPoint, SwingPoint, SwingPoint, SwingPoint]
    score: float


def confirmed_swings(df: pd.DataFrame, *, left: int = 3, right: int = 3) -> list[SwingPoint]:
    high = df["high"].to_numpy(dtype=float)
    low = df["low"].to_numpy(dtype=float)
    n = len(df)
    swings: list[SwingPoint] = []
    for i in range(left + right, n):
        c = i - right
        hwin = high[c - left : c + right + 1]
        lwin = low[c - left : c + right + 1]
        if not len(hwin) or not len(lwin):
            continue
        if high[c] == np.max(hwin) and np.sum(hwin == high[c]) == 1:
            swings.append(SwingPoint(c, i, "H", float(high[c])))
        if low[c] == np.min(lwin) and np.sum(lwin == low[c]) == 1:
            swings.append(SwingPoint(c, i, "L", float(low[c])))

    # De-duplicate adjacent same-kind pivots by keeping the more extreme one.
    compact: list[SwingPoint] = []
    for s in swings:
        if not compact or compact[-1].kind != s.kind:
            compact.append(s)
            continue
        prev = compact[-1]
        replace = (s.kind == "H" and s.price > prev.price) or (
            s.kind == "L" and s.price < prev.price
        )
        if replace:
            compact[-1] = s
    return compact


def _between(v: float, lo: float, hi: float) -> bool:
    return np.isfinite(v) and lo <= v <= hi


def _ratio(num: float, den: float) -> float:
    den = abs(den)
    return abs(num) / den if den > 0 else float("nan")


def _pattern_from_last_five(swings: list[SwingPoint]) -> HarmonicPattern | None:
    if len(swings) < 5:
        return None

    pts = tuple(swings[-5:])
    kinds = "".join(p.kind for p in pts)
    if kinds not in {"LHLHL", "HLHLH"}:
        return None

    x, a, b, c, d = [p.price for p in pts]
    xa = a - x
    ab = b - a
    bc = c - b
    cd = d - c
    ad = d - a

    ab_retrace = _ratio(ab, xa)
    bc_retrace = _ratio(bc, ab)
    cd_extend = _ratio(cd, bc)
    ad_retrace = _ratio(ad, xa)

    ratio_ok = (
        _between(ab_retrace, 0.382, 0.886)
        and _between(bc_retrace, 0.382, 0.886)
        and _between(cd_extend, 1.13, 2.618)
        and _between(ad_retrace, 0.55, 1.05)
    )
    if not ratio_ok:
        return None

    # Name by the closest AD retracement family.
    families = {
        "gartley": 0.786,
        "bat": 0.886,
        "butterfly": 1.00,
        "deep_crab": 1.618,
    }
    nearest = min(families, key=lambda k: abs(ad_retrace - families[k]))
    ratio_error = (
        abs(ab_retrace - 0.618)
        + abs(bc_retrace - 0.618)
        + min(abs(cd_extend - 1.272), abs(cd_extend - 1.618), abs(cd_extend - 2.0))
    )
    score = float(max(0.0, min(1.0, 1.0 - ratio_error / 3.0)))
    direction = 1 if kinds == "LHLHL" else -1
    return HarmonicPattern(direction, nearest, pts, score)


def detect_last_pattern(df: pd.DataFrame, **params) -> HarmonicPattern | None:
    left = int(params.get("left", 3))
    right = int(params.get("right", 3))
    swings = confirmed_swings(df, left=left, right=right)
    return _pattern_from_last_five(swings)


def _append_compact_swing(swings: list[SwingPoint], swing: SwingPoint) -> None:
    if not swings or swings[-1].kind != swing.kind:
        swings.append(swing)
        return
    prev = swings[-1]
    replace = (swing.kind == "H" and swing.price > prev.price) or (
        swing.kind == "L" and swing.price < prev.price
    )
    if replace:
        swings[-1] = swing


def signal(df: pd.DataFrame, **params) -> pd.Series:
    hold_bars = int(params.get("hold_bars", 5))
    left = int(params.get("left", 3))
    right = int(params.get("right", 3))
    out = pd.Series(0, index=df.index, dtype="int8")
    if len(df) < 10:
        return out

    high = df["high"].to_numpy(dtype=float)
    low = df["low"].to_numpy(dtype=float)
    swings: list[SwingPoint] = []

    # Incremental causal pass. At row i, only the pivot at i-right can become
    # confirmed, matching detect_last_pattern(df.iloc[:i+1]) without O(n^2).
    for i in range(left + right, len(df)):
        c = i - right
        hwin = high[c - left : c + right + 1]
        lwin = low[c - left : c + right + 1]
        if not len(hwin) or not len(lwin):
            continue

        if high[c] == np.max(hwin) and np.sum(hwin == high[c]) == 1:
            _append_compact_swing(swings, SwingPoint(c, i, "H", float(high[c])))
        if low[c] == np.min(lwin) and np.sum(lwin == low[c]) == 1:
            _append_compact_swing(swings, SwingPoint(c, i, "L", float(low[c])))

        pat = _pattern_from_last_five(swings)
        if pat is not None and pat.points[-1].confirmed_at == i:
            end = min(len(df), i + max(1, hold_bars))
            out.iloc[i:end] = pat.direction
    return out
