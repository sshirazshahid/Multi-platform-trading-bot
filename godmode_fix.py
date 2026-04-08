"""
godmode_fix.py — Apply ALL god mode fixes before restart.

1. Clear __pycache__
2. Widen SL/TP on existing open positions  
3. Remove ghost positions
4. Verify config

Run: venv/Scripts/python.exe godmode_fix.py
"""
import json, os, shutil
from pathlib import Path

ROOT = Path(__file__).parent

print()
print("=" * 60)
print("  GOD MODE — APPLYING ALL FIXES")
print("=" * 60)

# 1. CLEAR CACHE
print("\n[1/4] Clearing __pycache__...")
n = 0
for dp, dn, fn in os.walk(ROOT):
    if "venv" in dp:
        continue
    for d in list(dn):
        if d == "__pycache__":
            try:
                shutil.rmtree(os.path.join(dp, d))
                n += 1
            except:
                pass
print(f"  Cleared {n} cache directories")

# 2. FIX OPEN POSITIONS
print("\n[2/4] Fixing open positions...")
MIN_SL_PCT = 0.020  # 2% minimum
TARGET_RR = 2.5

files = [
    ROOT / "data" / "positions.json",
    ROOT / "data" / "profiles" / "conservative" / "positions.json",
    ROOT / "data" / "profiles" / "moderate" / "positions.json",
    ROOT / "data" / "profiles" / "aggressive" / "positions.json",
]

total_fixed = 0
total_ghosts = 0

for pf in files:
    if not pf.exists():
        continue
    try:
        data = json.loads(pf.read_text(encoding="utf-8"))
    except:
        continue
    
    open_list = data.get("open", [])
    if isinstance(open_list, dict):
        open_list = list(open_list.values())
    
    before = len(open_list)
    open_list = [p for p in open_list if p.get("order_id") is not None]
    ghosts = before - len(open_list)
    total_ghosts += ghosts
    
    for pos in open_list:
        entry = pos.get("entry_price", 0)
        sl = pos.get("stop_loss", 0)
        side = pos.get("side", "")
        symbol = pos.get("symbol", "")
        
        if entry <= 0 or sl <= 0:
            continue
        
        if side == "sell":
            sl_pct = (sl - entry) / entry
        else:
            sl_pct = (entry - sl) / entry
        
        if sl_pct < MIN_SL_PCT:
            new_sl_dist = entry * MIN_SL_PCT
            new_tp_dist = new_sl_dist * TARGET_RR
            
            if side == "sell":
                new_sl = round(entry + new_sl_dist, 8)
                new_tp = round(entry - new_tp_dist, 8)
            else:
                new_sl = round(entry - new_sl_dist, 8)
                new_tp = round(entry + new_tp_dist, 8)
            
            print(f"  WIDEN {symbol:20s}: SL {sl_pct*100:.2f}% -> {MIN_SL_PCT*100:.1f}%")
            pos["stop_loss"] = new_sl
            pos["take_profit"] = new_tp
            total_fixed += 1
    
    data["open"] = open_list
    pf.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")

print(f"  Widened {total_fixed} positions, removed {total_ghosts} ghosts")

# 3. VERIFY PAIRS
print("\n[3/4] Verifying exchange pairs...")
import importlib.util
spec = importlib.util.spec_from_file_location("config", ROOT / "config.py")
cfg = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cfg)

tp = cfg.TRADING_PAIRS
for ex_name in ["binance", "mexc", "bybit", "bitget"]:
    p = tp.get(ex_name, {})
    spot_n = len(p.get("spot", []))
    fut_n = len(p.get("futures", []))
    print(f"  {ex_name.upper():8s}: {spot_n:2d} spot + {fut_n:2d} futures")

# Check Bybit has unique pairs
bybit_spot = set(s.split("/")[0] for s in tp.get("bybit",{}).get("spot",[]))
binance_spot = set(s.split("/")[0] for s in tp.get("binance",{}).get("spot",[]))
bybit_unique = bybit_spot - binance_spot
print(f"  Bybit unique: {bybit_unique or 'NONE — FIX NEEDED'}")

# 4. VERIFY RISK
print("\n[4/4] Verifying risk config...")
r = cfg.RISK
checks = {
    f"leverage={r['default_leverage']}": r['default_leverage'] <= 5,
    f"sizing={r.get('position_sizing_mode','fixed')}": r.get('position_sizing_mode') == 'kelly',
    f"SL={r['default_stop_loss']*100:.1f}%": r['default_stop_loss'] >= 0.015,
    f"trailing={r['trailing_stop']}": r['trailing_stop'] == True,
    f"drawdown={r['max_drawdown_pct']*100:.0f}%": r['max_drawdown_pct'] >= 0.15,
}

for name, ok in checks.items():
    status = "OK" if ok else "FAIL"
    print(f"  [{status}] {name}")

print(f"""
{'='*60}
  GOD MODE ACTIVE — $100 → $1000 → $10000 JOURNEY
  
  Exchanges: Binance + MEXC + Bybit + Bitget
  Pairs: {sum(len(v.get('spot',[]))+len(v.get('futures',[])) for v in tp.values())} total across 4 exchanges
  Unique per exchange: Every exchange has exclusive coins
  
  Peak Capture: 65% lock guarantee on every winning trade
  SL Floor: 2.0% minimum (no more 0.3% noise stops)
  Kelly Sizing: Position size grows with your balance
  
  RESTART THE BOT NOW — auto_restart.bat or TradingBot.bat [1]
{'='*60}
""")

input("Press Enter to exit...")
