"""Reconcile data/virtual_wallet.json to a clean seed baseline.

Clears cumulative phantom credits (e.g. the 2026-06-11 +913.52 unmatched-margin
event documented in core/virtual_wallet.py) by resetting each exchange balance to
the configured start, then RE-DEBITING the margin (+ est. entry fee) of every
currently-open paper position so their eventual on_close credits have a matching
debit and no NEW phantom is created.

Past realized performance is NOT touched — it lives in the warehouse and the
dashboard's "Trade PnL (all history)" line. This only repairs the corrupted
cumulative wallet number.

The running bot holds the wallet in memory and rewrites the file on every trade,
so the bot MUST be stopped before --apply and restarted after (it _load()s the
corrected file on boot).

Usage:  python scripts/reconcile_virtual_wallet.py            # dry-run
        python scripts/reconcile_virtual_wallet.py --apply    # writes + backs up
"""
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WALLET = ROOT / "data" / "virtual_wallet.json"
POSITIONS = ROOT / "data" / "positions.json"

try:
    from config import DRY_RUN_BALANCE
    START = float(DRY_RUN_BALANCE)
except Exception:
    START = 5000.0

try:
    from core.position_tracker import _fee_rate
except Exception:
    def _fee_rate(mt):
        return 0.0005 if mt == "futures" else 0.001


def open_margins():
    """Per-exchange (margin + est entry fee) of open paper positions."""
    out = {}
    if not POSITIONS.exists():
        return out
    rows = json.loads(POSITIONS.read_text(encoding="utf-8")).get("open", [])
    for d in rows:
        if not d.get("paper_trade"):
            continue
        ex = str(d.get("exchange", "")).lower()
        size = float(d.get("size") or 0)
        entry = float(d.get("entry_price") or 0)
        lev = int(d.get("leverage") or 1)
        mt = d.get("market_type", "futures")
        if not ex or size <= 0 or entry <= 0:
            continue
        notional = size * entry
        margin = notional / lev if (mt == "futures" and lev > 1) else notional
        cost = margin + notional * _fee_rate(mt)
        out[ex] = out.get(ex, 0.0) + cost
    return out


def main():
    apply = "--apply" in sys.argv
    cur = json.loads(WALLET.read_text(encoding="utf-8")) if WALLET.exists() else {}
    cur_bal = cur.get("balances", {})
    margins = open_margins()
    exchanges = sorted(set(list(cur_bal) + list(margins)) or ["binance", "bybit", "bitget"])
    new_bal = {ex: round(START - margins.get(ex, 0.0), 8) for ex in exchanges}

    print(f"seed/exchange={START}  open-position re-debits={ {k: round(v,4) for k,v in margins.items()} }")
    print(f"{'exchange':<10}{'current':>14}{'reconciled':>14}{'delta':>14}")
    for ex in exchanges:
        c = float(cur_bal.get(ex, START))
        n = new_bal[ex]
        print(f"{ex:<10}{c:>14.4f}{n:>14.4f}{n-c:>14.4f}")
    tot_cur = sum(float(cur_bal.get(ex, START)) for ex in exchanges)
    tot_new = sum(new_bal.values())
    print(f"{'TOTAL':<10}{tot_cur:>14.4f}{tot_new:>14.4f}{tot_new-tot_cur:>14.4f}")

    if not apply:
        print("\nDRY-RUN — no file written. Re-run with --apply (bot must be stopped first).")
        return

    backup = WALLET.with_name(f"virtual_wallet.pre_reconcile_{int(time.time())}.json")
    if WALLET.exists():
        backup.write_text(WALLET.read_text(encoding="utf-8"), encoding="utf-8")
        print(f"\nbackup -> {backup.name}")
    WALLET.write_text(json.dumps({"start": START, "balances": new_bal, "saved_at": time.time()}, indent=2), encoding="utf-8")
    print(f"written -> {WALLET}  (restart the bot to load it)")


if __name__ == "__main__":
    main()
