"""
fix_ghost_positions.py — Remove phantom open positions with invalid symbols.

Run this if the dashboard shows positions for symbols that no longer exist
or were from a previous session that didn't close cleanly.
"""

import json
from pathlib import Path

PROFILES = ["conservative", "moderate", "aggressive"]

# Any open position NOT on this list is considered a ghost
VALID_SYMBOLS = {
    "BTC/USDT", "BTC/USDT:USDT",
    "ETH/USDT", "ETH/USDT:USDT",
    "BNB/USDT", "BNB/USDT:USDT",
    "SOL/USDT", "SOL/USDT:USDT",
    "XRP/USDT", "XRP/USDT:USDT",
    "ADA/USDT", "ADA/USDT:USDT",
    "DOGE/USDT","DOGE/USDT:USDT",
    "AVAX/USDT","AVAX/USDT:USDT",
    "DOT/USDT", "DOT/USDT:USDT",
    "LINK/USDT","LINK/USDT:USDT",
    "XAU/USDT", "XAU/USDT:USDT",   # Gold
    "XAG/USDT", "XAG/USDT:USDT",   # Silver
    "WTI/USDT", "WTI/USDT:USDT",   # Oil
}

total_removed = 0

for profile in PROFILES:
    path = Path(f"data/profiles/{profile}/positions.json")
    if not path.exists():
        print(f"[{profile}] No positions file — skipping")
        continue

    data = json.loads(path.read_text(encoding="utf-8"))
    before = len(data.get("open", []))

    clean_open = [
        p for p in data.get("open", [])
        if p.get("symbol") in VALID_SYMBOLS
    ]
    removed = before - len(clean_open)
    total_removed += removed

    if removed:
        ghosts = [p for p in data.get("open", []) if p.get("symbol") not in VALID_SYMBOLS]
        print(f"[{profile}] Removing {removed} ghost position(s):")
        for g in ghosts:
            print(f"     {g.get('exchange','?')} {g.get('side','?')} "
                  f"{g.get('symbol','?')} @ {g.get('entry_price',0):.4g}")
        data["open"] = clean_open
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        print(f"[{profile}] Saved — {len(clean_open)} open position(s) remain")
    else:
        print(f"[{profile}] Clean — {before} open position(s), none are ghosts")

print()
if total_removed == 0:
    print("All profiles clean — no ghost positions found.")
else:
    print(f"Done — removed {total_removed} ghost position(s).")
    print("Restart the dashboard to see the updated state.")
