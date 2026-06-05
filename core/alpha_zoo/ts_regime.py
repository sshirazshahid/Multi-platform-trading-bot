"""Time-series regime gate for single market-wide signals (e.g. stablecoin-supply growth).

The cross-sectional IC frame is mis-specified for ONE market-wide variable, so this gate tests
whether a regime signal predicts a market forward return over time — with the same frozen rigor:

  * a high rank-correlation t-stat (|t| >= 3.5, the post-multiple-testing bar, not 2.0),
  * a label-shuffle null the real |rho| must exceed by >= 3.5 sigma (so it isn't spurious), and
  * sign-consistent predictive power in BOTH halves of the sample (OOS stability).

Honest prior for slow macro overlays: NO_EDGE / regime-only. Deterministic given ``seed``.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from scipy.stats import spearmanr


def _rho(a, b) -> float:
    r = spearmanr(a, b)[0]
    return 0.0 if r is None or np.isnan(r) else float(r)


def timeseries_regime_gate(signal, fwd, *, n_shuffles=500, seed=0, min_abs_t=3.5, min_obs=30,
                           step=1):
    """Return a verdict dict for whether ``signal`` predicts ``fwd`` (aligned by index).

    ``step``: subsample to every ``step``-th aligned observation BEFORE testing. Set this to the
    forward-return horizon ``h`` when ``fwd`` is an h-day overlapping forward return — overlapping
    returns are autocorrelated, which inflates both the t-stat (effective N is ~n/h, not n) and
    deflates the shuffle null, manufacturing false positives. Non-overlapping subsampling makes
    the observations independent so the t-stat and shuffle null are honest. Default 1 (no overlap).

    Keys: n, rho, t, z_vs_null, null_mean_abs_rho, null_std_abs_rho, rho_first_half,
    rho_second_half, both_halves, passes, reason.
    """
    df = pd.concat([pd.Series(signal).rename("s"), pd.Series(fwd).rename("f")], axis=1).dropna()
    if int(step) > 1:
        df = df.iloc[:: int(step)]
    n = len(df)
    base = {
        "n": n, "rho": float("nan"), "t": float("nan"), "z_vs_null": float("nan"),
        "null_mean_abs_rho": float("nan"), "null_std_abs_rho": float("nan"),
        "rho_first_half": float("nan"), "rho_second_half": float("nan"),
        "both_halves": False, "passes": False, "n_shuffles": int(n_shuffles),
    }
    if n < int(min_obs):
        return {**base, "reason": "insufficient_obs"}

    x = df["s"].to_numpy()
    y = df["f"].to_numpy()
    rho = _rho(x, y)
    t = abs(rho) * np.sqrt(max(n - 2, 1)) / np.sqrt(max(1.0 - rho * rho, 1e-12))

    rng = np.random.default_rng(seed)
    null = np.empty(int(n_shuffles), dtype=float)
    for k in range(int(n_shuffles)):
        yp = y.copy()
        rng.shuffle(yp)
        null[k] = abs(_rho(x, yp))
    null_mu = float(np.nanmean(null)) if null.size else 0.0
    null_sd = float(np.nanstd(null)) if null.size else 0.0
    z_vs_null = (abs(rho) - null_mu) / (null_sd + 1e-12)

    h = n // 2
    r1 = _rho(x[:h], y[:h])
    r2 = _rho(x[h:], y[h:])
    both_halves = bool(
        r1 != 0.0 and r2 != 0.0 and np.sign(r1) == np.sign(r2) and np.sign(r1) == np.sign(rho)
    )

    passes = bool(t >= min_abs_t and z_vs_null >= min_abs_t and both_halves)
    return {
        **base, "rho": rho, "t": float(t), "z_vs_null": float(z_vs_null),
        "null_mean_abs_rho": null_mu, "null_std_abs_rho": null_sd,
        "rho_first_half": float(r1), "rho_second_half": float(r2),
        "both_halves": both_halves, "passes": passes, "reason": "ok",
    }
