"""ensemble_backtest.py - compare bot's current engine (multifactor) vs the
regime-switching ensemble on a liquid basket. Saves JSON to reports/."""
from __future__ import annotations
import time, json
from pathlib import Path
import pandas as pd, ccxt
from . import engine as E

CACHE = Path("/tmp/qs_cache"); CACHE.mkdir(parents=True, exist_ok=True)
BASKET = ["BTC/USDT:USDT", "ETH/USDT:USDT", "SOL/USDT:USDT", "BNB/USDT:USDT",
          "XRP/USDT:USDT", "DOGE/USDT:USDT", "AVAX/USDT:USDT", "LINK/USDT:USDT",
          "LTC/USDT:USDT", "ADA/USDT:USDT"]


def fetch(ex, sym, tf="1h", bars=1500):
    cache = CACHE / f"{sym.replace('/','_').replace(':','-')}_{tf}_{bars}.parquet"
    if cache.exists() and time.time()-cache.stat().st_mtime < 86400:
        try: return pd.read_parquet(cache)
        except Exception: pass
    tf_s = 3600; now = ex.milliseconds(); cur = now-bars*tf_s*1000; rows = []
    g = 0
    while g < 60:
        g += 1
        b = ex.fetch_ohlcv(sym, tf, since=cur, limit=1000)
        if not b: break
        rows += b; nxt = b[-1][0]+tf_s*1000
        if nxt <= cur or b[-1][0] >= now or len(b) < 1000: break
        cur = nxt
    if not rows: return pd.DataFrame()
    d = pd.DataFrame(rows, columns=["ts","open","high","low","close","volume"]).drop_duplicates("ts").sort_values("ts").reset_index(drop=True)
    d["dt"] = pd.to_datetime(d["ts"], unit="ms", utc=True)
    try: d.to_parquet(cache)
    except Exception: pass
    return d


def run(symbols=None, bars=1500):
    ex = ccxt.binance({"options": {"defaultType": "future"}, "enableRateLimit": True}); ex.load_markets()
    symbols = symbols or BASKET
    btc = fetch(ex, "BTC/USDT:USDT", "1h", bars)
    pooled = {"multifactor": [], "ensemble": [], "confluence": [], "regime": []}
    n_ok = 0
    for s in symbols:
        d = fetch(ex, s, "1h", bars)
        if len(d) < 300: continue
        n_ok += 1
        dd = E.prep(d, df_btc1h=btc, is_benchmark=s.startswith("BTC"))
        for m in pooled:
            pooled[m] += E.backtest(dd, m)
    res = {"symbols_tested": n_ok, "bars": bars,
           "multifactor_baseline": E.stats(pooled["multifactor"]),
           "regime_only": E.stats(pooled["regime"]),
           "ensemble": E.stats(pooled["ensemble"]),
           "confluence_WINNER": E.stats(pooled["confluence"])}
    ens = pooled["ensemble"]
    res["ensemble_by_tier"] = {int(t): E.stats([x for x in ens if x["tier"] == t])
                               for t in sorted(set(x["tier"] for x in ens))}
    out = Path("/sessions/festive-magical-fermat/mnt/Trading_Bot/reports")
    out.mkdir(exist_ok=True)
    (out/"ensemble_backtest.json").write_text(json.dumps(res, indent=2, default=str))
    return res


if __name__ == "__main__":
    r = run()
    b, e = r["multifactor_baseline"], r["ensemble"]
    print("="*66)
    print(f"ENSEMBLE vs BASELINE  ({r['symbols_tested']} symbols, {r['bars']} 1h bars)")
    print("="*66)
    hdr = f"{'metric':<18}{'BASELINE(mf)':>16}{'ENSEMBLE':>14}"
    print(hdr); print("-"*66)
    for k in ["trades","win_rate","tp_rate","expectancy_pct","avg_R","profit_factor","total_return_pct","max_dd_pct"]:
        print(f"{k:<18}{str(b.get(k,'-')):>16}{str(e.get(k,'-')):>14}")
    print("\nEnsemble by conviction tier (1=range MR, 2=trend confluence, 3=all-aligned):")
    for t, st in r["ensemble_by_tier"].items():
        print(f"  tier {t}: trades={st.get('trades')} win%={st.get('win_rate')} tp%={st.get('tp_rate')} exp%={st.get('expectancy_pct')} avgR={st.get('avg_R')}")
