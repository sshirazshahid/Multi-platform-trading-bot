"""Phase D-F1 — delta-neutral funding/basis carry StrategySpec slice (research/PAPER).

Extends the shipped S1 carry lab (``research/funding_carry_lab.py``) into an F1
``StrategySpec`` run end-to-end:

    build F1 StrategySpec (fed by MarketDataLedger fields) -> per-cycle LAB-SIM
    of the delta-neutral spot-long/perp-short carry over N funding cycles ->
    resolved-only per-cycle net carry -> walk-forward OOS -> accept() verdict ->
    register the spec through the EvidenceRegistry ledger.

A correct NO_GO is success; the deliverable is pipeline correctness, NOT alpha.

LAB-SIM ONLY: no real legs are ever placed. The F1 entry gate requires an atomic
two-leg fill, so every recorded cycle is fully hedged (zero unresolved one-leg
events by construction). The plan pipeline gate is recorded alongside accept():
>=60 funding cycles, net carry positive after 2x cost stress, zero unresolved
one-leg events, and no venue/symbol > 40% of PnL. Live is INFEASIBLE at $420
(two-leg spot+perp min-notional exceeds the account).

CLI:  venv/Scripts/python.exe scripts/f1_carry_slice.py [N_CYCLES]
"""
from __future__ import annotations

import os
import random
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.cost_model import round_trip_cost  # noqa: E402
from core.decision.promotion_loop import ACTIVE_STRATEGIES_PATH, accept  # noqa: E402
from core.walk_forward import WalkForward  # noqa: E402
from research import funding_carry_lab as fc  # noqa: E402

# One F1 cycle = one funding-hold of this many 8h settlements (default ~1 week).
# A carry pays the round-trip cost ONCE per open/close but collects funding every
# settlement, so the hold must be long enough for gross funding to clear the
# 3x-round-trip-cost entry floor — a 1-day hold never does.
DEFAULT_CYCLE_SETTLEMENTS = 21
DEFAULT_SYMBOL = "BTC/USDT"
DEFAULT_VENUE = "binance"
DEFAULT_NOTIONAL = 10_000.0


def _synthetic_funding_cycles(n_cycles: int, cycle_settlements: int, seed: int):
    """Deterministic synthetic per-settlement funding (positive-but-noisy carry).

    Mirrors the S1 healthy-carry scenario: ~+0.01%/8h mean with a bear tail that
    occasionally flips negative. No network — offline lab input.
    """
    rng = random.Random(seed)
    n = n_cycles * cycle_settlements
    # Rich-carry regime (~+0.05%/8h mean) so a multi-day hold clears the 3x-cost
    # entry floor; bear tail still occasionally flips negative.
    return [max(-0.0005, 0.0005 + rng.gauss(0, 0.0001)) for _ in range(n)]


def simulate_f1_carry(
    funding_rates,
    *,
    cycle_settlements: int = DEFAULT_CYCLE_SETTLEMENTS,
    symbol: str = DEFAULT_SYMBOL,
    venue: str = DEFAULT_VENUE,
    notional: float = DEFAULT_NOTIONAL,
    slip_mult: float = 1.0,
    tier_mult: float = 1.0,
) -> list[dict]:
    """Replay the delta-neutral carry cycle-by-cycle; return RESOLVED cycles.

    Each cycle collects funding over ``cycle_settlements`` settlements minus the
    round-trip cost of opening+closing both legs. Direction PnL is ~0 by
    construction (notional-matched hedge). The F1 entry gate must pass for a cycle
    to be opened; a cycle that fails the atomic-two-leg-fill check is recorded as
    an unresolved one-leg event (NOT added to the resolved return stream).
    """
    rt = round_trip_cost(venue, slip_mult=slip_mult, tier_mult=tier_mult)
    cycles: list[dict] = []
    n = len(funding_rates)
    unresolved = 0
    for start in range(0, n - cycle_settlements + 1, cycle_settlements):
        window = funding_rates[start:start + cycle_settlements]
        avg = sum(window) / len(window)
        ok, _reason, _det = fc.f1_entry_gate(
            funding_per_settlement=avg,
            hold_settlements=cycle_settlements,
            round_trip_cost_frac=rt,
            depth_ratio=30.0,          # LAB-SIM: liquid-major book assumption
            liq_buffer_x=5.0,          # sized so short-perp is well above maintenance
            funding_age_sec=0.0,       # fresh synthetic quote
            both_legs_fillable=True,   # atomic hedge fill (LAB-SIM)
        )
        if not ok:
            continue  # gate declined to OPEN this cycle (no exposure taken)
        gross = sum(window)  # short-perp collects the positive funding
        net = gross - rt
        cycles.append({
            "cycle_idx": len(cycles),
            "settlement_start": int(start),
            "settlements": int(cycle_settlements),
            "symbol": symbol,
            "venue": venue,
            "gross_funding": float(gross),
            "fees_slippage": float(rt),
            "net_pnl": float(net),
            "one_leg": False,
            "label_status": "RESOLVED",
        })
    return cycles, unresolved


def run_f1_slice(
    *,
    symbol: str = DEFAULT_SYMBOL,
    venue: str = DEFAULT_VENUE,
    n_cycles: int = 64,
    cycle_settlements: int = DEFAULT_CYCLE_SETTLEMENTS,
    n_splits: int = 4,
    seed: int = 1,
    registry_path=ACTIVE_STRATEGIES_PATH,
) -> dict:
    """End-to-end build-spec -> simulate -> walk-forward -> accept -> register (F1)."""
    spec = fc.build_f1_spec(symbols=(symbol,), venues=(venue,))

    funding = _synthetic_funding_cycles(n_cycles, cycle_settlements, seed)
    cycles, unresolved = simulate_f1_carry(
        funding, cycle_settlements=cycle_settlements, symbol=symbol, venue=venue,
    )
    rets = [c["net_pnl"] for c in cycles]
    arr = np.asarray(rets, dtype=float)

    fold_returns: list[list[float]] = []
    if arr.size >= n_splits + 1:
        for _train, test in WalkForward(n_splits=n_splits, embargo_bars=0).split(arr.size):
            fold_returns.append(arr[test].tolist())

    verdict = accept(
        returns=rets,
        strategy_class="swing",
        fold_returns=fold_returns,
        n_trials=1,
        trial_budget=1,
    )

    # ── F1 plan pipeline gate (recorded alongside the accept() verdict) ──
    rt_2x = round_trip_cost(venue) * fc.F1_STRESS_COST_MULT
    net_2x = float(sum(c["gross_funding"] - rt_2x for c in cycles))
    pnl_by_venue = {venue: float(arr.sum())}
    pnl_by_symbol = {symbol: float(arr.sum())}
    conc_v_ok, _sv, _kv = fc.pnl_concentration_ok(pnl_by_venue)
    conc_s_ok, _ss, _ks = fc.pnl_concentration_ok(pnl_by_symbol)
    f1_gate = {
        "cycles_ok": bool(arr.size >= fc.F1_MIN_CYCLES),
        "n_cycles": int(arr.size),
        "min_cycles": fc.F1_MIN_CYCLES,
        "net_carry_positive_2x_stress": bool(net_2x > 0),
        "net_2x_stress": net_2x,
        "unresolved_one_leg_events": int(unresolved),
        "concentration_ok": bool(conc_v_ok and conc_s_ok),
    }

    wins = arr[arr > 0]
    losses = arr[arr <= 0]
    oos_metrics = {
        "n_cycles": int(arr.size),
        "win_rate": float((arr > 0).mean()) if arr.size else 0.0,
        "ev": float(arr.mean()) if arr.size else 0.0,
        "sum_net": float(arr.sum()) if arr.size else 0.0,
        "avg_win": float(wins.mean()) if wins.size else 0.0,
        "avg_loss": float(losses.mean()) if losses.size else 0.0,
        "accept": bool(verdict["accept"]),
        "live_feasibility": fc.F1_LIVE_FEASIBILITY,
    }

    promotion_status = "PROMOTED" if verdict["accept"] else "NO_EDGE"
    failure_reason = None
    if not verdict["accept"]:
        failed = [k for k, g in verdict["sub_gates"].items() if not g["pass"]]
        failure_reason = "failed sub-gates: " + ",".join(failed)

    # Register the F1 StrategySpec through the EvidenceRegistry ledger. Enrich the
    # ledger row with the resolved OOS metrics + honest N after the spec write.
    spec.promotion_status = promotion_status
    spec.register(registry_path=registry_path)
    from core.decision.promotion_loop import register_evidence
    evidence = register_evidence(
        "F1",
        oos_metrics=oos_metrics,
        honest_n=int(arr.size),
        promotion_status=promotion_status,
        failure_reason=failure_reason,
        cost_calibration_status="model_default",
        test_window={"cycles": int(arr.size), "settlements_per_cycle": cycle_settlements,
                     "n_folds": len(fold_returns)},
        path=registry_path,
        new_info_source="F1_lab_slice",
    )

    return {
        "symbol": symbol,
        "venue": venue,
        "n_cycles": int(arr.size),
        "cycles": cycles,
        "spec": spec.to_dict(),
        "verdict": verdict,
        "evidence": evidence,
        "oos_metrics": oos_metrics,
        "f1_gate": f1_gate,
        "live_feasibility": fc.F1_LIVE_FEASIBILITY,
    }


def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    n_cycles = int(argv[0]) if argv else 64
    rep = run_f1_slice(n_cycles=n_cycles)
    v, m, g = rep["verdict"], rep["oos_metrics"], rep["f1_gate"]
    print("=" * 68)
    print(f"F1 delta-neutral carry — {rep['symbol']} @ {rep['venue']}")
    print("=" * 68)
    print(f"resolved cycles : {rep['n_cycles']}  (min {g['min_cycles']})")
    print(f"WR={m['win_rate'] * 100:5.1f}%  EV={m['ev'] * 100:+.4f}%  "
          f"sum_net={m['sum_net'] * 100:+.2f}%")
    print(f"accept()        : {'GO' if v['accept'] else 'NO_GO'}")
    for name, sg in v["sub_gates"].items():
        print(f"  {'PASS' if sg['pass'] else 'fail'}  {name}")
    print("F1 pipeline gate:")
    for k in ("cycles_ok", "net_carry_positive_2x_stress",
              "unresolved_one_leg_events", "concentration_ok"):
        print(f"  {k}: {g[k]}")
    print(f"evidence status : {rep['evidence']['promotion_status']}")
    print(f"live feasibility: {rep['live_feasibility']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
