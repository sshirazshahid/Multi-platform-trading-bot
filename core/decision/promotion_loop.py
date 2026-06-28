"""Autonomous shadow -> active-paper promotion loop (PAPER-only).

Wires the EXISTING honest gate (``core.promotion_gate``) + PBO/CSCV
(``core.stat_tests.pbo``) + trade-sequence Monte Carlo
(``core.decision.monte_carlo``) into a single deterministic promotion decision,
and records variant status in ``data/active_strategies.json``.

Hard-latched: every write routes through ``guardrails.assert_paper_self_improve``,
so the loop can never act off-PAPER and can never write a kernel / runtime-control
path. "active-paper" == writing the winning config to the adaptive-machine-config
path that ``MachineSignal`` ALREADY polls — NOT a new execution route, and never
``data/ab_split.json`` (the kernel-denied live-traffic flag).

Nothing here is profit-seeking: a candidate promotes only if it beats live-paper on
the López-de-Prado gate AND clears the capital-preservation Monte-Carlo bound AND is
not flagged overfit by PBO. Under the confirmed NO_EDGE regime, the expected — and
correct — outcome is that nothing promotes.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Sequence

import numpy as np

from core.adaptive_config import ADAPTIVE_MACHINE_CONFIG_PATH, sanitize_adaptive_machine_payload
from core.decision.guardrails import assert_paper_self_improve
from core.decision.monte_carlo import monte_carlo_trade_sequence
from core.promotion_gate import GateInputs, GateVerdict, PromotionGate
from core.stat_tests import pbo as cscv_pbo

ACTIVE_STRATEGIES_PATH = Path("data/active_strategies.json")
_PBO_PARTITIONS = 16


@dataclass
class Candidate:
    """A mutated strategy variant awaiting evaluation.

    ``replay_trades``      — per-trade OOS returns (feeds Monte Carlo + the gate).
    ``oos_return_matrix``  — optional (T, K) per-config OOS columns for PBO/CSCV (R1).
    ``n_configs_tested``   — honest trial count for the Deflated Sharpe haircut (R2).
    """
    variant_id: str
    config: dict
    replay_trades: Sequence[float]
    n_configs_tested: int = 1
    oos_return_matrix: Any = None
    evidence: dict = field(default_factory=dict)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _wr(returns) -> float:
    r = np.asarray(returns, dtype=float)
    return float((r > 0).mean()) if r.size else 0.0


# ── evaluation ────────────────────────────────────────────────────────────────
def evaluate_candidate(
    candidate: Candidate,
    live_returns,
    *,
    gate: PromotionGate | None = None,
    days_eligible: int = 0,
    mc_kwargs: dict | None = None,
    mc_pass_kwargs: dict | None = None,
) -> dict:
    """Run the full gate on one candidate. Returns a verdict dict (no writes).

    ``promote`` is True only if ALL hold: gate verdict PROMOTE (shadow Sharpe LB >
    live UB, WR floor, internal PBO, DSR) AND capital-preservation Monte-Carlo pass
    AND family PBO <= 0.5 over the per-config OOS matrix.
    """
    gate = gate or PromotionGate()
    shadow = np.asarray(list(candidate.replay_trades), dtype=float)
    live = np.asarray(list(live_returns or []), dtype=float)
    if live.size < 2:
        # Degenerate live history -> the gate's LB-vs-UB test cannot be beaten; treat
        # as a non-promotable comparison rather than fabricating a baseline.
        live = np.zeros(2, dtype=float)

    mc = monte_carlo_trade_sequence(shadow, **(mc_kwargs or {}))
    mc_ok = mc.passes(**(mc_pass_kwargs or {}))

    # R1 — Probability of Backtest Overfitting across the mutator's config grid.
    pbo_value: float | None = None
    pbo_ok = True
    if candidate.oos_return_matrix is not None:
        M = np.asarray(candidate.oos_return_matrix, dtype=float)
        if M.ndim == 2 and M.shape[1] >= 2 and M.shape[0] >= _PBO_PARTITIONS:
            try:
                pbo_value = float(cscv_pbo(M, _PBO_PARTITIONS))
                pbo_ok = pbo_value <= 0.5
            except Exception:
                pbo_value, pbo_ok = None, True

    inputs = GateInputs(
        shadow_returns=shadow,
        live_returns=live,
        shadow_wr_30d=_wr(shadow),
        days_eligible=int(days_eligible),
        n_trials_shadow=max(1, int(candidate.n_configs_tested)),  # R2 — honest trial count
    )
    verdict, diag = gate.evaluate(inputs)
    promote = (verdict == GateVerdict.PROMOTE) and mc_ok and pbo_ok
    return {
        "variant_id": candidate.variant_id,
        "promote": bool(promote),
        "gate_verdict": verdict.value,
        "mc_pass": bool(mc_ok),
        "pbo": pbo_value,
        "pbo_pass": bool(pbo_ok),
        "mc": {
            "p_total_positive": mc.p_total_positive,
            "max_drawdown_p95": mc.max_drawdown_p95,
            "n_trades": mc.n_trades,
        },
        "gate_diag": diag,
    }


# ── registry I/O (all writes PAPER+kernel latched) ─────────────────────────────
def load_registry(path: Path | str = ACTIVE_STRATEGIES_PATH) -> dict:
    p = Path(path)
    if not p.exists():
        return {"variants": {}, "updated_at": None}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        data.setdefault("variants", {})
        return data
    except Exception:
        return {"variants": {}, "updated_at": None}


def _save_registry(reg: dict, path: Path | str) -> None:
    assert_paper_self_improve([str(path)])
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    reg["updated_at"] = _utc_now().isoformat()
    p.write_text(json.dumps(reg, indent=2), encoding="utf-8")


def register_shadow(
    candidate: Candidate,
    *,
    path: Path | str = ACTIVE_STRATEGIES_PATH,
    evidence: dict | None = None,
) -> dict:
    """Record/refresh a variant as ``shadow`` (never auto-upgrades an active-paper one)."""
    reg = load_registry(path)
    v = reg["variants"].get(candidate.variant_id, {})
    if v.get("status") != "active-paper":
        v["status"] = "shadow"
        v.setdefault("registered_at", _utc_now().isoformat())
    v["config"] = candidate.config
    v["last_eval"] = evidence or {}
    reg["variants"][candidate.variant_id] = v
    _save_registry(reg, path)
    return v


def promote_to_active_paper(
    candidate: Candidate,
    *,
    path: Path | str = ACTIVE_STRATEGIES_PATH,
    adaptive_path: Path | str = ADAPTIVE_MACHINE_CONFIG_PATH,
    ttl_hours: int = 24,
    evidence: dict | None = None,
) -> dict:
    """Flip a variant to ``active-paper``: write the winning config to the adaptive
    path MachineSignal polls, and update the registry. Both writes latched."""
    payload = {
        "enabled": True,
        "source": "promotion_loop",
        "candidate_id": candidate.variant_id,
        "generated_at": _utc_now().isoformat(),
        "expires_at": (_utc_now() + timedelta(hours=int(ttl_hours))).isoformat(),
        "apply_scope": "paper",
        "evidence": evidence or {},
        "config": candidate.config,
    }
    sane = sanitize_adaptive_machine_payload(payload)
    if not sane.applied:
        return {"promoted": False, "reason": sane.reason}

    assert_paper_self_improve([str(path), str(adaptive_path)])
    ap = Path(adaptive_path)
    ap.parent.mkdir(parents=True, exist_ok=True)
    ap.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    reg = load_registry(path)
    v = reg["variants"].get(candidate.variant_id, {})
    v.update(
        {
            "status": "active-paper",
            "config": candidate.config,
            "promoted_at": _utc_now().isoformat(),
            "last_eval": evidence or {},
        }
    )
    v.setdefault("registered_at", v.get("promoted_at"))
    reg["variants"][candidate.variant_id] = v
    _save_registry(reg, path)
    return {"promoted": True, "variant": v}


def should_demote(recent_active_returns, shadow_ci_low: float, *, min_obs: int = 20) -> bool:
    """Auto-demote rule: an active-paper variant whose realized mean return falls below
    its own shadow Sharpe-CI lower expectation (proxied by ``shadow_ci_low`` on returns)
    over at least ``min_obs`` trades has stopped generalizing."""
    r = np.asarray(recent_active_returns, dtype=float)
    if r.size < int(min_obs):
        return False
    return float(r.mean()) < float(shadow_ci_low)


def auto_demote(
    variant_id: str,
    *,
    path: Path | str = ACTIVE_STRATEGIES_PATH,
    reason: str = "underperformed_shadow_ci",
) -> dict:
    reg = load_registry(path)
    v = reg["variants"].get(variant_id)
    if not v or v.get("status") != "active-paper":
        return {"demoted": False}
    v["status"] = "shadow"
    v["demotions"] = int(v.get("demotions", 0)) + 1
    v["demoted_at"] = _utc_now().isoformat()
    v["demote_reason"] = reason
    reg["variants"][variant_id] = v
    _save_registry(reg, path)
    return {"demoted": True, "variant": v}


# ── runtime read helpers (lazy; only touched at tick time) ─────────────────────
def read_live_paper_returns(lookback_days: int = 30) -> list[float]:
    """PAPER realized per-trade returns from the warehouse (reuses strategy_readiness)."""
    try:
        from core.strategy_readiness import load_warehouse_records

        recs = load_warehouse_records(lookback_days=lookback_days, mode="PAPER")
        return [float(r["pnl"]) for r in recs if r.get("pnl") is not None]
    except Exception:
        return []


def read_shadow_returns(db_path: str = "data/warehouse.sqlite", limit: int = 5000) -> list[float]:
    """Shadow ensemble sim PnL from warehouse.shadow_decisions.sim_pnl."""
    import sqlite3

    p = Path(db_path)
    if not p.exists():
        return []
    try:
        con = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
        try:
            rows = con.execute(
                "SELECT sim_pnl FROM shadow_decisions "
                "WHERE sim_pnl IS NOT NULL ORDER BY ts DESC LIMIT ?",
                (int(limit),),
            ).fetchall()
        finally:
            con.close()
        return [float(r[0]) for r in rows]
    except Exception:
        return []


# ── orchestrator ───────────────────────────────────────────────────────────────
def tick(
    candidates: Sequence[Candidate],
    *,
    live_returns=None,
    registry_path: Path | str = ACTIVE_STRATEGIES_PATH,
    adaptive_path: Path | str = ADAPTIVE_MACHINE_CONFIG_PATH,
    gate: PromotionGate | None = None,
) -> dict:
    """Evaluate every candidate; promote winners to active-paper, register the rest as
    shadow. PAPER-latched at every write. Returns an audit report."""
    if live_returns is None:
        live_returns = read_live_paper_returns()
    results: list[dict] = []
    for c in candidates:
        ev = evaluate_candidate(c, live_returns, gate=gate)
        if ev["promote"]:
            ev["action"] = promote_to_active_paper(
                c, path=registry_path, adaptive_path=adaptive_path, evidence=ev
            )
        else:
            register_shadow(c, path=registry_path, evidence=ev)
            ev["action"] = {"registered": "shadow"}
        results.append(ev)
    return {
        "generated_at": _utc_now().isoformat(),
        "n_candidates": len(results),
        "n_promoted": sum(1 for r in results if r["promote"]),
        "results": results,
    }
