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

from core.alpha_zoo import operators as op
from core.alpha_zoo.registry import AlphaDef

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
