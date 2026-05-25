# core/alpha_zoo/alphas.py
"""Formulaic-alpha registry for the alpha search.

Each alpha is an `AlphaDef`: an id, a source tag, a function Panel->(T×N)
signal DataFrame built from `operators`, and computability metadata. Only
`computable=True` alphas count toward `n_computable()`; `n_eff() = 2 *
n_computable()` is the trials count fed to the deflated Sharpe.

This starter set exercises every operator. Grow toward the full zoo (later
task) by appending AlphaDefs:
  - Kakushadze-101  : arXiv:1601.00991 (source="K101")
  - GTJA-191        : Guotai-Junan 191 Alphas (source="GTJA")
  - Qlib158         : github.com/microsoft/qlib Alpha158 (source="Qlib")
  - Fama-French     : price/volume-computable proxies only (source="FF")
Mark any alpha needing indneutralize-as-its-whole-signal, fundamentals, or
book values as computable=False with a reason. Where indneutralize is one
step inside a price/volume formula, degrade it to identity and keep it
computable, noting that in `needs`.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

import pandas as pd

from core.alpha_zoo import operators as op
from core.alpha_zoo.panel import Panel


@dataclass
class AlphaDef:
    id: str
    source: str                       # 'K101' | 'GTJA' | 'Qlib' | 'FF'
    fn: Callable[[Panel], pd.DataFrame] | None
    computable: bool = True
    needs: list[str] = field(default_factory=list)
    reason_if_dropped: str = ""


# ── Starter catalog (exercises the full operator surface) ────────────────
def _k001(p: Panel) -> pd.DataFrame:
    # rank(ts_argmax(signedpower(returns<0 ? stddev(returns,20):close, 2),5))-0.5
    r = p.fields["returns"]
    inner = r.where(r < 0, p.fields["close"])
    return op.rank(op.ts_argmax(op.signed_power(inner, 2.0), 5)) - 0.5


def _k003(p: Panel) -> pd.DataFrame:
    return -1.0 * op.correlation(op.rank(p.fields["open"]),
                                 op.rank(p.fields["volume"]), 10)


def _k004(p: Panel) -> pd.DataFrame:
    return -1.0 * op.ts_rank(op.rank(p.fields["low"]), 9)


def _k006(p: Panel) -> pd.DataFrame:
    return -1.0 * op.correlation(p.fields["open"], p.fields["volume"], 10)


def _k012(p: Panel) -> pd.DataFrame:
    return op.sign(op.delta(p.fields["volume"], 1)) * (-1.0 * op.delta(p.fields["close"], 1))


def _k019(p: Panel) -> pd.DataFrame:
    c = p.fields["close"]
    term = op.sign((c - op.delay(c, 7)) + op.delta(c, 7))
    return -1.0 * term * (1.0 + op.rank(1.0 + op.ts_sum(p.fields["returns"], 250)))


def _k033(p: Panel) -> pd.DataFrame:
    return op.rank(-1.0 * (1.0 - (p.fields["open"] / p.fields["close"])))


def _k041(p: Panel) -> pd.DataFrame:
    return op.signed_power(p.fields["high"] * p.fields["low"], 0.5) - p.fields["vwap"]


def _k101(p: Panel) -> pd.DataFrame:
    o, c, h, l = (p.fields["open"], p.fields["close"],
                  p.fields["high"], p.fields["low"])
    return (c - o) / ((h - l) + 0.001)


def _decay_mom(p: Panel) -> pd.DataFrame:
    # operator-coverage alpha: linearly-decayed 10-bar momentum, ranked
    return op.rank(op.decay_linear(op.delta(p.fields["close"], 10), 10))


def _vol_scaled_rev(p: Panel) -> pd.DataFrame:
    # short-term reversal scaled by inverse vol (coverage: scale + stddev)
    rev = -1.0 * op.delta(p.fields["close"], 1)
    return op.scale(rev / (op.stddev(p.fields["returns"], 20) + 1e-9))


ALPHAS: list[AlphaDef] = [
    AlphaDef("K001", "K101", _k001),
    AlphaDef("K003", "K101", _k003),
    AlphaDef("K004", "K101", _k004),
    AlphaDef("K006", "K101", _k006),
    AlphaDef("K012", "K101", _k012),
    AlphaDef("K019", "K101", _k019),
    AlphaDef("K033", "K101", _k033),
    AlphaDef("K041", "K101", _k041),
    AlphaDef("K101", "K101", _k101),
    AlphaDef("COV_DECAY_MOM", "Qlib", _decay_mom),
    AlphaDef("COV_VOL_REV", "Qlib", _vol_scaled_rev),
    # Example of a registered-but-dropped alpha (template for the full-zoo task):
    AlphaDef("K048", "K101", None, computable=False,
             needs=["indneutralize(subindustry)"],
             reason_if_dropped="whole signal is industry-neutralization; "
                               "no clean crypto industry map"),
]


def computable_alphas() -> list[AlphaDef]:
    return [a for a in ALPHAS if a.computable]


def n_computable() -> int:
    return len(computable_alphas())


def n_eff() -> int:
    """Trials count for DSR: 2× computable (pays for in-sample sign-fitting)."""
    return 2 * n_computable()
