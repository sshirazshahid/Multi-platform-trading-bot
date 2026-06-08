"""
daily_scan.py - automated daily market monitor + confluence scan (paper/advisory).
Writes reports/daily_scan_<date>.md and data/ensemble_setups.json (bot-readable).
Run: python -m quant_suite.daily_scan
"""
from __future__ import annotations
from datetime import datetime, timezone
import json
from pathlib import Path
from . import bot_adapter as A

ROOT = Path(__file__).resolve().parents[1]


def run(top_n: int = 30, equity: float = 10000.0) -> dict:
    res = A.scan_top(top_n, equity)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    setups = res.get("setups", [])
    btc = res.get("btc", {})
    g = res.get("gainers", [])
    lo = res.get("losers", [])

    (ROOT / "data").mkdir(exist_ok=True)
    (ROOT / "data" / "ensemble_setups.json").write_text(json.dumps(
        {"generated": now, "operating_mode": res.get("operating_mode"), "mode": res.get("mode"),
         "enabled": A.is_enabled(), "per_coin_tf": res.get("per_coin_tf"), "btc": btc,
         "gainers": g, "losers": lo, "setups": setups}, indent=2, default=str), encoding="utf-8")

    (ROOT / "reports").mkdir(exist_ok=True)
    L = [f"# Daily Scan - {date}",
         f"_Generated {now} - mode={res.get('mode')} - {res.get('operating_mode')} - advisory-only (no orders)_", "",
         f"**BTC 4h:** ADX {btc.get('adx_4h')} - trend={btc.get('trend')} - RSI {btc.get('rsi')} - px {btc.get('close')}",
         f"\nScanned top {res.get('scanned')} liquid perps (per-coin TF={res.get('per_coin_tf')}) -> **{len(setups)} confluence setup(s)**\n"]
    if setups:
        L += ["| Side | Symbol | TF | Tier | Entry | Stop | Target | SL% | R:R | Risk% |",
              "|---|---|---|---|---|---|---|---|---|---|"]
        for s in setups:
            L.append(f"| {s['side'].upper()} | {s['symbol']} | {s.get('tf')} | {s['conviction_tier']} | "
                     f"{s['entry']} | {s['stop_loss']} | {s['take_profit']} | {s['sl_pct']} | {s['rr']} | {s['risk_pct']} |")
    else:
        L.append("_No confluence setups - 4h regime not aligned; engine stays flat by design._")
    if g:
        L.append("\n**Hot movers 24h** - gainers: " + ", ".join(f"{x['coin']} {x['pct']:+.1f}%" for x in g))
        L.append("losers: " + ", ".join(f"{x['coin']} {x['pct']:+.1f}%" for x in lo))
    (ROOT / "reports" / f"daily_scan_{date}.md").write_text("\n".join(L), encoding="utf-8")
    return res


if __name__ == "__main__":
    r = run()
    print(f"setups={len(r.get('setups', []))} scanned={r.get('scanned')} mode={r.get('mode')} {r.get('operating_mode')}")
    for s in r.get("setups", []):
        print(f"  {s['side'].upper():4} {s['symbol']:16} {s.get('tf')} tier{s['conviction_tier']} entry {s['entry']}")
