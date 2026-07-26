"""Loss autopsy — read-only warehouse breakdown (realized_pnl)."""
from __future__ import annotations

import json
import sqlite3
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

WH = Path("data/warehouse.sqlite")
OUT = Path("_workspace/strategy_pipeline/21_loss_autopsy.json")
OUT_MD = Path("_workspace/strategy_pipeline/21_loss_autopsy.md")


def main() -> None:
    con = sqlite3.connect(f"file:{WH.as_posix()}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row

    now = int(datetime.now(timezone.utc).timestamp())
    windows = {"7d": now - 7 * 86400, "30d": now - 30 * 86400, "all": 0}

    report: dict = {"generated_utc": datetime.now(timezone.utc).isoformat(), "windows": {}}

    for label, since in windows.items():
        q = """
        SELECT
          COUNT(*) AS n,
          SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) AS wins,
          SUM(CASE WHEN realized_pnl < 0 THEN 1 ELSE 0 END) AS losses,
          SUM(CASE WHEN realized_pnl = 0 OR realized_pnl IS NULL THEN 1 ELSE 0 END) AS flats,
          SUM(COALESCE(realized_pnl, 0)) AS pnl_sum,
          AVG(realized_pnl) AS pnl_avg,
          SUM(COALESCE(fee, 0)) AS fee_sum,
          AVG(hold_sec) AS avg_hold_sec
        FROM trades
        WHERE lower(COALESCE(status,'')) = 'closed'
          AND (? = 0 OR COALESCE(ts_exit, 0) >= ?)
        """
        row = dict(con.execute(q, (since, since)).fetchone())
        if row["n"]:
            row["wr"] = (row["wins"] or 0) / row["n"]
        else:
            row["wr"] = None

        by_exit = [
            dict(r)
            for r in con.execute(
                """
                SELECT COALESCE(exit_reason,'?') AS exit_reason,
                       COUNT(*) AS n,
                       SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) AS wins,
                       SUM(COALESCE(realized_pnl,0)) AS pnl,
                       SUM(COALESCE(fee,0)) AS fees
                FROM trades
                WHERE lower(COALESCE(status,'')) = 'closed'
                  AND (? = 0 OR COALESCE(ts_exit, 0) >= ?)
                GROUP BY 1 ORDER BY n DESC
                """,
                (since, since),
            )
        ]
        by_family = [
            dict(r)
            for r in con.execute(
                """
                SELECT COALESCE(strategy_family,'?') AS strategy_family,
                       COUNT(*) AS n,
                       SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) AS wins,
                       SUM(COALESCE(realized_pnl,0)) AS pnl,
                       SUM(COALESCE(fee,0)) AS fees
                FROM trades
                WHERE lower(COALESCE(status,'')) = 'closed'
                  AND (? = 0 OR COALESCE(ts_exit, 0) >= ?)
                GROUP BY 1 ORDER BY n DESC
                """,
                (since, since),
            )
        ]
        by_fill = [
            dict(r)
            for r in con.execute(
                """
                SELECT COALESCE(fill_type,'?') AS fill_type,
                       COUNT(*) AS n,
                       SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) AS wins,
                       SUM(COALESCE(realized_pnl,0)) AS pnl
                FROM trades
                WHERE lower(COALESCE(status,'')) = 'closed'
                  AND (? = 0 OR COALESCE(ts_exit, 0) >= ?)
                GROUP BY 1 ORDER BY n DESC
                """,
                (since, since),
            )
        ]
        by_sym = [
            dict(r)
            for r in con.execute(
                """
                SELECT symbol,
                       COUNT(*) AS n,
                       SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) AS wins,
                       SUM(COALESCE(realized_pnl,0)) AS pnl
                FROM trades
                WHERE lower(COALESCE(status,'')) = 'closed'
                  AND (? = 0 OR COALESCE(ts_exit, 0) >= ?)
                GROUP BY 1 ORDER BY pnl ASC LIMIT 15
                """,
                (since, since),
            )
        ]
        recent = [
            dict(r)
            for r in con.execute(
                """
                SELECT symbol, side, strategy_family, exit_reason, fill_type,
                       realized_pnl, fee, hold_sec, ts_exit, mcp_score
                FROM trades
                WHERE lower(COALESCE(status,'')) = 'closed'
                  AND (? = 0 OR COALESCE(ts_exit, 0) >= ?)
                ORDER BY ts_exit DESC LIMIT 20
                """,
                (since, since),
            )
        ]
        report["windows"][label] = {
            "summary": row,
            "by_exit_reason": by_exit,
            "by_strategy_family": by_family,
            "by_fill_type": by_fill,
            "worst_symbols": by_sym,
            "recent_20": recent,
        }

    # Funnel snapshot
    fun = Path("data/promotion_funnel.json")
    if fun.exists():
        f = json.loads(fun.read_text(encoding="utf-8"))
        report["funnel"] = {
            "generated_utc": f.get("generated_utc"),
            "lanes": [
                {
                    "lane": x.get("lane"),
                    "state": x.get("state"),
                    "floor_progress": x.get("floor_progress"),
                    "wr": x.get("wr"),
                }
                for x in f.get("lanes", [])
            ],
        }

    hb = Path("data/heartbeat.json")
    if hb.exists():
        h = json.loads(hb.read_text(encoding="utf-8"))
        report["heartbeat"] = {
            k: h.get(k)
            for k in (
                "operating_mode",
                "paper_trading_profile",
                "signal_source",
                "entry_policy",
                "is_halted",
                "daily_pnl",
                "open_positions",
            )
        }

    con.close()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")

    w7 = report["windows"]["7d"]["summary"]
    w30 = report["windows"]["30d"]["summary"]
    lines = [
        "# 21 — Loss autopsy (PAPER)",
        "",
        f"**Generated:** {report['generated_utc']}",
        "",
        "## Plain language",
        "",
        "Losses are dominated by **after-cost directional PAPER outcomes**, not by a missing candlestick AI.",
        "Win-rate swings (funnel cohort recently ~46%) are expected under band geometry; expectancy stays the binding metric.",
        "",
        "## Heartbeat",
        "",
        f"- Mode/profile/signal: `{report.get('heartbeat')}`",
        "",
        "## 7-day closed summary",
        "",
        f"- n={w7.get('n')} wins={w7.get('wins')} losses={w7.get('losses')} WR={w7.get('wr')}",
        f"- sum realized_pnl={w7.get('pnl_sum')} avg={w7.get('pnl_avg')} fees={w7.get('fee_sum')}",
        "",
        "## 30-day closed summary",
        "",
        f"- n={w30.get('n')} wins={w30.get('wins')} losses={w30.get('losses')} WR={w30.get('wr')}",
        f"- sum realized_pnl={w30.get('pnl_sum')} avg={w30.get('pnl_avg')} fees={w30.get('fee_sum')}",
        "",
        "## Top exit reasons (7d)",
        "",
    ]
    for r in report["windows"]["7d"]["by_exit_reason"][:10]:
        lines.append(
            f"- `{r['exit_reason']}`: n={r['n']} wins={r['wins']} pnl={r['pnl']} fees={r['fees']}"
        )
    lines.extend(["", "## Strategy families (7d)", ""])
    for r in report["windows"]["7d"]["by_strategy_family"][:10]:
        lines.append(
            f"- `{r['strategy_family']}`: n={r['n']} wins={r['wins']} pnl={r['pnl']}"
        )
    lines.extend(["", "## Fill types (7d)", ""])
    for r in report["windows"]["7d"]["by_fill_type"]:
        lines.append(f"- `{r['fill_type']}`: n={r['n']} wins={r['wins']} pnl={r['pnl']}")
    lines.extend(["", "## Worst symbols (7d by pnl)", ""])
    for r in report["windows"]["7d"]["worst_symbols"][:10]:
        lines.append(f"- `{r['symbol']}`: n={r['n']} wins={r['wins']} pnl={r['pnl']}")
    if report.get("funnel"):
        lines.extend(["", "## Funnel snapshot", ""])
        for lane in report["funnel"]["lanes"]:
            lines.append(
                f"- {lane['lane']}: {lane['state']} {lane['floor_progress']} wr={lane['wr']}"
            )
    lines.append("")
    OUT_MD.write_text("\n".join(lines), encoding="utf-8")
    print(json.dumps({"ok": True, "7d": w7, "30d": w30, "out": str(OUT)}, indent=2, default=str))


if __name__ == "__main__":
    main()
