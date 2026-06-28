"""Bounded machine-strategy mutator for the PAPER self-improvement loop (R5 / R6).

Extends ``SelfHealingSupervisor``'s candidate grid toward SHORT lookbacks (R6 — the
edge, if any, concentrates at short horizons; never hardcode volume-weighting or a
preprint trailing-stop, let the gate decide) and runs each variant through the
EXISTING T+1, cost-realistic replay (``scripts.machine_strategy_replay``) to produce
the per-trade OOS return list that ``promotion_loop.Candidate`` needs.

This module writes NOTHING — ``promotion_loop`` owns every (PAPER-latched) write.
Every grid config is bounded to ``adaptive_config.TOP_LEVEL_RANGES`` so a promoted
variant is always sanitizer-valid. The default replay is fail-soft: missing OHLCV
cache or any replay error yields an empty trade list (→ no candidate → nothing
promotes), which is the correct outcome under the confirmed NO_EDGE regime.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Callable

from core.decision.promotion_loop import Candidate

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# R6: weight the grid toward SHORT horizons (small scalp_time_stop_bars first).
_SHORT_TIME_STOPS = (6, 12, 24)
_MIN_SCORES = (0.34, 0.45)


def mutate_machine_configs() -> list[dict[str, Any]]:
    """Short-lookback-weighted machine-strategy variants (bounded, sanitizer-valid)."""
    grid: list[dict[str, Any]] = []
    for tstop in _SHORT_TIME_STOPS:
        for min_score in _MIN_SCORES:
            unconfirmed = max(min_score, 0.45)
            grid.append(
                {
                    "id": f"mut_ts{tstop}_ms{int(min_score * 100)}",
                    "args": {
                        "min_score": min_score,
                        "unconfirmed_min_score": unconfirmed,
                        "rr": 2.0,
                        "atr_stop_mult": 2.0,
                    },
                    "config": {
                        "min_score": min_score,
                        "unconfirmed_min_score": unconfirmed,
                        "rr": 2.0,
                        "atr_stop_mult": 2.0,
                        "scalp_time_stop_bars": tstop,
                    },
                }
            )
    return grid


def _default_replay(
    entry: dict[str, Any],
    *,
    cache_dir: str = "data/ohlcv_cache",
    timeframe: str = "15m",
    max_files: int = 8,
    max_bars: int = 1500,
) -> list[float]:
    """Run one config through the existing T+1, cost-realistic replay in-process.

    Returns the flat list of per-trade returns across the cache. Fail-soft: returns
    [] if the cache is absent or any replay step errors (R5 cost realism is inherited
    from machine_strategy_replay's fee/slippage defaults)."""
    try:
        import argparse

        from core.machine_strategy_engine import MachineStrategyConfig, MachineStrategyEngine
        from scripts.machine_strategy_replay import (
            files_for,
            load_ohlcv,
            run_one,
            symbol_from_path,
        )

        a = entry["args"]
        cfg = MachineStrategyConfig(
            min_score=float(a["min_score"]),
            rr=float(a["rr"]),
            atr_stop_mult=float(a["atr_stop_mult"]),
            scalp_time_stop_bars=int(entry["config"].get("scalp_time_stop_bars", 12)),
            confirmation_detectors=("ema_rsi",),
            unconfirmed_min_score=float(a["unconfirmed_min_score"]),
        )
        engine = MachineStrategyEngine(cfg)
        args = argparse.Namespace(
            cache_dir=cache_dir,
            timeframe=timeframe,
            market_type="futures",
            exact=False,
            step=1,
            min_bars=80,
            max_files=max_files,
            max_bars=max_bars,
            fee_bps_side=6.0,
            entry_slip_bps=5.0,
            tp_slip_bps=5.0,
            stop_slip_bps=10.0,
        )
        rets: list[float] = []
        for path in files_for(args):
            try:
                df = load_ohlcv(path)
            except Exception:
                continue
            if max_bars > 0:
                df = df.tail(max_bars).reset_index(drop=True)
            for trade in run_one(df, symbol_from_path(path, timeframe), engine, args):
                rets.append(float(trade["ret"]))
        return rets
    except Exception:
        return []


def build_candidates(
    replay_fn: Callable[[dict], list[float]] | None = None,
    *,
    min_trades: int = 30,
) -> list[Candidate]:
    """Produce promotion-ready candidates from the mutated grid.

    ``replay_fn(entry) -> list[float]`` is injectable (tests pass a synthetic one);
    the default runs the in-process cost-realistic replay. A variant with fewer than
    ``min_trades`` OOS trades is dropped (too little evidence to evaluate honestly).
    ``n_configs_tested`` is the full grid size so the Deflated-Sharpe haircut (R2) is
    truthful for every candidate.
    """
    replay_fn = replay_fn or _default_replay
    grid = mutate_machine_configs()
    n_tested = len(grid)
    out: list[Candidate] = []
    for entry in grid:
        try:
            rets = list(replay_fn(entry))
        except Exception:
            rets = []
        if len(rets) < int(min_trades):
            continue
        out.append(
            Candidate(
                variant_id=entry["id"],
                config=entry["config"],
                replay_trades=rets,
                n_configs_tested=n_tested,
                evidence={"source": "research.mutator", "n_trades": len(rets)},
            )
        )
    return out
