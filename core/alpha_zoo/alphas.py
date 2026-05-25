# core/alpha_zoo/alphas.py
"""Formulaic-alpha registry for the alpha search.

Each alpha is an `AlphaDef`: an id, a source tag, a function Panel->(T×N)
signal DataFrame built from `operators`, and computability metadata. Only
`computable=True` alphas count toward `n_computable()`; `n_eff() = 2 *
n_computable()` is the trials count fed to the deflated Sharpe.

Currently ported: the full Kakushadze-101 (arXiv:1601.00991, "101 Formulaic
Alphas"), source="K101". Faithful transcription with three documented
crypto adaptations:
  * `indneutralize(x, IndClass.*)` -> identity (no clean crypto industry map);
    affected alphas carry needs=["indneutralize->identity"].
  * `cap` (market cap) is unavailable -> Alpha#56 is computable=False.
  * `min(x,d)`/`max(x,d)` with an integer window are time-series ts_min/ts_max
    (paper A.2); `min(A,B)`/`max(A,B)` between two series are element-wise.
Grow toward the full zoo by appending GTJA-191 (source="GTJA") and Qlib158
(source="Qlib") with their required operators.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from core.alpha_zoo import operators as op
from core.alpha_zoo.panel import Panel

_IND = ["indneutralize->identity"]  # crypto has no industry map; degrade to identity


@dataclass
class AlphaDef:
    id: str
    source: str                       # 'K101' | 'GTJA' | 'Qlib' | 'FF'
    fn: Callable[[Panel], pd.DataFrame] | None
    computable: bool = True
    needs: list[str] = field(default_factory=list)
    reason_if_dropped: str = ""


def _iif(cond: pd.DataFrame, a, b) -> pd.DataFrame:
    """Ternary (cond ? a : b) over panels; a/b may be DataFrame or scalar."""
    av = a.to_numpy() if isinstance(a, pd.DataFrame) else a
    bv = b.to_numpy() if isinstance(b, pd.DataFrame) else b
    return pd.DataFrame(np.where(cond.to_numpy(), av, bv),
                        index=cond.index, columns=cond.columns)


# ── Kakushadze-101 ───────────────────────────────────────────────────────
def _k1(p):
    r = p.fields["returns"]
    base = _iif(r < 0, op.stddev(r, 20), p.fields["close"])
    return op.rank(op.ts_argmax(op.signed_power(base, 2.0), 5)) - 0.5


def _k2(p):
    c, o, v = p.fields["close"], p.fields["open"], p.fields["volume"]
    return -1 * op.correlation(op.rank(op.delta(op.log(v), 2)), op.rank((c - o) / o), 6)


def _k3(p):
    return -1 * op.correlation(op.rank(p.fields["open"]), op.rank(p.fields["volume"]), 10)


def _k4(p):
    return -1 * op.ts_rank(op.rank(p.fields["low"]), 9)


def _k5(p):
    o, vwap, c = p.fields["open"], p.fields["vwap"], p.fields["close"]
    return op.rank(o - (op.ts_sum(vwap, 10) / 10)) * (-1 * op.abs_(op.rank(c - vwap)))


def _k6(p):
    return -1 * op.correlation(p.fields["open"], p.fields["volume"], 10)


def _k7(p):
    adv20, v, c = p.adv(20), p.fields["volume"], p.fields["close"]
    expr = (-1 * op.ts_rank(op.abs_(op.delta(c, 7)), 60)) * op.sign(op.delta(c, 7))
    return _iif(adv20 < v, expr, -1.0)


def _k8(p):
    o, r = p.fields["open"], p.fields["returns"]
    x = op.ts_sum(o, 5) * op.ts_sum(r, 5)
    return -1 * op.rank(x - op.delay(x, 10))


def _k9(p):
    c = p.fields["close"]
    d = op.delta(c, 1)
    inner = _iif(op.ts_max(d, 5) < 0, d, -1 * d)
    return _iif(0 < op.ts_min(d, 5), d, inner)


def _k10(p):
    c = p.fields["close"]
    d = op.delta(c, 1)
    inner = _iif(op.ts_max(d, 4) < 0, d, -1 * d)
    return op.rank(_iif(0 < op.ts_min(d, 4), d, inner))


def _k11(p):
    vwap, c, v = p.fields["vwap"], p.fields["close"], p.fields["volume"]
    x = vwap - c
    return (op.rank(op.ts_max(x, 3)) + op.rank(op.ts_min(x, 3))) * op.rank(op.delta(v, 3))


def _k12(p):
    return op.sign(op.delta(p.fields["volume"], 1)) * (-1 * op.delta(p.fields["close"], 1))


def _k13(p):
    return -1 * op.rank(op.covariance(op.rank(p.fields["close"]), op.rank(p.fields["volume"]), 5))


def _k14(p):
    return (-1 * op.rank(op.delta(p.fields["returns"], 3))) * op.correlation(
        p.fields["open"], p.fields["volume"], 10)


def _k15(p):
    h, v = p.fields["high"], p.fields["volume"]
    return -1 * op.ts_sum(op.rank(op.correlation(op.rank(h), op.rank(v), 3)), 3)


def _k16(p):
    return -1 * op.rank(op.covariance(op.rank(p.fields["high"]), op.rank(p.fields["volume"]), 5))


def _k17(p):
    c, v, adv20 = p.fields["close"], p.fields["volume"], p.adv(20)
    return ((-1 * op.rank(op.ts_rank(c, 10))) * op.rank(op.delta(op.delta(c, 1), 1))) * \
        op.rank(op.ts_rank(v / adv20, 5))


def _k18(p):
    c, o = p.fields["close"], p.fields["open"]
    return -1 * op.rank((op.stddev(op.abs_(c - o), 5) + (c - o)) + op.correlation(c, o, 10))


def _k19(p):
    c, r = p.fields["close"], p.fields["returns"]
    return (-1 * op.sign((c - op.delay(c, 7)) + op.delta(c, 7))) * (1 + op.rank(1 + op.ts_sum(r, 250)))


def _k20(p):
    o, h, c, l = p.fields["open"], p.fields["high"], p.fields["close"], p.fields["low"]
    return ((-1 * op.rank(o - op.delay(h, 1))) * op.rank(o - op.delay(c, 1))) * op.rank(o - op.delay(l, 1))


def _k21(p):
    c, v, adv20 = p.fields["close"], p.fields["volume"], p.adv(20)
    m8, s8, m2 = op.ts_sum(c, 8) / 8, op.stddev(c, 8), op.ts_sum(c, 2) / 2
    cond3 = (1 < (v / adv20)) | ((v / adv20) == 1)
    inner2 = _iif(cond3, 1.0, -1.0)
    inner1 = _iif(m2 < (m8 - s8), 1.0, inner2)
    return _iif((m8 + s8) < m2, -1.0, inner1)


def _k22(p):
    h, v, c = p.fields["high"], p.fields["volume"], p.fields["close"]
    return -1 * (op.delta(op.correlation(h, v, 5), 5) * op.rank(op.stddev(c, 20)))


def _k23(p):
    h = p.fields["high"]
    return _iif((op.ts_sum(h, 20) / 20) < h, -1 * op.delta(h, 2), 0.0)


def _k24(p):
    c = p.fields["close"]
    val = op.delta(op.ts_sum(c, 100) / 100, 100) / op.delay(c, 100)
    cond = (val < 0.05) | (val == 0.05)
    return _iif(cond, -1 * (c - op.ts_min(c, 100)), -1 * op.delta(c, 3))


def _k25(p):
    r, adv20, vwap, h, c = (p.fields["returns"], p.adv(20), p.fields["vwap"],
                            p.fields["high"], p.fields["close"])
    return op.rank((((-1 * r) * adv20) * vwap) * (h - c))


def _k26(p):
    v, h = p.fields["volume"], p.fields["high"]
    return -1 * op.ts_max(op.correlation(op.ts_rank(v, 5), op.ts_rank(h, 5), 5), 3)


def _k27(p):
    v, vwap = p.fields["volume"], p.fields["vwap"]
    x = op.rank(op.ts_sum(op.correlation(op.rank(v), op.rank(vwap), 6), 2) / 2.0)
    return _iif(0.5 < x, -1.0, 1.0)


def _k28(p):
    adv20, l, h, c = p.adv(20), p.fields["low"], p.fields["high"], p.fields["close"]
    return op.scale((op.correlation(adv20, l, 5) + ((h + l) / 2)) - c)


def _k29(p):
    c, r = p.fields["close"], p.fields["returns"]
    tsm = op.ts_min(op.rank(op.rank(-1 * op.rank(op.delta(c - 1, 5)))), 2)
    body = op.rank(op.rank(op.scale(op.log(op.ts_sum(tsm, 1)))))
    return op.ts_min(op.product(body, 1), 5) + op.ts_rank(op.delay(-1 * r, 6), 5)


def _k30(p):
    c, v = p.fields["close"], p.fields["volume"]
    s = (op.sign(c - op.delay(c, 1)) + op.sign(op.delay(c, 1) - op.delay(c, 2))
         + op.sign(op.delay(c, 2) - op.delay(c, 3)))
    return ((1.0 - op.rank(s)) * op.ts_sum(v, 5)) / op.ts_sum(v, 20)


def _k31(p):
    c, adv20, l = p.fields["close"], p.adv(20), p.fields["low"]
    a = op.rank(op.rank(op.rank(op.decay_linear(-1 * op.rank(op.rank(op.delta(c, 10))), 10))))
    b = op.rank(-1 * op.delta(c, 3))
    return (a + b) + op.sign(op.scale(op.correlation(adv20, l, 12)))


def _k32(p):
    c, vwap = p.fields["close"], p.fields["vwap"]
    return op.scale((op.ts_sum(c, 7) / 7) - c) + (20 * op.scale(op.correlation(vwap, op.delay(c, 5), 230)))


def _k33(p):
    o, c = p.fields["open"], p.fields["close"]
    return op.rank(-1 * ((1 - (o / c)) ** 1))


def _k34(p):
    r, c = p.fields["returns"], p.fields["close"]
    return op.rank((1 - op.rank(op.stddev(r, 2) / op.stddev(r, 5))) + (1 - op.rank(op.delta(c, 1))))


def _k35(p):
    v, c, h, l, r = (p.fields["volume"], p.fields["close"], p.fields["high"],
                     p.fields["low"], p.fields["returns"])
    return (op.ts_rank(v, 32) * (1 - op.ts_rank((c + h) - l, 16))) * (1 - op.ts_rank(r, 32))


def _k36(p):
    c, o, v, r, vwap, adv20 = (p.fields["close"], p.fields["open"], p.fields["volume"],
                               p.fields["returns"], p.fields["vwap"], p.adv(20))
    return (2.21 * op.rank(op.correlation(c - o, op.delay(v, 1), 15))
            + 0.7 * op.rank(o - c)
            + 0.73 * op.rank(op.ts_rank(op.delay(-1 * r, 6), 5))
            + op.rank(op.abs_(op.correlation(vwap, adv20, 6)))
            + 0.6 * op.rank(((op.ts_sum(c, 200) / 200) - o) * (c - o)))


def _k37(p):
    o, c = p.fields["open"], p.fields["close"]
    return op.rank(op.correlation(op.delay(o - c, 1), c, 200)) + op.rank(o - c)


def _k38(p):
    c, o = p.fields["close"], p.fields["open"]
    return (-1 * op.rank(op.ts_rank(c, 10))) * op.rank(c / o)


def _k39(p):
    c, v, adv20, r = p.fields["close"], p.fields["volume"], p.adv(20), p.fields["returns"]
    return (-1 * op.rank(op.delta(c, 7) * (1 - op.rank(op.decay_linear(v / adv20, 9))))) * \
        (1 + op.rank(op.ts_sum(r, 250)))


def _k40(p):
    h, v = p.fields["high"], p.fields["volume"]
    return (-1 * op.rank(op.stddev(h, 10))) * op.correlation(h, v, 10)


def _k41(p):
    h, l, vwap = p.fields["high"], p.fields["low"], p.fields["vwap"]
    return ((h * l) ** 0.5) - vwap


def _k42(p):
    vwap, c = p.fields["vwap"], p.fields["close"]
    return op.rank(vwap - c) / op.rank(vwap + c)


def _k43(p):
    v, adv20, c = p.fields["volume"], p.adv(20), p.fields["close"]
    return op.ts_rank(v / adv20, 20) * op.ts_rank(-1 * op.delta(c, 7), 8)


def _k44(p):
    h, v = p.fields["high"], p.fields["volume"]
    return -1 * op.correlation(h, op.rank(v), 5)


def _k45(p):
    c, v = p.fields["close"], p.fields["volume"]
    return -1 * ((op.rank(op.ts_sum(op.delay(c, 5), 20) / 20) * op.correlation(c, v, 2))
                 * op.rank(op.correlation(op.ts_sum(c, 5), op.ts_sum(c, 20), 2)))


def _k46(p):
    c = p.fields["close"]
    x = ((op.delay(c, 20) - op.delay(c, 10)) / 10) - ((op.delay(c, 10) - c) / 10)
    inner = _iif(x < 0, 1.0, (-1.0) * (c - op.delay(c, 1)))
    return _iif(0.25 < x, -1.0, inner)


def _k47(p):
    c, v, adv20, h, vwap = (p.fields["close"], p.fields["volume"], p.adv(20),
                            p.fields["high"], p.fields["vwap"])
    return (((op.rank(1 / c) * v) / adv20) * ((h * op.rank(h - c)) / (op.ts_sum(h, 5) / 5))) \
        - op.rank(vwap - op.delay(vwap, 5))


def _k48(p):  # indneutralize -> identity
    c = p.fields["close"]
    num = (op.correlation(op.delta(c, 1), op.delta(op.delay(c, 1), 1), 250) * op.delta(c, 1)) / c
    den = op.ts_sum((op.delta(c, 1) / op.delay(c, 1)) ** 2, 250)
    return num / den


def _k49(p):
    c = p.fields["close"]
    x = ((op.delay(c, 20) - op.delay(c, 10)) / 10) - ((op.delay(c, 10) - c) / 10)
    return _iif(x < (-0.1), 1.0, (-1.0) * (c - op.delay(c, 1)))


def _k50(p):
    v, vwap = p.fields["volume"], p.fields["vwap"]
    return -1 * op.ts_max(op.rank(op.correlation(op.rank(v), op.rank(vwap), 5)), 5)


def _k51(p):
    c = p.fields["close"]
    x = ((op.delay(c, 20) - op.delay(c, 10)) / 10) - ((op.delay(c, 10) - c) / 10)
    return _iif(x < (-0.05), 1.0, (-1.0) * (c - op.delay(c, 1)))


def _k52(p):
    l, r, v = p.fields["low"], p.fields["returns"], p.fields["volume"]
    tsl = op.ts_min(l, 5)
    return (((-1 * tsl) + op.delay(tsl, 5)) * op.rank((op.ts_sum(r, 240) - op.ts_sum(r, 20)) / 220)) \
        * op.ts_rank(v, 5)


def _k53(p):
    c, l, h = p.fields["close"], p.fields["low"], p.fields["high"]
    return -1 * op.delta(((c - l) - (h - c)) / (c - l), 9)


def _k54(p):
    l, c, o, h = p.fields["low"], p.fields["close"], p.fields["open"], p.fields["high"]
    return (-1 * ((l - c) * (o ** 5))) / ((l - h) * (c ** 5))


def _k55(p):
    c, l, h, v = p.fields["close"], p.fields["low"], p.fields["high"], p.fields["volume"]
    x = op.rank((c - op.ts_min(l, 12)) / (op.ts_max(h, 12) - op.ts_min(l, 12)))
    return -1 * op.correlation(x, op.rank(v), 6)


# _k56 uses market cap -> not computable in crypto (registered below as dropped)


def _k57(p):
    c, vwap = p.fields["close"], p.fields["vwap"]
    return 0 - (1 * ((c - vwap) / op.decay_linear(op.rank(op.ts_argmax(c, 30)), 2)))


def _k58(p):  # indneutralize(vwap) -> vwap
    vwap, v = p.fields["vwap"], p.fields["volume"]
    return -1 * op.ts_rank(op.decay_linear(op.correlation(vwap, v, 3.92795), 7.89291), 5.50322)


def _k59(p):  # indneutralize -> identity; weighted vwap collapses to vwap
    vwap, v = p.fields["vwap"], p.fields["volume"]
    return -1 * op.ts_rank(op.decay_linear(op.correlation(vwap, v, 4.25197), 16.2289), 8.19648)


def _k60(p):
    c, l, h, v = p.fields["close"], p.fields["low"], p.fields["high"], p.fields["volume"]
    inner = op.scale(op.rank((((c - l) - (h - c)) / (h - l)) * v))
    return 0 - (1 * ((2 * inner) - op.scale(op.rank(op.ts_argmax(c, 10)))))


def _k61(p):
    vwap, adv180 = p.fields["vwap"], p.adv(180)
    return (op.rank(vwap - op.ts_min(vwap, 16.1219))
            < op.rank(op.correlation(vwap, adv180, 17.9282))).astype(float)


def _k62(p):
    vwap, adv20, o, h, l = (p.fields["vwap"], p.adv(20), p.fields["open"],
                            p.fields["high"], p.fields["low"])
    inner = ((op.rank(o) + op.rank(o)) < (op.rank((h + l) / 2) + op.rank(h))).astype(float)
    a = op.rank(op.correlation(vwap, op.ts_sum(adv20, 22.4101), 9.91009))
    return ((a < op.rank(inner)).astype(float)) * -1


def _k63(p):  # indneutralize(close) -> close
    c, vwap, o, adv180 = p.fields["close"], p.fields["vwap"], p.fields["open"], p.adv(180)
    a = op.rank(op.decay_linear(op.delta(c, 2.25164), 8.22237))
    b = op.rank(op.decay_linear(op.correlation((vwap * 0.318108) + (o * (1 - 0.318108)),
                                               op.ts_sum(adv180, 37.2467), 13.557), 12.2883))
    return (a - b) * -1


def _k64(p):
    o, l, adv120, h, vwap = (p.fields["open"], p.fields["low"], p.adv(120),
                             p.fields["high"], p.fields["vwap"])
    a = op.rank(op.correlation(op.ts_sum((o * 0.178404) + (l * (1 - 0.178404)), 12.7054),
                               op.ts_sum(adv120, 12.7054), 16.6208))
    b = op.rank(op.delta((((h + l) / 2) * 0.178404) + (vwap * (1 - 0.178404)), 3.69741))
    return ((a < b).astype(float)) * -1


def _k65(p):
    o, vwap, adv60 = p.fields["open"], p.fields["vwap"], p.adv(60)
    a = op.rank(op.correlation((o * 0.00817205) + (vwap * (1 - 0.00817205)),
                               op.ts_sum(adv60, 8.6911), 6.40374))
    b = op.rank(o - op.ts_min(o, 13.635))
    return ((a < b).astype(float)) * -1


def _k66(p):
    vwap, l, o, h = p.fields["vwap"], p.fields["low"], p.fields["open"], p.fields["high"]
    a = op.rank(op.decay_linear(op.delta(vwap, 3.51013), 7.23052))
    b = op.ts_rank(op.decay_linear((l - vwap) / (o - ((h + l) / 2)), 11.4157), 6.72611)
    return (a + b) * -1


def _k67(p):  # indneutralize -> identity (vwap, adv20)
    h, vwap, adv20 = p.fields["high"], p.fields["vwap"], p.adv(20)
    a = op.rank(h - op.ts_min(h, 2.14593))
    b = op.rank(op.correlation(vwap, adv20, 6.02936))
    return (a ** b) * -1


def _k68(p):
    h, adv15, c, l = p.fields["high"], p.adv(15), p.fields["close"], p.fields["low"]
    a = op.ts_rank(op.correlation(op.rank(h), op.rank(adv15), 8.91644), 13.9333)
    b = op.rank(op.delta((c * 0.518371) + (l * (1 - 0.518371)), 1.06157))
    return ((a < b).astype(float)) * -1


def _k69(p):  # indneutralize(vwap) -> vwap
    vwap, c, adv20 = p.fields["vwap"], p.fields["close"], p.adv(20)
    a = op.rank(op.ts_max(op.delta(vwap, 2.72412), 4.79344))
    b = op.ts_rank(op.correlation((c * 0.490655) + (vwap * (1 - 0.490655)), adv20, 4.92416), 9.0615)
    return (a ** b) * -1


def _k70(p):  # indneutralize(close) -> close
    vwap, c, adv50 = p.fields["vwap"], p.fields["close"], p.adv(50)
    a = op.rank(op.delta(vwap, 1.29456))
    b = op.ts_rank(op.correlation(c, adv50, 17.8256), 17.9171)
    return (a ** b) * -1


def _k71(p):
    c, adv180, l, o, vwap = (p.fields["close"], p.adv(180), p.fields["low"],
                             p.fields["open"], p.fields["vwap"])
    a = op.ts_rank(op.decay_linear(op.correlation(op.ts_rank(c, 3.43976),
                   op.ts_rank(adv180, 12.0647), 18.0175), 4.20501), 15.6948)
    b = op.ts_rank(op.decay_linear(op.rank((l + o) - (vwap + vwap)) ** 2, 16.4662), 4.4388)
    return op.elem_max(a, b)


def _k72(p):
    h, l, adv40, vwap, v = (p.fields["high"], p.fields["low"], p.adv(40),
                            p.fields["vwap"], p.fields["volume"])
    a = op.rank(op.decay_linear(op.correlation((h + l) / 2, adv40, 8.93345), 10.1519))
    b = op.rank(op.decay_linear(op.correlation(op.ts_rank(vwap, 3.72469),
                op.ts_rank(v, 18.5188), 6.86671), 2.95011))
    return a / b


def _k73(p):
    vwap, o, l = p.fields["vwap"], p.fields["open"], p.fields["low"]
    mix = (o * 0.147155) + (l * (1 - 0.147155))
    a = op.rank(op.decay_linear(op.delta(vwap, 4.72775), 2.91864))
    b = op.ts_rank(op.decay_linear((op.delta(mix, 2.03608) / mix) * -1, 3.33829), 16.7411)
    return op.elem_max(a, b) * -1


def _k74(p):
    c, adv30, h, vwap, v = (p.fields["close"], p.adv(30), p.fields["high"],
                            p.fields["vwap"], p.fields["volume"])
    a = op.rank(op.correlation(c, op.ts_sum(adv30, 37.4843), 15.1365))
    b = op.rank(op.correlation(op.rank((h * 0.0261661) + (vwap * (1 - 0.0261661))), op.rank(v), 11.4791))
    return ((a < b).astype(float)) * -1


def _k75(p):
    vwap, v, l, adv50 = p.fields["vwap"], p.fields["volume"], p.fields["low"], p.adv(50)
    return (op.rank(op.correlation(vwap, v, 4.24304))
            < op.rank(op.correlation(op.rank(l), op.rank(adv50), 12.4413))).astype(float)


def _k76(p):  # indneutralize(low) -> low
    vwap, l, adv81 = p.fields["vwap"], p.fields["low"], p.adv(81)
    a = op.rank(op.decay_linear(op.delta(vwap, 1.24383), 11.8259))
    b = op.ts_rank(op.decay_linear(op.ts_rank(op.correlation(l, adv81, 8.14941), 19.569), 17.1543), 19.383)
    return op.elem_max(a, b) * -1


def _k77(p):
    h, l, vwap, adv40 = p.fields["high"], p.fields["low"], p.fields["vwap"], p.adv(40)
    a = op.rank(op.decay_linear((((h + l) / 2) + h) - (vwap + h), 20.0451))
    b = op.rank(op.decay_linear(op.correlation((h + l) / 2, adv40, 3.1614), 5.64125))
    return op.elem_min(a, b)


def _k78(p):
    l, vwap, adv40, v = p.fields["low"], p.fields["vwap"], p.adv(40), p.fields["volume"]
    a = op.rank(op.correlation(op.ts_sum((l * 0.352233) + (vwap * (1 - 0.352233)), 19.7428),
                               op.ts_sum(adv40, 19.7428), 6.83313))
    b = op.rank(op.correlation(op.rank(vwap), op.rank(v), 5.77492))
    return a ** b


def _k79(p):  # indneutralize -> identity
    c, o, vwap, adv150 = p.fields["close"], p.fields["open"], p.fields["vwap"], p.adv(150)
    a = op.rank(op.delta((c * 0.60733) + (o * (1 - 0.60733)), 1.23438))
    b = op.rank(op.correlation(op.ts_rank(vwap, 3.60973), op.ts_rank(adv150, 9.18637), 14.6644))
    return (a < b).astype(float)


def _k80(p):  # indneutralize -> identity
    o, h, adv10 = p.fields["open"], p.fields["high"], p.adv(10)
    a = op.rank(op.sign(op.delta((o * 0.868128) + (h * (1 - 0.868128)), 4.04545)))
    b = op.ts_rank(op.correlation(h, adv10, 5.11456), 5.53756)
    return (a ** b) * -1


def _k81(p):
    vwap, adv10, v = p.fields["vwap"], p.adv(10), p.fields["volume"]
    inner = op.rank(op.correlation(vwap, op.ts_sum(adv10, 49.6054), 8.47743)) ** 4
    a = op.rank(op.log(op.product(op.rank(inner), 14.9655)))
    b = op.rank(op.correlation(op.rank(vwap), op.rank(v), 5.07914))
    return ((a < b).astype(float)) * -1


def _k82(p):  # indneutralize(volume) -> volume
    o, v = p.fields["open"], p.fields["volume"]
    a = op.rank(op.decay_linear(op.delta(o, 1.46063), 14.8717))
    b = op.ts_rank(op.decay_linear(op.correlation(v, o, 17.4842), 6.92131), 13.4283)
    return op.elem_min(a, b) * -1


def _k83(p):
    h, l, c, v, vwap = (p.fields["high"], p.fields["low"], p.fields["close"],
                        p.fields["volume"], p.fields["vwap"])
    hl = (h - l) / (op.ts_sum(c, 5) / 5)
    return (op.rank(op.delay(hl, 2)) * op.rank(op.rank(v))) / (hl / (vwap - c))


def _k84(p):
    vwap, c = p.fields["vwap"], p.fields["close"]
    base = op.ts_rank(vwap - op.ts_max(vwap, 15.3217), 20.7127)
    expo = op.delta(c, 4.96796)
    return op.sign(base) * (base.abs() ** expo)


def _k85(p):
    h, c, adv30, l, v = (p.fields["high"], p.fields["close"], p.adv(30),
                         p.fields["low"], p.fields["volume"])
    a = op.rank(op.correlation((h * 0.876703) + (c * (1 - 0.876703)), adv30, 9.61331))
    b = op.rank(op.correlation(op.ts_rank((h + l) / 2, 3.70596), op.ts_rank(v, 10.1595), 7.11408))
    return a ** b


def _k86(p):
    c, adv20, o, vwap = p.fields["close"], p.adv(20), p.fields["open"], p.fields["vwap"]
    a = op.ts_rank(op.correlation(c, op.ts_sum(adv20, 14.7444), 6.00049), 20.4195)
    b = op.rank((o + c) - (vwap + o))
    return ((a < b).astype(float)) * -1


def _k87(p):  # indneutralize(adv81) -> adv81
    c, vwap, adv81 = p.fields["close"], p.fields["vwap"], p.adv(81)
    a = op.rank(op.decay_linear(op.delta((c * 0.369701) + (vwap * (1 - 0.369701)), 1.91233), 2.65461))
    b = op.ts_rank(op.decay_linear(op.abs_(op.correlation(adv81, c, 13.4132)), 4.89768), 14.4535)
    return op.elem_max(a, b) * -1


def _k88(p):
    o, l, h, c, adv60 = (p.fields["open"], p.fields["low"], p.fields["high"],
                         p.fields["close"], p.adv(60))
    a = op.rank(op.decay_linear((op.rank(o) + op.rank(l)) - (op.rank(h) + op.rank(c)), 8.06882))
    b = op.ts_rank(op.decay_linear(op.correlation(op.ts_rank(c, 8.44728),
                   op.ts_rank(adv60, 20.6966), 8.01266), 6.65053), 2.61957)
    return op.elem_min(a, b)


def _k89(p):  # indneutralize(vwap) -> vwap
    l, adv10, vwap = p.fields["low"], p.adv(10), p.fields["vwap"]
    a = op.ts_rank(op.decay_linear(op.correlation(l, adv10, 6.94279), 5.51607), 3.79744)
    b = op.ts_rank(op.decay_linear(op.delta(vwap, 3.48158), 10.1466), 15.3012)
    return a - b


def _k90(p):  # indneutralize(adv40) -> adv40
    c, adv40, l = p.fields["close"], p.adv(40), p.fields["low"]
    a = op.rank(c - op.ts_max(c, 4.66719))
    b = op.ts_rank(op.correlation(adv40, l, 5.38375), 3.21856)
    return (a ** b) * -1


def _k91(p):  # indneutralize(close) -> close
    c, v, vwap, adv30 = p.fields["close"], p.fields["volume"], p.fields["vwap"], p.adv(30)
    a = op.ts_rank(op.decay_linear(op.decay_linear(op.correlation(c, v, 9.74928), 16.398), 3.83219), 4.8667)
    b = op.rank(op.decay_linear(op.correlation(vwap, adv30, 4.01303), 2.6809))
    return (a - b) * -1


def _k92(p):
    h, l, c, o, adv30 = (p.fields["high"], p.fields["low"], p.fields["close"],
                         p.fields["open"], p.adv(30))
    cond = ((((h + l) / 2) + c) < (l + o)).astype(float)
    a = op.ts_rank(op.decay_linear(cond, 14.7221), 18.8683)
    b = op.ts_rank(op.decay_linear(op.correlation(op.rank(l), op.rank(adv30), 7.58555), 6.94024), 6.80584)
    return op.elem_min(a, b)


def _k93(p):  # indneutralize(vwap) -> vwap
    vwap, adv81, c = p.fields["vwap"], p.adv(81), p.fields["close"]
    a = op.ts_rank(op.decay_linear(op.correlation(vwap, adv81, 17.4193), 19.848), 7.54455)
    b = op.rank(op.decay_linear(op.delta((c * 0.524434) + (vwap * (1 - 0.524434)), 2.77377), 16.2664))
    return a / b


def _k94(p):
    vwap, adv60 = p.fields["vwap"], p.adv(60)
    a = op.rank(vwap - op.ts_min(vwap, 11.5783))
    b = op.ts_rank(op.correlation(op.ts_rank(vwap, 19.6462), op.ts_rank(adv60, 4.02992), 18.0926), 2.70756)
    return (a ** b) * -1


def _k95(p):
    o, h, l, adv40 = p.fields["open"], p.fields["high"], p.fields["low"], p.adv(40)
    a = op.rank(o - op.ts_min(o, 12.4105))
    inner = op.rank(op.correlation(op.ts_sum((h + l) / 2, 19.1351), op.ts_sum(adv40, 19.1351), 12.8742)) ** 5
    b = op.ts_rank(inner, 11.7584)
    return (a < b).astype(float)


def _k96(p):
    vwap, v, c, adv60 = p.fields["vwap"], p.fields["volume"], p.fields["close"], p.adv(60)
    a = op.ts_rank(op.decay_linear(op.correlation(op.rank(vwap), op.rank(v), 3.83878), 4.16783), 8.38151)
    b = op.ts_rank(op.decay_linear(op.ts_argmax(op.correlation(op.ts_rank(c, 7.45404),
                   op.ts_rank(adv60, 4.13242), 3.65459), 12.6556), 14.0365), 13.4143)
    return op.elem_max(a, b) * -1


def _k97(p):  # indneutralize -> identity
    l, vwap, adv60 = p.fields["low"], p.fields["vwap"], p.adv(60)
    a = op.rank(op.decay_linear(op.delta((l * 0.721001) + (vwap * (1 - 0.721001)), 3.3705), 20.4523))
    b = op.ts_rank(op.decay_linear(op.ts_rank(op.correlation(op.ts_rank(l, 7.87871),
                   op.ts_rank(adv60, 17.255), 4.97547), 18.5925), 15.7152), 6.71659)
    return (a - b) * -1


def _k98(p):
    vwap, adv5, o, adv15 = p.fields["vwap"], p.adv(5), p.fields["open"], p.adv(15)
    a = op.rank(op.decay_linear(op.correlation(vwap, op.ts_sum(adv5, 26.4719), 4.58418), 7.18088))
    b = op.rank(op.decay_linear(op.ts_rank(op.ts_argmin(op.correlation(op.rank(o),
                op.rank(adv15), 20.8187), 8.62571), 6.95668), 8.07206))
    return a - b


def _k99(p):
    h, l, adv60, v = p.fields["high"], p.fields["low"], p.adv(60), p.fields["volume"]
    a = op.rank(op.correlation(op.ts_sum((h + l) / 2, 19.8975), op.ts_sum(adv60, 19.8975), 8.8136))
    b = op.rank(op.correlation(l, v, 6.28259))
    return ((a < b).astype(float)) * -1


def _k100(p):  # indneutralize -> identity (all)
    c, l, h, v, adv20 = (p.fields["close"], p.fields["low"], p.fields["high"],
                         p.fields["volume"], p.adv(20))
    t1 = 1.5 * op.scale(op.rank((((c - l) - (h - c)) / (h - l)) * v))
    t2 = op.scale(op.correlation(c, op.rank(adv20), 5) - op.rank(op.ts_argmin(c, 30)))
    return 0 - (1 * ((t1 - t2) * (v / adv20)))


def _k101(p):
    c, o, h, l = p.fields["close"], p.fields["open"], p.fields["high"], p.fields["low"]
    return (c - o) / ((h - l) + 0.001)


_K101_FNS = {
    1: _k1, 2: _k2, 3: _k3, 4: _k4, 5: _k5, 6: _k6, 7: _k7, 8: _k8, 9: _k9,
    10: _k10, 11: _k11, 12: _k12, 13: _k13, 14: _k14, 15: _k15, 16: _k16,
    17: _k17, 18: _k18, 19: _k19, 20: _k20, 21: _k21, 22: _k22, 23: _k23,
    24: _k24, 25: _k25, 26: _k26, 27: _k27, 28: _k28, 29: _k29, 30: _k30,
    31: _k31, 32: _k32, 33: _k33, 34: _k34, 35: _k35, 36: _k36, 37: _k37,
    38: _k38, 39: _k39, 40: _k40, 41: _k41, 42: _k42, 43: _k43, 44: _k44,
    45: _k45, 46: _k46, 47: _k47, 48: _k48, 49: _k49, 50: _k50, 51: _k51,
    52: _k52, 53: _k53, 54: _k54, 55: _k55, 57: _k57, 58: _k58, 59: _k59,
    60: _k60, 61: _k61, 62: _k62, 63: _k63, 64: _k64, 65: _k65, 66: _k66,
    67: _k67, 68: _k68, 69: _k69, 70: _k70, 71: _k71, 72: _k72, 73: _k73,
    74: _k74, 75: _k75, 76: _k76, 77: _k77, 78: _k78, 79: _k79, 80: _k80,
    81: _k81, 82: _k82, 83: _k83, 84: _k84, 85: _k85, 86: _k86, 87: _k87,
    88: _k88, 89: _k89, 90: _k90, 91: _k91, 92: _k92, 93: _k93, 94: _k94,
    95: _k95, 96: _k96, 97: _k97, 98: _k98, 99: _k99, 100: _k100, 101: _k101,
}

# Alphas whose `indneutralize(...)` was degraded to identity (no crypto industry map).
_IND_IDS = {48, 58, 59, 63, 67, 69, 70, 76, 79, 80, 82, 87, 89, 90, 91, 93, 97, 100}


def _build_k101() -> list[AlphaDef]:
    out: list[AlphaDef] = []
    for n in range(1, 102):
        if n == 56:  # uses market cap — unavailable in crypto
            out.append(AlphaDef("K056", "K101", None, computable=False,
                                needs=["cap"],
                                reason_if_dropped="uses market cap (cap); no crypto equivalent"))
            continue
        out.append(AlphaDef(f"K{n:03d}", "K101", _K101_FNS[n],
                            needs=list(_IND) if n in _IND_IDS else []))
    return out


ALPHAS: list[AlphaDef] = _build_k101()


def computable_alphas() -> list[AlphaDef]:
    return [a for a in ALPHAS if a.computable]


def n_computable() -> int:
    return len(computable_alphas())


def n_eff() -> int:
    """Trials count for DSR: 2× computable (pays for in-sample sign-fitting)."""
    return 2 * n_computable()
