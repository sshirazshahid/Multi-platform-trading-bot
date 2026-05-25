# core/alpha_zoo/alphas_gtja.py
"""GTJA-191 (Guotai-Junan "191 Alphas") ported to the bot's operator API.

Source notation is the canonical GTJA report (cross-checked against the
reference transcription Daic115/alpha191). Each alpha is an `AlphaDef`
tagged source="GTJA". Crypto adaptations (documented per alpha via `needs`):
  * Alphas using BANCHMARKINDEX{OPEN,CLOSE} (the CSI300 benchmark) have no
    crypto-universe equivalent -> computable=False, reason "needs benchmark".
  * GTJA `MIN(A,B)`/`MAX(A,B)` between two series -> op.elem_min/elem_max;
    `TSMIN(X,d)`/`TSMAX(X,d)` -> op.ts_min/ts_max.
  * `SMA(X,N,M)` -> op.sma_m ; `WMA` -> op.wma ; `COUNT` -> op.count ;
    `HIGHDAY/LOWDAY` -> op.highday/lowday ; `REGBETA/REGRESI` -> op.regbeta/
    regresi ; ternary -> op.iif ; `MEAN` -> op.sma ; `SUM` -> op.ts_sum.

Grow by appending `_gNNN` functions and `GTJA_ALPHAS.extend([...])` per batch.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from core.alpha_zoo import operators as op
from core.alpha_zoo.registry import AlphaDef


def _seq_like(df: pd.DataFrame) -> pd.DataFrame:
    """Return a DataFrame of the same shape filled with 0,1,...,T-1 per row
    (same value broadcast across symbols), used to build the SEQUENCE(N)
    operand in G021: REGBETA(MEAN(CLOSE,6), SEQUENCE(6))."""
    return pd.DataFrame(
        np.broadcast_to(
            np.arange(len(df), dtype=float).reshape(-1, 1),
            df.shape,
        ),
        index=df.index,
        columns=df.columns,
    )

GTJA_ALPHAS: list[AlphaDef] = []


# ── Batch 001-006 (worked template) ──────────────────────────────────────
def _g1(p):
    # (-1 * CORR(RANK(DELTA(LOG(VOLUME),1)), RANK((CLOSE-OPEN)/OPEN), 6))
    v, c, o = p.fields["volume"], p.fields["close"], p.fields["open"]
    return -1 * op.correlation(op.rank(op.delta(op.log(v), 1)), op.rank((c - o) / o), 6)


def _g2(p):
    # (-1 * DELTA(((CLOSE-LOW)-(HIGH-CLOSE))/(HIGH-LOW), 1))
    c, l, h = p.fields["close"], p.fields["low"], p.fields["high"]
    return -1 * op.delta(((c - l) - (h - c)) / (h - l), 1)


def _g3(p):
    # SUM(CLOSE=DELAY(CLOSE,1)?0:CLOSE-(CLOSE>DELAY(CLOSE,1)?MIN(LOW,DELAY):MAX(HIGH,DELAY)), 6)
    c, l, h = p.fields["close"], p.fields["low"], p.fields["high"]
    dc = op.delay(c, 1)
    term = op.iif(c > dc, c - op.elem_min(l, dc), op.iif(c < dc, c - op.elem_max(h, dc), 0.0))
    return op.ts_sum(term, 6)


def _g4(p):
    # ((MEAN(C,8)+STD(C,8)<MEAN(C,2))?-1:((MEAN(C,2)<MEAN(C,8)-STD(C,8))?1:((1<=V/MEAN(V,20))?1:-1)))
    c, v = p.fields["close"], p.fields["volume"]
    m8, s8, m2 = op.sma(c, 8), op.stddev(c, 8), op.sma(c, 2)
    vt = v / op.sma(v, 20)
    mid = op.iif(m2 < (m8 - s8), 1.0, op.iif(1 <= vt, 1.0, -1.0))
    return op.iif((m8 + s8) < m2, -1.0, mid)


def _g5(p):
    # (-1 * TSMAX(CORR(TSRANK(VOLUME,5), TSRANK(HIGH,5), 5), 3))
    v, h = p.fields["volume"], p.fields["high"]
    return -1 * op.ts_max(op.correlation(op.ts_rank(v, 5), op.ts_rank(h, 5), 5), 3)


def _g6(p):
    # (RANK(SIGN(DELTA((OPEN*0.85)+(HIGH*0.15), 4))) * -1)
    o, h = p.fields["open"], p.fields["high"]
    return op.rank(op.sign(op.delta((o * 0.85) + (h * 0.15), 4))) * -1


GTJA_ALPHAS.extend([
    AlphaDef("G001", "GTJA", _g1),
    AlphaDef("G002", "GTJA", _g2),
    AlphaDef("G003", "GTJA", _g3),
    AlphaDef("G004", "GTJA", _g4),
    AlphaDef("G005", "GTJA", _g5),
    AlphaDef("G006", "GTJA", _g6),
])


# ── Batch 007-050 ─────────────────────────────────────────────────────────
def _g7(p):
    # (RANK(MAX(VWAP-CLOSE,3)) + RANK(MIN(VWAP-CLOSE,3))) * RANK(DELTA(VOLUME,3))
    vwap, c, v = p.fields["vwap"], p.fields["close"], p.fields["volume"]
    diff = vwap - c
    return (op.rank(op.ts_max(diff, 3)) + op.rank(op.ts_min(diff, 3))) * op.rank(op.delta(v, 3))


def _g8(p):
    # RANK(DELTA(((HIGH+LOW)/2)*0.2 + VWAP*0.8, 4) * -1)
    h, l, vwap = p.fields["high"], p.fields["low"], p.fields["vwap"]
    return op.rank(op.delta(((h + l) / 2) * 0.2 + vwap * 0.8, 4) * -1)


def _g9(p):
    # SMA(((HIGH+LOW)/2-(DELAY(HIGH,1)+DELAY(LOW,1))/2)*(HIGH-LOW)/VOLUME, 7, 2)
    h, l, v = p.fields["high"], p.fields["low"], p.fields["volume"]
    mid_diff = (h + l) / 2 - (op.delay(h, 1) + op.delay(l, 1)) / 2
    return op.sma_m(mid_diff * (h - l) / v, 7, 2)


def _g10(p):
    # RANK(TSMAX(((RET < 0) ? STD(RET,20) : CLOSE)^2, 5))
    c = p.fields["close"]
    ret = p.fields["returns"]
    inner = op.iif(ret < 0, op.stddev(ret, 20), c)
    return op.rank(op.ts_max(inner ** 2, 5))


def _g11(p):
    # SUM(((CLOSE-LOW)-(HIGH-CLOSE))/(HIGH-LOW)*VOLUME, 6)
    c, l, h, v = p.fields["close"], p.fields["low"], p.fields["high"], p.fields["volume"]
    return op.ts_sum(((c - l) - (h - c)) / (h - l) * v, 6)


def _g12(p):
    # RANK(OPEN - MEAN(VWAP,10)) * (-1 * RANK(ABS(CLOSE-VWAP)))
    o, c, vwap = p.fields["open"], p.fields["close"], p.fields["vwap"]
    return op.rank(o - op.sma(vwap, 10)) * (-1 * op.rank(op.abs_(c - vwap)))


def _g13(p):
    # (HIGH*LOW)^0.5 - VWAP
    h, l, vwap = p.fields["high"], p.fields["low"], p.fields["vwap"]
    return (h * l) ** 0.5 - vwap


def _g14(p):
    # CLOSE - DELAY(CLOSE, 5)
    c = p.fields["close"]
    return c - op.delay(c, 5)


def _g15(p):
    # OPEN/DELAY(CLOSE,1) - 1
    o, c = p.fields["open"], p.fields["close"]
    return o / op.delay(c, 1) - 1


def _g16(p):
    # -1 * TSMAX(RANK(CORR(RANK(VOLUME), RANK(VWAP), 5)), 5)
    v, vwap = p.fields["volume"], p.fields["vwap"]
    return -1 * op.ts_max(op.rank(op.correlation(op.rank(v), op.rank(vwap), 5)), 5)


def _g17(p):
    # RANK(VWAP - TSMAX(VWAP,15)) ^ DELTA(CLOSE,5)
    c, vwap = p.fields["close"], p.fields["vwap"]
    return op.rank(vwap - op.ts_max(vwap, 15)) ** op.delta(c, 5)


def _g18(p):
    # CLOSE / DELAY(CLOSE, 5)
    c = p.fields["close"]
    return c / op.delay(c, 5)


def _g19(p):
    # if CLOSE < DELAY(CLOSE,5): (CLOSE-DELAY(CLOSE,5))/DELAY(CLOSE,5)
    # elif CLOSE == DELAY(CLOSE,5): 0
    # else: (CLOSE-DELAY(CLOSE,5))/CLOSE
    c = p.fields["close"]
    dc5 = op.delay(c, 5)
    return op.iif(c < dc5, (c - dc5) / dc5, op.iif(c == dc5, 0.0, (c - dc5) / c))


def _g20(p):
    # (CLOSE-DELAY(CLOSE,6))/DELAY(CLOSE,6)*100
    c = p.fields["close"]
    dc6 = op.delay(c, 6)
    return (c - dc6) / dc6 * 100


def _g21(p):
    # REGBETA(MEAN(CLOSE,6), SEQUENCE(6))
    # SEQUENCE is a 0..T-1 time index broadcast across symbols
    c = p.fields["close"]
    return op.regbeta(op.sma(c, 6), _seq_like(c), 6)


def _g22(p):
    # SMA(((CLOSE-MEAN(CLOSE,6))/MEAN(CLOSE,6) - DELAY((CLOSE-MEAN(CLOSE,6))/MEAN(CLOSE,6),3)), 12, 1)
    c = p.fields["close"]
    m6 = op.sma(c, 6)
    ratio = (c - m6) / m6
    return op.sma_m(ratio - op.delay(ratio, 3), 12, 1)


def _g23(p):
    # condition = CLOSE > DELAY(CLOSE,1)
    # std = STD(CLOSE,20)
    # SMA(condition ? std : 0, 20, 1) / (SMA(condition?std:0,20,1) + SMA(!condition?std:0,20,1)) * 100
    c = p.fields["close"]
    cond = c > op.delay(c, 1)
    std = op.stddev(c, 20)
    up = op.sma_m(op.iif(cond, std, 0.0), 20, 1)
    dn = op.sma_m(op.iif(~cond, std, 0.0), 20, 1)
    return up / (up + dn) * 100


def _g24(p):
    # SMA(CLOSE-DELAY(CLOSE,5), 5, 1)
    c = p.fields["close"]
    return op.sma_m(c - op.delay(c, 5), 5, 1)


def _g25(p):
    # (-1*RANK(DELTA(CLOSE,7)*(1-RANK(DECAY_LINEAR(VOLUME/MEAN(VOLUME,20),9))))) * (1+RANK(SUM(RET,250)))
    c, v = p.fields["close"], p.fields["volume"]
    ret = p.fields["returns"]
    part1 = -1 * op.rank(op.delta(c, 7) * (1 - op.rank(op.decay_linear(v / op.sma(v, 20), 9))))
    return part1 * (1 + op.rank(op.ts_sum(ret, 250)))


def _g26(p):
    # (MEAN(CLOSE,7) - CLOSE) + CORR(VWAP, DELAY(CLOSE,5), 230)
    c, vwap = p.fields["close"], p.fields["vwap"]
    return (op.sma(c, 7) - c) + op.correlation(vwap, op.delay(c, 5), 230)


def _g27(p):
    # WMA((CLOSE-DELAY(CLOSE,3))/DELAY(CLOSE,3)*100 + (CLOSE-DELAY(CLOSE,6))/DELAY(CLOSE,6)*100, 12)
    c = p.fields["close"]
    dc3, dc6 = op.delay(c, 3), op.delay(c, 6)
    return op.wma((c - dc3) / dc3 * 100 + (c - dc6) / dc6 * 100, 12)


def _g28(p):
    # x = (CLOSE-TSMIN(LOW,9))/(TSMAX(HIGH,9)-TSMIN(LOW,9))*100
    # 3*SMA(x,3,1) - 2*SMA(SMA(x,3,1),3,1)
    c, h, l = p.fields["close"], p.fields["high"], p.fields["low"]
    lmin9 = op.ts_min(l, 9)
    hmax9 = op.ts_max(h, 9)
    x = (c - lmin9) / (hmax9 - lmin9) * 100
    s1 = op.sma_m(x, 3, 1)
    return 3 * s1 - 2 * op.sma_m(s1, 3, 1)


def _g29(p):
    # (CLOSE-DELAY(CLOSE,6))/DELAY(CLOSE,6) * VOLUME
    c, v = p.fields["close"], p.fields["volume"]
    dc6 = op.delay(c, 6)
    return (c - dc6) / dc6 * v


def _g31(p):
    # (CLOSE-MEAN(CLOSE,12))/MEAN(CLOSE,12)*100
    c = p.fields["close"]
    m12 = op.sma(c, 12)
    return (c - m12) / m12 * 100


def _g32(p):
    # -1 * SUM(RANK(CORR(RANK(HIGH),RANK(VOLUME),3)), 3)
    h, v = p.fields["high"], p.fields["volume"]
    return -1 * op.ts_sum(op.rank(op.correlation(op.rank(h), op.rank(v), 3)), 3)


def _g33(p):
    # ((-1*TSMIN(LOW,5)) + DELAY(TSMIN(LOW,5),5)) * RANK((SUM(RET,240)-SUM(RET,20))/220) * TSRANK(VOLUME,5)
    l, v = p.fields["low"], p.fields["volume"]
    ret = p.fields["returns"]
    lmin5 = op.ts_min(l, 5)
    ret_diff = (op.ts_sum(ret, 240) - op.ts_sum(ret, 20)) / 220
    return (-1 * lmin5 + op.delay(lmin5, 5)) * op.rank(ret_diff) * op.ts_rank(v, 5)


def _g34(p):
    # MEAN(CLOSE,12) / CLOSE
    c = p.fields["close"]
    return op.sma(c, 12) / c


def _g35(p):
    # MIN(RANK(DECAY_LINEAR(DELTA(OPEN,1),15)), RANK(DECAY_LINEAR(CORR(VOLUME,OPEN*0.65+OPEN*0.35,17),7))) * -1
    # Note: OPEN*0.65+OPEN*0.35 = OPEN (docstring uses both coefficients on OPEN)
    o, v = p.fields["open"], p.fields["volume"]
    p1 = op.rank(op.decay_linear(op.delta(o, 1), 15))
    p2 = op.rank(op.decay_linear(op.correlation(v, o, 17), 7))
    return op.elem_min(p1, p2) * -1


def _g36(p):
    # RANK(SUM(CORR(RANK(VOLUME),RANK(VWAP),6), 2))
    v, vwap = p.fields["volume"], p.fields["vwap"]
    return op.rank(op.ts_sum(op.correlation(op.rank(v), op.rank(vwap), 6), 2))


def _g37(p):
    # -1 * RANK((SUM(OPEN,5)*SUM(RET,5)) - DELAY(SUM(OPEN,5)*SUM(RET,5),10))
    o = p.fields["open"]
    ret = p.fields["returns"]
    prod = op.ts_sum(o, 5) * op.ts_sum(ret, 5)
    return -1 * op.rank(prod - op.delay(prod, 10))


def _g38(p):
    # (MEAN(HIGH,20) < HIGH) ? (-1 * DELTA(HIGH,2)) : 0
    h = p.fields["high"]
    return op.iif(op.sma(h, 20) < h, -1 * op.delta(h, 2), 0.0)


def _g39(p):
    # (RANK(DECAY_LINEAR(DELTA(CLOSE,2),8))
    #  - RANK(DECAY_LINEAR(CORR(VWAP*0.3+OPEN*0.7, SUM(MEAN(VOLUME,180),37), 14), 12))) * -1
    c, vwap, o, v = p.fields["close"], p.fields["vwap"], p.fields["open"], p.fields["volume"]
    p1 = op.rank(op.decay_linear(op.delta(c, 2), 8))
    price_mix = vwap * 0.3 + o * 0.7
    vol_sum = op.ts_sum(op.sma(v, 180), 37)
    p2 = op.rank(op.decay_linear(op.correlation(price_mix, vol_sum, 14), 12))
    return (p1 - p2) * -1


def _g40(p):
    # SUM(CLOSE > DELAY(CLOSE,1) ? VOLUME : 0, 26)
    # / SUM(CLOSE <= DELAY(CLOSE,1) ? VOLUME : 0, 26) * 100
    c, v = p.fields["close"], p.fields["volume"]
    dc1 = op.delay(c, 1)
    up = op.ts_sum(op.iif(c > dc1, v, 0.0), 26)
    dn = op.ts_sum(op.iif(c <= dc1, v, 0.0), 26)
    return up / dn * 100


def _g41(p):
    # RANK(TSMAX(DELTA(VWAP,3),5)) * -1
    vwap = p.fields["vwap"]
    return op.rank(op.ts_max(op.delta(vwap, 3), 5)) * -1


def _g42(p):
    # -1 * RANK(STD(HIGH,10)) * CORR(HIGH,VOLUME,10)
    h, v = p.fields["high"], p.fields["volume"]
    return -1 * op.rank(op.stddev(h, 10)) * op.correlation(h, v, 10)


def _g43(p):
    # SUM(CLOSE > DELAY(CLOSE,1) ? VOLUME : (CLOSE < DELAY(CLOSE,1) ? -VOLUME : 0), 6)
    c, v = p.fields["close"], p.fields["volume"]
    dc1 = op.delay(c, 1)
    term = op.iif(c > dc1, v, op.iif(c < dc1, -v, 0.0))
    return op.ts_sum(term, 6)


def _g44(p):
    # TSRANK(DECAY_LINEAR(CORR(LOW,MEAN(VOLUME,10),7),6),4)
    # + TSRANK(DECAY_LINEAR(DELTA(VWAP,3),10),15)
    l, v, vwap = p.fields["low"], p.fields["volume"], p.fields["vwap"]
    left = op.ts_rank(op.decay_linear(op.correlation(l, op.sma(v, 10), 7), 6), 4)
    right = op.ts_rank(op.decay_linear(op.delta(vwap, 3), 10), 15)
    return left + right


def _g45(p):
    # RANK(DELTA(CLOSE*0.6+OPEN*0.4,1)) * RANK(CORR(VWAP,MEAN(VOLUME,150),15))
    c, o, v, vwap = p.fields["close"], p.fields["open"], p.fields["volume"], p.fields["vwap"]
    return op.rank(op.delta(c * 0.6 + o * 0.4, 1)) * op.rank(op.correlation(vwap, op.sma(v, 150), 15))


def _g46(p):
    # (MEAN(CLOSE,3)+MEAN(CLOSE,6)+MEAN(CLOSE,12)+MEAN(CLOSE,24)) / (4*CLOSE)
    c = p.fields["close"]
    return (op.sma(c, 3) + op.sma(c, 6) + op.sma(c, 12) + op.sma(c, 24)) / (4 * c)


def _g47(p):
    # SMA((TSMAX(HIGH,6)-CLOSE)/(TSMAX(HIGH,6)-TSMIN(LOW,6))*100, 9, 1)
    c, h, l = p.fields["close"], p.fields["high"], p.fields["low"]
    hmax6 = op.ts_max(h, 6)
    lmin6 = op.ts_min(l, 6)
    return op.sma_m((hmax6 - c) / (hmax6 - lmin6) * 100, 9, 1)


def _g48(p):
    # -1 * (RANK(SIGN(CLOSE-DELAY(CLOSE,1)) + SIGN(DELAY(CLOSE,1)-DELAY(CLOSE,2))
    #        + SIGN(DELAY(CLOSE,2)-DELAY(CLOSE,3))) * SUM(VOLUME,5)) / SUM(VOLUME,20)
    c, v = p.fields["close"], p.fields["volume"]
    dc1, dc2, dc3 = op.delay(c, 1), op.delay(c, 2), op.delay(c, 3)
    signs = op.sign(c - dc1) + op.sign(dc1 - dc2) + op.sign(dc2 - dc3)
    return -1 * (op.rank(signs) * op.ts_sum(v, 5)) / op.ts_sum(v, 20)


def _g49(p):
    # condition = (HIGH+LOW) >= (DELAY(HIGH,1)+DELAY(LOW,1))
    # part = MAX(ABS(HIGH-DELAY(HIGH,1)), ABS(LOW-DELAY(LOW,1)))
    # s_false = SUM(condition ? 0 : part, 12)
    # s_true  = SUM(condition ? part : 0, 12)    [~condition → false branch uses part]
    # s_false / (s_false + s_true)
    # i.e.: SUM(~condition ? part : 0, 12) / (SUM(~condition?part:0,12) + SUM(condition?part:0,12))
    h, l = p.fields["high"], p.fields["low"]
    dh1, dl1 = op.delay(h, 1), op.delay(l, 1)
    cond = (h + l) >= (dh1 + dl1)
    part = op.elem_max(op.abs_(h - dh1), op.abs_(l - dl1))
    s_false = op.ts_sum(op.iif(~cond, part, 0.0), 12)
    s_true = op.ts_sum(op.iif(cond, part, 0.0), 12)
    return s_false / (s_false + s_true)


def _g50(p):
    # condition1 = (HIGH+LOW) <= (DELAY(HIGH,1)+DELAY(LOW,1))
    # condition2 = (HIGH+LOW) >= (DELAY(HIGH,1)+DELAY(LOW,1))
    # part = MAX(ABS(HIGH-DELAY(HIGH,1)), ABS(LOW-DELAY(LOW,1)))
    # p1 = SUM(condition1 ? 0 : part, 12)   i.e. SUM(~condition1 ? part : 0, 12)
    # p2 = SUM(condition2 ? 0 : part, 12)   i.e. SUM(~condition2 ? part : 0, 12)
    # p1/(p1+p2) - p2/(p2+p1)  = (p1-p2)/(p1+p2)
    h, l = p.fields["high"], p.fields["low"]
    dh1, dl1 = op.delay(h, 1), op.delay(l, 1)
    cond1 = (h + l) <= (dh1 + dl1)
    cond2 = (h + l) >= (dh1 + dl1)
    part = op.elem_max(op.abs_(h - dh1), op.abs_(l - dl1))
    p1 = op.ts_sum(op.iif(~cond1, part, 0.0), 12)
    p2 = op.ts_sum(op.iif(~cond2, part, 0.0), 12)
    return (p1 - p2) / (p1 + p2)


GTJA_ALPHAS.extend([
    AlphaDef("G007", "GTJA", _g7),
    AlphaDef("G008", "GTJA", _g8),
    AlphaDef("G009", "GTJA", _g9),
    AlphaDef("G010", "GTJA", _g10),
    AlphaDef("G011", "GTJA", _g11),
    AlphaDef("G012", "GTJA", _g12),
    AlphaDef("G013", "GTJA", _g13),
    AlphaDef("G014", "GTJA", _g14),
    AlphaDef("G015", "GTJA", _g15),
    AlphaDef("G016", "GTJA", _g16),
    AlphaDef("G017", "GTJA", _g17),
    AlphaDef("G018", "GTJA", _g18),
    AlphaDef("G019", "GTJA", _g19),
    AlphaDef("G020", "GTJA", _g20),
    AlphaDef("G021", "GTJA", _g21),
    AlphaDef("G022", "GTJA", _g22),
    AlphaDef("G023", "GTJA", _g23),
    AlphaDef("G024", "GTJA", _g24),
    AlphaDef("G025", "GTJA", _g25),
    AlphaDef("G026", "GTJA", _g26),
    AlphaDef("G027", "GTJA", _g27),
    AlphaDef("G028", "GTJA", _g28),
    AlphaDef("G029", "GTJA", _g29),
    AlphaDef("G030", "GTJA", None, computable=False, needs=["fama-french-factors"],
             reason_if_dropped="needs Fama-French MKT/SMB/HML factors (no crypto equivalent)"),
    AlphaDef("G031", "GTJA", _g31),
    AlphaDef("G032", "GTJA", _g32),
    AlphaDef("G033", "GTJA", _g33),
    AlphaDef("G034", "GTJA", _g34),
    AlphaDef("G035", "GTJA", _g35),
    AlphaDef("G036", "GTJA", _g36),
    AlphaDef("G037", "GTJA", _g37),
    AlphaDef("G038", "GTJA", _g38),
    AlphaDef("G039", "GTJA", _g39),
    AlphaDef("G040", "GTJA", _g40),
    AlphaDef("G041", "GTJA", _g41),
    AlphaDef("G042", "GTJA", _g42),
    AlphaDef("G043", "GTJA", _g43),
    AlphaDef("G044", "GTJA", _g44),
    AlphaDef("G045", "GTJA", _g45),
    AlphaDef("G046", "GTJA", _g46),
    AlphaDef("G047", "GTJA", _g47),
    AlphaDef("G048", "GTJA", _g48),
    AlphaDef("G049", "GTJA", _g49),
    AlphaDef("G050", "GTJA", _g50),
])
