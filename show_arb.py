"""
show_arb.py — Print last arbitrage scan results from data/arbitrage/opportunities.json
Called by TradingBot.bat Option [Z].
"""
import json
from pathlib import Path

p = Path("data/arbitrage/opportunities.json")
if not p.exists():
    print("  No arbitrage data yet.")
    print("  Start Multi-Profile Learning (Option M) to begin arb scanning.")
else:
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        opps = d.get("opportunities", [])
        if opps:
            print("  {:<12} {:<10} {:<10} {:>9}  {:>6}  {:>6}".format(
                "Symbol", "Buy", "Sell", "Net Sprd", "Conf", "Z-Score"))
            print("  " + "-" * 58)
            for o in opps:
                sym    = o.get("symbol", "?")
                buy_ex = o.get("buy_ex", "?")
                sel_ex = o.get("sell_ex", "?")
                ns     = o.get("net_spread", 0) * 100
                conf   = o.get("confidence", 0) * 100
                zsc    = o.get("zscore", 0)
                print("  {:<12} {:<10} {:<10} {:>8.3f}%  {:>5.0f}%  {:>6.1f}".format(
                    sym, buy_ex, sel_ex, ns, conf, zsc))
        else:
            print("  No opportunities found in last scan.")

        print()
        print("  Open arbs  : {}".format(d.get("open_arbs", 0)))
        print("  Closed arbs: {}".format(d.get("closed_arbs", 0)))
        total_pnl = d.get("total_pnl", 0)
        sign = "+" if total_pnl >= 0 else ""
        print("  Total PnL  : {}{:.4f} USDT".format(sign, total_pnl))
        print("  Scan #     : {}".format(d.get("scan_count", 0)))
        print("  Updated    : {}".format(d.get("updated_at", "?")[:19]))
    except Exception as e:
        print("  Error reading arbitrage data: {}".format(e))
