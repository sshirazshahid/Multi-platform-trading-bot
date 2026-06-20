"""Trade-analysis breakdown — freqtrade-style grouped reports over the warehouse.

The ONE freqtrade capability genuinely worth adding to this bot (per the 2026-06-07
capability-gap analysis): freqtrade's `backtesting-analysis` per-group breakdowns
(per exit-reason / per-pair / per-tag / rejected-signals), adapted to this bot's
`data/warehouse.sqlite`. It is read-only and EXPOSES where PnL leaks rather than
manufacturing edge — exactly the honest diagnostic a confirmed no-edge book needs.

Goes beyond freqtrade in two ways the warehouse uniquely allows:
  * cost attribution (alpha vs spread vs slippage vs funding vs fees) from the
    `attribution` table — *where the money actually goes*;
  * beta decomposition (realized PnL vs alpha_vs_btc) — how much loss is BTC-beta
    vs intrinsic, the bot's central finding.

Records reports/trade_breakdown_<date>.md (+ .json). Read-only.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "data" / "warehouse.sqlite"
REPORTS = ROOT / "reports"


# ---- pure, testable aggregation core ---------------------------------------
def summarize(pnl: np.ndarray) -> dict:
    """Headline stats for a vector of realized PnL ($). Pure."""
    pnl = np.asarray(pnl, dtype=float)
    n = len(pnl)
    if n == 0:
        return {"n": 0}
    wins = pnl[pnl > 0]
    losses = pnl[pnl <= 0]
    avg_w = float(wins.mean()) if len(wins) else 0.0
    avg_l = float(losses.mean()) if len(losses) else 0.0  # <= 0
    payoff = (avg_w / abs(avg_l)) if avg_l < 0 else float("inf") if avg_w > 0 else 0.0
    pf = (wins.sum() / abs(losses.sum())) if losses.sum() < 0 else float("inf")
    # breakeven WR given this payoff: |avg_l| / (avg_w + |avg_l|)
    denom = avg_w + abs(avg_l)
    be_wr = (abs(avg_l) / denom) if denom > 0 else float("nan")
    # max consecutive losses (input order assumed chronological)
    mcl = cur = 0
    for x in pnl:
        cur = cur + 1 if x <= 0 else 0
        mcl = max(mcl, cur)
    return {
        "n": n,
        "win_rate": float(len(wins) / n),
        "total_pnl": float(pnl.sum()),
        "avg_pnl": float(pnl.mean()),
        "avg_win": avg_w,
        "avg_loss": avg_l,
        "payoff": payoff,
        "profit_factor": float(pf),
        "expectancy_per_trade": float(pnl.mean()),
        "breakeven_wr": float(be_wr),
        "actual_minus_breakeven_wr": float(len(wins) / n - be_wr) if denom > 0 else float("nan"),
        "max_consec_losses": int(mcl),
    }


_KNOWN_EXITS = {
    "stop_loss",
    "take_profit",
    "trailing_stop",
    "entry_invalidated",
    "mcp_brain_close",
    "systematic_close",
    "stale",
    "ghost_sync",
    "reconciled_from_exchange",
    "manual",
    "liquidation",
    "breakeven",
    "partial_tp",
    "time_exit",
    "daily_loss_lock",
}


def norm_exit(reason) -> str:
    """Collapse free-text Claude rationales used as exit_reason into one bucket.

    Surfaces (and contains) a real data-quality issue: exit_reason is sometimes the
    Claude commentary string instead of a categorical reason. Keep categorical tokens;
    bucket prose as 'claude_freetext'."""
    if reason is None or (isinstance(reason, float) and np.isnan(reason)):
        return "∅"
    r = str(reason).strip()
    if r.lower() in _KNOWN_EXITS:
        return r.lower()
    # short single-token, no spaces → treat as a (possibly new) categorical reason
    if len(r) <= 20 and " " not in r:
        return r
    return "claude_freetext"


def group_breakdown(
    df: pd.DataFrame, by: str, pnl_col: str = "realized_pnl", r_col: str = "r_multiple"
) -> list[dict]:
    """Per-group {group, n, wr, sum_pnl, avg_pnl, avg_r, pct_of_total_pnl}. Pure."""
    if by not in df.columns or len(df) == 0:
        return []
    total = df[pnl_col].sum()
    out = []
    for g, sub in df.groupby(by, dropna=False, observed=True):
        p = sub[pnl_col].to_numpy(dtype=float)
        wr = float((p > 0).mean()) if len(p) else 0.0
        avg_r = (
            float(pd.to_numeric(sub[r_col], errors="coerce").dropna().mean())
            if r_col in sub
            else float("nan")
        )
        out.append(
            {
                "group": "∅" if (g is None or (isinstance(g, float) and np.isnan(g))) else str(g),
                "n": int(len(sub)),
                "win_rate": wr,
                "sum_pnl": float(p.sum()),
                "avg_pnl": float(p.mean()) if len(p) else 0.0,
                "avg_r": avg_r,
                "pct_of_total_pnl": float(p.sum() / total * 100) if total != 0 else float("nan"),
            }
        )
    return sorted(out, key=lambda r: r["sum_pnl"])  # worst first (where loss concentrates)


def score_buckets(df: pd.DataFrame) -> list[dict]:
    """Breakdown by mcp_score bucket — the bot's 'enter_tag' analog (tests predictiveness)."""
    if "mcp_score" not in df.columns:
        return []
    d = df.copy()
    d["mcp_score"] = pd.to_numeric(d["mcp_score"], errors="coerce")
    d = d.dropna(subset=["mcp_score"])
    if len(d) == 0:
        return []
    edges = [-np.inf, 0, 65, 70, 75, 80, 85, np.inf]
    labels = ["<=0(none)", "(0,65)", "[65,70)", "[70,75)", "[75,80)", "[80,85)", ">=85"]
    d["bucket"] = pd.cut(d["mcp_score"], bins=edges, labels=labels, right=False)
    return group_breakdown(d, "bucket")


def _fmt_rows(rows: list[dict], topn: int | None = None) -> list[str]:
    L = [
        "| group | n | WR% | sum $ | avg $ | avg R | % of total |",
        "|---|---|---|---|---|---|---|",
    ]
    if topn is not None and len(rows) > 2 * topn:
        show = rows[:topn] + [{"group": "…"}] + rows[-topn:]
    else:
        show = rows
    for r in show:
        if r.get("group") == "…":
            L.append("| … | | | | | | |")
            continue
        ar = (
            ""
            if r.get("avg_r") is None
            or (isinstance(r.get("avg_r"), float) and np.isnan(r["avg_r"]))
            else f"{r['avg_r']:+.2f}"
        )
        L.append(
            f"| {r['group']} | {r['n']} | {r['win_rate'] * 100:.0f} | {r['sum_pnl']:+.2f} | "
            f"{r['avg_pnl']:+.3f} | {ar} | {r['pct_of_total_pnl']:+.1f}% |"
        )
    return L


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    if not DB.exists():
        print(f"warehouse not found: {DB}")
        return 1
    con = sqlite3.connect(str(DB))
    tr = pd.read_sql_query(
        "SELECT * FROM trades WHERE status IN ('closed','CLOSED') OR exit_reason IS NOT NULL "
        "ORDER BY ts_exit",
        con,
    )
    # closed only: must have an exit
    tr = tr[tr["exit_px"].notna() & tr["realized_pnl"].notna()].reset_index(drop=True)
    n = len(tr)
    if n == 0:
        print("no closed trades in warehouse")
        return 1
    pnl = tr["realized_pnl"].to_numpy(dtype=float)
    s = summarize(pnl)
    if "exit_reason" in tr.columns:
        tr["exit_reason_norm"] = tr["exit_reason"].map(norm_exit)

    # cost attribution (where the money goes)
    attr = pd.read_sql_query("SELECT * FROM attribution", con)
    attr_tot = (
        {
            c: float(attr[c].sum())
            for c in ("alpha", "spread", "slippage", "funding", "fees", "realized_pnl")
            if c in attr.columns
        }
        if len(attr)
        else {}
    )

    # rejected signals
    try:
        rej = pd.read_sql_query(
            "SELECT skip_reason, COUNT(*) n FROM candidates WHERE decision IN ('SKIP','skip','REJECT','reject') "
            "OR skip_reason IS NOT NULL GROUP BY skip_reason ORDER BY n DESC LIMIT 15",
            con,
        )
    except Exception:
        rej = pd.DataFrame()
    con.close()

    # beta decomposition
    beta_line = ""
    if "alpha_vs_btc" in tr.columns:
        a = pd.to_numeric(tr["alpha_vs_btc"], errors="coerce").dropna()
        if len(a):
            beta_line = (
                f"Total realized PnL **${s['total_pnl']:+.2f}** vs total alpha-vs-BTC "
                f"**${a.sum():+.2f}** over {len(a)} labelled trades → "
                f"BTC-beta share ≈ **${s['total_pnl'] - a.sum():+.2f}**; the rest is intrinsic."
            )

    today = date.today().isoformat()
    REPORTS.mkdir(exist_ok=True)
    L = [
        f"# Trade-analysis breakdown — {today}",
        "",
        f"Closed trades: **{n}** (warehouse `trades`). Read-only diagnostic "
        "(freqtrade-style grouping, adapted to this bot's warehouse).",
        "",
        "## Overall",
        f"- Win rate: **{s['win_rate'] * 100:.1f}%**  | Total PnL: **${s['total_pnl']:+.2f}**  | "
        f"Expectancy: **${s['expectancy_per_trade']:+.3f}/trade**",
        f"- Avg win **${s['avg_win']:+.3f}** / avg loss **${s['avg_loss']:+.3f}** → payoff **{s['payoff']:.2f}**, "
        f"profit factor **{s['profit_factor']:.2f}**",
        f"- Breakeven WR **{s['breakeven_wr'] * 100:.1f}%** vs actual **{s['win_rate'] * 100:.1f}%** → "
        f"gap **{s['actual_minus_breakeven_wr'] * 100:+.1f} pts**  | max consec losses **{s['max_consec_losses']}**",
    ]
    if beta_line:
        L += ["", "## Beta vs intrinsic", f"- {beta_line}"]
    if attr_tot:
        L += [
            "",
            "## Cost / PnL attribution (where the money goes)",
            "| component | total $ |",
            "|---|---|",
        ]
        for k in ("alpha", "spread", "slippage", "funding", "fees", "realized_pnl"):
            if k in attr_tot:
                L.append(f"| {k} | {attr_tot[k]:+.2f} |")
        L += [f"_(from {len(attr)} attributed trades)_"]

    L += ["", "## By exit reason (where PnL leaks)"] + _fmt_rows(
        group_breakdown(tr, "exit_reason_norm")
    )
    L += ["", "## By side"] + _fmt_rows(group_breakdown(tr, "side"))
    L += ["", "## By mcp_score bucket (is the score predictive?)"] + _fmt_rows(score_buckets(tr))
    L += ["", "## By strategy family"] + _fmt_rows(group_breakdown(tr, "strategy_family"))
    L += ["", "## By symbol (worst & best)"] + _fmt_rows(group_breakdown(tr, "symbol"), topn=10)
    if "mode" in tr.columns:
        L += ["", "## By mode"] + _fmt_rows(group_breakdown(tr, "mode"))
    if len(rej):
        L += ["", "## Rejected signals (top skip reasons)", "| skip_reason | count |", "|---|---|"]
        L += [f"| {r.skip_reason} | {int(r.n)} |" for r in rej.itertuples()]

    L += [
        "",
        "_Loss concentrating in one exit reason or a flat score-bucket gradient is the "
        "no-edge signature, not a tuning target — exits/tuning cannot manufacture EV from a "
        "near-zero entry edge._",
    ]

    out_md = REPORTS / f"trade_breakdown_{today}.md"
    out_md.write_text("\n".join(L), encoding="utf-8")
    (REPORTS / f"trade_breakdown_{today}.json").write_text(
        json.dumps(
            {
                "date": today,
                "overall": s,
                "attribution_totals": attr_tot,
                "by_exit_reason": group_breakdown(tr, "exit_reason_norm"),
                "by_side": group_breakdown(tr, "side"),
                "by_score_bucket": score_buckets(tr),
                "by_strategy": group_breakdown(tr, "strategy_family"),
                "by_symbol": group_breakdown(tr, "symbol"),
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    print(
        f"Closed trades: {n} | WR {s['win_rate'] * 100:.1f}% | PnL ${s['total_pnl']:+.2f} | "
        f"breakeven WR {s['breakeven_wr'] * 100:.1f}% (gap {s['actual_minus_breakeven_wr'] * 100:+.1f}pts)"
    )
    print("By exit reason (worst first):")
    for r in group_breakdown(tr, "exit_reason_norm"):
        print(
            f"  {r['group']:18} n={r['n']:>4} WR={r['win_rate'] * 100:>4.0f}% sum=${r['sum_pnl']:+8.2f} "
            f"avgR={r['avg_r'] if not (isinstance(r['avg_r'], float) and np.isnan(r['avg_r'])) else 0:+.2f}"
        )
    print(f"Report: {out_md}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
