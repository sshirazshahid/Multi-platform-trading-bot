"""dca.py - Dollar-Cost Averaging accumulation (low/moderate risk). Periodic
fixed-notional buys, optionally 1.5x on dips below the 50-EMA. Backtest reports
average cost basis vs final price."""
from __future__ import annotations
import pandas as pd
from . import indicators as ind


def plan(notional_per_buy: float, interval_bars: int, n_buys: int, dip_weight: bool = True) -> dict:
    return {"notional_per_buy": notional_per_buy, "interval_bars": interval_bars,
            "n_buys": n_buys, "dip_weight": dip_weight, "places_orders": False}


def backtest(df: pd.DataFrame, notional: float = 100.0, interval: int = 24,
             dip_weight: bool = True, fee: float = 0.0005) -> dict:
    c = df["close"].values.astype(float)
    if len(c) < interval * 3:
        return {"buys": 0, "note": "insufficient_history"}
    ema = ind.ema(pd.Series(c), 50).values
    spent = 0.0; coins = 0.0; buys = 0
    for i in range(0, len(c), interval):
        p = c[i]; amt = notional * (1.5 if (dip_weight and p < ema[i]) else 1.0)
        coins += amt / p * (1 - fee); spent += amt; buys += 1
    val = coins * c[-1]
    return {"buys": buys, "invested": round(spent, 2),
            "avg_cost": round(spent / coins, 4) if coins else 0,
            "last_price": round(float(c[-1]), 4), "value": round(float(val), 2),
            "pnl_pct": round(float((val / spent - 1) * 100), 2) if spent else 0}
