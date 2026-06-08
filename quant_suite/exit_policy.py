"""
exit_policy.py - test improved EXIT management on the confluence strategy:
fixed vs breakeven vs ATR-trail vs partial-take-profit. Entry logic is the
engine's confluence signal (unchanged); only the exit differs. The winner (if
any) is applied in run_confluence_paper.py.
"""
from __future__ import annotations
import numpy as np
from . import engine as E


def run_exit(d, p=None, strat="confluence", exit_mode="fixed",
             trail_mult=2.0, partial_at=1.5, be_at=1.0):
    p = p or E.BTParams()
    long_ok, short_ok = E._ok_arrays(d, strat)
    o = d["open"].values.astype(float); h = d["high"].values.astype(float)
    l = d["low"].values.astype(float); c = d["close"].values.astype(float)
    atr = d["atr"].values.astype(float)
    n = len(c); trades = []; i = 60; cd = 0
    be = exit_mode in ("breakeven", "trail", "partial")
    while i < n - 1:
        if cd > 0:
            cd -= 1; i += 1; continue
        go_l, go_s = bool(long_ok[i]), bool(short_ok[i])
        if (go_l or go_s) and atr[i] == atr[i] and atr[i] > 0 and o[i + 1] > 0:
            s = 1 if go_l else -1
            entry = o[i + 1]
            sl_d = min(max(p.atr_mult * atr[i] / c[i], p.sl_min), p.sl_max)
            if s == 1:
                sl = entry * (1 - sl_d); tp = entry * (1 + sl_d * p.rr); ef = entry * (1 + E.SLIP_OPEN)
            else:
                sl = entry * (1 + sl_d); tp = entry * (1 - sl_d * p.rr); ef = entry * (1 - E.SLIP_OPEN)
            atr_e = atr[i]; expx = None; outc = "TIME"; bars = 0; pdone = False; pgross = 0.0; qty = 1.0
            for j in range(i + 1, min(i + 1 + p.max_hold, n)):
                bars = j - i
                hit_sl = (l[j] <= sl) if s == 1 else (h[j] >= sl)
                hit_tp = (h[j] >= tp) if s == 1 else (l[j] <= tp)
                if hit_sl:
                    expx = sl * (1 - E.SLIP_SL) if s == 1 else sl * (1 + E.SLIP_SL); outc = "SL"; break
                if hit_tp:
                    expx = tp * (1 - E.SLIP_CLOSE) if s == 1 else tp * (1 + E.SLIP_CLOSE); outc = "TP"; break
                if be:
                    prof = ((c[j] - ef) / ef if s == 1 else (ef - c[j]) / ef) / sl_d
                    if exit_mode == "partial" and not pdone and prof >= partial_at:
                        pgross = (c[j] - ef) / ef if s == 1 else (ef - c[j]) / ef
                        qty = 0.5; pdone = True
                        sl = max(sl, ef) if s == 1 else min(sl, ef)
                    if prof >= be_at:
                        sl = max(sl, ef) if s == 1 else min(sl, ef)
                    if exit_mode == "trail" and prof >= be_at:
                        t = c[j] - trail_mult * atr_e if s == 1 else c[j] + trail_mult * atr_e
                        sl = max(sl, t) if s == 1 else min(sl, t)
            if expx is None:
                jb = min(i + p.max_hold, n - 1)
                expx = c[jb] * (1 - E.SLIP_CLOSE) if s == 1 else c[jb] * (1 + E.SLIP_CLOSE); bars = jb - i
            gf = (expx - ef) / ef if s == 1 else (ef - expx) / ef
            gross_total = (pgross * 0.5 + gf * qty) if pdone else gf
            net = gross_total - 2 * E.TAKER_FEE
            trades.append({"ret": net, "R": net / sl_d if sl_d else 0.0,
                           "outcome": ("TP" if (pdone or outc == "TP") else outc)})
            i = i + bars + p.cooldown
        else:
            i += 1
    return trades


def stats(tr):
    if not tr:
        return {"trades": 0}
    r = np.array([t["ret"] for t in tr]); R = np.array([t["R"] for t in tr])
    w = r[r > 0]; gl = -r[r <= 0].sum()
    eq = (1 + r).cumprod(); dd = (eq - np.maximum.accumulate(eq)) / np.maximum.accumulate(eq)
    return {"trades": len(tr), "win%": round(len(w) / len(tr) * 100, 1),
            "tp%": round(sum(t["outcome"] == "TP" for t in tr) / len(tr) * 100, 1),
            "exp%": round(float(r.mean() * 100), 4), "avgR": round(float(R.mean()), 3),
            "PF": round(float(w.sum() / gl), 2) if gl > 0 else 99.0,
            "maxDD%": round(float(dd.min() * 100), 2)}
