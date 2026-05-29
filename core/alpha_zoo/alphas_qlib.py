# core/alpha_zoo/alphas_qlib.py
"""Qlib Alpha158 feature family, generated programmatically.

Alpha158 (github.com/microsoft/qlib, source="Qlib") is a parametrized feature
set, NOT 158 distinct hand-written formulas: 9 KBAR candle features + 4 price
ratios + ~29 rolling operators evaluated at windows [5,10,20,30,60]. We emit
each as an `AlphaDef` and screen them cross-sectionally exactly like K101/GTJA.

This is the most redundant slice of the zoo — these are ratios-to-close that
rank-collapse onto momentum/mean-reversion/correlation signals already present
(and the bot's own 14 features). Included for completeness of the search.

Reuses: regbeta/regresi (Slope/Resi vs time), correlation**2 (Rsquare),
count (CNTP/CNTN), ts_sum+clip (SUMP/SUMN), quantile (QTLU/QTLD).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from core.alpha_zoo import operators as op
from core.alpha_zoo.registry import AlphaDef

_WINDOWS = (5, 10, 20, 30, 60)
_EPS = 1e-12


def _seq(df: pd.DataFrame) -> pd.DataFrame:
    """0,1,..,T-1 broadcast across symbols — the time index for Slope/Resi/Rsquare."""
    return pd.DataFrame(
        np.broadcast_to(np.arange(len(df), dtype=float).reshape(-1, 1), df.shape),
        index=df.index, columns=df.columns)


def _ret1(p: pd.DataFrame):
    c = p.fields["close"]
    return c / op.delay(c, 1) - 1.0


# ── KBAR (9) ─────────────────────────────────────────────────────────────
def _kmid(p):  o, c = p.fields["open"], p.fields["close"]; return (c - o) / o
def _klen(p):  o, h, l = p.fields["open"], p.fields["high"], p.fields["low"]; return (h - l) / o
def _kmid2(p): o, c, h, l = p.fields["open"], p.fields["close"], p.fields["high"], p.fields["low"]; return (c - o) / (h - l + _EPS)
def _kup(p):   o, c, h = p.fields["open"], p.fields["close"], p.fields["high"]; return (h - op.elem_max(o, c)) / o
def _kup2(p):  o, c, h, l = p.fields["open"], p.fields["close"], p.fields["high"], p.fields["low"]; return (h - op.elem_max(o, c)) / (h - l + _EPS)
def _klow(p):  o, c, l = p.fields["open"], p.fields["close"], p.fields["low"]; return (op.elem_min(o, c) - l) / o
def _klow2(p): o, c, h, l = p.fields["open"], p.fields["close"], p.fields["high"], p.fields["low"]; return (op.elem_min(o, c) - l) / (h - l + _EPS)
def _ksft(p):  o, c, h, l = p.fields["open"], p.fields["close"], p.fields["high"], p.fields["low"]; return (2 * c - h - l) / o
def _ksft2(p): c, h, l = p.fields["close"], p.fields["high"], p.fields["low"]; return (2 * c - h - l) / (h - l + _EPS)

_KBAR = {"KMID": _kmid, "KLEN": _klen, "KMID2": _kmid2, "KUP": _kup, "KUP2": _kup2,
         "KLOW": _klow, "KLOW2": _klow2, "KSFT": _ksft, "KSFT2": _ksft2}


def _price_ratio(field: str):
    def f(p):
        return p.fields[field] / p.fields["close"]
    return f


# ── Rolling feature factories: factory(w) -> fn(panel) ───────────────────
def _roc(w):
    def f(p): c = p.fields["close"]; return op.delay(c, w) / c
    return f


def _ma(w):
    def f(p): c = p.fields["close"]; return op.sma(c, w) / c
    return f


def _std(w):
    def f(p): c = p.fields["close"]; return op.stddev(c, w) / c
    return f


def _beta(w):
    def f(p): c = p.fields["close"]; return op.regbeta(c, _seq(c), w) / c
    return f


def _rsqr(w):
    def f(p): c = p.fields["close"]; return op.correlation(c, _seq(c), w) ** 2
    return f


def _resi(w):
    def f(p): c = p.fields["close"]; return op.regresi(c, _seq(c), w) / c
    return f


def _maxw(w):
    def f(p): return op.ts_max(p.fields["high"], w) / p.fields["close"]
    return f


def _minw(w):
    def f(p): return op.ts_min(p.fields["low"], w) / p.fields["close"]
    return f


def _qtlu(w):
    def f(p): c = p.fields["close"]; return op.quantile(c, w, 0.8) / c
    return f


def _qtld(w):
    def f(p): c = p.fields["close"]; return op.quantile(c, w, 0.2) / c
    return f


def _rankw(w):
    def f(p): return op.ts_rank(p.fields["close"], w)
    return f


def _rsv(w):
    def f(p):
        c, h, l = p.fields["close"], p.fields["high"], p.fields["low"]
        lo = op.ts_min(l, w)
        return (c - lo) / (op.ts_max(h, w) - lo + _EPS)
    return f


def _imax(w):
    def f(p): return op.ts_argmax(p.fields["high"], w) / w
    return f


def _imin(w):
    def f(p): return op.ts_argmin(p.fields["low"], w) / w
    return f


def _imxd(w):
    def f(p):
        return (op.ts_argmax(p.fields["high"], w) - op.ts_argmin(p.fields["low"], w)) / w
    return f


def _corr(w):
    def f(p):
        c, v = p.fields["close"], p.fields["volume"]
        return op.correlation(c, op.log(v + 1.0), w)
    return f


def _cord(w):
    def f(p):
        c, v = p.fields["close"], p.fields["volume"]
        return op.correlation(c / op.delay(c, 1), op.log((v / op.delay(v, 1)) + 1.0), w)
    return f


def _cntp(w):
    def f(p): c = p.fields["close"]; return op.count(c > op.delay(c, 1), w) / w
    return f


def _cntn(w):
    def f(p): c = p.fields["close"]; return op.count(c < op.delay(c, 1), w) / w
    return f


def _cntd(w):
    def f(p):
        c = p.fields["close"]
        return (op.count(c > op.delay(c, 1), w) - op.count(c < op.delay(c, 1), w)) / w
    return f


def _sump(w):
    def f(p):
        d = op.delta(p.fields["close"], 1)
        return op.ts_sum(op.elem_max(d, 0.0), w) / (op.ts_sum(op.abs_(d), w) + _EPS)
    return f


def _sumn(w):
    def f(p):
        d = op.delta(p.fields["close"], 1)
        return op.ts_sum(op.elem_max(-d, 0.0), w) / (op.ts_sum(op.abs_(d), w) + _EPS)
    return f


def _sumd(w):
    def f(p):
        d = op.delta(p.fields["close"], 1)
        denom = op.ts_sum(op.abs_(d), w) + _EPS
        return (op.ts_sum(op.elem_max(d, 0.0), w) - op.ts_sum(op.elem_max(-d, 0.0), w)) / denom
    return f


def _vma(w):
    def f(p): v = p.fields["volume"]; return op.sma(v, w) / (v + _EPS)
    return f


def _vstd(w):
    def f(p): v = p.fields["volume"]; return op.stddev(v, w) / (v + _EPS)
    return f


def _wvma(w):
    def f(p):
        x = op.abs_(_ret1(p)) * p.fields["volume"]
        return op.stddev(x, w) / (op.sma(x, w) + _EPS)
    return f


def _vsump(w):
    def f(p):
        d = op.delta(p.fields["volume"], 1)
        return op.ts_sum(op.elem_max(d, 0.0), w) / (op.ts_sum(op.abs_(d), w) + _EPS)
    return f


def _vsumn(w):
    def f(p):
        d = op.delta(p.fields["volume"], 1)
        return op.ts_sum(op.elem_max(-d, 0.0), w) / (op.ts_sum(op.abs_(d), w) + _EPS)
    return f


def _vsumd(w):
    def f(p):
        d = op.delta(p.fields["volume"], 1)
        denom = op.ts_sum(op.abs_(d), w) + _EPS
        return (op.ts_sum(op.elem_max(d, 0.0), w) - op.ts_sum(op.elem_max(-d, 0.0), w)) / denom
    return f


_ROLLING = {
    "ROC": _roc, "MA": _ma, "STD": _std, "BETA": _beta, "RSQR": _rsqr, "RESI": _resi,
    "MAX": _maxw, "MIN": _minw, "QTLU": _qtlu, "QTLD": _qtld, "RANK": _rankw, "RSV": _rsv,
    "IMAX": _imax, "IMIN": _imin, "IMXD": _imxd, "CORR": _corr, "CORD": _cord,
    "CNTP": _cntp, "CNTN": _cntn, "CNTD": _cntd, "SUMP": _sump, "SUMN": _sumn, "SUMD": _sumd,
    "VMA": _vma, "VSTD": _vstd, "WVMA": _wvma, "VSUMP": _vsump, "VSUMN": _vsumn, "VSUMD": _vsumd,
}


def _build_qlib158() -> list[AlphaDef]:
    out: list[AlphaDef] = []
    for name, fn in _KBAR.items():
        out.append(AlphaDef(f"Q_{name}", "Qlib", fn))
    for fld, nm in (("open", "OPEN"), ("high", "HIGH"), ("low", "LOW"), ("vwap", "VWAP")):
        out.append(AlphaDef(f"Q_{nm}0", "Qlib", _price_ratio(fld)))
    for name, factory in _ROLLING.items():
        for w in _WINDOWS:
            out.append(AlphaDef(f"Q_{name}{w}", "Qlib", factory(w)))
    return out


QLIB_ALPHAS: list[AlphaDef] = _build_qlib158()
