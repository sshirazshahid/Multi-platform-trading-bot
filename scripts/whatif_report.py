"""
scripts/whatif_report.py — Raw vs. Meta-filtered vs. Claude-vetoed comparison.

Walks the warehouse and, for every candidate the system saw, projects a PnL
as if that candidate had been executed. Then it projects the same PnL for
two filtered variants:

  - Meta-filtered: only candidates with decision='ALLOW' (meta-filter pass)
  - Claude-vetoed: ALLOW further filtered by any pre_trade_review that
                   returned `decision='ALLOW'` (no Claude review = pass)

For every series we compute:

  - trade count
  - net PnL (sum of realized_pnl where an actual trade exists, otherwise the
    candidate is skipped — we cannot project PnL for candidates that never
    executed, so raw/filtered counts reflect *candidates* and pnl reflects
    *executed trades that would have passed the filter*)
  - win rate, expectancy, profit factor, avg R
  - max drawdown (running cumulative PnL)

Output: markdown under reports/whatif_<YYYY-MM-DD>.md plus JSON sidecar.

Spec §15.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import importlib.util as _il
_spec = _il.spec_from_file_location("warehouse", ROOT / "core" / "warehouse.py")
_mod = _il.module_from_spec(_spec)
sys.modules["warehouse"] = _mod
_spec.loader.exec_module(_mod)
Warehouse = _mod.Warehouse


def _summarize(pnls: list[float]) -> dict:
    """Compute summary stats for a PnL series."""
    n = len(pnls)
    if n == 0:
        return {"n": 0, "net": 0.0, "wr": 0.0, "expectancy": 0.0,
                "pf": 0.0, "max_dd": 0.0}
    wins   = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    net    = sum(pnls)
    avg_win  = sum(wins)   / len(wins)   if wins   else 0.0
    avg_loss = sum(losses) / len(losses) if losses else 0.0
    wr = (len(wins) / n * 100) if n else 0.0
    # Profit factor = gross wins / |gross losses|
    gross_win  = sum(wins)
    gross_loss = -sum(losses)
    pf = (gross_win / gross_loss) if gross_loss > 0 else (999.0 if gross_win > 0 else 0.0)
    expectancy = net / n
    # Running DD
    running = 0.0
    peak = 0.0
    max_dd = 0.0
    for p in pnls:
        running += p
        peak = max(peak, running)
        max_dd = max(max_dd, peak - running)
    return {
        "n": n,
        "net": round(net, 2),
        "wr": round(wr, 1),
        "expectancy": round(expectancy, 4),
        "pf": round(pf, 2),
        "avg_win": round(avg_win, 3),
        "avg_loss": round(avg_loss, 3),
        "max_dd": round(max_dd, 2),
    }


def build_series(wh: Warehouse) -> dict:
    """Return {'raw': [pnl...], 'meta': [pnl...], 'claude': [pnl...]}.

    Each list is the realized PnL for candidates that would have produced
    executed trades under that filter. Candidates with decision='SKIP' that
    never became trades contribute nothing to PnL (there is no trade to
    reference), but they are counted in the candidate totals elsewhere.
    """
    # Join trades -> candidates so we can key each trade by what decision
    # the scorer made. Candidates without a trade simply don't show up.
    rows = wh.query(
        """SELECT t.realized_pnl AS pnl, t.r_multiple AS r, t.exit_reason AS er,
                  c.decision AS cand_decision, c.id AS cand_id
           FROM trades t
           LEFT JOIN candidates c ON c.id = t.candidate_id
           WHERE t.status = 'CLOSED'
           ORDER BY t.ts_entry ASC"""
    )

    raw = []
    meta = []
    claude = []

    # Pre-fetch claude pre_trade_review decisions keyed by candidate id
    claude_rows = wh.query(
        """SELECT ref_id, decision FROM claude_reviews
           WHERE ref_type='candidate' AND task='pre_trade_review'"""
    )
    claude_decisions = {int(r["ref_id"]): r["decision"]
                        for r in claude_rows if r.get("ref_id") is not None}

    for r in rows:
        pnl = float(r.get("pnl") or 0.0)
        cand_id = r.get("cand_id")
        cand_decision = (r.get("cand_decision") or "").upper()

        raw.append(pnl)

        # Meta-filter keeps ALLOW + TAKEN (historical imports are TAKEN).
        if cand_decision in ("ALLOW", "TAKEN"):
            meta.append(pnl)

            # Claude-vetoed: if there's no claude review, we pass the candidate
            # through (absence of veto = implicit pass). If there IS a review
            # and it said SKIP, we veto.
            c_dec = claude_decisions.get(int(cand_id)) if cand_id is not None else None
            if c_dec is None or c_dec.upper() == "ALLOW":
                claude.append(pnl)

    return {"raw": raw, "meta": meta, "claude": claude}


def render_markdown(stats: dict, series: dict) -> str:
    dt = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    md = [
        f"# What-If Comparison Report — {dt}",
        "",
        "Compares three hypothetical PnL series built from the warehouse:",
        "",
        " - **Raw**: every closed trade (our actual history).",
        " - **Meta-filtered**: only trades whose candidate was `ALLOW` or `TAKEN`",
        "   after the rule-based meta-filter would have evaluated it.",
        " - **Claude-vetoed**: meta-filtered *and* not SKIP'd by a ClaudeCode",
        "   pre_trade_review. Trades with no Claude review pass through.",
        "",
        "## Summary",
        "",
        "| Series | N | Net PnL | WR | PF | Expect | AvgW | AvgL | MaxDD |",
        "|--------|---|---------|----|----|--------|------|------|-------|",
    ]
    for label, key in [("Raw", "raw"), ("Meta-filtered", "meta"),
                       ("Claude-vetoed", "claude")]:
        s = stats[key]
        md.append(
            f"| {label} | {s['n']} | {s['net']:+.2f} | {s['wr']:.1f}% | "
            f"{s['pf']:.2f} | {s['expectancy']:+.4f} | "
            f"{s.get('avg_win', 0):+.3f} | {s.get('avg_loss', 0):+.3f} | "
            f"{s['max_dd']:.2f} |"
        )
    md += [
        "",
        "## Interpretation",
        "",
        "- Series with higher PF and lower MaxDD are the higher-quality ones.",
        "- If **Meta-filtered** significantly beats **Raw**, the meta-filter is",
        "  earning its keep — it's filtering out the worst setups.",
        "- If **Claude-vetoed** ≈ **Meta-filtered**, ClaudeCode isn't adding",
        "  edge on top of the rules yet (prompt or sample size).",
        "",
        f"**Source sample size:** {len(series['raw'])} closed trades.",
        "",
    ]
    return "\n".join(md)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--output-dir", type=str, default="reports")
    args = ap.parse_args()

    wh = Warehouse()
    series = build_series(wh)
    stats = {k: _summarize(v) for k, v in series.items()}

    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    date = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    md_path = out_dir / f"whatif_{date}.md"
    json_path = out_dir / f"whatif_{date}.json"

    md_path.write_text(render_markdown(stats, series), encoding="utf-8")
    json_path.write_text(json.dumps({
        "generated_at": time.time(),
        "stats": stats,
        "sample_sizes": {k: len(v) for k, v in series.items()},
    }, indent=2), encoding="utf-8")

    print(f"[whatif] wrote {md_path}")
    print(f"[whatif] wrote {json_path}")
    for k in ("raw", "meta", "claude"):
        s = stats[k]
        print(f"[whatif] {k:8s} n={s['n']:4d} net={s['net']:+.2f} "
              f"wr={s['wr']:.1f}% pf={s['pf']:.2f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
