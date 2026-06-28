"""Strategy readiness and promotion evidence.

This module is deliberately conservative. It does not try to find the most
profitable parameter set; it checks whether realized or replayed trades have
enough after-cost evidence to be considered for live promotion.
"""

from __future__ import annotations

import json
import math
import sqlite3
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class StrategyReadinessThresholds:
    min_trades: int = 100
    min_profit_factor: float = 1.20
    min_avg_pnl: float = 0.0
    min_win_rate: float = 0.35
    min_positive_folds: int = 2
    min_fold_trades: int = 10
    max_drawdown_to_gross_profit: float = 1.50
    folds: int = 3
    lookback_days: int = 30


DEFAULT_THRESHOLDS = StrategyReadinessThresholds()


def _finite_float(value: Any, default: float = 0.0) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return default
    return out if math.isfinite(out) else default


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def summarize_trades(records: list[dict[str, Any]]) -> dict[str, Any]:
    pnls = [_finite_float(r.get("pnl")) for r in records]
    n = len(pnls)
    if n == 0:
        return {
            "n": 0,
            "pnl": 0.0,
            "avg_pnl": 0.0,
            "win_rate": 0.0,
            "gross_win": 0.0,
            "gross_loss": 0.0,
            "profit_factor": None,
            "max_drawdown": 0.0,
            "max_drawdown_to_gross_profit": None,
        }

    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    gross_win = sum(wins)
    gross_loss = abs(sum(losses))
    profit_factor = gross_win / gross_loss if gross_loss > 0 else None

    equity = 0.0
    peak = 0.0
    max_dd = 0.0
    for pnl in pnls:
        equity += pnl
        peak = max(peak, equity)
        max_dd = max(max_dd, peak - equity)

    dd_to_gross = max_dd / gross_win if gross_win > 0 else None
    return {
        "n": n,
        "pnl": round(sum(pnls), 6),
        "avg_pnl": round(sum(pnls) / n, 6),
        "win_rate": round(len(wins) / n, 6),
        "gross_win": round(gross_win, 6),
        "gross_loss": round(gross_loss, 6),
        "profit_factor": round(profit_factor, 6) if profit_factor is not None else None,
        "max_drawdown": round(max_dd, 6),
        "max_drawdown_to_gross_profit": (
            round(dd_to_gross, 6) if dd_to_gross is not None else None
        ),
    }


def fold_summaries(
    records: list[dict[str, Any]],
    *,
    folds: int = DEFAULT_THRESHOLDS.folds,
) -> list[dict[str, Any]]:
    if not records:
        return []
    ordered = sorted(records, key=lambda r: _finite_float(r.get("ts")))
    folds = max(1, int(folds))
    out: list[dict[str, Any]] = []
    for i in range(folds):
        start = int(i * len(ordered) / folds)
        end = int((i + 1) * len(ordered) / folds)
        chunk = ordered[start:end]
        summary = summarize_trades(chunk)
        summary["fold"] = i + 1
        if chunk:
            summary["ts_start"] = _finite_float(chunk[0].get("ts"))
            summary["ts_end"] = _finite_float(chunk[-1].get("ts"))
        out.append(summary)
    return out


def detector_attribution(records: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    buckets: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        detectors = record.get("detectors") or {}
        if not isinstance(detectors, dict):
            continue
        for name, value in detectors.items():
            v = _finite_float(value)
            if v == 0:
                continue
            direction = "bull" if v > 0 else "bear"
            buckets.setdefault(f"{name}:{direction}", []).append(record)
    return {key: summarize_trades(value) for key, value in sorted(buckets.items())}


def strategy_breakdown(records: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    buckets: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        strategy = str(record.get("strategy") or "unknown")
        buckets.setdefault(strategy, []).append(record)
    return {key: summarize_trades(value) for key, value in sorted(buckets.items())}


def evaluate_records(
    records: list[dict[str, Any]],
    *,
    thresholds: StrategyReadinessThresholds = DEFAULT_THRESHOLDS,
    name: str = "strategy",
) -> dict[str, Any]:
    summary = summarize_trades(records)
    folds = fold_summaries(records, folds=thresholds.folds)
    positive_folds = sum(
        1
        for fold in folds
        if int(fold.get("n") or 0) >= thresholds.min_fold_trades
        and _finite_float(fold.get("avg_pnl")) > thresholds.min_avg_pnl
        and _finite_float(fold.get("profit_factor"), 0.0) >= 1.0
    )

    reasons: list[str] = []
    if summary["n"] < thresholds.min_trades:
        reasons.append(f"n {summary['n']} < {thresholds.min_trades}")
    if summary["avg_pnl"] <= thresholds.min_avg_pnl:
        reasons.append(f"avg_pnl {summary['avg_pnl']:.6f} <= {thresholds.min_avg_pnl:.6f}")
    pf = summary["profit_factor"]
    if pf is None or pf < thresholds.min_profit_factor:
        reasons.append(f"profit_factor {pf} < {thresholds.min_profit_factor:.2f}")
    if summary["win_rate"] < thresholds.min_win_rate:
        reasons.append(f"win_rate {summary['win_rate']:.3f} < {thresholds.min_win_rate:.2f}")
    if positive_folds < thresholds.min_positive_folds:
        reasons.append(
            f"positive_folds {positive_folds} < {thresholds.min_positive_folds}"
        )
    dd_ratio = summary["max_drawdown_to_gross_profit"]
    if dd_ratio is None or dd_ratio > thresholds.max_drawdown_to_gross_profit:
        reasons.append(
            "max_drawdown_to_gross_profit "
            f"{dd_ratio} > {thresholds.max_drawdown_to_gross_profit:.2f}"
        )

    verdict = "PROMOTE_ELIGIBLE" if not reasons else "PAPER_ONLY"
    return {
        "name": name,
        "verdict": verdict,
        "ready": not reasons,
        "generated_at": _utc_now().isoformat(),
        "thresholds": asdict(thresholds),
        "summary": summary,
        "folds": folds,
        "positive_folds": positive_folds,
        "reasons": reasons,
        "by_strategy": strategy_breakdown(records),
        "detector_attribution": detector_attribution(records),
    }


def load_warehouse_records(
    db_path: str | Path = "data/warehouse.sqlite",
    *,
    lookback_days: int = DEFAULT_THRESHOLDS.lookback_days,
    mode: str | None = "PAPER",
    strategy_family: str | None = None,
) -> list[dict[str, Any]]:
    path = Path(db_path)
    if not path.exists():
        return []

    where = ["status='CLOSED'", "ts_exit IS NOT NULL", "ts_exit >= ?"]
    params: list[Any] = [time.time() - int(lookback_days) * 86_400]
    if mode:
        where.append("mode=?")
        params.append(mode)
    if strategy_family:
        where.append("strategy_family=?")
        params.append(strategy_family)

    sql = f"""
        SELECT
            ts_exit AS ts,
            symbol,
            side,
            COALESCE(strategy_family, 'unknown') AS strategy,
            mode,
            realized_pnl + COALESCE(partial_realized_pnl, 0.0) AS pnl,
            mcp_score,
            decision_id
        FROM trades
        WHERE {' AND '.join(where)}
        ORDER BY ts_exit
    """
    con = sqlite3.connect(str(path))
    con.row_factory = sqlite3.Row
    try:
        rows = con.execute(sql, tuple(params)).fetchall()
    finally:
        con.close()
    detector_map = _load_machine_detector_map(path.parent / "machine_decisions.jsonl")
    records = []
    for row in rows:
        if row["pnl"] is None:
            continue
        record = dict(row)
        decision_id = str(record.get("decision_id") or "")
        if decision_id in detector_map:
            record["detectors"] = detector_map[decision_id]
        records.append(record)
    return records


def _load_machine_detector_map(path: Path) -> dict[str, dict[str, int]]:
    if not path.exists():
        return {}
    out: dict[str, dict[str, int]] = {}
    try:
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                decision_id = str(rec.get("decision_id") or "")
                diagnostics = rec.get("diagnostics") or {}
                detectors = diagnostics.get("detectors") or {}
                if decision_id and isinstance(detectors, dict):
                    out[decision_id] = {
                        str(k): int(v)
                        for k, v in detectors.items()
                        if _finite_float(v) != 0.0
                    }
    except OSError:
        return {}
    return out


def evaluate_warehouse(
    db_path: str | Path = "data/warehouse.sqlite",
    *,
    thresholds: StrategyReadinessThresholds = DEFAULT_THRESHOLDS,
    mode: str | None = "PAPER",
    strategy_family: str | None = None,
) -> dict[str, Any]:
    records = load_warehouse_records(
        db_path,
        lookback_days=thresholds.lookback_days,
        mode=mode,
        strategy_family=strategy_family,
    )
    name = f"warehouse:{mode or 'ALL'}:{strategy_family or 'ALL'}"
    return evaluate_records(records, thresholds=thresholds, name=name)


def write_readiness_reports(
    report: dict[str, Any],
    *,
    out_dir: str | Path = "reports",
    latest_name: str = "strategy_readiness_latest.json",
) -> tuple[Path, Path, Path]:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    stamp = _utc_now().strftime("%Y%m%d_%H%M%S")
    json_path = out / f"strategy_readiness_{stamp}.json"
    md_path = out / f"strategy_readiness_{stamp}.md"
    latest_path = out / latest_name
    payload = json.dumps(report, indent=2, default=str)
    json_path.write_text(payload, encoding="utf-8")
    latest_path.write_text(payload, encoding="utf-8")

    summary = report.get("summary", {})
    lines = [
        "# Strategy Readiness",
        "",
        f"Generated: {report.get('generated_at')}",
        f"Verdict: **{report.get('verdict')}**",
        "",
        "| Metric | Value |",
        "| --- | ---: |",
        f"| Trades | {summary.get('n', 0)} |",
        f"| PnL | {summary.get('pnl', 0.0)} |",
        f"| Avg PnL | {summary.get('avg_pnl', 0.0)} |",
        f"| Win rate | {summary.get('win_rate', 0.0)} |",
        f"| Profit factor | {summary.get('profit_factor')} |",
        f"| Max drawdown | {summary.get('max_drawdown', 0.0)} |",
        "",
        "## Reasons",
    ]
    reasons = report.get("reasons") or []
    lines.extend([f"- {r}" for r in reasons] or ["- none"])
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return json_path, md_path, latest_path
