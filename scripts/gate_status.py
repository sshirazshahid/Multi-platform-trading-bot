"""Promotion-readiness status reporter — REPORTING ONLY, never a gate authority.

Modeled on the Codex snapshot's gate_status.py output pattern (per-criterion
``[PASS|FAIL] scope.name: actual (requirement)`` lines, informational readiness
score, --json, exit code 2 while anything is blocked), pointed at THIS bot's
evidence stores: the warehouse shadow tables (shadow_decisions joined to
resolver-written shadow_outcomes) and the deep_breakout PAPER-lane trades.

Reported per shadow probe arm (listing_short, unlock_short W1/W2, tsmom 1h/4h,
breakout_60d) and for the deep_breakout PAPER lane:
  * resolved-event count vs the >=30 evaluation floor (imported from
    scripts/report_goal_progress.py — single owner of that number);
  * where computable, the frozen core/promotion_gate.py metrics
    (DSR / PBO / OOS-WR / AUC) against the IMPORTED thresholds — nothing is
    re-declared here, so this script can never drift from the real gate;
  * owner sign-off: PENDING — always; this reporter cannot grant it.

For the deep_breakout lane the Codex forward-gate progress markers (trades
n/60, PF vs 1.15, maxDD vs 12%, days vs 90) are reported PURELY
INFORMATIONALLY, plus honesty diagnostics (also informational, not gates):
  (a) expectancy-drift vs the frozen research baseline, flagged outside +/-30%;
  (b) outlier-removed profit factor (top max(1, ceil(5% n)) winners dropped),
      flagged when < 1.0;
  (c) per arm/lane, the 5th-percentile expectancy from a moving-block
      bootstrap (block ~10, 2000 resamples, RNG seeded from the data length
      so output is deterministic), flagged when < 0.

Read-only: opens the warehouse with sqlite mode=ro; degrades per-table when
schema pieces are missing (fresh installs report zeros, never crash).

Run: python scripts/gate_status.py [--db PATH] [--json]
Exit code: 0 all gate checks pass, 2 while anything is blocked.
"""

from __future__ import annotations

import argparse
import json
import math
import random
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import numpy as np  # noqa: E402

# Per-arm model_version strings — IMPORTED from the probe agents that write
# them, so a probe rename can never silently desync this reporter.
from core.agents.breakout_probe_agent import BREAKOUT_60D_MODEL_VERSION  # noqa: E402
from core.agents.listing_short_probe_agent import LISTING_SHORT_MODEL_VERSION  # noqa: E402
from core.agents.tsmom_probe_agent import (  # noqa: E402
    TSMOM_1H_MODEL_VERSION,
    TSMOM_4H_MODEL_VERSION,
)
from core.agents.unlock_short_probe_agent import (  # noqa: E402
    UNLOCK_W1_MODEL_VERSION,
    UNLOCK_W2_MODEL_VERSION,
)

# Frozen gate thresholds — IMPORTED, never re-declared (drift protection).
from core.promotion_gate import (  # noqa: E402
    MAX_PBO,
    MIN_AUC,
    MIN_DSR,
    MIN_OOS_WR,
    _kurt,
    _skew,
)
from core.stat_tests import deflated_sharpe, sharpe  # noqa: E402

# The >=30 resolved-events evaluation floor is owned by
# scripts/report_goal_progress.py (auditor minimum, 2026-07-09). Imported so
# the two reporters can never drift apart.
from scripts.report_goal_progress import RESOLVED_FLOOR  # noqa: E402

# ── Frozen research baseline (honesty diagnostic a) ─────────────────────────
# Provenance: Codex/reports/deep_futures_runs.csv — the 10 `full_history` rows
# for strategy=breakout_60d (BTC ETH SOL XRP BNB ADA DOGE LINK AVAX DOT,
# backtests 2020-08-02 .. 2026-07-11), each converted per the Codex reference
# (Codex/bot/gates/forward.py::_research_expectancy_r):
#     per_trade_equity_return = (1 + return_pct/100) ** (1/trades) - 1
#     expectancy_R            = per_trade_equity_return / research_risk_pct
# with the Codex-frozen research_risk_pct = 0.01; baseline = MEDIAN of the 10.
# Computed 2026-07-12 and FROZEN here — do not recompute from a mutated CSV;
# the drift diagnostic compares live lane trades against THIS number.
BREAKOUT_60D_RESEARCH_EXPECTANCY_R = 0.0944360889

# Codex GateConfig.max_expectancy_deviation_pct — informational flag band only.
EXPECTANCY_DRIFT_TOLERANCE = 0.30

# Codex forward-gate progress markers for the deep_breakout PAPER lane —
# PURELY INFORMATIONAL (port review 2026-07-12 scoped these as progress
# markers; Codex's own full gate was stricter: 100 trades / PF 1.25 /
# maxDD 15% / 270 days, see Codex/bot/config.py GateConfig). These lines never
# gate anything; promotion authority stays with core/promotion_gate.py + owner.
DEEP_BREAKOUT_FORWARD_TARGETS = {
    "trades": 60,
    "profit_factor": 1.15,
    "max_drawdown": 0.12,
    "days": 90,
}

# Shadow probe arms: exact shadow_decisions.model_version per arm (unlock
# '-sl8' counterfactual rows are deliberately EXCLUDED — they are SL-frozen
# diagnostics, not the promoted arm) + the probe table carrying the frozen
# pre-outcome discriminating score (for AUC).
ARMS = (
    {
        "name": "listing_short",
        "model_version": LISTING_SHORT_MODEL_VERSION,
        "probe_table": "shadow_listing_probe",
    },
    {
        "name": "unlock_short_w1",
        "model_version": UNLOCK_W1_MODEL_VERSION,
        "probe_table": "shadow_unlock_probe",
    },
    {
        "name": "unlock_short_w2",
        "model_version": UNLOCK_W2_MODEL_VERSION,
        "probe_table": "shadow_unlock_probe",
    },
    {
        "name": "tsmom_1h",
        "model_version": TSMOM_1H_MODEL_VERSION,
        "probe_table": "shadow_tsmom_probe",
    },
    {
        "name": "tsmom_4h",
        "model_version": TSMOM_4H_MODEL_VERSION,
        "probe_table": "shadow_tsmom_probe",
    },
    {
        "name": "breakout_60d",
        "model_version": BREAKOUT_60D_MODEL_VERSION,
        "probe_table": "shadow_breakout_probe",
    },
)


# Moving-block bootstrap (honesty diagnostic c, Codex statistics.py port
# 2026-07-12) — block size pinned ~10 per the port review, 2000 resamples.
BOOTSTRAP_BLOCK_SIZE = 10
BOOTSTRAP_RESAMPLES = 2000
BOOTSTRAP_PCT = 0.05


# ── pure metric helpers ──────────────────────────────────────────────────────
def bootstrap_expectancy_lb(returns, *, block_size=BOOTSTRAP_BLOCK_SIZE,
                            simulations=BOOTSTRAP_RESAMPLES,
                            pct=BOOTSTRAP_PCT):
    """5th-percentile expectancy from a moving-block circular bootstrap
    (Codex statistics.py::moving_block_bootstrap_lower_mean semantics with
    the block size pinned to ~10). Deterministic: the RNG is seeded from the
    data LENGTH — same resolved set, same answer; no wall-clock randomness
    anywhere in the output. INFORMATIONAL ONLY — never a gate input.
    Returns None below 2 finite observations."""
    clean = [float(v) for v in returns if math.isfinite(float(v))]
    n = len(clean)
    if n < 2 or simulations < 1 or not 0.0 < pct < 0.5:
        return None
    block = max(1, min(int(block_size), n))
    blocks_needed = math.ceil(n / block)
    rng = random.Random(n)  # length-seeded — deterministic per resolved count
    means = []
    for _ in range(int(simulations)):
        sample = []
        for _ in range(blocks_needed):
            start = rng.randrange(n)
            sample.extend(clean[(start + off) % n] for off in range(block))
        means.append(sum(sample[:n]) / n)
    means.sort()
    idx = max(0, min(len(means) - 1, math.floor(pct * len(means))))
    return means[idx]


def profit_factor(returns) -> float:
    """Sum(gains)/|sum(losses)|; inf when loss-free, 0.0 when gain-free."""
    gains = sum(v for v in returns if v > 0)
    losses = abs(sum(v for v in returns if v < 0))
    if losses == 0:
        return math.inf if gains > 0 else 0.0
    return gains / losses


def outlier_removed_profit_factor(returns) -> float:
    """PF after dropping the top max(1, ceil(5% n)) winners (Codex
    forward.py::_outlier_removed_profit_factor semantics). 0.0 when there are
    no winners at all. Informational: is the book carried by a few outliers?"""
    returns = list(returns)
    pos_idx = [i for i, v in enumerate(returns) if v > 0]
    if not pos_idx:
        return 0.0
    remove = max(1, math.ceil(len(returns) * 0.05))
    largest = sorted(pos_idx, key=lambda i: returns[i], reverse=True)[:remove]
    removed = set(largest)
    return profit_factor([v for i, v in enumerate(returns) if i not in removed])


def max_drawdown(equity_returns) -> float:
    """Max peak-to-trough drawdown of a compounded equity curve (Codex
    forward.py::_max_drawdown semantics)."""
    equity = peak = 1.0
    dd = 0.0
    for v in equity_returns:
        equity *= max(0.0, 1.0 + v)
        peak = max(peak, equity)
        dd = max(dd, 1.0 - equity / peak if peak else 1.0)
    return dd


def rank_auc(scores, labels):
    """Mann-Whitney rank AUC (ties half-credit). None when only one class is
    present — AUC is then undefined."""
    pairs = list(zip(scores, labels))
    pos = [s for s, y in pairs if y]
    neg = [s for s, y in pairs if not y]
    if not pos or not neg:
        return None
    wins = 0.0
    for p in pos:
        for q in neg:
            if p > q:
                wins += 1.0
            elif p == q:
                wins += 0.5
    return wins / (len(pos) * len(neg))


def expectancy_drift(r_multiples):
    """Signed relative deviation of realized mean R vs the frozen research
    baseline: realized/baseline - 1. None when there are no resolved trades."""
    r = [float(v) for v in r_multiples]
    if not r:
        return None
    return float(np.mean(r)) / BREAKOUT_60D_RESEARCH_EXPECTANCY_R - 1.0


def _dsr(returns):
    """Pr[true SR > 0] via the shared Deflated Sharpe (same sr_var=1/n
    convention as core/promotion_gate.py). None below 2 observations."""
    r = np.asarray([float(v) for v in returns], dtype=float)
    n = int(r.size)
    if n < 2:
        return None
    sk = float(_skew(r)) if n >= 4 else 0.0
    ku = float(_kurt(r)) if n >= 4 else 3.0
    return float(
        deflated_sharpe(
            sr_observed=sharpe(r), n_trials=1, n_obs=n, skew=sk, kurt=ku, sr_var=1.0 / n
        )
    )


# ── warehouse access (read-only) ─────────────────────────────────────────────
def _ro_conn(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{Path(db_path).as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _arm_rows(conn, model_version: str, probe_table: str) -> list:
    """Resolved (net_pnl, r_multiple, score) rows for one arm. Score comes
    from the arm's probe table (frozen pre-outcome); when that table is
    missing the query degrades to score=NULL (AUC then reports n/a)."""
    base = (
        "SELECT o.net_pnl AS net_pnl, o.r_multiple AS r_multiple{sel} "
        "FROM shadow_decisions d "
        "JOIN shadow_outcomes o ON o.proposal_id = d.proposal_id "
        "{join}"
        "WHERE d.model_version = ? AND o.label_status = 'RESOLVED'"
    )
    try:
        q = base.format(
            sel=", p.score AS score",
            # probe_table is a frozen literal from ARMS, never user input
            join=f"LEFT JOIN {probe_table} p ON p.proposal_id = d.proposal_id ",
        )
        return [dict(r) for r in conn.execute(q, (model_version,))]
    except sqlite3.Error:
        pass
    try:
        q = base.format(sel=", NULL AS score", join="")
        return [dict(r) for r in conn.execute(q, (model_version,))]
    except sqlite3.Error:
        return []


def _deep_breakout_rows(conn) -> list:
    """Closed deep_breakout PAPER-lane trades, chronological by exit."""
    try:
        rows = conn.execute(
            "SELECT ts_entry, ts_exit, r_multiple, "
            "       realized_pnl + COALESCE(partial_realized_pnl, 0) AS net_pnl "
            "FROM trades WHERE strategy_family = 'deep_breakout' "
            "AND ts_exit IS NOT NULL ORDER BY ts_exit"
        ).fetchall()
        return [dict(r) for r in rows]
    except sqlite3.Error:
        return []


def _lane_risk_frac() -> float:
    """The lane's researched per-trade risk fraction (for the informational
    maxDD equity curve). Read from config when importable; 1% otherwise."""
    try:
        import config

        return float(config.DEEP_BREAKOUT_LANE.get("risk_pct", 1.0)) / 100.0
    except Exception:
        return 0.01


# ── check construction ───────────────────────────────────────────────────────
def _check(scope, name, passed, actual, requirement, info=False, flagged=False):
    return {
        "scope": scope,
        "name": name,
        "passed": passed,
        "actual": actual,
        "requirement": requirement,
        "info": info,
        "flagged": flagged,
    }


def _returns_of(rows) -> list:
    """Per-event return series: the complete r_multiple array when every row
    carries one (probes with barriers), else the net_pnl array (fixed-stake
    probes — Sharpe/DSR are scale-invariant per consistent array)."""
    rs = [r.get("r_multiple") for r in rows]
    if rows and all(v is not None for v in rs):
        return [float(v) for v in rs]
    return [float(r["net_pnl"]) for r in rows if r.get("net_pnl") is not None]


def _gate_checks(scope: str, rows: list) -> list:
    """The frozen-gate readout for one arm/lane: resolved floor + the four
    core/promotion_gate.py metrics against IMPORTED thresholds. Uncomputable
    metrics report actual=None and FAIL (fail-closed) — the real gate run,
    not this reporter, is the promotion authority either way."""
    n = len(rows)
    checks = [
        _check(
            scope,
            "resolved_events",
            n >= RESOLVED_FLOOR,
            n,
            f">= {RESOLVED_FLOOR} resolved forward events",
        )
    ]

    wins = sum(1 for r in rows if float(r.get("net_pnl") or 0.0) > 0.0)
    wr = (wins / n) if n else None
    checks.append(
        _check(
            scope, "oos_wr", bool(wr is not None and wr >= MIN_OOS_WR), wr, f">= {MIN_OOS_WR:.2f}"
        )
    )

    rets = _returns_of(rows)
    dsr = _dsr(rets) if rets else None
    checks.append(
        _check(
            scope,
            "dsr",
            bool(dsr is not None and dsr >= MIN_DSR),
            dsr,
            f">= {MIN_DSR:.2f} Pr[true SR > 0]",
        )
    )

    # PBO needs a multi-strategy returns matrix (see core/promotion_gate.py
    # PBO note) — never computable from one arm's outcomes alone.
    checks.append(
        _check(
            scope,
            "pbo",
            False,
            None,
            f"<= {MAX_PBO:.2f}; not computable from one arm's "
            f"outcomes — full frozen-gate run required",
        )
    )

    scored = [
        (float(r["score"]), float(r.get("net_pnl") or 0.0) > 0.0)
        for r in rows
        if r.get("score") is not None
    ]
    auc = rank_auc([s for s, _ in scored], [y for _, y in scored]) if scored else None
    checks.append(
        _check(scope, "auc", bool(auc is not None and auc >= MIN_AUC), auc, f">= {MIN_AUC:.2f}")
    )

    # (c) moving-block bootstrap expectancy lower bound — INFORMATIONAL line
    # per arm/lane (never a gate; core/promotion_gate.py imports nothing here).
    lb = bootstrap_expectancy_lb(rets) if rets else None
    checks.append(
        _check(
            scope,
            "bootstrap_expectancy_lb_p05",
            None,
            lb,
            f"5th-pct moving-block bootstrap mean (block={BOOTSTRAP_BLOCK_SIZE}, "
            f"{BOOTSTRAP_RESAMPLES} resamples, length-seeded) — informational, "
            f"flagged when < 0",
            info=True,
            flagged=bool(lb is not None and lb < 0.0),
        )
    )
    return checks


def _deep_breakout_info(conn, rows: list, now: float) -> list:
    """Codex forward-gate progress + the two honesty diagnostics. ALL lines
    here are informational (info=True) — they never gate or block."""
    t = DEEP_BREAKOUT_FORWARD_TARGETS
    scope = "deep_breakout"
    rets = [float(r["r_multiple"]) for r in rows if r.get("r_multiple") is not None]
    checks = [
        _check(
            scope,
            "forward_trades",
            None,
            len(rows),
            f"target >= {t['trades']} closed trades (Codex forward gate — informational)",
            info=True,
        ),
        _check(
            scope,
            "forward_profit_factor",
            None,
            profit_factor(rets) if rets else None,
            f"target >= {t['profit_factor']:.2f} (informational)",
            info=True,
        ),
        _check(
            scope,
            "forward_max_drawdown",
            None,
            max_drawdown([v * _lane_risk_frac() for v in rets]) if rets else None,
            f"target <= {t['max_drawdown']:.0%} of lane equity (informational)",
            info=True,
        ),
    ]
    days = None
    if conn is not None:
        try:
            first = conn.execute(
                "SELECT MIN(ts_entry) AS m FROM trades WHERE strategy_family = 'deep_breakout'"
            ).fetchone()["m"]
            if first is not None:
                days = (now - float(first)) / 86400.0
        except sqlite3.Error:
            days = None
    checks.append(
        _check(
            scope,
            "forward_days_elapsed",
            None,
            days,
            f"target >= {t['days']} days (informational)",
            info=True,
        )
    )

    # (a) expectancy-drift vs the frozen research baseline
    drift = expectancy_drift(rets)
    checks.append(
        _check(
            scope,
            "expectancy_drift",
            None,
            drift,
            f"within +/-{EXPECTANCY_DRIFT_TOLERANCE:.0%} of frozen research "
            f"baseline {BREAKOUT_60D_RESEARCH_EXPECTANCY_R:.4f} R/trade — "
            f"informational",
            info=True,
            flagged=bool(drift is not None and abs(drift) > EXPECTANCY_DRIFT_TOLERANCE),
        )
    )

    # (b) outlier-removed profit factor
    opf = outlier_removed_profit_factor(rets) if rets else None
    checks.append(
        _check(
            scope,
            "outlier_removed_pf",
            None,
            opf,
            ">= 1.00 after dropping top max(1, ceil(5% n)) winners — informational",
            info=True,
            flagged=bool(opf is not None and opf < 1.0),
        )
    )
    return checks


# ── report assembly ──────────────────────────────────────────────────────────
def build_report(db_path, now=None) -> dict:
    now = time.time() if now is None else float(now)
    db_path = Path(db_path)
    conn = None
    if db_path.exists():
        try:
            conn = _ro_conn(db_path)
        except sqlite3.Error:
            conn = None
    try:
        checks: list = []
        for arm in ARMS:
            rows = (
                _arm_rows(conn, arm["model_version"], arm["probe_table"])
                if conn is not None
                else []
            )
            checks.extend(_gate_checks(arm["name"], rows))
        lane_rows = _deep_breakout_rows(conn) if conn is not None else []
        checks.extend(_gate_checks("deep_breakout", lane_rows))
        checks.extend(_deep_breakout_info(conn, lane_rows, now))
    finally:
        if conn is not None:
            conn.close()

    checks.append(
        _check(
            "promotion",
            "owner_signoff",
            False,
            "PENDING",
            "explicit owner grant required — this reporter cannot grant it",
        )
    )

    gate_checks = [c for c in checks if not c["info"]]
    passed = sum(1 for c in gate_checks if c["passed"])
    return {
        "generated_utc": datetime.fromtimestamp(now, tz=timezone.utc).isoformat(timespec="seconds"),
        "db": str(db_path),
        "checks": checks,
        "score": round(10.0 * passed / len(gate_checks), 1) if gate_checks else 0.0,
        "blocked": any(not c["passed"] for c in gate_checks),
    }


def _fmt(v) -> str:
    if v is None:
        return "n/a"
    if isinstance(v, float):
        return "inf" if math.isinf(v) else f"{v:.4f}"
    return str(v)


def render_text(report: dict) -> str:
    lines = [
        f"Promotion-readiness status (REPORTING ONLY) — {report['generated_utc']}",
        f"warehouse: {report['db']}",
    ]
    for c in report["checks"]:
        if c["info"]:
            marker = "FLAG" if c["flagged"] else "INFO"
        else:
            marker = "PASS" if c["passed"] else "FAIL"
        lines.append(
            f"[{marker}] {c['scope']}.{c['name']}: {_fmt(c['actual'])} ({c['requirement']})"
        )
    lines.append(
        f"readiness score: {report['score']:.1f}/10 (informational — promotion "
        f"authority stays with core/promotion_gate.py + owner)"
    )
    lines.append(
        "owner sign-off: PENDING — required for any promotion; this reporter cannot grant it."
    )
    lines.append("status: BLOCKED" if report["blocked"] else "status: all gate checks pass")
    return "\n".join(lines)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Promotion-readiness status (reporting only; exit 2 while blocked)"
    )
    parser.add_argument(
        "--db",
        default=str(ROOT / "data" / "warehouse.sqlite"),
        help="warehouse path (opened read-only)",
    )
    parser.add_argument("--json", action="store_true", help="emit JSON")
    args = parser.parse_args(argv)

    report = build_report(args.db)
    if args.json:
        print(json.dumps(report, indent=2, default=str))
    else:
        print(render_text(report))
    return 2 if report["blocked"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
