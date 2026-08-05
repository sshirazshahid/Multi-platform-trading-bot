"""S1 dossier data pull: tsmom_20d_1h resolved-outcome breakdown (read-only).

Adapted from 55_zfade_breakdown.py. Per binding condition C5
(48_review_rsi2_4h_cfg226.md): AUC is computed here directly from
shadow_tsmom_probe.score joined on proposal_id, using the funnel's own _auc
formula, and cross-checked against the funnel snapshot. Lane selected by
model_version = 'tsmom_20d_1h_v1' (TsmomProbeAgent serves both 1h and 4h lanes).
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
       p.score, p.arm
FROM shadow_outcomes o
JOIN shadow_decisions d ON d.proposal_id = o.proposal_id
LEFT JOIN shadow_tsmom_probe p ON p.proposal_id = d.proposal_id
WHERE d.agent_id = 'TsmomProbeAgent' AND d.model_version = 'tsmom_20d_1h_v1'
  AND o.label_status = 'RESOLVED'
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

bya = defaultdict(lambda: [0, 0, 0.0])
for r in rows:
    b = bya[r["arm"]]
    b[0] += 1
    b[1] += 1 if r["net_pnl"] > 0 else 0
    b[2] += r["net_pnl"]
print("--- by arm: n, wins, net")
for k, v in sorted(bya.items(), key=lambda kv: str(kv[0])):
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

# majors (frozen 5-basket) vs any widened symbols
MAJORS = {"BTC/USDT:USDT", "ETH/USDT:USDT", "SOL/USDT:USDT",
          "BNB/USDT:USDT", "XRP/USDT:USDT"}
maj = [r for r in rows if r["symbol"] in MAJORS]
oth = [r for r in rows if r["symbol"] not in MAJORS]
for name, seg in (("majors-5", maj), ("non-majors", oth)):
    if seg:
        w = sum(1 for r in seg if r["net_pnl"] > 0)
        print(name, "n", len(seg), "wr", round(w / len(seg), 3),
              "net", round(sum(r["net_pnl"] for r in seg), 2))

# monthly buckets (regime persistence check)
bym = defaultdict(lambda: [0, 0, 0.0])
for r in rows:
    mth = datetime.datetime.utcfromtimestamp(r["resolved_ts"]).strftime("%Y-%m")
    b = bym[mth]
    b[0] += 1
    b[1] += 1 if r["net_pnl"] > 0 else 0
    b[2] += r["net_pnl"]
print("--- by month: n, wins, net")
for k, v in sorted(bym.items()):
    print(k, v[0], v[1], round(v[2], 2))

# pending count for closure bookkeeping (C4 precedent)
pend = db.execute(
    "SELECT COUNT(*) FROM shadow_decisions WHERE agent_id='TsmomProbeAgent'"
    " AND model_version='tsmom_20d_1h_v1' AND label_status='PENDING'").fetchone()[0]
tot = db.execute(
    "SELECT COUNT(*) FROM shadow_decisions WHERE agent_id='TsmomProbeAgent'"
    " AND model_version='tsmom_20d_1h_v1'").fetchone()[0]
print("decisions total =", tot, "pending =", pend)
