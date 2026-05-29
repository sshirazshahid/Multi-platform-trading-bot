"""Split analysis by strategy + date — is legacy still running?"""
import json
from collections import defaultdict
from pathlib import Path


def load_trades():
    trades = []
    for p in sorted(Path("data/compliance").glob("trades_*.json")):
        raw = p.read_text(encoding="utf-8").strip()
        if not raw:
            continue
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


def pair(trades):
    entries = {}
    rts = []
    for t in trades:
        oid = t.get("order_id", "")
        if t.get("reason") == "entry":
            entries[oid] = t
        elif oid in entries:
            e = entries.pop(oid)
            rts.append({
                "strategy": e.get("strategy", "?"),
                "pnl": t.get("pnl_usdt", 0),
                "reason": t.get("reason", "?"),
                "entry_ts": e["timestamp"],
                "exit_ts": t["timestamp"],
                "notional": e["notional"],
                "symbol": t["symbol"],
                "paper": t.get("paper_trade", False),
            })
    return rts


def main():
    trades = load_trades()
    rts = [r for r in pair(trades) if not r["paper"]]

    # Group by strategy + day
    by_day_strat = defaultdict(lambda: defaultdict(list))
    for r in rts:
        day = r["exit_ts"][:10]
        by_day_strat[day][r["strategy"]].append(r)

    print(f"\n{'Date':<12} {'Strategy':<20} {'N':>3} {'Win%':>5} {'PnL$':>8}")
    print("-" * 52)
    for day in sorted(by_day_strat.keys()):
        for strat, rs in sorted(by_day_strat[day].items()):
            w = sum(1 for r in rs if r["pnl"] > 0)
            p = sum(r["pnl"] for r in rs)
            print(f"{day:<12} {strat:<20} {len(rs):>3} {w/len(rs)*100:>4.0f}% {p:>+8.2f}")

    # Is legacy still firing post-Apr 9 (v3 engine activation)?
    print(f"\n{'='*60}")
    print("LEGACY STRATEGIES POST v3 (after 2026-04-09):")
    print(f"{'='*60}")
    post_v3 = [r for r in rts if r["exit_ts"] >= "2026-04-09"]
    legacy_strats = {"Supertrend", "MultiTF", "TrendFollowing", "MeanReversion"}
    legacy_post = [r for r in post_v3 if r["strategy"] in legacy_strats]
    if legacy_post:
        print(f"YES — {len(legacy_post)} legacy trades since Apr 9:")
        for r in legacy_post:
            print(f"  {r['exit_ts'][:16]} {r['strategy']:<15} {r['symbol']:<18} "
                  f"{r['reason']:<20} ${r['pnl']:+.2f}")
    else:
        print("NO — only claude_portfolio trades since Apr 9. Legacy disabled.")

    # v3 engine performance
    v3 = [r for r in rts if r["strategy"] == "claude_portfolio"]
    if v3:
        w = sum(1 for r in v3 if r["pnl"] > 0)
        p = sum(r["pnl"] for r in v3)
        wins = [r["pnl"] for r in v3 if r["pnl"] > 0]
        losses = [r["pnl"] for r in v3 if r["pnl"] < 0]
        aw = sum(wins) / len(wins) if wins else 0
        al = sum(losses) / len(losses) if losses else 0
        print(f"\n{'='*60}")
        print(f"V3 ENGINE (claude_portfolio) — PURE PERFORMANCE:")
        print(f"{'='*60}")
        print(f"  Trades:   {len(v3)}")
        print(f"  Win Rate: {w/len(v3)*100:.1f}%")
        print(f"  Avg Win:  ${aw:+.2f}")
        print(f"  Avg Loss: ${al:+.2f}")
        print(f"  Total:    ${p:+.2f}")
        print(f"  Realized R:R: {abs(aw/al):.2f}:1" if al else "inf")
        print(f"  Break-even WR: {abs(al) / (abs(al) + aw) * 100:.1f}%")


if __name__ == "__main__":
    main()
