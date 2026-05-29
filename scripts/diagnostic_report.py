"""Attribution diagnostic report — the Phase 1 gate.

Reads `attribution` joined to `trades` from warehouse, computes per-strategy
and per-symbol decomposition of realized PnL into alpha / spread / slippage
/ funding / fees, and emits a verdict:

  EDGE_PRESENT   alpha 95% CI lower bound > 0 (signals have edge)
  EDGE_AMBIGUOUS 0 ∈ alpha CI (uncertain)
  NEGATIVE_EDGE  alpha 95% CI upper bound < 0 (signals are anti-predictive)

Verdict drives whether Phase 2+ of the quant rebuild proceeds.

Caveat: backfilled rows have spread=slippage=funding=0 because mid prices
weren't captured at entry/exit historically. alpha on backfilled rows ==
gross PnL. Phase 1.4 captures real mids for new trades, enabling full
decomposition going forward.

Usage:
    python scripts/diagnostic_report.py             # stdout
    python scripts/diagnostic_report.py --save      # also writes reports/...
"""
from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.stat_tests import bootstrap_ci  # noqa: E402
from core.warehouse import get_warehouse  # noqa: E402

# Minimum rows in a group before we'll compute a CI / emit a verdict.
MIN_GROUP_N = 10


def _parse_since(raw: str) -> float:
    """Parse a `--since` arg into a unix timestamp.

    Accepts:
      - ISO-8601 date:    2026-04-27
      - ISO-8601 datetime: 2026-04-27T00:00:00 (timezone-aware or naive UTC)
      - relative duration: 7d, 24h, 30d
    """
    s = (raw or "").strip()
    if not s:
        raise ValueError("empty --since")
    if s.endswith("d") and s[:-1].isdigit():
        return time.time() - int(s[:-1]) * 86400.0
    if s.endswith("h") and s[:-1].isdigit():
        return time.time() - int(s[:-1]) * 3600.0
    # ISO date / datetime
    try:
        if "T" in s:
            dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        else:
            dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except ValueError as e:
        raise ValueError(
            f"--since must be ISO date (2026-04-27), ISO datetime, or "
            f"a relative duration like 7d/24h: got {raw!r}"
        ) from e


def _is_zero_cost_backfill(row: dict) -> bool:
    """A row is 'zero-cost backfill' when spread, slippage, AND funding are
    all exactly zero. Newly-attributed trades from Phase 2 / Task A onward
    will carry non-zero spread (live spread ≥ 1bp on any real fill) so this
    predicate cleanly partitions backfilled-in-bulk historical rows from
    cleanly-attributed forward rows."""
    return (
        float(row.get("spread") or 0.0) == 0.0
        and float(row.get("slippage") or 0.0) == 0.0
        and float(row.get("funding") or 0.0) == 0.0
    )


def verdict(lb: float, ub: float) -> str:
    """Map an alpha CI (lb, ub) to a verdict label.

    Strict inequalities: an LB at exactly 0 is ambiguous, not edge-present.
    """
    if lb > 0:
        return "EDGE_PRESENT"
    if ub < 0:
        return "NEGATIVE_EDGE"
    return "EDGE_AMBIGUOUS"


def _agg(rows: list[dict]) -> dict:
    """Compute aggregate stats + alpha CI for a group of attribution rows."""
    alphas = np.array([r["alpha"] for r in rows], dtype=float)
    spreads = np.array([r["spread"] for r in rows], dtype=float)
    slips = np.array([r["slippage"] for r in rows], dtype=float)
    fundings = np.array([r["funding"] for r in rows], dtype=float)
    fees = np.array([r["fees"] for r in rows], dtype=float)
    pnls = np.array([r["realized_pnl"] for r in rows], dtype=float)
    lb, ub = bootstrap_ci(
        alphas,
        statistic=lambda x: float(np.mean(x)),
        n_resamples=10000,
        ci=0.95,
        random_state=42,
    )
    return {
        "n": len(rows),
        "mean_alpha": float(alphas.mean()),
        "mean_spread": float(spreads.mean()),
        "mean_slippage": float(slips.mean()),
        "mean_funding": float(fundings.mean()),
        "mean_fees": float(fees.mean()),
        "mean_realized_pnl": float(pnls.mean()),
        "alpha_ci_lb": lb,
        "alpha_ci_ub": ub,
        "verdict": verdict(lb, ub),
    }


def per_group_report(rows: list[dict], group_field: str) -> list[dict]:
    """Group rows by `group_field`, compute per-group aggregates."""
    groups: dict[str, list[dict]] = {}
    for r in rows:
        g = r.get(group_field) or "unknown"
        groups.setdefault(str(g), []).append(r)
    out = []
    for g, grp in sorted(groups.items()):
        if len(grp) < MIN_GROUP_N:
            continue
        agg = _agg(grp)
        agg["group"] = g
        out.append(agg)
    return out


def format_report(
    overall: dict | None,
    by_strategy: list[dict],
    by_symbol: list[dict],
    *,
    n_zero_cost: int = 0,
    n_total: int = 0,
    since: str | None = None,
) -> str:
    L = []
    L.append("# Attribution Diagnostic Report")
    L.append(f"\nGenerated: {datetime.now(timezone.utc).isoformat(timespec='seconds')}\n")
    L.append(
        "**IMPORTANT** — Backfilled rows have spread = slippage = funding = 0 "
        "because mid prices were not captured at entry/exit historically. "
        "alpha on those rows is gross PnL. Phase 1.4 wires real-mid capture "
        "so newly-closed trades carry full decomposition.\n"
    )
    if since:
        L.append(f"**Window:** ts_entry >= `{since}`\n")
    if n_total > 0:
        pct = (n_zero_cost / n_total * 100.0) if n_total else 0.0
        L.append(
            f"**Backfill blind spot:** {n_zero_cost} of {n_total} matched rows "
            f"({pct:.1f}%) carry spread=slippage=funding=0. Use "
            f"`--exclude-zero-cost` to drop them for a clean re-gate.\n"
        )

    if overall:
        L.append("## Overall (all closed trades)\n")
        L.append(f"- n_trades: **{overall['n']}**")
        L.append(f"- mean alpha (gross PnL approx): **${overall['mean_alpha']:+.4f}**")
        L.append(f"- mean fees: ${overall['mean_fees']:.4f}")
        L.append(f"- mean realized PnL: ${overall['mean_realized_pnl']:+.4f}")
        L.append(
            f"- alpha 95% CI: [${overall['alpha_ci_lb']:+.4f}, "
            f"${overall['alpha_ci_ub']:+.4f}]"
        )
        L.append(f"- **VERDICT: `{overall['verdict']}`**\n")

    L.append("## Per Strategy\n")
    L.append("| Strategy | n | mean alpha | mean fees | mean PnL | alpha CI95% | verdict |")
    L.append("|---|---:|---:|---:|---:|:---|:---:|")
    for r in by_strategy:
        L.append(
            f"| `{r['group']}` | {r['n']} | "
            f"${r['mean_alpha']:+.4f} | ${r['mean_fees']:.4f} | "
            f"${r['mean_realized_pnl']:+.4f} | "
            f"[${r['alpha_ci_lb']:+.3f}, ${r['alpha_ci_ub']:+.3f}] | "
            f"**{r['verdict']}** |"
        )

    L.append("\n## Per Symbol (top 20 by trade count)\n")
    L.append("| Symbol | n | mean alpha | mean PnL | verdict |")
    L.append("|---|---:|---:|---:|:---:|")
    top = sorted(by_symbol, key=lambda x: -x["n"])[:20]
    for r in top:
        L.append(
            f"| `{r['group']}` | {r['n']} | "
            f"${r['mean_alpha']:+.4f} | "
            f"${r['mean_realized_pnl']:+.4f} | "
            f"**{r['verdict']}** |"
        )

    L.append("\n## Decision Gate\n")
    edge_strats = [r["group"] for r in by_strategy if r["verdict"] == "EDGE_PRESENT"]
    neg_strats = [r["group"] for r in by_strategy if r["verdict"] == "NEGATIVE_EDGE"]

    if overall and overall["verdict"] == "EDGE_PRESENT":
        L.append(
            "**Proceed to Phase 2** (feature engineering) → Phase 3 (model "
            "layer). Overall alpha CI lower bound > 0 — signals have real "
            "edge. ML rebuild on top is justified."
        )
    elif edge_strats:
        L.append(
            f"**Proceed to Phase 2** focused on the strategies with "
            f"positive-CI alpha: `{edge_strats}`. Other strategies should be "
            "shadow-mode-only until they prove edge."
        )
    elif overall and overall["verdict"] == "NEGATIVE_EDGE":
        L.append(
            "**STOP. Escalate to operator.** Overall alpha CI upper bound < 0 "
            "— signals are anti-predictive. Adding ML on top will not rescue "
            "them. Rethink signal universe before Phase 2+."
        )
    elif neg_strats and not edge_strats:
        L.append(
            f"**STOP. Escalate to operator.** Strategies "
            f"`{neg_strats}` are negatively-attributed and no strategy is "
            "edge-present. Rethink signal universe."
        )
    else:
        L.append(
            "**EDGE_AMBIGUOUS overall.** Plan recommends descoping to Phase 4 "
            "(execution-only fixes: distribution-fit SL/TP, vol targeting, "
            "smart execution). Skip the ML phases until alpha CI tightens "
            "above zero or more data accumulates."
        )

    return "\n".join(L)


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--save", action="store_true",
        help="Also write to reports/attribution_diagnostic_<YYYYMMDD>.md",
    )
    parser.add_argument(
        "--exclude-strategy", action="append", default=[],
        help="Strategy family to exclude (repeatable, e.g. --exclude-strategy Supertrend)",
    )
    parser.add_argument(
        "--mode", default=None,
        help="Restrict to one trade mode (e.g. CONTROLLED_LIVE, PAPER)",
    )
    parser.add_argument(
        "--exclude-symbol", action="append", default=[],
        help="Symbol to exclude (repeatable, e.g. --exclude-symbol SOL/USDT:USDT)",
    )
    parser.add_argument(
        "--tag", default=None,
        help="Tag to embed in the saved filename (e.g. live_only)",
    )
    parser.add_argument(
        "--since", default=None,
        help=("Filter trades by ts_entry. Accepts ISO date (2026-04-27), "
              "ISO datetime, or relative duration like 7d/24h. Use this to "
              "re-gate against post-Phase-2 trades only."),
    )
    parser.add_argument(
        "--exclude-zero-cost", action="store_true",
        help=("Skip rows where spread=slippage=funding=0 (the pre-Task-A "
              "backfill blind spot). Recommended for any --since that "
              "post-dates the forward-attribution wire-up."),
    )
    args = parser.parse_args()

    wh = get_warehouse()
    sql = (
        "SELECT a.*, t.strategy_family, t.symbol, t.exchange, t.mode, t.side "
        "FROM attribution a "
        "JOIN trades t ON t.id = a.trade_id "
        "WHERE t.status = 'CLOSED'"
    )
    params: list = []
    if args.mode:
        sql += " AND t.mode = ?"
        params.append(args.mode)
    if args.exclude_strategy:
        placeholders = ",".join("?" * len(args.exclude_strategy))
        sql += f" AND t.strategy_family NOT IN ({placeholders})"
        params.extend(args.exclude_strategy)
    if args.exclude_symbol:
        placeholders = ",".join("?" * len(args.exclude_symbol))
        sql += f" AND t.symbol NOT IN ({placeholders})"
        params.extend(args.exclude_symbol)
    if args.since:
        since_ts = _parse_since(args.since)
        sql += " AND t.ts_entry >= ?"
        params.append(since_ts)
    rows = wh.query(sql, params)
    if not rows:
        print(
            "No attribution rows. "
            "Run scripts/backfill_attribution.py --commit first.",
            file=sys.stderr,
        )
        sys.exit(1)

    n_pre_filter = len(rows)
    n_zero_cost = sum(1 for r in rows if _is_zero_cost_backfill(r))
    if args.exclude_zero_cost and n_zero_cost > 0:
        rows = [r for r in rows if not _is_zero_cost_backfill(r)]
        print(
            f"[diagnostic] --exclude-zero-cost: skipped {n_zero_cost} of "
            f"{n_pre_filter} rows with spread=slippage=funding=0 "
            f"(pre-Task-A backfill blind spot)",
            file=sys.stderr,
        )
    if not rows:
        print(
            "All rows excluded. The warehouse only contains pre-Task-A "
            "backfill so far — wait for forward-attributed trades to "
            "accumulate, or drop --exclude-zero-cost to inspect history.",
            file=sys.stderr,
        )
        sys.exit(1)

    overall = _agg(rows)
    overall["group"] = "ALL"
    by_strategy = per_group_report(rows, "strategy_family")
    by_symbol = per_group_report(rows, "symbol")

    report = format_report(
        overall, by_strategy, by_symbol,
        n_zero_cost=n_zero_cost, n_total=n_pre_filter, since=args.since,
    )
    print(report)

    if args.save:
        date_str = datetime.now(timezone.utc).strftime("%Y%m%d")
        suffix = f"_{args.tag}" if args.tag else ""
        out = ROOT / "reports" / f"attribution_diagnostic_{date_str}{suffix}.md"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(report, encoding="utf-8")
        print(f"\nSaved to {out}", file=sys.stderr)


if __name__ == "__main__":
    main()
