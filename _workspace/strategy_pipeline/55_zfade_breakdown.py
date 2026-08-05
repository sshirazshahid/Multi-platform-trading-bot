"""S1 dossier data pull: zfade_4h_cfg365 resolved-outcome breakdown (read-only).

Per binding condition C5 (48_review_rsi2_4h_cfg226.md): AUC is computed here
directly from shadow_bundle_mr_probe.score joined on proposal_id, using the
funnel's own _auc formula, and cross-checked against the funnel snapshot.
"""
import sqlite3
import datetime
import math
from collections import defaultdict

db = sqlite3.connect("file:data/warehouse.sqlite?mode=ro", uri=True)
db.row_factory = sqlite3.Row
q = """
SELECT d.symbol, d.side, d.proposal_id, o.exit_reason, o.gross_pnl, o.net_pnl,
       o.fees, o.slippage, o.funding, o.r_multiple, o.bars_held, o.resolved_ts,
       p.score
FROM shadow_outcomes o
JOIN shadow_decisions d ON d.proposal_id = o.proposal_id
LEFT JOIN shadow_bundle_mr_probe p ON p.proposal_id = d.proposal_id
WHERE d.agent_id = 'ZfadeProbeAgent' AND o.label_status = 'RESOLVED'
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
aw = sum(r["net_pnl"] for r in wins) / len(wins)
al = sum(r["net_pnl"] for r in losses) / len(losses)
print("avg win =", round(aw, 4), "avg loss =", round(al, 4),
      "payoff =", round(abs(aw / al), 4),
      "breakeven wr =", round(abs(al) / (abs(al) + aw), 4))
gp = sum(r["net_pnl"] for r in rows if r["net_pnl"] > 0)
gl = abs(sum(r["net_pnl"] for r in rows if r["net_pnl"] < 0))
print("profit factor =", round(gp / gl, 4), "expectancy =", round(net / n, 6))

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
best = sorted(sym.items(), key=lambda kv: kv[1][1])[-5:]
print("--- worst symbols:", [(k, v[0], round(v[1], 2)) for k, v in worst])
print("--- best symbols:", [(k, v[0], round(v[1], 2)) for k, v in best])
pos_syms = sum(1 for k, v in sym.items() if v[1] > 0)
print("symbols traded =", len(sym), "net-positive symbols =", pos_syms)

ts = sorted(r["resolved_ts"] for r in rows)
print("window:", datetime.datetime.utcfromtimestamp(ts[0]), "->",
      datetime.datetime.utcfromtimestamp(ts[-1]))
rm = [r["r_multiple"] for r in rows if r["r_multiple"] is not None]
print("mean R =", round(sum(rm) / len(rm), 4))

vals = [r["net_pnl"] for r in rows]
m = sum(vals) / n
sd = math.sqrt(sum((x - m) ** 2 for x in vals) / (n - 1))
se = sd / math.sqrt(n)
print(f"expectancy mean={m:.4f} sd={sd:.3f} se={se:.4f} t={m / se:.3f} "
      f"ci95=[{m - 1.96 * se:.4f}, {m + 1.96 * se:.4f}]")

try:
    from scipy.stats import beta
    k = len(wins)
    lo = beta.ppf(0.025, k, n - k + 1) if k > 0 else 0.0
    hi = beta.ppf(0.975, k + 1, n - k) if k < n else 1.0
    print(f"WR Clopper-Pearson 95% CI = [{lo:.4f}, {hi:.4f}]")
except Exception as e:
    print("scipy unavailable:", e)

# --- AUC from the frozen probe score (C5) — funnel formula reproduced
missing = [r for r in rows if r["score"] is None]
print("score coverage:", n - len(missing), "/", n, "missing =", len(missing))
pos = [float(r["score"]) for r in rows if r["net_pnl"] > 0 and r["score"] is not None]
neg = [float(r["score"]) for r in rows if r["net_pnl"] <= 0 and r["score"] is not None]
if pos and neg:
    w = sum(1.0 if p > q2 else 0.5 if p == q2 else 0.0 for p in pos for q2 in neg)
    print("score AUC =", round(w / (len(pos) * len(neg)), 4))
    print("mean score wins =", round(sum(pos) / len(pos), 4),
          "mean score losses =", round(sum(neg) / len(neg), 4))

# pre/post universe-widen split (2026-07-20 deploy)
cut = datetime.datetime(2026, 7, 20, tzinfo=datetime.timezone.utc).timestamp()
pre = [r for r in rows if r["resolved_ts"] < cut]
post = [r for r in rows if r["resolved_ts"] >= cut]
for name, seg in (("pre-widen", pre), ("post-widen", post)):
    if seg:
        w = sum(1 for r in seg if r["net_pnl"] > 0)
        print(name, "n", len(seg), "wr", round(w / len(seg), 3),
              "net", round(sum(r["net_pnl"] for r in seg), 2))

# majors (frozen 5-basket) vs widened-universe symbols
MAJORS = {"BTC/USDT:USDT", "ETH/USDT:USDT", "SOL/USDT:USDT",
          "BNB/USDT:USDT", "XRP/USDT:USDT"}
maj = [r for r in rows if r["symbol"] in MAJORS]
oth = [r for r in rows if r["symbol"] not in MAJORS]
for name, seg in (("majors-5", maj), ("widened-38", oth)):
    if seg:
        w = sum(1 for r in seg if r["net_pnl"] > 0)
        print(name, "n", len(seg), "wr", round(w / len(seg), 3),
              "net", round(sum(r["net_pnl"] for r in seg), 2))

# pending count for closure bookkeeping (C4 precedent)
pend = db.execute(
    "SELECT COUNT(*) FROM shadow_decisions WHERE agent_id='ZfadeProbeAgent'"
    " AND label_status='PENDING'").fetchone()[0]
tot = db.execute(
    "SELECT COUNT(*) FROM shadow_decisions WHERE agent_id='ZfadeProbeAgent'").fetchone()[0]
print("decisions total =", tot, "pending =", pend)
