"""Trading system audit.

This script is an operational and research-readiness check. It does not place
orders. It intentionally separates "can the bot run?" from "is any strategy
validated enough for live money?".
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sqlite3
import subprocess
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _status_rank(status: str) -> int:
    return {"PASS": 0, "INFO": 1, "WARN": 2, "FAIL": 3}.get(status, 2)


def add_check(
    checks: list[dict[str, Any]],
    name: str,
    status: str,
    detail: str,
    **metrics: Any,
) -> None:
    checks.append(
        {
            "name": name,
            "status": status,
            "detail": detail,
            "metrics": metrics,
        }
    )


def _run_text(cmd: list[str], *, timeout: int = 10) -> str:
    try:
        cp = subprocess.run(
            cmd,
            cwd=ROOT,
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except Exception as exc:
        return f"ERROR: {exc}"
    return (cp.stdout or cp.stderr or "").strip()


def audit_config(checks: list[dict[str, Any]]) -> None:
    import config
    from core.live_gate import is_checklist_signed

    add_check(
        checks,
        "config.mode",
        "PASS" if config.DRY_RUN else "WARN",
        f"OPERATING_MODE={config.OPERATING_MODE}, DRY_RUN={config.DRY_RUN}",
        operating_mode=config.OPERATING_MODE,
        dry_run=config.DRY_RUN,
        controlled_live_enabled=config.CONTROLLED_LIVE_ENABLED,
    )
    add_check(
        checks,
        "config.signal_source",
        "PASS",
        f"SIGNAL_SOURCE={config.SIGNAL_SOURCE}",
        signal_source=config.SIGNAL_SOURCE,
        trading_mode=config.TRADING_MODE,
    )
    if config.SIGNAL_SOURCE == "machine":
        machine = config.MACHINE_STRATEGY
        min_tier_conf = min(
            float(t.get("min_confidence", 1.0)) for t in config.LEVERAGE_TIERS.values()
        )
        floor = float(machine.get("execution_confidence_floor", 0.0))
        ok = floor >= min_tier_conf
        add_check(
            checks,
            "machine.execution_gate",
            "PASS" if ok else "FAIL",
            (
                "machine execution confidence clears the lowest tier"
                if ok
                else "machine actions can be emitted but rejected by leverage tiering"
            ),
            min_score=float(machine.get("min_score", 0.0)),
            execution_confidence_floor=floor,
            lowest_tier_confidence=min_tier_conf,
        )

    signed, msg = is_checklist_signed()
    live_ok = signed and config.CONTROLLED_LIVE_ENABLED
    add_check(
        checks,
        "live_gate.readiness",
        "WARN" if not live_ok else "PASS",
        (
            "CONTROLLED_LIVE is not armed; this is expected for research/PAPER"
            if not live_ok
            else "CONTROLLED_LIVE checklist and env latch are armed"
        ),
        checklist_signed=signed,
        checklist_message=msg,
        controlled_live_enabled=config.CONTROLLED_LIVE_ENABLED,
    )


def audit_env_file(checks: list[dict[str, Any]]) -> None:
    env_path = ROOT / ".env"
    if not env_path.exists():
        add_check(checks, "secrets.env_file", "WARN", ".env is missing")
        return

    secret_names = [
        "API_KEY",
        "SECRET",
        "PASSPHRASE",
        "PASSWORD",
        "TOKEN",
    ]
    populated = 0
    for line in env_path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line or line.lstrip().startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if any(marker in key.upper() for marker in secret_names):
            v = value.strip()
            if v and "your_" not in v.lower() and "placeholder" not in v.lower():
                populated += 1

    tracked = _run_text(["git", "ls-files", ".env"]) == ".env"
    ignored = _run_text(["git", "check-ignore", ".env"]) == ".env"
    status = "FAIL" if tracked else ("PASS" if ignored else "WARN")
    add_check(
        checks,
        "secrets.env_file",
        status,
        ".env is ignored by git" if ignored and not tracked else ".env git handling needs review",
        populated_secret_fields=populated,
        git_tracked=tracked,
        git_ignored=ignored,
    )


def audit_secret_leak_scan(checks: list[dict[str, Any]]) -> None:
    pattern = (
        r"(API_KEY|SECRET|TOKEN|PASSWORD|PASSPHRASE)"
        r"\s*=\s*['\"]?[A-Za-z0-9_./+=:-]{16,}"
    )
    out = _run_text(
        ["git", "grep", "-I", "-n", "-E", pattern, "--", "."],
        timeout=20,
    )
    matches: list[tuple[str, int]] = []
    for line in out.splitlines():
        if not line or line.startswith("ERROR:"):
            continue
        try:
            file_part, line_no, text = line.split(":", 2)
        except ValueError:
            continue
        low = text.lower()
        if "os.getenv" in text or "placeholder" in low or "your_" in low:
            continue
        if "unauthorized_user_token" in low or "noqa: s105" in low:
            continue
        if file_part.endswith((".md", ".example", ".sample")):
            continue
        try:
            matches.append((file_part, int(line_no)))
        except ValueError:
            matches.append((file_part, 0))
    files = sorted({m[0] for m in matches})
    add_check(
        checks,
        "secrets.tracked_scan",
        "WARN" if matches else "PASS",
        (
            "potential tracked secret assignments found"
            if matches
            else "no tracked hardcoded secret assignments detected"
        ),
        matches=len(matches),
        files=files[:20],
    )


RUNTIME_SCRIPTS = {
    "main": "main.py",
    "confluence_paper": "run_confluence_paper.py",
    "liquidations": "harvest_liquidations.py",
    "skew": "harvest_skew.py",
    "l2": "harvest_l2.py",
    "tv": "harvest_tv.py",
}


def _optional_runtime_missing() -> list[str]:
    missing: list[str] = []
    confluence = ROOT / RUNTIME_SCRIPTS["confluence_paper"]
    if not confluence.exists():
        missing.append("confluence_paper")
    return missing


def _script_key(command_line: str | None) -> str | None:
    cl = str(command_line or "").lower().replace("/", "\\")
    for key, leaf in RUNTIME_SCRIPTS.items():
        if leaf.lower() in cl:
            return key
    return None


def _windows_python_processes() -> list[dict[str, Any]]:
    ps = (
        "$items = Get-CimInstance Win32_Process | "
        "Where-Object { $_.Name -match 'python|py.exe' -and "
        "($_.CommandLine -match 'Trading_Bot' -or $_.CommandLine -match 'main.py' "
        "-or $_.CommandLine -match 'harvest_' -or $_.CommandLine -match 'run_confluence') } | "
        "Select-Object ProcessId,ParentProcessId,CommandLine; "
        "$items | ConvertTo-Json -Compress"
    )
    out = _run_text(["powershell", "-NoProfile", "-Command", ps], timeout=15)
    if not out or out.startswith("ERROR:"):
        return []
    try:
        payload = json.loads(out)
    except json.JSONDecodeError:
        return []
    if isinstance(payload, dict):
        payload = [payload]
    if not isinstance(payload, list):
        return []
    return [p for p in payload if isinstance(p, dict)]


def _logical_script_counts(
    processes: list[dict[str, Any]],
) -> tuple[dict[str, int], dict[str, int]]:
    grouped: dict[str, list[dict[str, Any]]] = {k: [] for k in RUNTIME_SCRIPTS}
    for proc in processes:
        key = _script_key(proc.get("CommandLine"))
        if key:
            grouped.setdefault(key, []).append(proc)

    logical: dict[str, int] = {}
    raw: dict[str, int] = {}
    for key, group in grouped.items():
        raw[key] = len(group)
        if not group:
            logical[key] = 0
            continue
        ids = {int(p.get("ProcessId")) for p in group if p.get("ProcessId") is not None}
        roots = [p for p in group if int(p.get("ParentProcessId") or 0) not in ids]
        logical[key] = len(roots) if roots else 1
    return logical, raw


def _windows_process_audit_supported() -> bool:
    return os.name == "nt"


def audit_processes(checks: list[dict[str, Any]]) -> None:
    if not _windows_process_audit_supported():
        add_check(checks, "runtime.processes", "INFO", "process audit is Windows-only")
        return

    processes = _windows_python_processes()
    logical, raw = _logical_script_counts(processes)
    harvester_keys = ("liquidations", "skew", "l2", "tv")
    missing_harvesters = [k for k in harvester_keys if logical.get(k, 0) < 1]
    optional_missing = _optional_runtime_missing()
    required_aux = [k for k in ("confluence_paper",) if k not in optional_missing]
    missing_aux = [k for k in required_aux if logical.get(k, 0) < 1]
    duplicate_scripts = [k for k, n in logical.items() if n > 1]
    status = "PASS"
    detail = "runtime process layout is sane"
    if logical.get("main", 0) != 1:
        status = "FAIL"
        detail = "main.py logical instance count is not exactly one"
    elif duplicate_scripts:
        status = "FAIL"
        detail = "duplicate trading processes detected"
    elif missing_aux or missing_harvesters:
        status = "WARN"
        detail = "one or more auxiliary runtime processes are missing"
    add_check(
        checks,
        "runtime.processes",
        status,
        detail,
        logical_counts=logical,
        raw_counts=raw,
        missing_harvesters=missing_harvesters,
        missing_auxiliaries=missing_aux,
        optional_runtime_missing=optional_missing,
        duplicate_scripts=duplicate_scripts,
        python_processes=len(processes),
    )


def audit_forward_feeds(checks: list[dict[str, Any]]) -> None:
    from core.feed_health import read_forward_feed_status, unhealthy_forward_feeds

    records = read_forward_feed_status(ROOT)
    missing_processes: list[str] = []
    if _windows_process_audit_supported():
        logical, _raw = _logical_script_counts(_windows_python_processes())
        for rec in records:
            name = str(rec.get("name") or "")
            count = logical.get(name, 0)
            rec["process_count"] = count
            if count < 1:
                missing_processes.append(name)
    unhealthy = unhealthy_forward_feeds(records)
    bad = sorted(set(unhealthy + missing_processes))
    add_check(
        checks,
        "feeds.forward_harvesters",
        "FAIL" if bad else "PASS",
        (
            f"forward feeds unhealthy: {', '.join(bad)}"
            if bad
            else "forward feed harvesters are running and status files are fresh"
        ),
        unhealthy_feeds=bad,
        feeds=records,
    )


def audit_heartbeat(checks: list[dict[str, Any]]) -> None:
    path = ROOT / "data" / "heartbeat.json"
    if not path.exists():
        add_check(checks, "runtime.heartbeat", "WARN", "heartbeat.json is missing")
        return
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        ts = datetime.fromisoformat(str(payload["timestamp"]).replace("Z", "+00:00"))
        age_min = (_utc_now() - ts).total_seconds() / 60.0
    except Exception as exc:
        add_check(checks, "runtime.heartbeat", "FAIL", f"cannot parse heartbeat: {exc}")
        return
    add_check(
        checks,
        "runtime.heartbeat",
        "PASS" if age_min <= 10 else "WARN",
        f"heartbeat age is {age_min:.1f} minutes",
        age_minutes=round(age_min, 2),
        open_positions=payload.get("open_positions"),
        active_exchanges=payload.get("active_exchanges"),
        is_halted=payload.get("is_halted"),
        halt_reason=payload.get("halt_reason"),
    )


def _summary_sql(con: sqlite3.Connection, where: str, params: tuple[Any, ...]) -> dict[str, Any]:
    row = con.execute(
        f"""
        SELECT
            COUNT(*) AS n,
            COALESCE(SUM(realized_pnl), 0.0) AS pnl,
            COALESCE(AVG(realized_pnl), 0.0) AS avg_pnl,
            COALESCE(
                SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) * 1.0
                / NULLIF(COUNT(*), 0),
                0.0
            ) AS win_rate,
            COALESCE(SUM(CASE WHEN realized_pnl > 0 THEN realized_pnl ELSE 0 END), 0.0)
                AS gross_win,
            COALESCE(SUM(CASE WHEN realized_pnl <= 0 THEN realized_pnl ELSE 0 END), 0.0)
                AS gross_loss
        FROM trades
        WHERE {where}
        """,
        params,
    ).fetchone()
    gross_loss = abs(float(row["gross_loss"] or 0.0))
    pf = (float(row["gross_win"]) / gross_loss) if gross_loss > 0 else None
    return {
        "n": int(row["n"]),
        "pnl": round(float(row["pnl"] or 0.0), 4),
        "avg_pnl": round(float(row["avg_pnl"] or 0.0), 4),
        "win_rate": round(float(row["win_rate"] or 0.0), 4),
        "profit_factor": round(pf, 4) if pf is not None else None,
    }


def _group_rows(
    con: sqlite3.Connection,
    field: str,
    *,
    min_count: int = 1,
    limit: int = 8,
) -> list[dict[str, Any]]:
    rows = con.execute(
        f"""
        SELECT
            COALESCE({field}, '') AS bucket,
            COUNT(*) AS n,
            ROUND(COALESCE(SUM(realized_pnl), 0.0), 4) AS pnl,
            ROUND(COALESCE(AVG(realized_pnl), 0.0), 4) AS avg_pnl,
            ROUND(
                COALESCE(
                    SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) * 1.0
                    / NULLIF(COUNT(*), 0),
                    0.0
                ),
                4
            ) AS win_rate
        FROM trades
        WHERE status='CLOSED' AND realized_pnl IS NOT NULL
        GROUP BY COALESCE({field}, '')
        HAVING COUNT(*) >= ?
        ORDER BY pnl ASC
        LIMIT ?
        """,
        (int(min_count), int(limit)),
    ).fetchall()
    return [dict(row) for row in rows]


def audit_loss_forensics(checks: list[dict[str, Any]]) -> None:
    path = ROOT / "data" / "warehouse.sqlite"
    if not path.exists():
        add_check(checks, "strategy.loss_forensics", "INFO", "warehouse missing; no loss forensics")
        return
    try:
        con = sqlite3.connect(path)
        con.row_factory = sqlite3.Row
    except Exception as exc:
        add_check(checks, "strategy.loss_forensics", "WARN", f"cannot open warehouse: {exc}")
        return

    now = time.time()
    try:
        last_24h = _summary_sql(
            con,
            "status='CLOSED' AND ts_exit >= ?",
            (now - 86400,),
        )
        machine = _summary_sql(
            con,
            "status='CLOSED' AND strategy_family='machine'",
            (),
        )
        worst_strategies = _group_rows(con, "strategy_family", min_count=3, limit=8)
        worst_exit_reasons = _group_rows(con, "exit_reason", min_count=3, limit=8)
        worst_hours = con.execute(
            """
            SELECT
                CAST(strftime('%H', ts_exit, 'unixepoch') AS INTEGER) AS utc_hour,
                COUNT(*) AS n,
                ROUND(COALESCE(SUM(realized_pnl), 0.0), 4) AS pnl,
                ROUND(COALESCE(AVG(realized_pnl), 0.0), 4) AS avg_pnl,
                ROUND(
                    COALESCE(
                        SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) * 1.0
                        / NULLIF(COUNT(*), 0),
                        0.0
                    ),
                    4
                ) AS win_rate
            FROM trades
            WHERE status='CLOSED' AND realized_pnl IS NOT NULL
            GROUP BY utc_hour
            HAVING COUNT(*) >= 10
            ORDER BY pnl ASC
            LIMIT 8
            """
        ).fetchall()
    finally:
        con.close()

    negative_recent = last_24h["n"] > 0 and last_24h["pnl"] < 0
    add_check(
        checks,
        "strategy.loss_forensics",
        "WARN" if negative_recent else "INFO",
        (
            f"last 24h paper PnL is {last_24h['pnl']:+.4f}"
            if last_24h["n"]
            else "no closed trades in last 24h"
        ),
        last_24h=last_24h,
        machine=machine,
        worst_strategies=worst_strategies,
        worst_exit_reasons=worst_exit_reasons,
        worst_utc_hours=[dict(row) for row in worst_hours],
    )


def audit_warehouse(checks: list[dict[str, Any]]) -> None:
    path = ROOT / "data" / "warehouse.sqlite"
    if not path.exists():
        add_check(checks, "warehouse.exists", "WARN", "warehouse.sqlite is missing")
        return
    try:
        con = sqlite3.connect(path)
        con.row_factory = sqlite3.Row
    except Exception as exc:
        add_check(checks, "warehouse.exists", "FAIL", f"cannot open warehouse: {exc}")
        return

    now = time.time()
    try:
        all_closed = _summary_sql(con, "status='CLOSED'", ())
        last_30d = _summary_sql(
            con,
            "status='CLOSED' AND ts_exit >= ?",
            (now - 30 * 86400,),
        )
        open_n = int(con.execute("SELECT COUNT(*) FROM trades WHERE status='OPEN'").fetchone()[0])
        stuck_n = int(
            con.execute(
                "SELECT COUNT(*) FROM trades WHERE status='OPEN' AND ts_entry < ?",
                (now - 86400,),
            ).fetchone()[0]
        )
    finally:
        con.close()

    add_check(
        checks,
        "warehouse.closed_trades",
        "PASS" if all_closed["n"] >= 100 else "WARN",
        f"{all_closed['n']} closed trades in warehouse",
        **all_closed,
    )
    live_ready = (
        last_30d["n"] >= 100 and last_30d["pnl"] > 0 and (last_30d["profit_factor"] or 0.0) > 1.0
    )
    add_check(
        checks,
        "strategy.paper_30d_expectancy",
        "PASS" if live_ready else "FAIL",
        (
            "30d paper performance clears basic live-readiness floor"
            if live_ready
            else "30d paper performance is not live-ready"
        ),
        **last_30d,
    )
    add_check(
        checks,
        "warehouse.open_rows",
        "PASS" if stuck_n == 0 else "FAIL",
        f"{open_n} open warehouse rows, {stuck_n} older than 24h",
        open_rows=open_n,
        stuck_open_rows=stuck_n,
    )


def audit_warehouse_integrity(checks: list[dict[str, Any]]) -> None:
    path = ROOT / "data" / "warehouse.sqlite"
    if not path.exists():
        add_check(checks, "warehouse.integrity", "WARN", "warehouse.sqlite is missing")
        return
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    try:
        dup_open_keys = int(
            con.execute(
                """
                SELECT COUNT(*) FROM (
                    SELECT exchange, symbol, ts_entry, side, COUNT(*) AS n
                    FROM trades
                    GROUP BY exchange, symbol, ts_entry, side
                    HAVING n > 1
                )
                """
            ).fetchone()[0]
        )
        dup_decisions = int(
            con.execute(
                """
                SELECT COUNT(*) FROM (
                    SELECT decision_id, COUNT(*) AS n
                    FROM trades
                    WHERE decision_id IS NOT NULL AND decision_id != ''
                    GROUP BY decision_id
                    HAVING n > 1
                )
                """
            ).fetchone()[0]
        )
        invalid_status = int(
            con.execute(
                "SELECT COUNT(*) FROM trades WHERE status NOT IN ('OPEN', 'CLOSED')"
            ).fetchone()[0]
        )
        unknown_recovery = int(
            con.execute(
                """
                SELECT COUNT(*) FROM trades
                WHERE status='CLOSED'
                  AND (ts_exit IS NULL OR exit_px IS NULL OR realized_pnl IS NULL)
                  AND exit_reason IN ('stale_unrecoverable', 'phantom_stale')
                """
            ).fetchone()[0]
        )
        legacy_missing_exit_px = int(
            con.execute(
                """
                SELECT COUNT(*) FROM trades
                WHERE status='CLOSED'
                  AND exit_px IS NULL
                  AND realized_pnl IS NOT NULL
                  AND ts_exit IS NOT NULL
                  AND COALESCE(exit_reason, '') NOT IN (
                    'stale_unrecoverable', 'phantom_stale'
                  )
                """
            ).fetchone()[0]
        )
        bad_closed = int(
            con.execute(
                """
                SELECT COUNT(*) FROM trades
                WHERE status='CLOSED'
                  AND (ts_exit IS NULL OR realized_pnl IS NULL)
                  AND COALESCE(exit_reason, '') NOT IN (
                    'stale_unrecoverable', 'phantom_stale'
                  )
                """
            ).fetchone()[0]
        )
        has_events = bool(
            con.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='trade_events'"
            ).fetchone()
        )
    finally:
        con.close()

    failures = dup_open_keys + dup_decisions + invalid_status + bad_closed
    add_check(
        checks,
        "warehouse.integrity",
        "FAIL" if failures else "PASS",
        (
            "warehouse integrity issues detected"
            if failures
            else "warehouse trade keys and lifecycle fields are sane"
        ),
        duplicate_open_keys=dup_open_keys,
        duplicate_decision_ids=dup_decisions,
        invalid_status_rows=invalid_status,
        bad_closed_rows=bad_closed,
        unknown_recovery_closed_rows=unknown_recovery,
        legacy_missing_exit_px_rows=legacy_missing_exit_px,
        trade_events_table=has_events,
    )


def audit_paper_live_parity(checks: list[dict[str, Any]]) -> None:
    sim_path = ROOT / "core" / "sim_execution.py"
    test_path = ROOT / "tests" / "test_sim_live_parity.py"
    db_path = ROOT / "data" / "warehouse.sqlite"
    if not sim_path.exists():
        add_check(checks, "execution.paper_live_parity", "FAIL", "sim execution model missing")
        return

    recent_paper = 0
    cost_rows = 0
    if db_path.exists():
        con = sqlite3.connect(db_path)
        try:
            rows = con.execute(
                """
                SELECT fee, slippage FROM trades
                WHERE mode='PAPER' AND status='CLOSED'
                ORDER BY ts_exit DESC LIMIT 100
                """
            ).fetchall()
            recent_paper = len(rows)
            cost_rows = sum(1 for fee, slippage in rows if fee is not None or slippage is not None)
        finally:
            con.close()

    ok = test_path.exists() and (recent_paper == 0 or cost_rows == recent_paper)
    add_check(
        checks,
        "execution.paper_live_parity",
        "PASS" if ok else "WARN",
        (
            "paper/live pessimistic execution parity is covered"
            if ok
            else "paper/live parity coverage or cost fields need review"
        ),
        sim_execution_model=sim_path.exists(),
        parity_tests=test_path.exists(),
        recent_paper_rows=recent_paper,
        recent_paper_rows_with_cost_fields=cost_rows,
    )


def audit_strategy_readiness(checks: list[dict[str, Any]]) -> None:
    try:
        from core.strategy_readiness import evaluate_warehouse
    except Exception as exc:
        add_check(checks, "strategy.promotion_gate", "FAIL", f"cannot import gate: {exc}")
        return
    report = evaluate_warehouse(ROOT / "data" / "warehouse.sqlite")
    summary = report.get("summary", {})
    ready = bool(report.get("ready"))
    reasons = report.get("reasons") or []
    add_check(
        checks,
        "strategy.promotion_gate",
        "PASS" if ready else "FAIL",
        (
            "strategy clears strict promotion-readiness gate"
            if ready
            else "strict promotion-readiness gate blocks live promotion"
        ),
        verdict=report.get("verdict"),
        reasons=reasons[:8],
        summary=summary,
        positive_folds=report.get("positive_folds"),
        by_strategy=report.get("by_strategy"),
    )


def audit_confluence_paper(checks: list[dict[str, Any]]) -> None:
    path = ROOT / "reports" / "confluence_paper_trades.csv"
    if not path.exists():
        add_check(checks, "confluence.paper_log", "INFO", "no confluence paper CSV")
        return

    rows = list(csv.DictReader(path.open(newline="", encoding="utf-8")))
    key_fields = ("opened", "closed", "symbol", "side", "entry", "exit", "outcome")
    counts = Counter(tuple(row.get(k, "") for k in key_fields) for row in rows)
    duplicate_extra = sum(c - 1 for c in counts.values() if c > 1)
    pnl = 0.0
    wins = 0
    for row in rows:
        try:
            v = float(row.get("pnl_usd", 0.0) or 0.0)
        except ValueError:
            v = 0.0
        pnl += v
        wins += int(v > 0)
    add_check(
        checks,
        "confluence.paper_log",
        "FAIL" if duplicate_extra else "PASS",
        (
            "duplicate close rows detected; confluence paper metrics are contaminated"
            if duplicate_extra
            else "confluence paper CSV has no exact duplicate closes"
        ),
        rows=len(rows),
        duplicate_rows_extra=duplicate_extra,
        pnl=round(pnl, 2),
        win_rate=round(wins / len(rows), 4) if rows else 0.0,
    )


def audit_learning_report(checks: list[dict[str, Any]]) -> None:
    path = ROOT / "data" / "learning_report.json"
    if not path.exists():
        add_check(checks, "learning.report", "WARN", "learning_report.json is missing")
        return
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        add_check(checks, "learning.report", "FAIL", f"cannot parse learning report: {exc}")
        return
    pnl = float(payload.get("total_net_pnl", 0.0) or 0.0)
    add_check(
        checks,
        "learning.report",
        "PASS" if pnl > 0 else "FAIL",
        (
            "latest learning report is profitable"
            if pnl > 0
            else "latest learning report is negative; do not promote live"
        ),
        total_trades=payload.get("total_trades"),
        total_net_pnl=round(pnl, 4),
        win_rate=payload.get("overall_win_rate"),
        suggestions=payload.get("suggestions", [])[:5],
    )


def audit_model_pointers(checks: list[dict[str, Any]]) -> None:
    try:
        import config
        from core.promotion_gate import validate_model_pointer
    except Exception as exc:
        add_check(checks, "model.pointer", "WARN", f"model pointer check skipped: {exc}")
        return

    if not getattr(config, "MODEL_GATE", {}).get("enabled", False):
        add_check(checks, "model.pointer", "INFO", "MODEL_GATE disabled")
        return

    for market in ("futures", "spot"):
        path = ROOT / "data" / "models" / f"ensemble_{market}_latest.json"
        if not path.exists():
            add_check(
                checks,
                f"model.pointer.{market}",
                "WARN",
                f"latest model pointer missing for {market}",
            )
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            ok, reason, diag = validate_model_pointer(payload, market_type=market)
        except Exception as exc:
            add_check(
                checks,
                f"model.pointer.{market}",
                "FAIL",
                f"latest model pointer cannot be validated: {exc}",
            )
            continue
        add_check(
            checks,
            f"model.pointer.{market}",
            "PASS" if ok else "FAIL",
            "latest model pointer is valid" if ok else f"latest model pointer rejected: {reason}",
            diag=diag,
        )


def audit_retrain_automation(checks: list[dict[str, Any]]) -> None:
    wrapper = ROOT / "scripts" / "retrain_if_stale.py"
    weekly = ROOT / "scripts" / "retrain_weekly.ps1"
    ok = wrapper.exists() and weekly.exists()
    add_check(
        checks,
        "automation.retrain",
        "PASS" if ok else "WARN",
        (
            "stale-model retrain automation scripts are present"
            if ok
            else "stale-model retrain automation is incomplete"
        ),
        retrain_if_stale=wrapper.exists(),
        retrain_weekly=weekly.exists(),
    )


def write_reports(report: dict[str, Any], out_dir: Path) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = _utc_now().strftime("%Y%m%d_%H%M%S")
    json_path = out_dir / f"trading_system_audit_{stamp}.json"
    md_path = out_dir / f"trading_system_audit_{stamp}.md"
    json_path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")

    lines = [
        "# Trading System Audit",
        "",
        f"Generated: {report['generated_at']}",
        f"Verdict: **{report['verdict']}**",
        "",
        "| Status | Check | Detail |",
        "| --- | --- | --- |",
    ]
    for check in report["checks"]:
        detail = str(check["detail"]).replace("|", "\\|")
        lines.append(f"| {check['status']} | {check['name']} | {detail} |")
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return json_path, md_path


def run_audit() -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    audit_config(checks)
    audit_env_file(checks)
    audit_secret_leak_scan(checks)
    audit_processes(checks)
    audit_forward_feeds(checks)
    audit_heartbeat(checks)
    audit_warehouse(checks)
    audit_loss_forensics(checks)
    audit_warehouse_integrity(checks)
    audit_paper_live_parity(checks)
    audit_strategy_readiness(checks)
    audit_confluence_paper(checks)
    audit_learning_report(checks)
    audit_model_pointers(checks)
    audit_retrain_automation(checks)

    worst = max((_status_rank(c["status"]) for c in checks), default=0)
    if worst >= _status_rank("FAIL"):
        verdict = "NOT_READY"
    elif worst >= _status_rank("WARN"):
        verdict = "PAPER_ONLY_REVIEW"
    else:
        verdict = "PAPER_OK"
    return {
        "generated_at": _utc_now().isoformat(),
        "verdict": verdict,
        "checks": checks,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit trading system readiness")
    parser.add_argument("--output-dir", default="reports")
    parser.add_argument("--fail-on-critical", action="store_true")
    args = parser.parse_args()

    report = run_audit()
    json_path, md_path = write_reports(report, ROOT / args.output_dir)
    print(f"verdict={report['verdict']}")
    print(f"json={json_path}")
    print(f"md={md_path}")
    for check in report["checks"]:
        print(f"{check['status']:4} {check['name']}: {check['detail']}")
    if args.fail_on_critical and report["verdict"] == "NOT_READY":
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
