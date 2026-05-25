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


# ── Batch 051-095 ─────────────────────────────────────────────────────────
def _g51(p):
    # condition1 = (HIGH+LOW) <= (DELAY(HIGH,1)+DELAY(LOW,1))
    # condition2 = (HIGH+LOW) >= (DELAY(HIGH,1)+DELAY(LOW,1))
    # part = MAX(ABS(HIGH-DELAY(HIGH,1)), ABS(LOW-DELAY(LOW,1)))
    # SUM(condition1?0:part, 12) / (SUM(condition1?0:part,12) + SUM(condition2?0:part,12))
    h, l = p.fields["high"], p.fields["low"]
    dh1, dl1 = op.delay(h, 1), op.delay(l, 1)
    cond1 = (h + l) <= (dh1 + dl1)
    cond2 = (h + l) >= (dh1 + dl1)
    part = op.elem_max(op.abs_(h - dh1), op.abs_(l - dl1))
    p1 = op.ts_sum(op.iif(~cond1, part, 0.0), 12)
    p2 = op.ts_sum(op.iif(~cond2, part, 0.0), 12)
    return p1 / (p1 + p2)


def _g52(p):
    # SUM(MAX(0, HIGH-DELAY((HIGH+LOW+CLOSE)/3,1)),26) /
    # SUM(MAX(0, DELAY((HIGH+LOW+CLOSE)/3,1)-LOW),26) * 100
    h, l, c = p.fields["high"], p.fields["low"], p.fields["close"]
    pivot = op.delay((h + l + c) / 3, 1)
    num = op.ts_sum(op.elem_max(h - pivot, pd.DataFrame(0.0, index=h.index, columns=h.columns)), 26)
    den = op.ts_sum(op.elem_max(pivot - l, pd.DataFrame(0.0, index=h.index, columns=h.columns)), 26)
    return num / den * 100


def _g53(p):
    # COUNT(CLOSE > DELAY(CLOSE,1), 12) / 12 * 100
    c = p.fields["close"]
    return op.count(c > op.delay(c, 1), 12) / 12 * 100


def _g54(p):
    # -1 * RANK(STD(ABS(CLOSE-OPEN)+(CLOSE-OPEN), 10) + CORR(CLOSE,OPEN,10))
    c, o = p.fields["close"], p.fields["open"]
    inner = op.abs_(c - o) + (c - o)
    return -1 * op.rank(op.stddev(inner, 10) + op.correlation(c, o, 10))


def _g55(p):
    # SUM(
    #   16*(CLOSE-DELAY(CLOSE,1)+(CLOSE-OPEN)/2+DELAY(CLOSE,1)-DELAY(OPEN,1)) /
    #   (part1>part2 & part1>part3 ? part1+part2/2+part4/4 :
    #    (part2>part3 & part2>part1 ? part2+part1/2+part4/4 : part3+part4/4))
    #   * MAX(part1, part2)
    # , 20)
    # where part1=ABS(HIGH-DELAY(CLOSE,1)), part2=ABS(LOW-DELAY(CLOSE,1)),
    #       part3=ABS(HIGH-DELAY(LOW,1)),   part4=ABS(DELAY(CLOSE,1)-DELAY(OPEN,1))
    c, o, h, l = p.fields["close"], p.fields["open"], p.fields["high"], p.fields["low"]
    dc1, do1, dl1 = op.delay(c, 1), op.delay(o, 1), op.delay(l, 1)
    part1 = op.abs_(h - dc1)
    part2 = op.abs_(l - dc1)
    part3 = op.abs_(h - dl1)
    part4 = op.abs_(dc1 - do1)
    var1 = part1 + part2 / 2 + part4 / 4
    var2 = part2 + part1 / 2 + part4 / 4
    var3 = part3 + part4 / 4
    denom = op.iif((part1 > part2) & (part1 > part3), var1,
                   op.iif((part2 > part3) & (part2 > part1), var2, var3))
    numer = 16 * (c - dc1 + (c - o) / 2 + dc1 - do1)
    return op.ts_sum(numer / denom * op.elem_max(part1, part2), 20)


def _g56(p):
    # (RANK(OPEN-TSMIN(OPEN,12)) < RANK(RANK(CORR(SUM((HIGH+LOW)/2,19),SUM(MEAN(VOLUME,40),19),13))^5))
    # boolean result → cast to float
    o, h, l, v = p.fields["open"], p.fields["high"], p.fields["low"], p.fields["volume"]
    lhs = op.rank(o - op.ts_min(o, 12))
    rhs = op.rank(op.rank(op.correlation(op.ts_sum((h + l) / 2, 19),
                                          op.ts_sum(op.sma(v, 40), 19), 13)) ** 5)
    return (lhs < rhs).astype(float)


def _g57(p):
    # SMA((CLOSE-TSMIN(LOW,9))/(TSMAX(HIGH,9)-TSMIN(LOW,9))*100, 3, 1)
    c, h, l = p.fields["close"], p.fields["high"], p.fields["low"]
    lmin9 = op.ts_min(l, 9)
    hmax9 = op.ts_max(h, 9)
    return op.sma_m((c - lmin9) / (hmax9 - lmin9) * 100, 3, 1)


def _g58(p):
    # COUNT(CLOSE > DELAY(CLOSE,1), 20) / 20 * 100
    c = p.fields["close"]
    return op.count(c > op.delay(c, 1), 20) / 20 * 100


def _g59(p):
    # SUM(CLOSE=DELAY(CLOSE,1)?0:CLOSE-(CLOSE>DELAY(CLOSE,1)?MIN(LOW,DELAY(CLOSE,1)):MAX(HIGH,DELAY(CLOSE,1))), 20)
    c, l, h = p.fields["close"], p.fields["low"], p.fields["high"]
    dc1 = op.delay(c, 1)
    term = op.iif(c > dc1, c - op.elem_min(l, dc1), op.iif(c < dc1, c - op.elem_max(h, dc1), 0.0))
    return op.ts_sum(term, 20)


def _g60(p):
    # SUM(((CLOSE-LOW)-(HIGH-CLOSE))/(HIGH-LOW)*VOLUME, 20)
    c, l, h, v = p.fields["close"], p.fields["low"], p.fields["high"], p.fields["volume"]
    return op.ts_sum(((c - l) - (h - c)) / (h - l) * v, 20)


def _g61(p):
    # MAX(RANK(DECAYLINEAR(DELTA(VWAP,1),12)),
    #     RANK(DECAYLINEAR(RANK(CORR(LOW,MEAN(VOLUME,80),8)),17))) * -1
    vwap, l, v = p.fields["vwap"], p.fields["low"], p.fields["volume"]
    left = op.rank(op.decay_linear(op.delta(vwap, 1), 12))
    right = op.rank(op.decay_linear(op.rank(op.correlation(l, op.sma(v, 80), 8)), 17))
    return op.elem_max(left, right) * -1


def _g62(p):
    # -1 * CORR(HIGH, RANK(VOLUME), 5)
    h, v = p.fields["high"], p.fields["volume"]
    return -1 * op.correlation(h, op.rank(v), 5)


def _g63(p):
    # SMA(MAX(CLOSE-DELAY(CLOSE,1),0),6,1) / SMA(ABS(CLOSE-DELAY(CLOSE,1)),6,1) * 100
    c = p.fields["close"]
    diff = c - op.delay(c, 1)
    zero = pd.DataFrame(0.0, index=c.index, columns=c.columns)
    return op.sma_m(op.elem_max(diff, zero), 6, 1) / op.sma_m(op.abs_(diff), 6, 1) * 100


def _g64(p):
    # MAX(RANK(DECAYLINEAR(CORR(RANK(VWAP),RANK(VOLUME),4),4)),
    #     RANK(DECAYLINEAR(TSMAX(CORR(RANK(CLOSE),RANK(MEAN(VOLUME,60)),4),13),14))) * -1
    c, vwap, v = p.fields["close"], p.fields["vwap"], p.fields["volume"]
    left = op.rank(op.decay_linear(op.correlation(op.rank(vwap), op.rank(v), 4), 4))
    right = op.rank(op.decay_linear(op.ts_max(op.correlation(op.rank(c), op.rank(op.sma(v, 60)), 4), 13), 14))
    return op.elem_max(left, right) * -1


def _g65(p):
    # MEAN(CLOSE,6) / CLOSE
    c = p.fields["close"]
    return op.sma(c, 6) / c


def _g66(p):
    # (CLOSE-MEAN(CLOSE,6))/MEAN(CLOSE,6)*100
    c = p.fields["close"]
    m6 = op.sma(c, 6)
    return (c - m6) / m6 * 100


def _g67(p):
    # SMA(MAX(CLOSE-DELAY(CLOSE,1),0),24,1) / SMA(ABS(CLOSE-DELAY(CLOSE,1)),24,1) * 100
    c = p.fields["close"]
    diff = c - op.delay(c, 1)
    zero = pd.DataFrame(0.0, index=c.index, columns=c.columns)
    return op.sma_m(op.elem_max(diff, zero), 24, 1) / op.sma_m(op.abs_(diff), 24, 1) * 100


def _g68(p):
    # SMA(((HIGH+LOW)/2-(DELAY(HIGH,1)+DELAY(LOW,1))/2)*(HIGH-LOW)/VOLUME, 15, 2)
    h, l, v = p.fields["high"], p.fields["low"], p.fields["volume"]
    mid_diff = (h + l) / 2 - (op.delay(h, 1) + op.delay(l, 1)) / 2
    return op.sma_m(mid_diff * (h - l) / v, 15, 2)


def _g69(p):
    # DTM = OPEN<=DELAY(OPEN,1)?0:MAX(HIGH-OPEN, OPEN-DELAY(OPEN,1))
    # DBM = OPEN>=DELAY(OPEN,1)?0:MAX(OPEN-LOW, OPEN-DELAY(OPEN,1))
    # SUM(DTM,20)>SUM(DBM,20) → (DTM_sum-DBM_sum)/DTM_sum
    # SUM(DTM,20)==SUM(DBM,20) → 0
    # else → (DTM_sum-DBM_sum)/DBM_sum
    o, h, l = p.fields["open"], p.fields["high"], p.fields["low"]
    do1 = op.delay(o, 1)
    zero = pd.DataFrame(0.0, index=o.index, columns=o.columns)
    dtm = op.iif(o <= do1, zero, op.elem_max(h - o, o - do1))
    dbm = op.iif(o >= do1, zero, op.elem_max(o - l, o - do1))
    s_dtm = op.ts_sum(dtm, 20)
    s_dbm = op.ts_sum(dbm, 20)
    diff = s_dtm - s_dbm
    return op.iif(s_dtm > s_dbm, diff / s_dtm,
                  op.iif(s_dtm == s_dbm, zero, diff / s_dbm))


def _g70(p):
    # STD(AMOUNT, 6)   — AMOUNT proxied as close * volume
    c, v = p.fields["close"], p.fields["volume"]
    amount = c * v
    return op.stddev(amount, 6)


def _g71(p):
    # (CLOSE-MEAN(CLOSE,24))/MEAN(CLOSE,24)*100
    c = p.fields["close"]
    m24 = op.sma(c, 24)
    return (c - m24) / m24 * 100


def _g72(p):
    # SMA((TSMAX(HIGH,6)-CLOSE)/(TSMAX(HIGH,6)-TSMIN(LOW,6))*100, 15, 1)
    c, h, l = p.fields["close"], p.fields["high"], p.fields["low"]
    hmax6 = op.ts_max(h, 6)
    lmin6 = op.ts_min(l, 6)
    return op.sma_m((hmax6 - c) / (hmax6 - lmin6) * 100, 15, 1)


def _g73(p):
    # -1*TSRANK(DECAYLINEAR(DECAYLINEAR(CORR(CLOSE,VOLUME,10),16),4),5)
    # - RANK(DECAYLINEAR(CORR(VWAP,MEAN(VOLUME,30),4),3))
    c, v, vwap = p.fields["close"], p.fields["volume"], p.fields["vwap"]
    left = -1 * op.ts_rank(op.decay_linear(op.decay_linear(op.correlation(c, v, 10), 16), 4), 5)
    right = op.rank(op.decay_linear(op.correlation(vwap, op.sma(v, 30), 4), 3))
    return left - right


def _g74(p):
    # RANK(CORR(SUM(LOW*0.35+VWAP*0.65,20), SUM(MEAN(VOLUME,40),20), 7))
    # + RANK(CORR(RANK(VWAP), RANK(VOLUME), 6))
    l, vwap, v = p.fields["low"], p.fields["vwap"], p.fields["volume"]
    left = op.rank(op.correlation(op.ts_sum(l * 0.35 + vwap * 0.65, 20),
                                   op.ts_sum(op.sma(v, 40), 20), 7))
    right = op.rank(op.correlation(op.rank(vwap), op.rank(v), 6))
    return left + right


def _g76(p):
    # STD(ABS((CLOSE/DELAY(CLOSE,1)-1))/VOLUME, 20) / MEAN(ABS((CLOSE/DELAY(CLOSE,1)-1))/VOLUME, 20)
    c, v = p.fields["close"], p.fields["volume"]
    x = op.abs_((c / op.delay(c, 1)) - 1) / v
    return op.stddev(x, 20) / op.sma(x, 20)


def _g77(p):
    # MIN(RANK(DECAYLINEAR(((H+L)/2+H)-(VWAP+H),20)),
    #     RANK(DECAYLINEAR(CORR((H+L)/2, MEAN(VOLUME,40),3),6)))
    h, l, vwap, v = p.fields["high"], p.fields["low"], p.fields["vwap"], p.fields["volume"]
    inner = ((h + l) / 2 + h) - (vwap + h)  # simplifies to (h+l)/2 - vwap; kept verbatim
    left = op.rank(op.decay_linear(inner, 20))
    right = op.rank(op.decay_linear(op.correlation((h + l) / 2, op.sma(v, 40), 3), 6))
    return op.elem_min(left, right)


def _g78(p):
    # ((HIGH+LOW+CLOSE)/3 - MA((HIGH+LOW+CLOSE)/3,12)) /
    # (0.015 * MEAN(ABS(CLOSE - MEAN((HIGH+LOW+CLOSE)/3,12)), 12))
    c, h, l = p.fields["close"], p.fields["high"], p.fields["low"]
    tp = (h + l + c) / 3
    ma_tp = op.sma(tp, 12)
    return (tp - ma_tp) / (0.015 * op.sma(op.abs_(c - ma_tp), 12))


def _g79(p):
    # SMA(MAX(CLOSE-DELAY(CLOSE,1),0),12,1) / SMA(ABS(CLOSE-DELAY(CLOSE,1)),12,1) * 100
    c = p.fields["close"]
    diff = c - op.delay(c, 1)
    zero = pd.DataFrame(0.0, index=c.index, columns=c.columns)
    return op.sma_m(op.elem_max(diff, zero), 12, 1) / op.sma_m(op.abs_(diff), 12, 1) * 100


def _g80(p):
    # (VOLUME-DELAY(VOLUME,5))/DELAY(VOLUME,5)*100
    v = p.fields["volume"]
    dv5 = op.delay(v, 5)
    return (v - dv5) / dv5 * 100


def _g81(p):
    # SMA(VOLUME, 21, 2)
    v = p.fields["volume"]
    return op.sma_m(v, 21, 2)


def _g82(p):
    # SMA((TSMAX(HIGH,6)-CLOSE)/(TSMAX(HIGH,6)-TSMIN(LOW,6))*100, 20, 1)
    c, h, l = p.fields["close"], p.fields["high"], p.fields["low"]
    hmax6 = op.ts_max(h, 6)
    lmin6 = op.ts_min(l, 6)
    return op.sma_m((hmax6 - c) / (hmax6 - lmin6) * 100, 20, 1)


def _g83(p):
    # -1 * RANK(COVARIANCE(RANK(HIGH), RANK(VOLUME), 5))
    h, v = p.fields["high"], p.fields["volume"]
    return -1 * op.rank(op.covariance(op.rank(h), op.rank(v), 5))


def _g84(p):
    # SUM(CLOSE>DELAY(CLOSE,1)?VOLUME:(CLOSE<DELAY(CLOSE,1)?-VOLUME:0), 20)
    c, v = p.fields["close"], p.fields["volume"]
    dc1 = op.delay(c, 1)
    term = op.iif(c > dc1, v, op.iif(c < dc1, -v, 0.0))
    return op.ts_sum(term, 20)


def _g85(p):
    # TSRANK(VOLUME/MEAN(VOLUME,20),20) * TSRANK(-1*DELTA(CLOSE,7),8)
    c, v = p.fields["close"], p.fields["volume"]
    return op.ts_rank(v / op.sma(v, 20), 20) * op.ts_rank(-1 * op.delta(c, 7), 8)


def _g86(p):
    # part1 = (DELAY(CLOSE,20)-DELAY(CLOSE,10))/10
    # part2 = (DELAY(CLOSE,10)-CLOSE)/10
    # (part1-part2)>0.25 ? -1 : ((part1-part2)<0 ? 1 : -1*(CLOSE-DELAY(CLOSE,1)))
    c = p.fields["close"]
    part1 = (op.delay(c, 20) - op.delay(c, 10)) / 10
    part2 = (op.delay(c, 10) - c) / 10
    diff = part1 - part2
    return op.iif(diff > 0.25, -1.0,
                  op.iif(diff < 0, 1.0, -1 * (c - op.delay(c, 1))))


def _g87(p):
    # (RANK(DECAYLINEAR(DELTA(VWAP,4),7))
    #  + TSRANK(DECAYLINEAR((LOW-VWAP)/(OPEN-(HIGH+LOW)/2),11),7)) * -1
    # Note: (LOW*0.9+LOW*0.1) = LOW; simplified to LOW-VWAP
    vwap, l, o, h = p.fields["vwap"], p.fields["low"], p.fields["open"], p.fields["high"]
    left = op.rank(op.decay_linear(op.delta(vwap, 4), 7))
    right = op.ts_rank(op.decay_linear((l - vwap) / (o - (h + l) / 2), 11), 7)
    return (left + right) * -1


def _g88(p):
    # (CLOSE-DELAY(CLOSE,20))/DELAY(CLOSE,20)*100
    c = p.fields["close"]
    dc20 = op.delay(c, 20)
    return (c - dc20) / dc20 * 100


def _g89(p):
    # 2*(SMA(CLOSE,13,2)-SMA(CLOSE,27,2)-SMA(SMA(CLOSE,13,2)-SMA(CLOSE,27,2),10,2))
    c = p.fields["close"]
    fast = op.sma_m(c, 13, 2)
    slow = op.sma_m(c, 27, 2)
    diff = fast - slow
    return 2 * (diff - op.sma_m(diff, 10, 2))


def _g90(p):
    # RANK(CORR(RANK(VWAP), RANK(VOLUME), 5)) * -1
    vwap, v = p.fields["vwap"], p.fields["volume"]
    return op.rank(op.correlation(op.rank(vwap), op.rank(v), 5)) * -1


def _g91(p):
    # (RANK(CLOSE-TSMAX(CLOSE,5)) * RANK(CORR(MEAN(VOLUME,40),LOW,5))) * -1
    c, v, l = p.fields["close"], p.fields["volume"], p.fields["low"]
    return op.rank(c - op.ts_max(c, 5)) * op.rank(op.correlation(op.sma(v, 40), l, 5)) * -1


def _g92(p):
    # MAX(RANK(DECAYLINEAR(DELTA(CLOSE*0.35+VWAP*0.65,2),3)),
    #     TSRANK(DECAYLINEAR(ABS(CORR(MEAN(VOLUME,180),CLOSE,13)),5),15)) * -1
    c, vwap, v = p.fields["close"], p.fields["vwap"], p.fields["volume"]
    left = op.rank(op.decay_linear(op.delta(c * 0.35 + vwap * 0.65, 2), 3))
    right = op.ts_rank(op.decay_linear(op.abs_(op.correlation(op.sma(v, 180), c, 13)), 5), 15)
    return op.elem_max(left, right) * -1


def _g93(p):
    # SUM(OPEN>=DELAY(OPEN,1)?0:MAX(OPEN-LOW,OPEN-DELAY(OPEN,1)), 20)
    o, l = p.fields["open"], p.fields["low"]
    do1 = op.delay(o, 1)
    zero = pd.DataFrame(0.0, index=o.index, columns=o.columns)
    term = op.iif(o >= do1, zero, op.elem_max(o - l, o - do1))
    return op.ts_sum(term, 20)


def _g94(p):
    # SUM(CLOSE>DELAY(CLOSE,1)?VOLUME:(CLOSE<DELAY(CLOSE,1)?-VOLUME:0), 30)
    c, v = p.fields["close"], p.fields["volume"]
    dc1 = op.delay(c, 1)
    term = op.iif(c > dc1, v, op.iif(c < dc1, -v, 0.0))
    return op.ts_sum(term, 30)


def _g95(p):
    # STD(AMOUNT, 20)   — AMOUNT proxied as close * volume
    c, v = p.fields["close"], p.fields["volume"]
    amount = c * v
    return op.stddev(amount, 20)


GTJA_ALPHAS.extend([
    AlphaDef("G051", "GTJA", _g51),
    AlphaDef("G052", "GTJA", _g52),
    AlphaDef("G053", "GTJA", _g53),
    AlphaDef("G054", "GTJA", _g54),
    AlphaDef("G055", "GTJA", _g55),
    AlphaDef("G056", "GTJA", _g56),
    AlphaDef("G057", "GTJA", _g57),
    AlphaDef("G058", "GTJA", _g58),
    AlphaDef("G059", "GTJA", _g59),
    AlphaDef("G060", "GTJA", _g60),
    AlphaDef("G061", "GTJA", _g61),
    AlphaDef("G062", "GTJA", _g62),
    AlphaDef("G063", "GTJA", _g63),
    AlphaDef("G064", "GTJA", _g64),
    AlphaDef("G065", "GTJA", _g65),
    AlphaDef("G066", "GTJA", _g66),
    AlphaDef("G067", "GTJA", _g67),
    AlphaDef("G068", "GTJA", _g68),
    AlphaDef("G069", "GTJA", _g69),
    AlphaDef("G070", "GTJA", _g70, needs=["amount~close*volume"]),
    AlphaDef("G071", "GTJA", _g71),
    AlphaDef("G072", "GTJA", _g72),
    AlphaDef("G073", "GTJA", _g73),
    AlphaDef("G074", "GTJA", _g74),
    AlphaDef("G075", "GTJA", None, computable=False, needs=["benchmark"],
             reason_if_dropped="needs benchmark index BANCHMARKINDEXCLOSE/OPEN (no crypto equivalent)"),
    AlphaDef("G076", "GTJA", _g76),
    AlphaDef("G077", "GTJA", _g77),
    AlphaDef("G078", "GTJA", _g78),
    AlphaDef("G079", "GTJA", _g79),
    AlphaDef("G080", "GTJA", _g80),
    AlphaDef("G081", "GTJA", _g81),
    AlphaDef("G082", "GTJA", _g82),
    AlphaDef("G083", "GTJA", _g83),
    AlphaDef("G084", "GTJA", _g84),
    AlphaDef("G085", "GTJA", _g85),
    AlphaDef("G086", "GTJA", _g86),
    AlphaDef("G087", "GTJA", _g87),
    AlphaDef("G088", "GTJA", _g88),
    AlphaDef("G089", "GTJA", _g89),
    AlphaDef("G090", "GTJA", _g90),
    AlphaDef("G091", "GTJA", _g91),
    AlphaDef("G092", "GTJA", _g92),
    AlphaDef("G093", "GTJA", _g93),
    AlphaDef("G094", "GTJA", _g94),
    AlphaDef("G095", "GTJA", _g95, needs=["amount~close*volume"]),
])


# ── Batch 096-140 ─────────────────────────────────────────────────────────

def _g96(p):
    # SMA(SMA((CLOSE-TSMIN(LOW,9))/(TSMAX(HIGH,9)-TSMIN(LOW,9))*100,3,1),3,1)
    c, h, l = p.fields["close"], p.fields["high"], p.fields["low"]
    numer = c - op.ts_min(l, 9)
    denom = op.ts_max(h, 9) - op.ts_min(l, 9)
    inner = numer / denom * 100
    return op.sma_m(op.sma_m(inner, 3, 1), 3, 1)


def _g97(p):
    # STD(VOLUME,10)
    return op.stddev(p.fields["volume"], 10)


def _g98(p):
    # ((DELTA((SUM(CLOSE,100)/100),100)/DELAY(CLOSE,100)) <= 0.05)
    # ? (-1*(CLOSE-TSMIN(CLOSE,100))) : (-1*DELTA(CLOSE,3))
    c = p.fields["close"]
    part1 = op.delta(op.ts_sum(c, 100) / 100, 100) / op.delay(c, 100)
    return op.iif(part1 <= 0.05, -1 * (c - op.ts_min(c, 100)), -1 * op.delta(c, 3))


def _g99(p):
    # (-1 * RANK(COVARIANCE(RANK(CLOSE), RANK(VOLUME), 5)))
    c, v = p.fields["close"], p.fields["volume"]
    return -1 * op.rank(op.covariance(op.rank(c), op.rank(v), 5))


def _g100(p):
    # STD(VOLUME,20)
    return op.stddev(p.fields["volume"], 20)


def _g101(p):
    # (RANK(CORR(CLOSE,SUM(MEAN(VOLUME,30),37),15)) <
    #  RANK(CORR(RANK((HIGH*0.1+VWAP*0.9)),RANK(VOLUME),11))) * -1
    c, h, vwap, v = (p.fields["close"], p.fields["high"],
                     p.fields["vwap"], p.fields["volume"])
    lhs = op.rank(op.correlation(c, op.ts_sum(op.sma(v, 30), 37), 15))
    rhs = op.rank(op.correlation(op.rank(h * 0.1 + vwap * 0.9), op.rank(v), 11))
    return (lhs < rhs).astype(float) * -1


def _g102(p):
    # SMA(MAX(VOLUME-DELAY(VOLUME,1),0),6,1) / SMA(ABS(VOLUME-DELAY(VOLUME,1)),6,1) * 100
    v = p.fields["volume"]
    dv = v - op.delay(v, 1)
    zero = pd.DataFrame(0.0, index=v.index, columns=v.columns)
    return op.sma_m(op.elem_max(dv, zero), 6, 1) / op.sma_m(op.abs_(dv), 6, 1) * 100


def _g103(p):
    # ((20-LOWDAY(LOW,20))/20)*100
    l = p.fields["low"]
    return (20 - op.lowday(l, 20)) / 20 * 100


def _g104(p):
    # (-1 * (DELTA(CORR(HIGH,VOLUME,5),5) * RANK(STD(CLOSE,20))))
    c, h, v = p.fields["close"], p.fields["high"], p.fields["volume"]
    return -1 * (op.delta(op.correlation(h, v, 5), 5) * op.rank(op.stddev(c, 20)))


def _g105(p):
    # (-1 * CORR(RANK(OPEN), RANK(VOLUME), 10))
    o, v = p.fields["open"], p.fields["volume"]
    return -1 * op.correlation(op.rank(o), op.rank(v), 10)


def _g106(p):
    # CLOSE - DELAY(CLOSE, 20)
    c = p.fields["close"]
    return c - op.delay(c, 20)


def _g107(p):
    # ((-1*RANK((OPEN-DELAY(HIGH,1)))*RANK((OPEN-DELAY(CLOSE,1))))*RANK((OPEN-DELAY(LOW,1))))
    o, h, c, l = (p.fields["open"], p.fields["high"],
                  p.fields["close"], p.fields["low"])
    return (-1 * op.rank(o - op.delay(h, 1))
            * op.rank(o - op.delay(c, 1))
            * op.rank(o - op.delay(l, 1)))


def _g108(p):
    # (RANK((HIGH-MIN(HIGH,2)))^RANK(CORR(VWAP,MEAN(VOLUME,120),6))) * -1
    h, vwap, v = p.fields["high"], p.fields["vwap"], p.fields["volume"]
    return (op.rank(h - op.ts_min(h, 2)) ** op.rank(op.correlation(vwap, op.sma(v, 120), 6))) * -1


def _g109(p):
    # SMA(HIGH-LOW,10,2) / SMA(SMA(HIGH-LOW,10,2),10,2)
    h, l = p.fields["high"], p.fields["low"]
    hl = h - l
    s = op.sma_m(hl, 10, 2)
    return s / op.sma_m(s, 10, 2)


def _g110(p):
    # SUM(MAX(0,HIGH-DELAY(CLOSE,1)),20) / SUM(MAX(0,DELAY(CLOSE,1)-LOW),20) * 100
    c, h, l = p.fields["close"], p.fields["high"], p.fields["low"]
    dc1 = op.delay(c, 1)
    zero = pd.DataFrame(0.0, index=c.index, columns=c.columns)
    numer = op.ts_sum(op.elem_max(h - dc1, zero), 20)
    denom = op.ts_sum(op.elem_max(dc1 - l, zero), 20)
    return numer / denom * 100


def _g111(p):
    # SMA(VOL*((CLOSE-LOW)-(HIGH-CLOSE))/(HIGH-LOW),11,2) - SMA(VOL*((CLOSE-LOW)-(HIGH-CLOSE))/(HIGH-LOW),4,2)
    c, h, l, v = (p.fields["close"], p.fields["high"],
                  p.fields["low"], p.fields["volume"])
    part = v * ((c - l) - (h - c)) / (h - l)
    return op.sma_m(part, 11, 2) - op.sma_m(part, 4, 2)


def _g112(p):
    # (SUM(CLOSE-DELAY(CLOSE,1)>0?CLOSE-DELAY(CLOSE,1):0,12) -
    #  SUM(CLOSE-DELAY(CLOSE,1)<0?ABS(CLOSE-DELAY(CLOSE,1)):0,12)) /
    # (SUM(CLOSE-DELAY(CLOSE,1)>0?CLOSE-DELAY(CLOSE,1):0,12) +
    #  SUM(CLOSE-DELAY(CLOSE,1)<0?ABS(CLOSE-DELAY(CLOSE,1)):0,12)) * 100
    c = p.fields["close"]
    diff = c - op.delay(c, 1)
    zero = pd.DataFrame(0.0, index=c.index, columns=c.columns)
    up = op.ts_sum(op.iif(diff > 0, diff, zero), 12)
    dn = op.ts_sum(op.iif(diff < 0, op.abs_(diff), zero), 12)
    return (up - dn) / (up + dn) * 100


def _g113(p):
    # -1 * ((RANK((SUM(DELAY(CLOSE,5),20)/20)) * CORR(CLOSE,VOLUME,2)) *
    #        RANK(CORR(SUM(CLOSE,5),SUM(CLOSE,20),2)))
    c, v = p.fields["close"], p.fields["volume"]
    return -1 * (op.rank(op.ts_sum(op.delay(c, 5), 20) / 20)
                 * op.correlation(c, v, 2)
                 * op.rank(op.correlation(op.ts_sum(c, 5), op.ts_sum(c, 20), 2)))


def _g114(p):
    # (RANK(DELAY(((HIGH-LOW)/(SUM(CLOSE,5)/5)),2))*RANK(RANK(VOLUME))) /
    # (((HIGH-LOW)/(SUM(CLOSE,5)/5)) / (VWAP-CLOSE))
    c, h, l, vwap, v = (p.fields["close"], p.fields["high"], p.fields["low"],
                         p.fields["vwap"], p.fields["volume"])
    part = (h - l) / (op.ts_sum(c, 5) / 5)
    return (op.rank(op.delay(part, 2)) * op.rank(op.rank(v))) / (part / (vwap - c))


def _g115(p):
    # RANK(CORR((HIGH*0.9+CLOSE*0.1),MEAN(VOLUME,30),10)) ^
    # RANK(CORR(TSRANK((HIGH+LOW)/2,4),TSRANK(VOLUME,10),7))
    c, h, l, v = (p.fields["close"], p.fields["high"],
                  p.fields["low"], p.fields["volume"])
    lhs = op.rank(op.correlation(h * 0.9 + c * 0.1, op.sma(v, 30), 10))
    rhs = op.rank(op.correlation(op.ts_rank((h + l) / 2, 4), op.ts_rank(v, 10), 7))
    return lhs ** rhs


def _g116(p):
    # REGBETA(CLOSE, SEQUENCE, 20)
    c = p.fields["close"]
    return op.regbeta(c, _seq_like(c), 20)


def _g117(p):
    # (TSRANK(VOLUME,32)*(1-TSRANK((CLOSE+HIGH)-LOW,16)))*(1-TSRANK(RET,32))
    c, h, l, v, ret = (p.fields["close"], p.fields["high"], p.fields["low"],
                        p.fields["volume"], p.fields["returns"])
    return (op.ts_rank(v, 32) * (1 - op.ts_rank((c + h) - l, 16))) * (1 - op.ts_rank(ret, 32))


def _g118(p):
    # SUM(HIGH-OPEN,20)/SUM(OPEN-LOW,20)*100
    o, h, l = p.fields["open"], p.fields["high"], p.fields["low"]
    return op.ts_sum(h - o, 20) / op.ts_sum(o - l, 20) * 100


def _g119(p):
    # RANK(DECAYLINEAR(CORR(VWAP,SUM(MEAN(VOLUME,5),26),5),7)) -
    # RANK(DECAYLINEAR(TSRANK(MIN(CORR(RANK(OPEN),RANK(MEAN(VOLUME,15)),21),9),7),8))
    o, vwap, v = p.fields["open"], p.fields["vwap"], p.fields["volume"]
    left = op.rank(op.decay_linear(op.correlation(vwap, op.ts_sum(op.sma(v, 5), 26), 5), 7))
    inner = op.ts_min(op.correlation(op.rank(o), op.rank(op.sma(v, 15)), 21), 9)
    right = op.rank(op.decay_linear(op.ts_rank(inner, 7), 8))
    return left - right


def _g120(p):
    # RANK((VWAP-CLOSE)) / RANK((VWAP+CLOSE))
    c, vwap = p.fields["close"], p.fields["vwap"]
    return op.rank(vwap - c) / op.rank(vwap + c)


def _g121(p):
    # (RANK((VWAP-MIN(VWAP,12)))^TSRANK(CORR(TSRANK(VWAP,20),TSRANK(MEAN(VOLUME,60),2),18),3)) * -1
    vwap, v = p.fields["vwap"], p.fields["volume"]
    base = op.rank(vwap - op.ts_min(vwap, 12))
    exp_ = op.ts_rank(op.correlation(op.ts_rank(vwap, 20), op.ts_rank(op.sma(v, 60), 2), 18), 3)
    return (base ** exp_) * -1


def _g122(p):
    # (SMA(SMA(SMA(LOG(CLOSE),13,2),13,2),13,2) -
    #  DELAY(SMA(SMA(SMA(LOG(CLOSE),13,2),13,2),13,2),1)) /
    # DELAY(SMA(SMA(SMA(LOG(CLOSE),13,2),13,2),13,2),1)
    c = p.fields["close"]
    part = op.sma_m(op.sma_m(op.sma_m(op.log(c), 13, 2), 13, 2), 13, 2)
    return (part - op.delay(part, 1)) / op.delay(part, 1)


def _g123(p):
    # (RANK(CORR(SUM((HIGH+LOW)/2,20),SUM(MEAN(VOLUME,60),20),9)) <
    #  RANK(CORR(LOW,VOLUME,6))) * -1
    h, l, v = p.fields["high"], p.fields["low"], p.fields["volume"]
    lhs = op.rank(op.correlation(op.ts_sum((h + l) / 2, 20), op.ts_sum(op.sma(v, 60), 20), 9))
    rhs = op.rank(op.correlation(l, v, 6))
    return (lhs < rhs).astype(float) * -1


def _g124(p):
    # (CLOSE-VWAP) / DECAYLINEAR(RANK(TSMAX(CLOSE,30)),2)
    c, vwap = p.fields["close"], p.fields["vwap"]
    return (c - vwap) / op.decay_linear(op.rank(op.ts_max(c, 30)), 2)


def _g125(p):
    # RANK(DECAYLINEAR(CORR(VWAP,MEAN(VOLUME,80),17),20)) /
    # RANK(DECAYLINEAR(DELTA((CLOSE*0.5+VWAP*0.5),3),16))
    c, vwap, v = p.fields["close"], p.fields["vwap"], p.fields["volume"]
    numer = op.rank(op.decay_linear(op.correlation(vwap, op.sma(v, 80), 17), 20))
    denom = op.rank(op.decay_linear(op.delta(c * 0.5 + vwap * 0.5, 3), 16))
    return numer / denom


def _g126(p):
    # (CLOSE+HIGH+LOW)/3
    c, h, l = p.fields["close"], p.fields["high"], p.fields["low"]
    return (c + h + l) / 3


def _g127(p):
    # (MEAN((100*(CLOSE-MAX(CLOSE,12))/(MAX(CLOSE,12)))^2,12))^(1/2)
    c = p.fields["close"]
    tsmax12 = op.ts_max(c, 12)
    return (op.sma((100 * (c - tsmax12) / tsmax12) ** 2, 12)) ** 0.5


def _g128(p):
    # 100 - (100 / (1 + SUM(((H+L+C)/3>DELAY((H+L+C)/3,1)?(H+L+C)/3*VOL:0),14) /
    #                       SUM(((H+L+C)/3<DELAY((H+L+C)/3,1)?(H+L+C)/3*VOL:0),14)))
    c, h, l, v = (p.fields["close"], p.fields["high"],
                  p.fields["low"], p.fields["volume"])
    hlc3 = (h + l + c) / 3
    delayed = op.delay(hlc3, 1)
    zero = pd.DataFrame(0.0, index=c.index, columns=c.columns)
    up_sum = op.ts_sum(op.iif(hlc3 > delayed, hlc3 * v, zero), 14)
    dn_sum = op.ts_sum(op.iif(hlc3 < delayed, hlc3 * v, zero), 14)
    return 100 - (100 / (1 + up_sum / dn_sum))


def _g129(p):
    # SUM((CLOSE-DELAY(CLOSE,1)<0?ABS(CLOSE-DELAY(CLOSE,1)):0),12)
    c = p.fields["close"]
    diff = c - op.delay(c, 1)
    zero = pd.DataFrame(0.0, index=c.index, columns=c.columns)
    return op.ts_sum(op.iif(diff < 0, op.abs_(diff), zero), 12)


def _g130(p):
    # RANK(DECAYLINEAR(CORR((HIGH+LOW)/2,MEAN(VOLUME,40),9),10)) /
    # RANK(DECAYLINEAR(CORR(RANK(VWAP),RANK(VOLUME),7),3))
    h, l, vwap, v = (p.fields["high"], p.fields["low"],
                     p.fields["vwap"], p.fields["volume"])
    numer = op.rank(op.decay_linear(op.correlation((h + l) / 2, op.sma(v, 40), 9), 10))
    denom = op.rank(op.decay_linear(op.correlation(op.rank(vwap), op.rank(v), 7), 3))
    return numer / denom


def _g131(p):
    # (RANK(DELTA(VWAP,1))^TSRANK(CORR(CLOSE,MEAN(VOLUME,50),18),18))
    c, vwap, v = p.fields["close"], p.fields["vwap"], p.fields["volume"]
    return (op.rank(op.delta(vwap, 1))
            ** op.ts_rank(op.correlation(c, op.sma(v, 50), 18), 18))


def _g132(p):
    # MEAN(AMOUNT, 20)  — AMOUNT proxied as close * volume
    c, v = p.fields["close"], p.fields["volume"]
    amount = c * v
    return op.sma(amount, 20)


def _g133(p):
    # ((20-HIGHDAY(HIGH,20))/20)*100 - ((20-LOWDAY(LOW,20))/20)*100
    h, l = p.fields["high"], p.fields["low"]
    return (20 - op.highday(h, 20)) / 20 * 100 - (20 - op.lowday(l, 20)) / 20 * 100


def _g134(p):
    # (CLOSE-DELAY(CLOSE,12))/DELAY(CLOSE,12)*VOLUME
    c, v = p.fields["close"], p.fields["volume"]
    dc12 = op.delay(c, 12)
    return (c - dc12) / dc12 * v


def _g135(p):
    # SMA(DELAY(CLOSE/DELAY(CLOSE,20),1),20,1)
    c = p.fields["close"]
    return op.sma_m(op.delay(c / op.delay(c, 20), 1), 20, 1)


def _g136(p):
    # ((-1*RANK(DELTA(RET,3)))*CORR(OPEN,VOLUME,10))
    o, v, ret = p.fields["open"], p.fields["volume"], p.fields["returns"]
    return (-1 * op.rank(op.delta(ret, 3))) * op.correlation(o, v, 10)


def _g137(p):
    # 16*(CLOSE-DELAY(CLOSE,1)+(CLOSE-OPEN)/2+DELAY(CLOSE,1)-DELAY(OPEN,1)) /
    # ((abshc>abslc & abshc>abshl ? abshc+abslc/2+absco/4 :
    #   (abslc>abshl & abslc>abshc ? abslc+abshc/2+absco/4 : abshl+absco/4)))
    # * MAX(abshc, abslc)
    # where abshc=ABS(HIGH-DELAY(CLOSE,1)), abslc=ABS(LOW-DELAY(CLOSE,1)),
    #       absco=ABS(DELAY(CLOSE,1)-DELAY(OPEN,1)), abshl=ABS(HIGH-DELAY(LOW,1))
    c, h, l, o = (p.fields["close"], p.fields["high"],
                  p.fields["low"], p.fields["open"])
    dc1 = op.delay(c, 1)
    do1 = op.delay(o, 1)
    dl1 = op.delay(l, 1)
    abshc = op.abs_(h - dc1)
    abslc = op.abs_(l - dc1)
    absco = op.abs_(dc1 - do1)
    abshl = op.abs_(h - dl1)
    numer = 16 * (c - dc1 + (c - o) / 2 + dc1 - do1)
    cond1 = (abshc > abslc) & (abshc > abshl)
    cond2 = (abslc > abshl) & (abslc > abshc)
    denom = op.iif(cond1, abshc + abslc / 2 + absco / 4,
                   op.iif(cond2, abslc + abshc / 2 + absco / 4,
                          abshl + absco / 4))
    return numer / denom * op.elem_max(abshc, abslc)


def _g138(p):
    # (RANK(DECAYLINEAR(DELTA((LOW*0.7+VWAP*0.3),3),20)) -
    #  TSRANK(DECAYLINEAR(TSRANK(CORR(TSRANK(LOW,8),TSRANK(MEAN(VOLUME,60),17),5),19),16),7)) * -1
    l, vwap, v = p.fields["low"], p.fields["vwap"], p.fields["volume"]
    left = op.rank(op.decay_linear(op.delta(l * 0.7 + vwap * 0.3, 3), 20))
    inner = op.ts_rank(op.correlation(op.ts_rank(l, 8), op.ts_rank(op.sma(v, 60), 17), 5), 19)
    right = op.ts_rank(op.decay_linear(inner, 16), 7)
    return (left - right) * -1


def _g139(p):
    # (-1 * CORR(OPEN, VOLUME, 10))
    o, v = p.fields["open"], p.fields["volume"]
    return -1 * op.correlation(o, v, 10)


def _g140(p):
    # MIN(RANK(DECAYLINEAR((RANK(OPEN)+RANK(LOW))-(RANK(HIGH)+RANK(CLOSE)),8)),
    #     TSRANK(DECAYLINEAR(CORR(TSRANK(CLOSE,8),TSRANK(MEAN(VOLUME,60),20),8),7),3))
    c, h, l, o, v = (p.fields["close"], p.fields["high"], p.fields["low"],
                     p.fields["open"], p.fields["volume"])
    left = op.rank(op.decay_linear(
        (op.rank(o) + op.rank(l)) - (op.rank(h) + op.rank(c)), 8))
    right = op.ts_rank(op.decay_linear(
        op.correlation(op.ts_rank(c, 8), op.ts_rank(op.sma(v, 60), 20), 8), 7), 3)
    return op.elem_min(left, right)


GTJA_ALPHAS.extend([
    AlphaDef("G096", "GTJA", _g96),
    AlphaDef("G097", "GTJA", _g97),
    AlphaDef("G098", "GTJA", _g98),
    AlphaDef("G099", "GTJA", _g99),
    AlphaDef("G100", "GTJA", _g100),
    AlphaDef("G101", "GTJA", _g101),
    AlphaDef("G102", "GTJA", _g102),
    AlphaDef("G103", "GTJA", _g103),
    AlphaDef("G104", "GTJA", _g104),
    AlphaDef("G105", "GTJA", _g105),
    AlphaDef("G106", "GTJA", _g106),
    AlphaDef("G107", "GTJA", _g107),
    AlphaDef("G108", "GTJA", _g108),
    AlphaDef("G109", "GTJA", _g109),
    AlphaDef("G110", "GTJA", _g110),
    AlphaDef("G111", "GTJA", _g111),
    AlphaDef("G112", "GTJA", _g112),
    AlphaDef("G113", "GTJA", _g113),
    AlphaDef("G114", "GTJA", _g114),
    AlphaDef("G115", "GTJA", _g115),
    AlphaDef("G116", "GTJA", _g116),
    AlphaDef("G117", "GTJA", _g117),
    AlphaDef("G118", "GTJA", _g118),
    AlphaDef("G119", "GTJA", _g119),
    AlphaDef("G120", "GTJA", _g120),
    AlphaDef("G121", "GTJA", _g121),
    AlphaDef("G122", "GTJA", _g122),
    AlphaDef("G123", "GTJA", _g123),
    AlphaDef("G124", "GTJA", _g124),
    AlphaDef("G125", "GTJA", _g125),
    AlphaDef("G126", "GTJA", _g126),
    AlphaDef("G127", "GTJA", _g127),
    AlphaDef("G128", "GTJA", _g128),
    AlphaDef("G129", "GTJA", _g129),
    AlphaDef("G130", "GTJA", _g130),
    AlphaDef("G131", "GTJA", _g131),
    AlphaDef("G132", "GTJA", _g132, needs=["amount~close*volume"]),
    AlphaDef("G133", "GTJA", _g133),
    AlphaDef("G134", "GTJA", _g134),
    AlphaDef("G135", "GTJA", _g135),
    AlphaDef("G136", "GTJA", _g136),
    AlphaDef("G137", "GTJA", _g137),
    AlphaDef("G138", "GTJA", _g138),
    AlphaDef("G139", "GTJA", _g139),
    AlphaDef("G140", "GTJA", _g140),
])
