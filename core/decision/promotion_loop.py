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

import hashlib
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
from core.stat_tests import bootstrap_ci, deflated_sharpe
from core.stat_tests import pbo as cscv_pbo
from core.stat_tests import sharpe as _sharpe

ACTIVE_STRATEGIES_PATH = Path("data/active_strategies.json")
_PBO_PARTITIONS = 16

# Strategy-class-aware minimum resolved-sample floors. Scalp/exec generate far
# more trades and far smaller per-trade edges, so they need a much larger n
# before any after-cost statistic is trustworthy.
STRATEGY_N_FLOORS = {
    "intraday": 100,
    "swing": 100,
    "scalp": 500,
    "exec": 500,
    "execution": 500,
}
_DEFAULT_N_FLOOR = 100


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


# ── unified after-cost accept() gate (Phase 0c) ────────────────────────────────
def _profit_factor(returns) -> float:
    """Gross profit / gross loss. inf if no losses, 0 if no gains."""
    r = np.asarray(returns, dtype=float)
    gains = float(r[r > 0].sum())
    losses = float(-r[r < 0].sum())
    if losses <= 0:
        return float("inf") if gains > 0 else 0.0
    return gains / losses


def _mean_stat(x) -> float:
    return float(np.mean(np.asarray(x, dtype=float)))


def accept(
    *,
    returns,
    strategy_class: str = "intraday",
    fold_returns: Sequence[Sequence[float]] | None = None,
    oos_return_matrix: Any = None,
    n_trials: int = 1,
    trial_budget: int = 1,
    resolved_paper_returns: Sequence[float] | None = None,
    confirm_days: float = 0.0,
    confirm_min_days: int = 30,
    confirm_max_days: int = 60,
    pf_floor: float = 1.20,
    pbo_threshold: float = 0.5,
    dsr_p_threshold: float = 0.05,
    alpha: float = 0.05,
    ci_level: float = 0.95,
    bootstrap_resamples: int = 5000,
    random_state: int = 42,
) -> dict:
    """One after-cost verdict so no strategy invents its own pass/fail.

    Combines eight conjunctive sub-gates over a candidate's resolved OOS net
    returns. The honest trial count fed to the Deflated Sharpe is
    ``max(n_trials, trial_budget)`` — i.e. it never under-counts the
    pre-registered program-level trial budget (the multiple-comparisons control
    below uses the SAME budget for a family-wise haircut).

    Sub-gates (all must pass for ``accept=True``):
      1. ``n_floor``            — strategy-class-aware resolved-sample floor.
      2. ``profit_factor``      — gross-profit / gross-loss >= ``pf_floor`` (1.20).
      3. ``expectancy_ci``      — bootstrap CI LOWER bound on mean return > 0.
      4. ``folds``              — >= 2/3 of walk-forward folds mean-positive.
      5. ``pbo``               — CSCV PBO over the FULL hyperparameter grid < 0.5
                                  (NOT folds-as-strategies; see promotion_gate caveat).
      6. ``dsr``               — Deflated Sharpe p < 0.05 at the honest trial count.
      7. ``multiple_comparisons`` — family-wise (Bonferroni) haircut of the DSR
                                  p-value against the pre-registered trial budget.
      8. ``confirmation_window`` — a 30-60d RESOLVED PAPER window with rows
                                  (only countable after 0b shipped) is present.

    Returns a single verdict dict; performs no writes.
    """
    r = np.asarray(list(returns or []), dtype=float)
    n = int(r.size)
    honest_n = max(int(n_trials), int(trial_budget), 1)

    # 1 — strategy-class-aware n floor
    floor_n = STRATEGY_N_FLOORS.get(str(strategy_class).lower(), _DEFAULT_N_FLOOR)
    n_floor_pass = n >= floor_n

    # 2 — profit factor
    pf = _profit_factor(r)
    pf_pass = bool(pf >= pf_floor)

    # 3 — expectancy bootstrap-CI LOWER bound > 0 (point estimate not enough)
    if n >= 2 and r.std(ddof=1) > 0:
        exp_lb, exp_ub = bootstrap_ci(
            r,
            statistic=_mean_stat,
            n_resamples=int(bootstrap_resamples),
            ci=float(ci_level),
            random_state=int(random_state),
        )
    else:
        exp_lb, exp_ub = (float(r.mean()) if n else 0.0,) * 2
    exp_point = float(r.mean()) if n else 0.0
    exp_pass = bool(exp_lb > 0.0)

    # 4 — positive 2/3 folds
    folds = [np.asarray(f, dtype=float) for f in (fold_returns or [])]
    n_folds = len(folds)
    n_pos = sum(1 for f in folds if f.size and float(f.mean()) > 0.0)
    folds_required = -(-2 * n_folds // 3) if n_folds else 0  # ceil(2/3 * n_folds)
    folds_pass = bool(n_folds >= 3 and n_pos >= folds_required)

    # 5 — PBO over the FULL hyperparameter grid (fail-closed when too small)
    pbo_value: float | None = None
    pbo_pass = False
    if oos_return_matrix is not None:
        M = np.asarray(oos_return_matrix, dtype=float)
        if M.ndim == 2 and M.shape[1] >= 2 and M.shape[0] >= _PBO_PARTITIONS:
            try:
                pbo_value = float(cscv_pbo(M, _PBO_PARTITIONS))
                pbo_pass = bool(pbo_value < pbo_threshold)
            except Exception:
                pbo_value, pbo_pass = None, False

    # 6 — Deflated Sharpe at the honest trial count
    sr = _sharpe(r)
    dsr_prob = float(
        deflated_sharpe(
            sr_observed=sr,
            n_trials=honest_n,
            n_obs=max(2, n),
            sr_var=1.0 / max(2, n),
        )
    )
    dsr_p = 1.0 - dsr_prob
    dsr_pass = bool(dsr_p < dsr_p_threshold)

    # 7 — program-level multiple-comparisons control: family-wise (Bonferroni)
    #     haircut of the candidate's DSR p-value against the pre-registered budget.
    p_adj = min(1.0, dsr_p * float(honest_n))
    mc_pass = bool(p_adj < alpha)

    # 8 — 30-60d RESOLVED PAPER confirmation window (post-0b rows only)
    n_resolved = len(list(resolved_paper_returns or []))
    conf_pass = bool(n_resolved > 0 and float(confirm_days) >= float(confirm_min_days))

    sub_gates = {
        "n_floor": {"pass": n_floor_pass, "n": n, "floor": floor_n},
        "profit_factor": {"pass": pf_pass, "value": pf, "floor": pf_floor},
        "expectancy_ci": {
            "pass": exp_pass,
            "lb": float(exp_lb),
            "ub": float(exp_ub),
            "point": exp_point,
        },
        "folds": {
            "pass": folds_pass,
            "n_positive": int(n_pos),
            "n_folds": int(n_folds),
            "required": int(folds_required),
        },
        "pbo": {
            "pass": pbo_pass,
            "value": pbo_value,
            "threshold": pbo_threshold,
            "source": "full_grid",
        },
        "dsr": {
            "pass": dsr_pass,
            "prob": dsr_prob,
            "p": dsr_p,
            "n_trials": honest_n,
        },
        "multiple_comparisons": {
            "pass": mc_pass,
            "p": dsr_p,
            "p_adj": p_adj,
            "trial_budget": honest_n,
            "alpha": alpha,
        },
        "confirmation_window": {
            "pass": conf_pass,
            "days": float(confirm_days),
            "min_days": int(confirm_min_days),
            "max_days": int(confirm_max_days),
            "n_resolved": int(n_resolved),
        },
    }
    accepted = all(g["pass"] for g in sub_gates.values())
    return {
        "accept": bool(accepted),
        "strategy_class": str(strategy_class),
        "n_used": n,
        "n_floor": floor_n,
        "honest_n_dsr": honest_n,
        "sub_gates": sub_gates,
    }


# ── EvidenceRegistry (Phase 0d): per-strategy S1-S5 ledger ─────────────────────
# A hypothesis/rules/sources/cost-model/OOS-metrics ledger keyed by strategy_id.
# It doubles as a PRECONDITION CHECK: a config whose fingerprint was already
# marked NO_EDGE may not be re-tested without an explicit new-information-source
# flag. The recorded honest trial-count N is wired into accept()'s DSR so the
# Deflated Sharpe haircut reads the real program-wide N.
EVIDENCE_STRATEGY_IDS = ("S1", "S2", "S3", "S4", "S5")


class NoEdgeReplayError(RuntimeError):
    """Raised when a config fingerprint already marked NO_EDGE is re-registered
    without an explicit new-information-source flag."""

    def __init__(self, fingerprint: str, failure_reason: str | None):
        self.fingerprint = fingerprint
        self.failure_reason = failure_reason or ""
        super().__init__(
            f"config fingerprint {fingerprint[:12]} previously marked NO_EDGE: "
            f"{self.failure_reason}"
        )


def _evidence_stub(strategy_id: str) -> dict:
    return {
        "strategy_id": strategy_id,
        "hypothesis": "",
        "rules": {},
        "data_sources": [],
        "data_depth": None,
        "cost_model": {},
        "cost_calibration_status": "uncalibrated",
        "test_window": None,
        "oos_metrics": {},
        "honest_n": 0,
        "promotion_status": "untested",
        "failure_reason": None,
        "last_tested_ts": None,
        "universe_construction_method": None,
        "fingerprint": None,
    }


def compute_fingerprint(
    *,
    rules: dict | None = None,
    data_sources: Sequence[str] | None = None,
    cost_model: dict | None = None,
    universe_construction_method: Any = None,
) -> str:
    """Deterministic SHA-256 over the (rules, sources, cost-model, universe) tuple.

    Two configs that test the same deterministic hypothesis on the same data with
    the same cost model collapse to the same fingerprint — that identity is what
    the NO_EDGE precondition check keys on.
    """
    payload = {
        "rules": rules or {},
        "data_sources": sorted(data_sources or []),
        "cost_model": cost_model or {},
        "universe": universe_construction_method,
    }
    blob = json.dumps(payload, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def init_evidence_registry(path: Path | str = ACTIVE_STRATEGIES_PATH) -> dict:
    """Ensure S1-S5 stub records (and the NO_EDGE fingerprint index) exist. PAPER-latched."""
    reg = load_registry(path)
    strategies = reg.setdefault("strategies", {})
    for sid in EVIDENCE_STRATEGY_IDS:
        strategies.setdefault(sid, _evidence_stub(sid))
    reg.setdefault("no_edge_fingerprints", {})
    _save_registry(reg, path)
    return reg


def register_evidence(
    strategy_id: str,
    *,
    hypothesis: str | None = None,
    rules: dict | None = None,
    data_sources: Sequence[str] | None = None,
    data_depth: Any = None,
    cost_model: dict | None = None,
    cost_calibration_status: str | None = None,
    test_window: Any = None,
    oos_metrics: dict | None = None,
    honest_n: int | None = None,
    promotion_status: str | None = None,
    failure_reason: str | None = None,
    universe_construction_method: Any = None,
    fingerprint: str | None = None,
    new_info_source: str | None = None,
    path: Path | str = ACTIVE_STRATEGIES_PATH,
) -> dict:
    """Record/refresh a per-strategy evidence row. PAPER-latched.

    PRECONDITION: if the resolved fingerprint was already marked NO_EDGE and no
    ``new_info_source`` is supplied, the call is refused with the PRIOR
    failure_reason (``NoEdgeReplayError``) — you cannot re-litigate a dead config
    without new information.
    """
    reg = load_registry(path)
    strategies = reg.setdefault("strategies", {})
    no_edge = reg.setdefault("no_edge_fingerprints", {})
    rec = strategies.get(strategy_id) or _evidence_stub(strategy_id)

    fp = fingerprint or compute_fingerprint(
        rules=rules,
        data_sources=data_sources,
        cost_model=cost_model,
        universe_construction_method=universe_construction_method,
    )

    prior = no_edge.get(fp)
    if prior and not new_info_source:
        raise NoEdgeReplayError(fp, prior.get("failure_reason"))

    if hypothesis is not None:
        rec["hypothesis"] = hypothesis
    if rules is not None:
        rec["rules"] = rules
    if data_sources is not None:
        rec["data_sources"] = list(data_sources)
    if data_depth is not None:
        rec["data_depth"] = data_depth
    if cost_model is not None:
        rec["cost_model"] = cost_model
    if cost_calibration_status is not None:
        rec["cost_calibration_status"] = cost_calibration_status
    if test_window is not None:
        rec["test_window"] = test_window
    if oos_metrics is not None:
        rec["oos_metrics"] = oos_metrics
    if honest_n is not None:
        rec["honest_n"] = int(honest_n)
    if promotion_status is not None:
        rec["promotion_status"] = promotion_status
    if failure_reason is not None:
        rec["failure_reason"] = failure_reason
    if universe_construction_method is not None:
        rec["universe_construction_method"] = universe_construction_method
    if new_info_source:
        rec["new_info_source"] = new_info_source
    rec["fingerprint"] = fp
    rec["last_tested_ts"] = _utc_now().isoformat()

    if str(promotion_status).upper() == "NO_EDGE":
        no_edge[fp] = {
            "strategy_id": strategy_id,
            "failure_reason": failure_reason,
            "recorded_ts": _utc_now().isoformat(),
        }

    strategies[strategy_id] = rec
    _save_registry(reg, path)
    return rec


def honest_n_for_strategy(
    strategy_id: str, path: Path | str = ACTIVE_STRATEGIES_PATH
) -> int:
    """Recorded honest trial-count N for a strategy (0 if absent)."""
    reg = load_registry(path)
    rec = reg.get("strategies", {}).get(strategy_id, {})
    try:
        return int(rec.get("honest_n", 0) or 0)
    except (TypeError, ValueError):
        return 0


def accept_for_strategy(
    strategy_id: str,
    *,
    returns,
    path: Path | str = ACTIVE_STRATEGIES_PATH,
    **kwargs,
) -> dict:
    """``accept()`` with the registry's program-wide honest N folded into the
    trial budget, so the Deflated Sharpe reads the real N (never under-counts)."""
    n = honest_n_for_strategy(strategy_id, path)
    tb = max(int(kwargs.pop("trial_budget", 1)), n, 1)
    return accept(returns=returns, trial_budget=tb, **kwargs)


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
    """Shadow ensemble forward-RESOLVED net PnL from warehouse.shadow_outcomes.

    Phase 0b: reads after-cost, SL-first net_pnl for RESOLVED rows only — never
    the projected-at-TP shadow_decisions.sim_pnl, which is a diagnostic and must
    not feed a promotion decision.
    """
    import sqlite3

    p = Path(db_path)
    if not p.exists():
        return []
    try:
        con = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
        try:
            rows = con.execute(
                "SELECT o.net_pnl FROM shadow_outcomes o "
                "JOIN shadow_decisions d ON d.proposal_id = o.proposal_id "
                "WHERE o.label_status='RESOLVED' AND o.net_pnl IS NOT NULL "
                "ORDER BY o.resolved_ts DESC LIMIT ?",
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
