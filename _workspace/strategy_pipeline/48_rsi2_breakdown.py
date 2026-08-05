"""S1 dossier data pull: rsi2_4h_cfg226 resolved-outcome breakdown (read-only)."""
import sqlite3
import datetime
from collections import defaultdict

db = sqlite3.connect("file:data/warehouse.sqlite?mode=ro", uri=True)
db.row_factory = sqlite3.Row
q = """
SELECT d.symbol, d.side, o.exit_reason, o.gross_pnl, o.net_pnl, o.fees,
       o.slippage, o.funding, o.r_multiple, o.bars_held, o.resolved_ts
FROM shadow_outcomes o JOIN shadow_decisions d ON d.proposal_id = o.proposal_id
WHERE d.agent_id = 'Rsi2TrackerProbeAgent' AND o.label_status = 'RESOLVED'
"""
rows = [dict(r) for r in db.execute(q)]
n = len(rows)
wins = [r for r in rows if r["net_pnl"] > 0]
losses = [r for r in rows if r["net_pnl"] <= 0]
print("n =", n, "wins =", len(wins), "wr =", round(len(wins) / n, 4))
net = sum(r["net_pnl"] for r in rows)
gross = sum(r["gross_pnl"] for r in rows)
fees = sum(r["fees"] or 0 for r in rows)
slip = sum(r["slippage"] or 0 for r in rows)
fund = sum(r["funding"] or 0 for r in rows)
print(f"net={net:.2f} gross={gross:.2f} fees={fees:.2f} slip={slip:.2f} funding={fund:.2f}")
print("avg win =", round(sum(r["net_pnl"] for r in wins) / len(wins), 4),
      "avg loss =", round(sum(r["net_pnl"] for r in losses) / len(losses), 4))
print("payoff ratio =", round(abs(sum(r["net_pnl"] for r in wins) / len(wins)) /
      abs(sum(r["net_pnl"] for r in losses) / len(losses)), 4))

by = defaultdict(lambda: [0, 0, 0.0, 0.0])
for r in rows:
    b = by[r["exit_reason"]]
    b[0] += 1
    b[1] += 1 if r["net_pnl"] > 0 else 0
    b[2] += r["net_pnl"]
    b[3] += r["r_multiple"] or 0
print("--- by exit_reason: n, wins, net, avgR")
for k, v in sorted(by.items()):
    print(k, v[0], v[1], round(v[2], 2), round(v[3] / v[0], 3))

bys = defaultdict(lambda: [0, 0, 0.0])
for r in rows:
    b = bys[r["side"]]
    b[0] += 1
    b[1] += 1 if r["net_pnl"] > 0 else 0
    b[2] += r["net_pnl"]
print("--- by side: n, wins, net")
for k, v in sorted(bys.items()):
    print(k, v[0], v[1], round(v[2], 2))

sym = defaultdict(lambda: [0, 0.0])
for r in rows:
    b = sym[r["symbol"]]
    b[0] += 1
    b[1] += r["net_pnl"]
worst = sorted(sym.items(), key=lambda kv: kv[1][1])[:5]
best = sorted(sym.items(), key=lambda kv: kv[1][1])[-3:]
print("--- worst symbols:", [(k, v[0], round(v[1], 2)) for k, v in worst])
print("--- best symbols:", [(k, v[0], round(v[1], 2)) for k, v in best])

ts = sorted(r["resolved_ts"] for r in rows)
print("window:", datetime.datetime.utcfromtimestamp(ts[0]), "->",
      datetime.datetime.utcfromtimestamp(ts[-1]))
rm = [r["r_multiple"] for r in rows if r["r_multiple"] is not None]
print("mean R =", round(sum(rm) / len(rm), 4))

import math
vals = [r["net_pnl"] for r in rows]
m = sum(vals) / n
sd = math.sqrt(sum((x - m) ** 2 for x in vals) / (n - 1))
se = sd / math.sqrt(n)
print(f"expectancy mean={m:.4f} sd={sd:.3f} se={se:.4f} t={m / se:.3f} "
      f"ci95=[{m - 1.96 * se:.4f}, {m + 1.96 * se:.4f}]")
ex_zec = [r["net_pnl"] for r in rows if r["symbol"] != "ZEC/USDT:USDT"]
print("ex-ZEC net =", round(sum(ex_zec), 2), "n =", len(ex_zec))

cut = datetime.datetime(2026, 7, 20, tzinfo=datetime.timezone.utc).timestamp()
pre = [r for r in rows if r["resolved_ts"] < cut]
post = [r for r in rows if r["resolved_ts"] >= cut]
for name, seg in (("pre-widen", pre), ("post-widen", post)):
    if seg:
        w = sum(1 for r in seg if r["net_pnl"] > 0)
        print(name, "n", len(seg), "wr", round(w / len(seg), 3),
              "net", round(sum(r["net_pnl"] for r in seg), 2))
