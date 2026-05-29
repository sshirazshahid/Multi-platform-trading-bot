"""Tests for core.garch_vol — GARCH(1,1) vol forecaster (Phase 2.3)."""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import numpy as np
import pytest

pytest.importorskip("arch")
pytest.importorskip("joblib")

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.garch_vol import GarchVol  # noqa: E402

# -- Helpers ---------------------------------------------------------------

def _simulate_garch(
    n: int,
    omega: float,
    alpha: float,
    beta: float,
    seed: int = 42,
    burn: int = 500,
) -> np.ndarray:
    """Simulate returns from a GARCH(1,1) DGP with N(0,1) innovations."""
    rng = np.random.default_rng(seed)
    total = n + burn
    eps = np.zeros(total)
    sigma2 = np.zeros(total)
    sigma2[0] = omega / max(1.0 - alpha - beta, 1e-8)
    z = rng.standard_normal(total)
    for t in range(1, total):
        sigma2[t] = omega + alpha * eps[t - 1] ** 2 + beta * sigma2[t - 1]
        eps[t] = np.sqrt(sigma2[t]) * z[t]
    return eps[burn:]


# -- Tests -----------------------------------------------------------------


def test_param_recovery_on_known_garch():
    """Known-GARCH series → fitted parameters within tolerance of truth."""
    omega_true, alpha_true, beta_true = 1e-6, 0.05, 0.93
    returns = _simulate_garch(2000, omega_true, alpha_true, beta_true, seed=1)

    g = GarchVol().fit(returns)
    params = g.params()

    # arch package returns omega, alpha[1], beta[1]
    omega_hat = float(params["omega"])
    alpha_hat = float(params["alpha[1]"])
    beta_hat = float(params["beta[1]"])

    assert abs(omega_hat - omega_true) / omega_true < 0.20, (
        f"omega: hat={omega_hat:.3e} true={omega_true:.3e}"
    )
    assert abs(alpha_hat - alpha_true) < 0.02, (
        f"alpha: hat={alpha_hat:.4f} true={alpha_true:.4f}"
    )
    assert abs(beta_hat - beta_true) < 0.05, (
        f"beta: hat={beta_hat:.4f} true={beta_true:.4f}"
    )


def test_forecast_beats_naive_mse():
    """1-step GARCH forecast beats naive last-realized-vol by >=10% MSE."""
    omega_true, alpha_true, beta_true = 1e-6, 0.05, 0.93
    returns = _simulate_garch(2000, omega_true, alpha_true, beta_true, seed=2)

    holdout = 100
    train_end = len(returns) - holdout

    garch_vol_forecasts = []
    naive_vol_forecasts = []
    realized_vol = []

    # Walk-forward: refit window of last train_end returns at each step.
    # To keep test cheap, fit once on the train portion and roll the
    # conditional variance recursion forward using fitted params.
    g = GarchVol().fit(returns[:train_end])
    params = g.params()
    omega_h = float(params["omega"])
    alpha_h = float(params["alpha[1]"])
    beta_h = float(params["beta[1]"])

    # In-sample sigma^2 series gives us the last conditional variance.
    sigma2 = float(g.conditional_volatility()[-1] ** 2)
    last_eps = float(returns[train_end - 1])

    for i in range(holdout):
        # GARCH 1-step ahead variance forecast
        sigma2_next = omega_h + alpha_h * last_eps**2 + beta_h * sigma2
        garch_sigma_next = np.sqrt(sigma2_next)
        garch_vol_forecasts.append(garch_sigma_next)

        # Naive: yesterday's |return| as vol proxy (single-bar realized vol)
        naive_vol_forecasts.append(abs(last_eps))

        # Realize next bar
        actual = float(returns[train_end + i])
        realized_vol.append(abs(actual))

        # Roll state forward
        sigma2 = sigma2_next
        last_eps = actual

    g_arr = np.array(garch_vol_forecasts)
    n_arr = np.array(naive_vol_forecasts)
    r_arr = np.array(realized_vol)

    mse_garch = float(np.mean((g_arr - r_arr) ** 2))
    mse_naive = float(np.mean((n_arr - r_arr) ** 2))

    assert mse_garch < mse_naive * 0.90, (
        f"GARCH MSE {mse_garch:.3e} not <= 90% of naive {mse_naive:.3e}"
    )


def test_conditional_volatility_positive():
    """In-sample conditional vol must be > 0 everywhere."""
    returns = _simulate_garch(1000, 1e-6, 0.05, 0.93, seed=3)
    g = GarchVol().fit(returns)
    cv = g.conditional_volatility()
    assert cv.shape[0] == returns.shape[0]
    assert np.all(cv > 0), f"Found non-positive vol: min={cv.min()}"
    assert np.all(np.isfinite(cv))


def test_forecast_horizon_one():
    """forecast(horizon=1) returns shape (1,)."""
    returns = _simulate_garch(1000, 1e-6, 0.05, 0.93, seed=4)
    g = GarchVol().fit(returns)
    out = g.forecast(horizon=1)
    assert isinstance(out, np.ndarray)
    assert out.shape == (1,)
    assert out[0] > 0
    assert np.isfinite(out[0])


def test_forecast_horizon_n():
    """forecast(horizon=N) returns shape (N,)."""
    returns = _simulate_garch(1000, 1e-6, 0.05, 0.93, seed=5)
    g = GarchVol().fit(returns)
    n = 10
    out = g.forecast(horizon=n)
    assert out.shape == (n,)
    assert np.all(out > 0)
    assert np.all(np.isfinite(out))


def test_save_load_roundtrip():
    """save/load preserves forecast values."""
    returns = _simulate_garch(1000, 1e-6, 0.05, 0.93, seed=6)
    g = GarchVol().fit(returns)
    fc1 = g.forecast(horizon=5)

    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "garch.joblib"
        g.save(path)
        assert path.exists()
        g2 = GarchVol.load(path)

    fc2 = g2.forecast(horizon=5)
    np.testing.assert_allclose(fc1, fc2, rtol=1e-10, atol=1e-12)


def test_empty_returns_raises():
    """Empty / too-short returns raises ValueError."""
    g = GarchVol()
    with pytest.raises(ValueError):
        g.fit(np.array([], dtype=float))
    with pytest.raises(ValueError):
        g.fit(np.array([0.001, -0.002, 0.003], dtype=float))


def test_forecast_before_fit_raises():
    """Calling forecast() before fit() must raise."""
    g = GarchVol()
    with pytest.raises(RuntimeError):
        g.forecast(horizon=1)
    with pytest.raises(RuntimeError):
        g.conditional_volatility()


def test_reproducibility():
    """Fitting twice on identical data yields identical forecasts."""
    returns = _simulate_garch(1500, 1e-6, 0.05, 0.93, seed=7)

    g1 = GarchVol().fit(returns)
    g2 = GarchVol().fit(returns.copy())

    fc1 = g1.forecast(horizon=5)
    fc2 = g2.forecast(horizon=5)

    np.testing.assert_allclose(fc1, fc2, rtol=1e-8, atol=1e-10)
