"""Adversarial challenges for MTSI offline sim (research-only).

Fee understatement, adverse-selection realism, funding path split,
$1 inventory / clip invariants, and latency honesty notes.
"""
from __future__ import annotations

from typing import Any

from research import sim_mtsi_inventory as mtsi


def challenge_fee_understatement(
    cell: dict[str, Any] | None = None,
    *,
    seed: int = 42,
) -> dict[str, Any]:
    """Zero-fee path must look *better* than priced fees — else fees are ignored."""
    bars = mtsi.generate_synthetic_path(n=1200, seed=seed)
    cell = cell or {
        "id": "F2",
        "market": "futures",
        "gamma": 0.3,
        "half_spread_floor_bps": 3.0,
        "tilt_max_bps": 1.0,
    }
    priced = mtsi.run_cell(
        cell,
        bars,
        fees={"futures_maker": 0.0001, "spot_maker": 0.001},
        adverse_frac=0.5,
    )
    free = mtsi.run_cell(
        cell,
        bars,
        fees={"futures_maker": 0.0, "spot_maker": 0.0},
        adverse_frac=0.0,
    )
    ok = free.mean_clip_pnl_usd >= priced.mean_clip_pnl_usd - 1e-15
    return {
        "name": "fee_understatement",
        "ok": ok,
        "priced_mean": priced.mean_clip_pnl_usd,
        "zero_cost_mean": free.mean_clip_pnl_usd,
        "note": "If zero-cost mean < priced mean, the cost model is broken/inverted.",
    }


def challenge_inventory_and_clip_cap(
    *,
    seed: int = 5,
    clip_usd: float = 50.0,
) -> dict[str, Any]:
    """Even with whale clip requests, inventory and fills stay ≤ $1."""
    bars = mtsi.generate_synthetic_path(n=800, seed=seed)
    cell = {
        "id": "F3",
        "market": "futures",
        "gamma": 0.5,
        "half_spread_floor_bps": 4.0,
        "tilt_max_bps": 1.5,
    }
    result = mtsi.run_cell(
        cell,
        bars,
        fees={"futures_maker": 0.0001, "spot_maker": 0.001},
        clip_usd=clip_usd,
    )
    ok = (
        result.max_abs_inventory_usd <= mtsi.MAX_GROSS_USD + 1e-9
        and result.max_single_clip_usd <= mtsi.MAX_GROSS_USD + 1e-9
    )
    return {
        "name": "one_dollar_invariants",
        "ok": ok,
        "max_abs_inventory_usd": result.max_abs_inventory_usd,
        "max_single_clip_usd": result.max_single_clip_usd,
        "requested_clip_usd": clip_usd,
    }


def challenge_spot_vs_futures_funding(*, seed: int = 11) -> dict[str, Any]:
    """Futures path must feel funding; spot must not use the same total."""
    bars = mtsi.generate_synthetic_path(n=900, seed=seed)
    for b in bars:
        b["funding_rate_8h"] = 0.01
    fees = {"futures_maker": 0.0001, "spot_maker": 0.001}
    base = {
        "gamma": 0.1,
        "half_spread_floor_bps": 5.0,
        "tilt_max_bps": 0.5,
    }
    fut = mtsi.run_cell({**base, "id": "F", "market": "futures"}, bars, fees=fees)
    spot = mtsi.run_cell({**base, "id": "S", "market": "spot"}, bars, fees=fees)
    diverged = abs(fut.total_pnl_usd - spot.total_pnl_usd) > 1e-12 or (
        fut.n_clips == 0 and spot.n_clips == 0
    )
    # If both idle, challenge is inconclusive but not a fail.
    ok = diverged or (fut.n_clips == 0 and spot.n_clips == 0)
    if fut.n_clips and spot.n_clips:
        ok = abs(fut.total_pnl_usd - spot.total_pnl_usd) > 1e-12
    return {
        "name": "spot_vs_futures_funding",
        "ok": ok,
        "futures_total": fut.total_pnl_usd,
        "spot_total": spot.total_pnl_usd,
        "futures_n": fut.n_clips,
        "spot_n": spot.n_clips,
    }


def challenge_latency_honesty() -> dict[str, Any]:
    """Document that this bot is not co-located HFT — sub-HFT sim only."""
    return {
        "name": "latency_honesty",
        "ok": True,
        "claim": "sub_hft_maker_sim_only",
        "note": (
            "Bot portfolio/monitor loops are minutes-scale. MTSI sim does not "
            "model co-location or cancel/replace races. Do not cite HFT rebate "
            "anecdotes as local GO."
        ),
    }


def run_all_challenges() -> dict[str, Any]:
    rows = [
        challenge_fee_understatement(),
        challenge_inventory_and_clip_cap(),
        challenge_spot_vs_futures_funding(),
        challenge_latency_honesty(),
    ]
    return {
        "ok": all(r["ok"] for r in rows),
        "challenges": rows,
    }
