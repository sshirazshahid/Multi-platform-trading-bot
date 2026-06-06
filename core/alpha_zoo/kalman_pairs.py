"""Kalman-filter pairs trading — falsification primitives (WS7-A, 2026-06-06).

A user-requested strategy template. The honest prior is NO_EDGE: crypto cointegration is unstable
and breaks in exactly the regimes you'd trade, and the spread is thin versus two-legged fees on a
small book. This module gives the *deterministic, causal* pieces; `scripts/run_kalman_pairs_screen.py`
runs them through the SAME frozen statistical stack as every other edge claim (`core.alpha_zoo.screen`
+ `core.stat_tests`: DSR, PBO, FDR) so the verdict is trustworthy either way.

Design notes that keep the screen honest:
  * The dynamic hedge ratio is a slow forward Kalman filter (state = [beta, intercept], random-walk
    transition). It is strictly causal — beta_t uses only observations up to t.
  * The strategy trades a CAUSAL rolling z-score of the spread LEVEL ``log_y - beta_t*log_x``, NOT
    the filter's standardized innovation. (The innovation collapses to ~Δspread once beta locks in —
    corr(innovation, spread) ~ 0.16 vs corr(level, spread) ~ 0.83 — so the level is the right signal.)
    The filter's ``z = e_t / sqrt(S_t)`` is retained only as a causal diagnostic.
  * Strategy returns trade the long-y / short-(beta·x) spread, realize P&L on the NEXT bar, and pay
    a two-legged fee on position-change turnover. Pure numpy; no network, no live account.
"""
from __future__ import annotations

import numpy as np


def kalman_hedge_ratio(y, x, *, delta: float = 1e-7, obs_var: float = 1.0) -> dict:
    """Dynamic hedge ratio via a 2-state forward Kalman filter (Chan parameterization).

    State s_t = [beta_t, intercept_t], random-walk transition with process covariance
    Q = delta/(1-delta) * I; observation y_t = [x_t, 1] · s_t + noise, var = ``obs_var``.

    Defaults are deliberately conservative (slow ``delta``, ``obs_var`` ~ spread scale) so the
    filter tracks only the SLOW drift of the hedge ratio and does NOT absorb the mean-reverting
    spread into the state — the spread must stay in the LEVEL ``y - beta*x``, which is what the
    strategy trades. (A tiny obs_var makes the filter over-trust each bar and explain the spread
    away, leaving only Δspread in the innovation — empirically corr(innovation, spread) ~ 0.16 vs
    corr(level, spread) ~ 0.83.)

    Returns a dict of length-N arrays: ``beta``, ``intercept``, ``innovation`` (e_t),
    ``innovation_std`` (sqrt(S_t)), ``z`` (e_t / sqrt(S_t)). All strictly causal in t.
    """
    y = np.asarray(y, dtype=float)
    x = np.asarray(x, dtype=float)
    n = y.size
    beta = np.full(n, np.nan)
    intercept = np.full(n, np.nan)
    innov = np.full(n, np.nan)
    innov_std = np.full(n, np.nan)
    z = np.full(n, np.nan)

    q = float(delta) / (1.0 - float(delta))
    Q = np.eye(2) * q
    R = float(obs_var)

    state = np.zeros(2)        # [beta, intercept], diffuse start
    P = np.eye(2) * 1.0        # moderate initial uncertainty -> fast early adaptation
    for t in range(n):
        H = np.array([x[t], 1.0])
        # predict (F = I for a random walk)
        P_pred = P + Q
        # innovation using the PRIOR state (causal)
        e = y[t] - H @ state
        S = float(H @ P_pred @ H + R)
        K = (P_pred @ H) / S
        # update
        state = state + K * e
        P = P_pred - np.outer(K, H) @ P_pred

        beta[t] = state[0]
        intercept[t] = state[1]
        innov[t] = e
        innov_std[t] = np.sqrt(S)
        z[t] = e / np.sqrt(S)

    return {"beta": beta, "intercept": intercept, "innovation": innov,
            "innovation_std": innov_std, "z": z}


def rolling_zscore(e, window: int = 100, min_obs: int = 20) -> np.ndarray:
    """Causal rolling z-score of a series: (e_t - mean) / std over the last ``window`` values.

    Uses ONLY observations up to and including t (e_t is known at decision time t), so it is
    look-ahead-free. NaN until ``min_obs`` values have accumulated or when the window std is 0.
    """
    e = np.asarray(e, dtype=float)
    n = e.size
    z = np.full(n, np.nan)
    for t in range(n):
        lo = max(0, t - int(window) + 1)
        win = e[lo:t + 1]
        win = win[np.isfinite(win)]
        if win.size >= int(min_obs):
            sd = win.std(ddof=1)
            if sd > 0:
                z[t] = (e[t] - win.mean()) / sd
    return z


def pair_strategy_returns(log_y, log_x, *, entry_z: float = 2.0, exit_z: float = 0.5,
                          fee: float = 0.0008, delta: float = 1e-7, obs_var: float = 1.0,
                          z_window: int = 100, warmup: int = 50) -> np.ndarray:
    """Per-bar net return of a causal Kalman mean-reversion strategy on one pair.

    Inputs are LOG prices. The Kalman gives a slow dynamic hedge ratio beta_t; we trade a CAUSAL
    rolling z-score of the spread LEVEL ``log_y - beta_t*log_x`` (beta_t uses only data up to t):
    flat -> short-spread when z > entry_z, long-spread when z < -entry_z; exit toward z ~ 0
    (|z| < exit_z). The position decided at close of bar t is held into t+1; P&L realizes then.

    Spread P&L per bar = pos · [Δlog_y - beta·Δlog_x]; fee charged on turnover, scaled by both
    legs (1 + |beta|). Returns a length-N array (r[0]=0); deterministic given inputs.
    """
    ly = np.asarray(log_y, dtype=float)
    lx = np.asarray(log_x, dtype=float)
    n = ly.size
    kf = kalman_hedge_ratio(ly, lx, delta=delta, obs_var=obs_var)
    beta = kf["beta"]
    spread = ly - beta * lx  # causal spread LEVEL (beta_t uses obs up to t)
    z = rolling_zscore(spread, window=z_window)

    pos = np.zeros(n)
    cur = 0.0
    for t in range(n):
        if t < int(warmup) or not np.isfinite(z[t]):
            pos[t] = cur  # hold through warmup gaps (cur is 0 until warmup ends)
            continue
        if cur == 0.0:
            if z[t] > entry_z:
                cur = -1.0
            elif z[t] < -entry_z:
                cur = 1.0
        elif cur > 0.0:       # long spread -> exit once reverted up past -exit_z
            if z[t] > -exit_z:
                cur = 0.0
        else:                 # short spread -> exit once reverted down past exit_z
            if z[t] < exit_z:
                cur = 0.0
        pos[t] = cur

    r = np.zeros(n)
    prev_pos = 0.0
    for t in range(1, n):
        sc = (ly[t] - ly[t - 1]) - beta[t - 1] * (lx[t] - lx[t - 1])
        cost = float(fee) * abs(pos[t - 1] - prev_pos) * (1.0 + abs(beta[t - 1]))
        r[t] = pos[t - 1] * sc - cost
        prev_pos = pos[t - 1]
    return r


def cointegration_pvalue(y, x) -> float:
    """Engle-Granger cointegration p-value (statsmodels). Lower = more cointegrated."""
    from statsmodels.tsa.stattools import coint

    y = np.asarray(y, dtype=float)
    x = np.asarray(x, dtype=float)
    return float(coint(y, x)[1])


def half_life(spread) -> float:
    """Ornstein-Uhlenbeck mean-reversion half-life (bars). inf if not mean-reverting."""
    s = np.asarray(spread, dtype=float)
    ds = s[1:] - s[:-1]
    lag = s[:-1]
    A = np.column_stack([np.ones_like(lag), lag])
    b = np.linalg.lstsq(A, ds, rcond=None)[0][1]
    if b >= 0:
        return float("inf")
    return float(-np.log(2.0) / b)
