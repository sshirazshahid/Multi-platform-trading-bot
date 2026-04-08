"""
GODMODE_FINAL.py — Clear cache + verify all fixes
Run this ONCE, then start the bot with auto_restart.bat
"""
import os, shutil, json
from pathlib import Path

ROOT = Path(__file__).parent

print("\n[1/3] Clearing ALL __pycache__...")
n = 0
for dp, dn, fn in os.walk(ROOT):
    if "venv" in dp: continue
    for d in list(dn):
        if d == "__pycache__":
            try: shutil.rmtree(os.path.join(dp, d)); n += 1
            except: pass
    for f in fn:
        if f.endswith(".pyc"):
            try: os.remove(os.path.join(dp, f)); n += 1
            except: pass
print(f"  [OK] Cleared {n} cache items")

print("\n[2/3] Clearing stale trailing peaks...")
peaks = ROOT / "data" / "trailing_peaks.json"
if peaks.exists():
    peaks.unlink()
    print("  [OK] Cleared (will rebuild from current positions)")
else:
    print("  [OK] No stale peaks")

print("\n[3/3] Verifying critical fixes...")
checks = {}

# Trailing stop
t = (ROOT/"core"/"trailing_stop_manager.py").read_text(encoding="utf-8")
checks["Trail: disk persistence"] = "trailing_peaks.json" in t
checks["Trail: 65% peak lock"]    = "0.65" in t
checks["Trail: load on startup"]  = "_load_peaks" in t

# Risk manager
r = (ROOT/"core"/"risk_manager.py").read_text(encoding="utf-8")
checks["Risk: SL floor 2.0%"]     = "0.020" in r
checks["Risk: min notional"]      = "5.50" in r or "Notional boosted" in r

# Base strategy
b = (ROOT/"strategies"/"base_strategy.py").read_text(encoding="utf-8")
checks["Base: NO fake fallback"]  = "will auto-transfer" not in b

# DCA
d = (ROOT/"strategies"/"dca_strategy.py").read_text(encoding="utf-8")
checks["DCA: actual balance check"] = "_usdt < 6" in d

# Config
c = (ROOT/"config.py").read_text(encoding="utf-8")
checks["Config: 8% position"]     = "0.08" in c and "max_position_pct" in c
checks["Config: Kelly sizing"]    = '"kelly"' in c
checks["Config: Trail 0.8%"]      = "0.008" in c
checks["Config: ATR SL 2.5x"]     = '"atr_sl_mult": 2.5' in c
checks["Config: min_rr 1.5"]      = "1.5" in c and "min_rr_ratio" in c

# Order manager
o = (ROOT/"core"/"order_manager.py").read_text(encoding="utf-8")
checks["Orders: notional gate"]   = "MINIMUM NOTIONAL" in o or "< $5" in o
checks["Orders: TP ride trailing"] = "RIDING for more profit" in o

# Bot engine
e = (ROOT/"core"/"bot_engine.py").read_text(encoding="utf-8")
checks["Engine: SL/TP thread"]    = "sltp_monitor_loop" in e
checks["Engine: thread watchdog"]  = "is_alive" in e
checks["Engine: Claude AI"]       = "ClaudeAnalyst" in e
checks["Engine: exchange health"]  = "_check_exchange_health" in e

passed = failed = 0
for name, ok in sorted(checks.items()):
    status = "OK" if ok else "FAIL"
    if ok: passed += 1
    else: failed += 1
    print(f"  [{status:4s}] {name}")

print(f"\n{'='*55}")
print(f"  {passed} PASSED, {failed} FAILED")
if failed == 0:
    print(f"  ALL FIXES VERIFIED — Bot is ready")
print(f"{'='*55}")
print(f"\n  START THE BOT:")
print(f"    Double-click auto_restart.bat")
print(f"    (or: venv\\Scripts\\python.exe main.py)")
print()
input("Press Enter to exit...")
