"""STAR symbol auto-review — promotion/demotion candidates.

Runs against the warehouse and recommends:
  • Promote: non-STAR symbols with proven edge that should join STAR list
  • Demote:  current STARs whose edge has decayed and should be removed
  • Hold:    everything else

Does NOT auto-modify config.py — emits recommendations the operator
applies manually. Reason: STAR list is a load-bearing config decision;
warrant operator review before changing.

Criteria (60-day window, claude_portfolio strategy only):

  Promote candidate:
    n >= 12 trades         (statistical evidence)
    mean PnL > $0          (positive realised expectancy)
    WR >= 50%              (better than coin-flip)
    worst single trade >= -$2.5  (no catastrophic outliers)

  Demote candidate (current STAR):
    n >= 12 trades                       (recent sample)
    mean PnL < $-0.05  OR  WR < 40%      (sustained underperformance)

Usage:
    python scripts/star_review.py             # markdown report
    python scripts/star_review.py --window 30 # tighter window
    python scripts/star_review.py --json      # machine-readable
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WH_PATH = ROOT / "data" / "warehouse.sqlite"
REPORT_DIR = ROOT / "data" / "reports"

# Mirror the live config — these are the symbols currently treated as STAR.
sys.path.insert(0, str(ROOT))
from config import STAR_SYMBOLS  # noqa: E402

# Promotion thresholds — conservative on purpose.
PROMOTE_MIN_N = 12
PROMOTE_MIN_MEAN = 0.01
PROMOTE_MIN_WR = 0.50
PROMOTE_MAX_WORST = -2.50

# Demotion thresholds — conservative on purpose.
# A profitable symbol stays STAR even at low WR (winners > losers in R).
# Demotion fires only on net negative expected value.
DEMOTE_MIN_N = 12
DEMOTE_MEAN_FLOOR = -0.05  # mean PnL must be net-negative to demote


def _scan(window_days: int = 60) -> dict:
    if not WH_PATH.exists():
        raise SystemExit(f"warehouse not found: {WH_PATH}")
    con = sqlite3.connect(WH_PATH)
    con.row_factory = sqlite3.Row
    since = int(time.time()) - window_days * 86400

    # All symbols (claude_portfolio only) with at least 5 trades
    rows = con.execute("""
        SELECT symbol, COUNT(*) AS n,
               SUM(realized_pnl) AS total,
               AVG(realized_pnl) AS mean,
               MIN(realized_pnl) AS worst,
               MAX(realized_pnl) AS best,
               1.0 * SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END)/COUNT(*) AS wr
        FROM trades
        WHERE status='CLOSED' AND ts_entry >= ?
          AND strategy_family='claude_portfolio'
        GROUP BY symbol
        HAVING n >= 5
        ORDER BY total DESC
    """, (since,)).fetchall()

    promote, demote, watch = [], [], []
    star_set = set(STAR_SYMBOLS)

    for r in rows:
        sym = r["symbol"]
        is_star = sym in star_set
        if is_star:
            # Demotion check — only if expected value is net-negative.
            # A symbol with low WR but high R:R (mean > 0) is still
            # profitable; don't demote on WR alone.
            if (r["n"] >= DEMOTE_MIN_N and r["mean"] < DEMOTE_MEAN_FLOOR):
                demote.append(dict(r))
        else:
            # Promotion check
            if (r["n"] >= PROMOTE_MIN_N
                and r["mean"] > PROMOTE_MIN_MEAN
                and r["wr"] >= PROMOTE_MIN_WR
                and r["worst"] >= PROMOTE_MAX_WORST):
                promote.append(dict(r))
            elif r["n"] >= 8 and r["mean"] >= 0:
                watch.append(dict(r))

    return {
        "window_days": window_days,
        "current_stars": sorted(star_set),
        "promote": promote,
        "demote": demote,
        "watch": watch,
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }


def _md_report(data: dict) -> str:
    today = datetime.now().strftime("%Y-%m-%d")
    lines = [
        f"# STAR review - {today}",
        "",
        f"Window: last **{data['window_days']} days**, claude_portfolio strategy only.",
        "",
        "## Current STARs",
        "",
    ]
    for s in data["current_stars"]:
        lines.append(f"- `{s}`")

    lines += [
        "",
        "## Promotion candidates (would add)",
        "",
        f"_Criteria: n&gt;={PROMOTE_MIN_N}, mean>$%.2f, WR&gt;=%d%%, no trade worse than $%.2f_"
        % (PROMOTE_MIN_MEAN, int(PROMOTE_MIN_WR*100), PROMOTE_MAX_WORST),
        "",
    ]
    if data["promote"]:
        lines.append("| symbol | n | total | mean | worst | best | WR |")
        lines.append("|---|---:|---:|---:|---:|---:|---:|")
        for r in data["promote"]:
            lines.append(
                f"| `{r['symbol']}` | {r['n']} | "
                f"${r['total']:+.2f} | ${r['mean']:+.3f} | "
                f"${r['worst']:+.2f} | ${r['best']:+.2f} | "
                f"{r['wr']*100:.0f}% |"
            )
    else:
        lines.append("_(none — current STARs are still the best edge)_")

    lines += [
        "",
        "## Demotion candidates (would remove)",
        "",
        f"_Criteria: STAR with n&gt;={DEMOTE_MIN_N} AND mean<${DEMOTE_MEAN_FLOOR:.2f} "
        f"(net-negative expected value — profitable symbols stay STAR even at low WR)_",
        "",
    ]
    if data["demote"]:
        lines.append("| symbol | n | total | mean | WR |")
        lines.append("|---|---:|---:|---:|---:|")
        for r in data["demote"]:
            lines.append(
                f"| `{r['symbol']}` | {r['n']} | "
                f"${r['total']:+.2f} | ${r['mean']:+.3f} | "
                f"{r['wr']*100:.0f}% |"
            )
    else:
        lines.append("_(none — current STARs all healthy)_")

    lines += [
        "",
        "## Watch list (borderline — close to promotion threshold)",
        "",
        "_n&gt;=8, mean&gt;=$0 — gather more data before deciding_",
        "",
    ]
    if data["watch"]:
        lines.append("| symbol | n | total | mean | WR |")
        lines.append("|---|---:|---:|---:|---:|")
        for r in data["watch"]:
            lines.append(
                f"| `{r['symbol']}` | {r['n']} | "
                f"${r['total']:+.2f} | ${r['mean']:+.3f} | "
                f"{r['wr']*100:.0f}% |"
            )
    else:
        lines.append("_(none)_")

    lines += [
        "",
        "## How to apply",
        "",
        "To promote a symbol, edit `config.py` STAR_SYMBOLS set:",
        "```python",
        "STAR_SYMBOLS = {",
        '    "ATOM/USDT:USDT", "ARB/USDT:USDT", "DOGE/USDT:USDT",',
        '    # "<NEW>/USDT:USDT",  # added <date> per star_review',
        "}",
        "```",
        "",
        "To demote, simply remove the symbol from the set. The expectancy",
        "filter will then check it against the non-STAR floor ($0.05 mean).",
    ]
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--window", type=int, default=60,
                    help="Lookback window in days (default 60)")
    ap.add_argument("--json", action="store_true",
                    help="Emit JSON instead of markdown")
    args = ap.parse_args()

    data = _scan(args.window)
    if args.json:
        print(json.dumps(data, indent=2, default=str))
        return

    report = _md_report(data)
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    today = datetime.now().strftime("%Y-%m-%d")
    out_path = REPORT_DIR / f"star_review_{today}.md"
    out_path.write_text(report, encoding="utf-8")
    print(report)
    print(f"\nwritten: {out_path}")


if __name__ == "__main__":
    main()
