"""
timeframe_research.py - find the 'sweet' timeframe for the confluence strategy.

Backtests the shipped confluence strategy on a liquid basket across several
chart timeframes (each with its matching higher-TF context) and ranks them by
per-trade expectancy / profit factor / TP-hit rate.

Run one TF at a time (each fits tool time limits), then summarize:
    python -m quant_suite.timeframe_research 15m
    python -m quant_suite.timeframe_research 1h
    python -m quant_suite.timeframe_research 4h
    python -m quant_suite.timeframe_research 1d
    python -m quant_suite.timeframe_research summary
"""
from __future__ import annotations
import sys, json
from pathlib import Path
import ccxt
from . import engine as E, ensemble_backtest as B

ROOT = Path(__file__).resolve().parents[1]
ACC = Path("/tmp/qs_cache/tf_research.json")
ACC.parent.mkdir(parents=True, exist_ok=True)
# chart TF -> (higher-TF resample rule, bars to fetch)
PRESETS = {"15m": ("1h", 1500), "30m": ("2h", 1500), "1h": ("4h", 1500),
           "2h": ("8h", 2000), "4h": ("1D", 2500), "1d": ("1W", 2000)}
BASKET = B.BASKET


def run_tf(tf: str) -> dict:
    htf, bars = PRESETS[tf]
    ex = ccxt.binance({"options": {"defaultType": "future"}, "enableRateLimit": True}); ex.load_markets()
    btc = B.fetch(ex, "BTC/USDT:USDT", tf, bars)
    pooled = []
    used = 0
    for sym in BASKET:
        d = B.fetch(ex, sym, tf, bars)
        if len(d) < 250:
            continue
        used += 1
        dd = E.prep(d, df_btc1h=btc, is_benchmark=sym.startswith("BTC"), htf_rule=htf)
        pooled += E.backtest(dd, "confluence")
    st = E.stats(pooled)
    st.update({"tf": tf, "htf": htf, "bars": bars, "symbols": used})
    acc = json.loads(ACC.read_text()) if ACC.exists() else {}
    acc[tf] = st
    ACC.write_text(json.dumps(acc, indent=2))
    return st


def summary():
    acc = json.loads(ACC.read_text()) if ACC.exists() else {}
    rows = list(acc.values())
    order = ["15m", "30m", "1h", "2h", "4h", "1d"]
    rows.sort(key=lambda r: order.index(r["tf"]) if r["tf"] in order else 99)
    print("="*78)
    print("SWEET-TIMEFRAME RESEARCH - confluence strategy (per-trade edge by TF)")
    print("="*78)
    print(f"{'TF':>4}{'HTF':>5}{'trades':>8}{'win%':>7}{'TP%':>7}{'exp%':>8}{'avgR':>7}{'PF':>6}{'maxDD%':>8}")
    for r in rows:
        print(f"{r['tf']:>4}{r['htf']:>5}{r.get('trades',0):>8}{r.get('win_rate','-'):>7}"
              f"{r.get('tp_rate','-'):>7}{r.get('expectancy_pct','-'):>8}{r.get('avg_R','-'):>7}"
              f"{str(r.get('profit_factor','-')):>6}{r.get('max_dd_pct','-'):>8}")
    valid = [r for r in rows if r.get("trades", 0) >= 20]
    if valid:
        best = max(valid, key=lambda r: r.get("expectancy_pct", -9))
        print(f"\nSWEET TIMEFRAME: {best['tf']} (HTF {best['htf']}) - expectancy "
              f"{best['expectancy_pct']}%/trade, PF {best['profit_factor']}, "
              f"{best['trades']} trades, maxDD {best['max_dd_pct']}%")
    (ROOT/"reports").mkdir(exist_ok=True)
    (ROOT/"reports"/"timeframe_research.json").write_text(json.dumps(acc, indent=2))
    return acc


if __name__ == "__main__":
    arg = sys.argv[1] if len(sys.argv) > 1 else "summary"
    if arg == "summary":
        summary()
    else:
        st = run_tf(arg)
        print(f"{arg}: trades={st.get('trades')} win%={st.get('win_rate')} tp%={st.get('tp_rate')} "
              f"exp%={st.get('expectancy_pct')} avgR={st.get('avg_R')} PF={st.get('profit_factor')} maxDD={st.get('max_dd_pct')}")


def per_coin(min_trades: int = 8):
    """Best timeframe PER symbol (reuses cached data). Writes data/per_coin_tf.json
    (symbol -> best TF) + reports/per_coin_timeframe.json (full stats)."""
    import ccxt
    ex = ccxt.binance({"options": {"defaultType": "future"}, "enableRateLimit": True})
    btc_cache = {}
    out = {}
    for sym in BASKET:
        best = None
        per_tf = {}
        for tf, (htf, bars) in PRESETS.items():
            d = B.fetch(ex, sym, tf, bars)
            if len(d) < 250:
                continue
            if tf not in btc_cache:
                btc_cache[tf] = B.fetch(ex, "BTC/USDT:USDT", tf, bars)
            dd = E.prep(d, df_btc1h=btc_cache[tf], is_benchmark=sym.startswith("BTC"), htf_rule=htf)
            st = E.stats(E.backtest(dd, "confluence"))
            per_tf[tf] = {"trades": st.get("trades", 0), "exp": st.get("expectancy_pct"),
                          "pf": st.get("profit_factor"), "tp": st.get("tp_rate")}
            if st.get("trades", 0) >= min_trades and (st.get("expectancy_pct") or -9) > 0:
                if best is None or st["expectancy_pct"] > best["exp"]:
                    best = {"tf": tf, "exp": st["expectancy_pct"], "trades": st["trades"],
                            "pf": st.get("profit_factor"), "tp": st.get("tp_rate")}
        out[sym] = {"best_tf": best["tf"] if best else "1h", "best": best, "by_tf": per_tf}
    (ROOT / "reports").mkdir(exist_ok=True)
    (ROOT / "reports" / "per_coin_timeframe.json").write_text(json.dumps(out, indent=2, default=str))
    (ROOT / "data").mkdir(exist_ok=True)
    (ROOT / "data" / "per_coin_tf.json").write_text(json.dumps(
        {k.split('/')[0]: v["best_tf"] for k, v in out.items()}, indent=2))
    print("per-coin best timeframe (>= %d trades, else default 1h):" % min_trades)
    for k, v in out.items():
        b = v["best"]
        tag = f"{b['tf']} (exp {b['exp']}% PF {b['pf']} n {b['trades']})" if b else "1h (insufficient sample)"
        print(f"  {k.split('/')[0]:6} -> {tag}")
    return out
