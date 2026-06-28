"""Trade-sequence Monte Carlo — the validation leg CLAUDE.md Directive 1 requires.

``core/stat_tests.py`` has Sharpe / bootstrap-CI / Deflated-Sharpe / PBO, but NO
trade-sequence bootstrap. This module block-bootstraps a realized trade-return list
to answer the questions that actually gate promotion under a *capital-preservation*
objective:

    * P(total return > 0)        — is the edge robust to trade ordering / resampling?
    * max-drawdown distribution  — worst-case equity decline (the thing we minimize)
    * Sharpe confidence interval — is per-trade risk-adjusted return distinguishable from 0?

Objective (R4 — Teplova 2017 / the tsmom "don't lose" thesis): the gate is
DRAWDOWN-MINIMIZING / capital-preservation, NOT profit-maximizing. ``MonteCarloResult.passes()``
therefore gates on a drawdown bound AND P(>0), not on a Sharpe target.

Input is the per-trade return list already produced by ``scripts.machine_strategy_replay``
(each trade dict carries ``ret``); any 1-D sequence of per-trade returns works.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from core.stat_tests import sharpe


def _max_drawdown(equity: np.ndarray) -> float:
    """Max peak-to-trough decline of a cumulative-sum equity curve (return units)."""
    if equity.size == 0:
        return 0.0
    peak = np.maximum.accumulate(equity)
    return float(np.max(peak - equity))


def block_bootstrap(rets, block_len: int, n_resamples: int, seed: int) -> np.ndarray:
    """Circular block bootstrap of a return SEQUENCE (preserves serial dependence).

    Returns an ``(n_resamples, n)`` array of resampled return paths. Block sampling keeps
    short-run autocorrelation that an i.i.d. shuffle would destroy — the right null for a
    trade sequence that may carry momentum / streak structure.
    """
    r = np.asarray(rets, dtype=float)
    n = r.size
    if n == 0 or n_resamples <= 0:
        return np.empty((0, 0))
    block_len = max(1, min(int(block_len), n))
    rng = np.random.default_rng(int(seed))
    n_blocks = int(np.ceil(n / block_len))
    out = np.empty((int(n_resamples), n), dtype=float)
    offsets = np.arange(block_len)
    for i in range(int(n_resamples)):
        starts = rng.integers(0, n, size=n_blocks)
        idx = (starts[:, None] + offsets[None, :]).ravel() % n
        out[i] = r[idx[:n]]
    return out


@dataclass(frozen=True)
class MonteCarloResult:
    n_trades: int
    n_resamples: int
    p_total_positive: float      # P(sum of returns > 0) across resamples
    max_drawdown_p50: float      # median worst peak-to-trough (return units)
    max_drawdown_p95: float      # 95th-pctl worst peak-to-trough — the tail we bound
    sharpe_ci_low: float
    sharpe_ci_high: float
    observed_total: float
    observed_sharpe: float

    def passes(
        self,
        *,
        min_p_positive: float = 0.95,
        max_dd_p95: float = 0.25,
        min_trades: int = 30,
    ) -> bool:
        """Capital-preservation gate (R4): require a high probability the edge is positive
        AND a bounded 95th-pctl drawdown. Deliberately NOT a Sharpe target — risk-optimal,
        not profit-optimal."""
        if self.n_trades < min_trades:
            return False
        return self.p_total_positive >= min_p_positive and self.max_drawdown_p95 <= max_dd_p95


def monte_carlo_trade_sequence(
    rets,
    *,
    block_len: int = 5,
    n_resamples: int = 2000,
    seed: int = 7,
) -> MonteCarloResult:
    """Block-bootstrap a per-trade return list into a MonteCarloResult."""
    r = np.asarray(rets, dtype=float)
    n = r.size
    if n == 0:
        return MonteCarloResult(0, 0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
    paths = block_bootstrap(r, block_len, n_resamples, seed)
    if paths.size == 0:
        return MonteCarloResult(int(n), 0, 0.0, 0.0, 0.0, 0.0, 0.0, float(r.sum()), float(sharpe(r)))
    totals = paths.sum(axis=1)
    dds = np.array([_max_drawdown(np.cumsum(p)) for p in paths])
    sharpes = np.array([sharpe(p) for p in paths])
    return MonteCarloResult(
        n_trades=int(n),
        n_resamples=int(paths.shape[0]),
        p_total_positive=float((totals > 0).mean()),
        max_drawdown_p50=float(np.percentile(dds, 50)),
        max_drawdown_p95=float(np.percentile(dds, 95)),
        sharpe_ci_low=float(np.percentile(sharpes, 2.5)),
        sharpe_ci_high=float(np.percentile(sharpes, 97.5)),
        observed_total=float(r.sum()),
        observed_sharpe=float(sharpe(r)),
    )
