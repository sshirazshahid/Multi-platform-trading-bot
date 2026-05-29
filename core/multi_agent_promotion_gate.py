"""Phase B promotion gate for the multi-agent shadow build.

Reads warehouse.shadow_decisions (Phase A schema) and warehouse.trades
(live), computes side-by-side stats, applies criteria.

Verdicts:
  - PROMOTE: all criteria pass for the 7-day rolling window
  - HOLD: at least one criterion fails
  - REJECT: catastrophic shadow degradation (auto-rollback territory)
"""
from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import asdict, dataclass
from enum import Enum
from pathlib import Path
from typing import Optional


class MAVerdict(str, Enum):
    PROMOTE = "PROMOTE"
    HOLD = "HOLD"
    REJECT = "REJECT"


@dataclass
class MACriteria:
    min_decisions: int = 100
    days_window: int = 7
    wr_floor: float = 0.40
    fee_burn_max_x: float = 1.0  # tighter than KillCriteria's 2x
    sharpe_lb_must_exceed_live_ub: bool = True


@dataclass
class MAEvaluation:
    verdict: MAVerdict
    reason: str
    shadow_n: int
    shadow_wr: float
    shadow_sum_pnl: float
    shadow_sharpe: float
    live_n: int
    live_wr: float
    live_sum_pnl: float
    live_sharpe: float
    shadow_fee_per_trade: float
    live_fee_per_trade: float


def _connect(db: Path) -> sqlite3.Connection:
    con = sqlite3.connect(str(db))
    con.row_factory = sqlite3.Row
    return con


def _stats(rows: list, pnl_key: str, fee_key: Optional[str] = None) -> dict:
    n = len(rows)
    if n == 0:
        return {"n": 0, "wr": 0.0, "sum": 0.0, "sharpe": 0.0, "fee_per_trade": 0.0}
    pnls = [r[pnl_key] or 0.0 for r in rows]
    wins = sum(1 for x in pnls if x > 0)
    avg = sum(pnls) / n
    var = sum((x - avg) ** 2 for x in pnls) / max(1, n - 1)
    sd = var ** 0.5
    sharpe = avg / sd if sd > 0 else 0.0
    fee = 0.0
    if fee_key:
        fees = [r[fee_key] or 0.0 for r in rows]
        fee = sum(fees) / n if fees else 0.0
    return {"n": n, "wr": wins / n, "sum": sum(pnls), "sharpe": sharpe,
            "fee_per_trade": fee}


def evaluate_multi_agent_shadow(
    db_path: Path,
    criteria: Optional[MACriteria] = None,
    *,
    log_path: Optional[Path] = None,
    now_ts: Optional[int] = None,
) -> MAEvaluation:
    """Read shadow + live, return verdict."""
    cri = criteria or MACriteria()
    now = now_ts if now_ts is not None else int(time.time())
    cutoff = now - cri.days_window * 86400

    con = _connect(db_path)
    shadow_rows = list(con.execute(
        "SELECT * FROM shadow_decisions WHERE ts >= ? AND decision='ALLOW'",
        (cutoff,),
    ).fetchall())
    live_rows = list(con.execute(
        "SELECT * FROM trades WHERE ts_exit >= ? AND status='CLOSED'",
        (cutoff,),
    ).fetchall())
    con.close()

    s = _stats(shadow_rows, "sim_pnl", "projected_fee")
    l = _stats(live_rows, "realized_pnl", "fee")

    fail_reasons: list[str] = []
    if s["n"] < cri.min_decisions:
        fail_reasons.append(f"insufficient_shadow_decisions:{s['n']}<{cri.min_decisions}")
    else:
        if s["wr"] < cri.wr_floor:
            fail_reasons.append(f"shadow_wr_low:{s['wr']:.2f}<{cri.wr_floor}")
        if cri.sharpe_lb_must_exceed_live_ub and s["sharpe"] <= l["sharpe"]:
            fail_reasons.append(f"shadow_sharpe_not_better:{s['sharpe']:.3f}<={l['sharpe']:.3f}")
        if l["fee_per_trade"] > 0 and \
                s["fee_per_trade"] > cri.fee_burn_max_x * l["fee_per_trade"]:
            fail_reasons.append(
                f"shadow_fee_too_high:{s['fee_per_trade']:.4f}>{cri.fee_burn_max_x}x{l['fee_per_trade']:.4f}")

    # REJECT: shadow strictly catastrophic
    if s["n"] >= cri.min_decisions and (s["sharpe"] < -0.5 or s["wr"] < 0.20):
        verdict = MAVerdict.REJECT
        reason = f"shadow_catastrophic: sharpe={s['sharpe']:.3f} wr={s['wr']:.2f}"
    elif fail_reasons:
        verdict = MAVerdict.HOLD
        reason = "; ".join(fail_reasons)
    else:
        verdict = MAVerdict.PROMOTE
        reason = "all_criteria_passed"

    ev = MAEvaluation(
        verdict=verdict, reason=reason,
        shadow_n=s["n"], shadow_wr=s["wr"], shadow_sum_pnl=s["sum"],
        shadow_sharpe=s["sharpe"],
        live_n=l["n"], live_wr=l["wr"], live_sum_pnl=l["sum"],
        live_sharpe=l["sharpe"],
        shadow_fee_per_trade=s["fee_per_trade"],
        live_fee_per_trade=l["fee_per_trade"],
    )

    if log_path is not None:
        Path(log_path).parent.mkdir(parents=True, exist_ok=True)
        with open(log_path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"ts": now, **asdict(ev)}) + "\n")

    return ev


def write_split_flag(percent_to_shadow: float,
                     path: str | Path = "data/ab_split.json") -> None:
    """A/B controller flag — read by core/bot_engine to route a fraction
    of candidates to shadow. Kept disabled until Phase C cutover."""
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps({
        "pct_to_shadow": max(0.0, min(1.0, percent_to_shadow)),
        "set_at": int(time.time()),
    }, indent=2), encoding="utf-8")
