"""Gate Effectiveness Monitor — observability for the predictive stack.

Background
==========
Across 50+ commits this session the bot accumulated ~30 prediction +
filter layers (cell-filter, expectancy filter, model gate, score-85
cap, entry-staleness exit, etc.). Each is supposed to improve EV.
The honest question: are they actually doing their job, or are
they silently miscalibrated?

This script answers that question by reading the warehouse and
producing a report covering:

  1. Score calibration — are higher scores actually winning more often?
  2. Cell-filter performance — STAR vs band vs blocked-cells
  3. p_win_ensemble calibration — predicted vs realized
  4. Expectancy filter effectiveness — what got blocked, what got through
  5. Entry-staleness exit performance — net PnL of trades closed
     by this rule vs trades that closed via SL
  6. Recent (7d) vs lifetime expectancy per gate

Output: markdown report at `data/reports/gate_effectiveness_<date>.md`
plus a JSONL append to `data/reports/gate_effectiveness.jsonl` for
trend tracking.

This is OBSERVABILITY, not a new prediction layer. If a gate has gone
silent (always allowing) or anti-EV (allows lose more than it blocks),
this surfaces it without auto-disabling — operator decides.

Usage
=====
    python scripts/gate_effectiveness_report.py              # full report
    python scripts/gate_effectiveness_report.py --window 7   # last 7d only
    python scripts/gate_effectiveness_report.py --json       # also dump JSON
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WH_PATH = ROOT / "data" / "warehouse.sqlite"
REPORT_DIR = ROOT / "data" / "reports"
JSONL_LOG = REPORT_DIR / "gate_effectiveness.jsonl"

# Anchored in the 2026-05-01 cell-filter spec.
STAR_SYMBOLS = {"ATOM/USDT:USDT", "ARB/USDT:USDT", "DOGE/USDT:USDT"}

# When the cell-filter went live (commit 6ca74d9 at 2026-05-01 01:31 +0500
# = 2026-04-30 20:31 UTC). Trades opened before this bypass the filter
# entirely — they are NOT a filter leak; they are pre-wire history.
CELL_FILTER_ACTIVE_TS = 1777581060  # 2026-04-30 20:31:00 UTC


def _conn() -> sqlite3.Connection:
    if not WH_PATH.exists():
        raise SystemExit(f"warehouse not found: {WH_PATH}")
    c = sqlite3.connect(WH_PATH)
    c.row_factory = sqlite3.Row
    return c


def _fmt_pnl(v: float) -> str:
    return f"{v:+.2f}"


def _safe_div(a: float, b: float) -> float:
    return a / b if b else 0.0


def _section_header(title: str, body: str = "") -> str:
    out = f"\n## {title}\n\n"
    if body:
        out += body + "\n"
    return out


# ─────────────────────────────────────────────────────────────────────


def report_score_calibration(c, since_ts: int) -> str:
    """Score-bucket realised performance. Pinned the score-85 anti-EV
    finding."""
    out = _section_header(
        "1. Score-bucket calibration",
        "Are higher mcp_score values actually winning more often? "
        "Pre-fix data showed 85+ was anti-EV; let's see if cell-filter "
        "kept it that way or improved.",
    )
    rows = c.execute("""
        SELECT
          CASE
            WHEN mcp_score < 65          THEN '00-64'
            WHEN mcp_score < 70          THEN '65-69'
            WHEN mcp_score < 75          THEN '70-74'
            WHEN mcp_score < 80          THEN '75-79'
            WHEN mcp_score < 85          THEN '80-84'
            ELSE                              '85-100'
          END AS bucket,
          COUNT(*) AS n,
          ROUND(SUM(realized_pnl), 2) AS sum_pnl,
          ROUND(AVG(realized_pnl), 3) AS avg_pnl,
          ROUND(100.0 * SUM(CASE WHEN realized_pnl>0 THEN 1 ELSE 0 END)/COUNT(*), 1) AS wr
        FROM trades
        WHERE status='CLOSED'
          AND mcp_score IS NOT NULL
          AND ts_entry >= ?
        GROUP BY bucket
        ORDER BY bucket
    """, (since_ts,)).fetchall()
    if not rows:
        return out + "_no data in window_\n"
    out += "| bucket | n | sum | avg | WR |\n|---|---:|---:|---:|---:|\n"
    for r in rows:
        out += f"| {r['bucket']} | {r['n']} | {_fmt_pnl(r['sum_pnl'])} | {_fmt_pnl(r['avg_pnl'])} | {r['wr']}% |\n"
    return out


def report_cell_performance(c, since_ts: int) -> str:
    """STAR vs mid-band vs blocked-cells (visible via skip_reason)."""
    out = _section_header(
        "2. Cell-filter performance",
        "Closed-trade attribution by cell. STAR symbols, score-band "
        "non-STAR, and blocks (visible only via SKIP rows in the "
        "candidates table). A blocked cell with no realised data is "
        "good — the filter is doing its job. Trades opened before the "
        "cell-filter went live (2026-04-30 20:31 UTC) are split out "
        "separately as PRE-FILTER and do not represent filter leaks.",
    )
    star_list = "(" + ",".join(f"'{s}'" for s in STAR_SYMBOLS) + ")"
    rows = c.execute(f"""
        SELECT
          CASE WHEN ts_entry >= ? THEN 'POST' ELSE 'PRE' END AS era,
          CASE
            WHEN symbol IN {star_list} THEN 'STAR'
            WHEN mcp_score BETWEEN 70 AND 84 THEN 'BAND'
            ELSE 'OTHER'
          END AS cell,
          COUNT(*) AS n,
          ROUND(SUM(realized_pnl), 2) AS sum_pnl,
          ROUND(AVG(realized_pnl), 3) AS avg_pnl,
          ROUND(100.0 * SUM(CASE WHEN realized_pnl>0 THEN 1 ELSE 0 END)/COUNT(*), 1) AS wr
        FROM trades
        WHERE status='CLOSED' AND ts_entry >= ?
        GROUP BY era, cell
        ORDER BY era DESC, sum_pnl DESC
    """, (CELL_FILTER_ACTIVE_TS, since_ts)).fetchall()
    if not rows:
        return out + "_no data in window_\n"
    out += "| era | cell | n | sum | avg | WR |\n|---|---|---:|---:|---:|---:|\n"
    for r in rows:
        era_label = "active" if r["era"] == "POST" else "pre-wire"
        out += f"| {era_label} | {r['cell']} | {r['n']} | {_fmt_pnl(r['sum_pnl'])} | {_fmt_pnl(r['avg_pnl'])} | {r['wr']}% |\n"

    # Skip-reason distribution (gate firing rates)
    skips = c.execute("""
        SELECT skip_reason, COUNT(*) AS n
        FROM candidates
        WHERE decision='SKIP' AND ts >= ?
        GROUP BY skip_reason ORDER BY n DESC LIMIT 20
    """, (since_ts,)).fetchall()
    if skips:
        out += "\n### Skip reasons (top 20)\n\n"
        out += "| reason | n |\n|---|---:|\n"
        for r in skips:
            reason = (r["skip_reason"] or "(empty)")[:60]
            out += f"| `{reason}` | {r['n']} |\n"
    return out


def report_pwin_calibration(c, since_ts: int) -> str:
    """Predicted p_win vs realised WR — the model gate's calibration."""
    out = _section_header(
        "3. p_win_ensemble calibration",
        "For trades that fired with a predicted p_win, compare the "
        "predicted probability bucket to the actual win rate. Perfect "
        "calibration: predicted bucket = realised WR. Drift in either "
        "direction means the model's probabilities are stale.",
    )
    # Join predictions to trades on candidate_id (Phase 13.5b wiring)
    rows = c.execute("""
        SELECT
          CASE
            WHEN p.p_win < 0.50 THEN '0.30-0.49'
            WHEN p.p_win < 0.55 THEN '0.50-0.54'
            WHEN p.p_win < 0.60 THEN '0.55-0.59'
            WHEN p.p_win < 0.65 THEN '0.60-0.64'
            WHEN p.p_win < 0.70 THEN '0.65-0.69'
            ELSE                       '0.70+'
          END AS p_bucket,
          COUNT(*) AS n,
          ROUND(AVG(p.p_win), 3) AS avg_pwin,
          ROUND(100.0 * SUM(CASE WHEN t.realized_pnl>0 THEN 1 ELSE 0 END)/COUNT(*), 1) AS realized_wr,
          ROUND(SUM(t.realized_pnl), 2) AS sum_pnl
        FROM predictions p
        JOIN trades t ON t.candidate_id = p.candidate_id
        WHERE t.status='CLOSED' AND t.ts_entry >= ?
        GROUP BY p_bucket
        ORDER BY p_bucket
    """, (since_ts,)).fetchall()
    if not rows:
        return out + "_no joined predictions+trades data in window_\n"
    out += "| p_win bucket | n | avg_pwin | realized WR | sum |\n|---|---:|---:|---:|---:|\n"
    for r in rows:
        gap = (r["realized_wr"] or 0) - (r["avg_pwin"] or 0) * 100
        flag = " ⚠️" if abs(gap) > 15 else ""
        out += (
            f"| {r['p_bucket']} | {r['n']} | {r['avg_pwin']} | "
            f"{r['realized_wr']}% | {_fmt_pnl(r['sum_pnl'])}{flag} |\n"
        )
    return out


def report_exit_reason_breakdown(c, since_ts: int) -> str:
    """Per-exit-reason performance — surfaces if entry-staleness or
    AGE_LOSS are doing their job."""
    out = _section_header(
        "4. Exit-reason performance",
        "Net PnL by exit_reason. The entry-staleness rule "
        "(`entry_invalidated`) and AGE_LOSS rule should produce small "
        "negatives or breakeven (cutting before bigger losses). "
        "trailing_stop and mcp_take_profit should dominate the wins.",
    )
    rows = c.execute("""
        SELECT exit_reason, COUNT(*) AS n,
               ROUND(SUM(realized_pnl), 2) AS sum_pnl,
               ROUND(AVG(realized_pnl), 3) AS avg_pnl,
               ROUND(100.0 * SUM(CASE WHEN realized_pnl>0 THEN 1 ELSE 0 END)/COUNT(*), 1) AS wr
        FROM trades
        WHERE status='CLOSED' AND ts_entry >= ?
        GROUP BY exit_reason
        ORDER BY sum_pnl DESC
    """, (since_ts,)).fetchall()
    if not rows:
        return out + "_no data in window_\n"
    out += "| exit_reason | n | sum | avg | WR |\n|---|---:|---:|---:|---:|\n"
    for r in rows:
        reason = (r["exit_reason"] or "(none)")[:40]
        out += (
            f"| `{reason}` | {r['n']} | {_fmt_pnl(r['sum_pnl'])} | "
            f"{_fmt_pnl(r['avg_pnl'])} | {r['wr']}% |\n"
        )
    return out


def report_per_symbol(c, since_ts: int) -> str:
    """Per-symbol expectancy. Anchors the expectancy-filter logic."""
    out = _section_header(
        "5. Per-symbol expectancy (n>=3)",
        "What the expectancy filter sees. Negative-mean symbols would "
        "be auto-blocked once n >= min_sample_size (5) in the live "
        "lookback window.",
    )
    rows = c.execute("""
        SELECT symbol, COUNT(*) AS n,
               ROUND(SUM(realized_pnl), 2) AS sum_pnl,
               ROUND(AVG(realized_pnl), 3) AS avg_pnl,
               ROUND(100.0 * SUM(CASE WHEN realized_pnl>0 THEN 1 ELSE 0 END)/COUNT(*), 1) AS wr
        FROM trades
        WHERE status='CLOSED' AND ts_entry >= ?
        GROUP BY symbol
        HAVING n >= 3
        ORDER BY sum_pnl DESC
    """, (since_ts,)).fetchall()
    if not rows:
        return out + "_no data in window_\n"
    # Read live floor values from config so report stays in sync.
    try:
        from config import EXPECTANCY_FILTER as _EF, STAR_SYMBOLS as _STAR
        nonstar_floor = float(_EF.get("min_expected_dollar", 0.05))
        star_floor    = float(_EF.get("min_expected_star", 0.00))
        min_n         = int(_EF.get("min_sample_size", 5))
    except Exception:
        nonstar_floor, star_floor, min_n = 0.05, 0.00, 5
        _STAR = STAR_SYMBOLS

    out += (
        f"_floors: STAR ≥ ${star_floor:.2f} | non-STAR ≥ ${nonstar_floor:.2f} "
        f"| min_n={min_n}_\n\n"
    )
    out += "| symbol | n | sum | avg | WR | filter status |\n|---|---:|---:|---:|---:|---|\n"
    for r in rows:
        avg = r["avg_pnl"] or 0
        n = r["n"]
        is_star = r["symbol"] in _STAR
        floor = star_floor if is_star else nonstar_floor
        star_tag = " (STAR)" if is_star else ""
        if n < min_n:
            flag = f"_n<{min_n} (allow){star_tag}_"
        elif avg < floor:
            flag = f"**BLOCK**{star_tag}"
        else:
            flag = f"_pass_{star_tag}"
        out += (
            f"| {r['symbol']} | {n} | {_fmt_pnl(r['sum_pnl'])} | "
            f"{_fmt_pnl(avg)} | {r['wr']}% | {flag} |\n"
        )
    return out


def report_summary(c, since_ts: int) -> dict:
    """Top-line numbers for JSONL append + report header."""
    r = c.execute("""
        SELECT COUNT(*) AS n,
               ROUND(SUM(realized_pnl), 2) AS sum_pnl,
               ROUND(AVG(realized_pnl), 3) AS avg_pnl,
               ROUND(100.0 * SUM(CASE WHEN realized_pnl>0 THEN 1 ELSE 0 END)/COUNT(*), 1) AS wr,
               ROUND(SUM(fee), 2) AS sum_fees
        FROM trades
        WHERE status='CLOSED' AND ts_entry >= ?
    """, (since_ts,)).fetchone()
    if not r or r["n"] == 0:
        return {"n": 0, "sum_pnl": 0.0, "avg_pnl": 0.0, "wr": 0.0,
                "sum_fees": 0.0, "since_ts": since_ts}
    return {
        "n": r["n"], "sum_pnl": r["sum_pnl"] or 0.0,
        "avg_pnl": r["avg_pnl"] or 0.0, "wr": r["wr"] or 0.0,
        "sum_fees": r["sum_fees"] or 0.0, "since_ts": since_ts,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--window", type=int, default=30,
                    help="Lookback in days (default 30)")
    ap.add_argument("--json", action="store_true",
                    help="Also write a JSON snapshot alongside the markdown")
    args = ap.parse_args()

    import time
    since_ts = int(time.time() - args.window * 86400)
    REPORT_DIR.mkdir(parents=True, exist_ok=True)

    c = _conn()

    summary = report_summary(c, since_ts)
    if summary["n"] == 0:
        print(f"No closed trades in last {args.window}d. Nothing to report.")
        return 0

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    md = f"# Gate Effectiveness Report — {today}\n\n"
    md += f"Window: last **{args.window} days** (since "
    md += f"{datetime.fromtimestamp(since_ts, timezone.utc).date()})\n\n"
    md += "## Top-line\n\n"
    md += f"- Closed trades: **{summary['n']}**\n"
    md += f"- Net PnL: **{_fmt_pnl(summary['sum_pnl'])} USDT**\n"
    md += f"- Avg PnL/trade: {_fmt_pnl(summary['avg_pnl'])} USDT\n"
    md += f"- Win rate: **{summary['wr']}%**\n"
    md += f"- Total fees: {_fmt_pnl(-summary['sum_fees'])} USDT\n"

    md += report_score_calibration(c, since_ts)
    md += report_cell_performance(c, since_ts)
    md += report_pwin_calibration(c, since_ts)
    md += report_exit_reason_breakdown(c, since_ts)
    md += report_per_symbol(c, since_ts)

    md += "\n---\n\n"
    md += "## How to read this\n\n"
    md += "- **Calibration drift > 15%** in p_win section → model is "
    md += "stale, retrain or revert MODEL_GATE_SHADOW=true.\n"
    md += "- **Score 85+ with positive sum** → score-85 cap may be over-"
    md += "tuned, consider relaxing.\n"
    md += "- **OTHER cell with rows present** → cell-filter has a leak "
    md += "(should be 0 — investigate skip_reason distribution).\n"
    md += "- **Per-symbol with n>=5 and avg<-$0.30** → expectancy filter "
    md += "should now be auto-blocking it on next entry attempt.\n"
    md += "- **`entry_invalidated` in exit reasons** → the entry-"
    md += "staleness exit is firing; verify avg PnL is small-negative or "
    md += "breakeven (catching invalidations BEFORE they become losers).\n"

    out_md = REPORT_DIR / f"gate_effectiveness_{today}.md"
    out_md.write_text(md, encoding="utf-8")
    print(f"wrote: {out_md}")

    # JSONL append for trend tracking
    JSONL_LOG.parent.mkdir(parents=True, exist_ok=True)
    with JSONL_LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps({
            "report_date": today,
            "window_days": args.window,
            **summary,
        }) + "\n")
    print(f"appended to: {JSONL_LOG}")

    if args.json:
        out_json = REPORT_DIR / f"gate_effectiveness_{today}.json"
        out_json.write_text(json.dumps(summary, indent=2), encoding="utf-8")
        print(f"wrote: {out_json}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
