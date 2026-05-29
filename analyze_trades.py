"""Analyze live trade history to identify loss drivers."""
import json
from collections import defaultdict
from pathlib import Path

def load_trades():
    """Load all trades from compliance logs. Handles JSON array, JSONL, or mixed."""
    trades = []
    for p in sorted(Path("data/compliance").glob("trades_*.json")):
        raw = p.read_text(encoding="utf-8").strip()
        if not raw:
            continue
        # Try decoding as one array first; if there's extra data, split
        idx = 0
        dec = json.JSONDecoder()
        while idx < len(raw):
            while idx < len(raw) and raw[idx] in " \r\n\t":
                idx += 1
            if idx >= len(raw):
                break
            obj, offset = dec.raw_decode(raw, idx)
            if isinstance(obj, list):
                trades.extend(obj)
            elif isinstance(obj, dict):
                trades.append(obj)
            idx = offset
    return trades


def pair_entries_exits(trades):
    """Match exits to entries by order_id. Returns list of round-trip trades."""
    entries = {}  # order_id -> entry trade
    roundtrips = []
    for t in trades:
        oid = t.get("order_id", "")
        if t.get("reason") == "entry" or t.get("pnl_usdt", 0) == 0:
            # Treat as entry if marked entry OR if pnl==0 (could be tracked as entry)
            if t.get("reason") == "entry":
                entries[oid] = t
        else:
            # Exit
            if oid in entries:
                entry = entries.pop(oid)
                roundtrips.append({
                    "symbol": t["symbol"],
                    "exchange": t["exchange"],
                    "strategy": entry.get("strategy", "unknown"),
                    "entry_side": entry["side"],
                    "entry_price": entry["price"],
                    "exit_price": t["price"],
                    "notional": entry["notional"],
                    "pnl_usdt": t.get("pnl_usdt", 0),
                    "exit_reason": t.get("reason", "unknown"),
                    "entry_time": entry["timestamp"],
                    "exit_time": t["timestamp"],
                    "paper": t.get("paper_trade", False),
                })
    return roundtrips, entries


def analyze(roundtrips):
    """Print comprehensive analysis."""
    real = [r for r in roundtrips if not r["paper"]]
    print(f"\n{'='*80}")
    print(f"TOTAL ROUND-TRIP TRADES: {len(real)} (real only)")
    print(f"{'='*80}\n")

    wins = [r for r in real if r["pnl_usdt"] > 0]
    losses = [r for r in real if r["pnl_usdt"] < 0]
    flats = [r for r in real if r["pnl_usdt"] == 0]

    total_pnl = sum(r["pnl_usdt"] for r in real)
    total_win = sum(r["pnl_usdt"] for r in wins)
    total_loss = sum(r["pnl_usdt"] for r in losses)

    print(f"Wins:   {len(wins)}  (avg ${total_win/len(wins):+.2f})" if wins else "Wins: 0")
    print(f"Losses: {len(losses)} (avg ${total_loss/len(losses):+.2f})" if losses else "Losses: 0")
    print(f"Flat:   {len(flats)}")
    print(f"Win Rate:  {len(wins)/max(1,len(real))*100:.1f}%")
    print(f"Total PnL: ${total_pnl:+.2f}")
    print(f"Profit Factor: {total_win / abs(total_loss):.2f}" if losses else "PF: inf")
    print(f"Break-even WR needed: {abs(total_loss/len(losses)) / (abs(total_loss/len(losses)) + total_win/max(1,len(wins))) * 100:.1f}%")

    # By strategy
    print(f"\n{'='*80}")
    print("BY STRATEGY")
    print(f"{'='*80}")
    print(f"{'Strategy':<25} {'N':>5} {'Wins':>6} {'WR%':>6} {'PnL':>10} {'Avg':>8}")
    by_strat = defaultdict(list)
    for r in real:
        by_strat[r["strategy"]].append(r)
    for strat, rs in sorted(by_strat.items(), key=lambda x: sum(r["pnl_usdt"] for r in x[1])):
        w = sum(1 for r in rs if r["pnl_usdt"] > 0)
        pnl = sum(r["pnl_usdt"] for r in rs)
        avg = pnl / len(rs)
        print(f"{strat:<25} {len(rs):>5} {w:>6} {w/len(rs)*100:>5.0f}% {pnl:>+10.2f} {avg:>+8.2f}")

    # By exit reason
    print(f"\n{'='*80}")
    print("BY EXIT REASON")
    print(f"{'='*80}")
    print(f"{'Reason':<25} {'N':>5} {'Wins':>6} {'WR%':>6} {'PnL':>10} {'Avg':>8}")
    by_reason = defaultdict(list)
    for r in real:
        by_reason[r["exit_reason"]].append(r)
    for reason, rs in sorted(by_reason.items(), key=lambda x: sum(r["pnl_usdt"] for r in x[1])):
        w = sum(1 for r in rs if r["pnl_usdt"] > 0)
        pnl = sum(r["pnl_usdt"] for r in rs)
        avg = pnl / len(rs)
        print(f"{reason:<25} {len(rs):>5} {w:>6} {w/len(rs)*100:>5.0f}% {pnl:>+10.2f} {avg:>+8.2f}")

    # By exchange
    print(f"\n{'='*80}")
    print("BY EXCHANGE")
    print(f"{'='*80}")
    print(f"{'Exchange':<15} {'N':>5} {'Wins':>6} {'WR%':>6} {'PnL':>10} {'Avg':>8}")
    by_ex = defaultdict(list)
    for r in real:
        by_ex[r["exchange"]].append(r)
    for ex, rs in sorted(by_ex.items(), key=lambda x: sum(r["pnl_usdt"] for r in x[1])):
        w = sum(1 for r in rs if r["pnl_usdt"] > 0)
        pnl = sum(r["pnl_usdt"] for r in rs)
        avg = pnl / len(rs)
        print(f"{ex:<15} {len(rs):>5} {w:>6} {w/len(rs)*100:>5.0f}% {pnl:>+10.2f} {avg:>+8.2f}")

    # Worst 10 trades
    print(f"\n{'='*80}")
    print("WORST 15 TRADES (drag)")
    print(f"{'='*80}")
    print(f"{'Symbol':<20} {'Strategy':<20} {'Reason':<20} {'Notional':>10} {'PnL':>10}")
    worst = sorted(real, key=lambda r: r["pnl_usdt"])[:15]
    for r in worst:
        print(f"{r['symbol']:<20} {r['strategy']:<20} {r['exit_reason']:<20} {r['notional']:>10.2f} {r['pnl_usdt']:>+10.2f}")

    # Best 10 trades
    print(f"\n{'='*80}")
    print("BEST 10 TRADES")
    print(f"{'='*80}")
    print(f"{'Symbol':<20} {'Strategy':<20} {'Reason':<20} {'Notional':>10} {'PnL':>10}")
    best = sorted(real, key=lambda r: -r["pnl_usdt"])[:10]
    for r in best:
        print(f"{r['symbol']:<20} {r['strategy']:<20} {r['exit_reason']:<20} {r['notional']:>10.2f} {r['pnl_usdt']:>+10.2f}")

    # Notional size distribution
    print(f"\n{'='*80}")
    print("POSITION SIZE DISTRIBUTION")
    print(f"{'='*80}")
    buckets = [(0, 10), (10, 25), (25, 50), (50, 100), (100, 500)]
    for lo, hi in buckets:
        rs = [r for r in real if lo <= r["notional"] < hi]
        if not rs:
            continue
        w = sum(1 for r in rs if r["pnl_usdt"] > 0)
        pnl = sum(r["pnl_usdt"] for r in rs)
        print(f"  ${lo:>3}-${hi:<3}: N={len(rs):>3}  WR={w/len(rs)*100:>5.1f}%  PnL=${pnl:>+7.2f}  avg=${pnl/len(rs):>+6.2f}")

    # Strategy + reason cross
    print(f"\n{'='*80}")
    print("STRATEGY x EXIT REASON — where are losses coming from?")
    print(f"{'='*80}")
    print(f"{'Strategy':<20} {'Reason':<25} {'N':>4} {'Wins':>5} {'Loss$':>8} {'Total$':>8}")
    combos = defaultdict(list)
    for r in real:
        combos[(r["strategy"], r["exit_reason"])].append(r)
    for (s, rsn), rs in sorted(combos.items(), key=lambda x: sum(r["pnl_usdt"] for r in x[1])):
        w = sum(1 for r in rs if r["pnl_usdt"] > 0)
        tot = sum(r["pnl_usdt"] for r in rs)
        avg_loss = sum(r["pnl_usdt"] for r in rs if r["pnl_usdt"] < 0) / max(1, sum(1 for r in rs if r["pnl_usdt"] < 0))
        print(f"{s:<20} {rsn:<25} {len(rs):>4} {w:>5} {avg_loss:>+8.2f} {tot:>+8.2f}")


if __name__ == "__main__":
    trades = load_trades()
    print(f"Loaded {len(trades)} trade records from compliance logs.")
    rts, unmatched = pair_entries_exits(trades)
    print(f"Matched {len(rts)} round-trips. Unmatched entries: {len(unmatched)}")
    analyze(rts)
