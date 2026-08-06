"""Prereg 52 — Cost-aware AccBand admit filter screen (kappa x stressed RT).

Offline, read-only screen over data/warehouse.sqlite closed PAPER trades.
Frozen spec: _workspace/strategy_pipeline/52_prereg_cost_aware_accband_kappa.md
(sha256 8d94bd24583e5a2c93017d0d0eb5ac7b4a16d6d704437bb3c5d89f3f8fe6dae6).
NO live path: this module is never imported by the bot.

Run: venv/Scripts/python.exe research/screen_cost_aware_accband_kappa.py
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PREREG_MD = ROOT / "_workspace" / "strategy_pipeline" / "52_prereg_cost_aware_accband_kappa.md"
EXPECTED_SHA256 = "8d94bd24583e5a2c93017d0d0eb5ac7b4a16d6d704437bb3c5d89f3f8fe6dae6"

# Frozen constants (prereg sections 3-5)
C_STRESS = 0.00315
KAPPA_CELLS = (1.5, 2.0, 2.5, 3.0)
MIN_N_PER_CELL = 80
COHORT_FAMILIES = ("algo_det", "algo", "claude", "systematic_v3_1")
TP_FRAC = {"buy": 0.45, "sell": 0.35}
WR_BAND = (0.59, 0.67)


def planned_tp_pct(row: dict) -> float | None:
    """Planned TP fraction per prereg section 4.

    The warehouse trades table has no target_px column, so the first
    preference branch is vacuous; derivation uses entry_stop_px geometry.
    Returns None when the row must be excluded (no geometry).
    """
    entry = row.get("entry_px")
    target = row.get("target_px")
    if entry and target and entry > 0 and target > 0:
        return abs(target - entry) / entry
    stop = row.get("entry_stop_px")
    if not entry or not stop or entry <= 0 or stop <= 0:
        return None
    sl_pct = abs(entry - stop) / entry
    frac = TP_FRAC.get((row.get("side") or "").lower(), 0.50)
    return sl_pct * frac


def admit(tp_pct: float, kappa: float, c_stress: float = C_STRESS) -> bool:
    return tp_pct >= kappa * c_stress


def cell_metrics(rows: list[dict]) -> dict:
    n = len(rows)
    if n == 0:
        return {
            "n": 0,
            "win_rate": None,
            "mean_realized_pnl": None,
            "mean_r_multiple": None,
            "profit_factor": None,
        }
    pnls = [r["realized_pnl"] for r in rows]
    rs = [r["r_multiple"] for r in rows]
    wins = sum(1 for p in pnls if p > 0)
    gross_win = sum(p for p in pnls if p > 0)
    gross_loss = abs(sum(p for p in pnls if p < 0))
    return {
        "n": n,
        "win_rate": wins / n,
        "mean_realized_pnl": sum(pnls) / n,
        "mean_r_multiple": sum(rs) / n,
        "profit_factor": (gross_win / gross_loss) if gross_loss > 0 else None,
    }


def _gates(metrics: dict, baseline_mean_r: float) -> dict:
    """Joint GO gates (prereg section 5) for one treatment cell."""
    pf = metrics["profit_factor"]
    wr = metrics["win_rate"]
    delta_ev = metrics["mean_r_multiple"] - baseline_mean_r
    checks = {
        "mean_realized_pnl_gt_0": metrics["mean_realized_pnl"] > 0,
        "mean_r_multiple_gt_0": metrics["mean_r_multiple"] > 0,
        "profit_factor_gt_1": pf is not None and pf > 1.0,
        "win_rate_in_band": WR_BAND[0] <= wr <= WR_BAND[1],
        "delta_ev_gt_0": delta_ev > 0,
    }
    return {"checks": checks, "delta_ev": delta_ev, "all_pass": all(checks.values())}


def decide_verdict(cells: dict) -> str:
    """Verdict per prereg section 6. cells: kappa -> {n, all_gates_pass, delta_ev}."""
    if all(c["n"] < MIN_N_PER_CELL for c in cells.values()):
        return "INSUFFICIENT_DATA"
    ordered = sorted(cells)
    for i, k in enumerate(ordered):
        cell = cells[k]
        if not (cell["n"] >= MIN_N_PER_CELL and cell["all_gates_pass"]):
            continue
        neighbors = [ordered[j] for j in (i - 1, i + 1) if 0 <= j < len(ordered)]
        if any(cells[nk]["delta_ev"] is not None and cells[nk]["delta_ev"] > 0 for nk in neighbors):
            return "GO"
    return "NO_GO"


def run_screen(db_path: str) -> dict:
    prereg_sha = hashlib.sha256(PREREG_MD.read_bytes()).hexdigest() if PREREG_MD.exists() else None
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    placeholders = ",".join("?" for _ in COHORT_FAMILIES)
    raw = [
        dict(r)
        for r in con.execute(
            f"""SELECT side, strategy_family, entry_px, entry_stop_px, realized_pnl,
                       r_multiple, ts_exit
                FROM trades
                WHERE mode='PAPER' AND status='CLOSED'
                  AND strategy_family IN ({placeholders})""",
            COHORT_FAMILIES,
        )
    ]
    con.close()

    eligible, excluded_geometry, excluded_metrics = [], 0, 0
    for row in raw:
        tp = planned_tp_pct(row)
        if tp is None:
            excluded_geometry += 1
            continue
        if row["realized_pnl"] is None or row["r_multiple"] is None:
            excluded_metrics += 1
            continue
        row["planned_tp_pct"] = tp
        eligible.append(row)

    baseline = cell_metrics(eligible)
    cells_out, verdict_cells = {}, {}
    for kappa in KAPPA_CELLS:
        treated = [r for r in eligible if admit(r["planned_tp_pct"], kappa)]
        metrics = cell_metrics(treated)
        insufficient = metrics["n"] < MIN_N_PER_CELL
        if metrics["n"] > 0:
            gates = _gates(metrics, baseline["mean_r_multiple"])
            if insufficient:
                gates["all_pass"] = False  # insufficient n can never GO
        else:
            gates = {"checks": None, "delta_ev": None, "all_pass": False}
        cells_out[kappa] = {**metrics, "insufficient": insufficient, "gates": gates}
        verdict_cells[kappa] = {
            "n": metrics["n"],
            "all_gates_pass": gates["all_pass"] and not insufficient,
            "delta_ev": gates["delta_ev"],
        }

    ts_vals = [r["ts_exit"] for r in eligible if r.get("ts_exit")]
    return {
        "prereg_id": "cost_aware_accband_kappa_2026-07-31",
        "expected_sha256": EXPECTED_SHA256,
        "prereg_sha256": prereg_sha,
        "c_stress": C_STRESS,
        "cohort_families": list(COHORT_FAMILIES),
        "rows_fetched": len(raw),
        "excluded_no_geometry": excluded_geometry,
        "excluded_null_metrics": excluded_metrics,
        "window": {"first_exit": min(ts_vals) if ts_vals else None,
                   "last_exit": max(ts_vals) if ts_vals else None},
        "baseline": baseline,
        "cells": cells_out,
        "verdict": decide_verdict(verdict_cells),
    }


def main() -> None:
    if not PREREG_MD.exists():
        raise SystemExit("prereg markdown missing — refusing to run (fail closed)")
    sha = hashlib.sha256(PREREG_MD.read_bytes()).hexdigest()
    if sha != EXPECTED_SHA256:
        raise SystemExit(f"prereg hash mismatch ({sha}) — refusing to run (fail closed)")
    result = run_screen(str(ROOT / "data" / "warehouse.sqlite"))
    result["run_utc"] = datetime.now(timezone.utc).isoformat()
    out = ROOT / "_workspace" / "strategy_pipeline" / "52_screen_cost_aware_accband_kappa.json"
    out.write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
