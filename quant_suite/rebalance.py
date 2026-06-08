"""rebalance.py - portfolio rebalancing to target weights. Given current holding
values and target weights, emit the trades to restore targets beyond a drift
threshold. Advisory only."""
from __future__ import annotations


def rebalance(holdings_value: dict, target_weights: dict, threshold: float = 0.05) -> dict:
    total = sum(holdings_value.values())
    if total <= 0:
        return {"total": 0, "trades": [], "rebalanced": False}
    trades = []
    for sym, tw in target_weights.items():
        cur = holdings_value.get(sym, 0.0) / total
        drift = cur - tw
        if abs(drift) >= threshold:
            trades.append({"symbol": sym, "current_w": round(cur, 4), "target_w": round(tw, 4),
                           "action": "sell" if drift > 0 else "buy", "usd": round(abs(drift) * total, 2)})
    return {"total": round(total, 2), "trades": trades, "rebalanced": len(trades) > 0, "places_orders": False}
