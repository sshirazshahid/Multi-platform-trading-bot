"""
execution.py - TWAP / POV / iceberg execution scheduler (LOXM-style analog).
Plans how to slice a parent order to reduce market impact. PLANNER ONLY: returns
a child-slice schedule; it NEVER places orders.
"""
from __future__ import annotations
import numpy as np


def twap(total_qty: float, n_slices: int = 10):
    q = total_qty / max(n_slices, 1)
    return [{"slice": i + 1, "qty": round(q, 8), "type": "TWAP"} for i in range(n_slices)]


def pov(total_qty: float, market_volumes, participation: float = 0.1):
    sched = []; remaining = total_qty
    for i, mv in enumerate(market_volumes):
        if remaining <= 0:
            break
        q = min(remaining, mv * participation)
        sched.append({"slice": i + 1, "qty": round(q, 8), "cap": round(mv * participation, 8), "type": "POV"})
        remaining -= q
    if remaining > 0 and sched:
        sched[-1]["qty"] = round(sched[-1]["qty"] + remaining, 8)
    return sched


def iceberg(total_qty: float, clip: float):
    rem = total_qty; sched = []; i = 0
    while rem > 1e-12:
        q = min(clip, rem)
        sched.append({"slice": i + 1, "display": round(clip, 8), "qty": round(q, 8), "type": "ICEBERG"})
        rem -= q; i += 1
    return sched


def plan(side: str, total_qty: float, algo: str = "twap", n_slices: int = 10,
         market_volumes=None, participation: float = 0.1, clip: float = None) -> dict:
    algo = (algo or "twap").lower()
    if algo == "pov" and market_volumes:
        sl = pov(total_qty, market_volumes, participation)
    elif algo == "iceberg" and clip:
        sl = iceberg(total_qty, clip)
    else:
        algo = "twap"; sl = twap(total_qty, n_slices)
    return {"side": side, "algo": algo, "total_qty": total_qty, "n_slices": len(sl),
            "slices": sl, "places_orders": False, "advisory_only": True}
