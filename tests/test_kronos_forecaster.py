"""CI-safe tests for the Kronos adapter (no torch / no weights required).

These verify the isolation contract (importing the module must NOT import torch)
and the pure forecast math. The model itself is exercised by the eval scripts
(scripts/kronos_*.py), which need GPU + weights and are not part of CI.
"""

from __future__ import annotations

import subprocess
import sys

import numpy as np
import pytest


def test_import_does_not_pull_in_torch():
    """Importing the adapter must be cheap + side-effect-free (no torch at module top).

    Run in a subprocess so the check is hermetic — never mutates the shared
    interpreter's sys.modules (which would contaminate other tests).
    """
    code = (
        "import sys; import core.kronos_forecaster; "
        "assert 'torch' not in sys.modules, 'torch imported at module level'"
    )
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert r.returncode == 0, f"adapter import pulled in torch / failed:\n{r.stderr}"


def test_forecast_math():
    from core.kronos_forecaster import Forecast

    f = Forecast(
        entry_close=100.0,
        pred_close=101.0,
        exp_ret=0.01,
        p_up=0.88,
        horizon=4,
        pred_path=np.array([100.2, 100.5, 100.8, 101.0]),
    )
    assert f.direction == 1
    assert f.exp_ret_at(1) == pytest.approx(0.002)
    assert f.exp_ret_at(4) == pytest.approx(0.01)

    down = Forecast(
        entry_close=100.0,
        pred_close=99.0,
        exp_ret=-0.01,
        p_up=0.12,
        horizon=2,
        pred_path=np.array([99.5, 99.0]),
    )
    assert down.direction == -1
    assert down.exp_ret_at(2) == pytest.approx(-0.01)


def test_singleton_and_availability_is_boolean():
    from core.kronos_forecaster import MODEL_VERSION, get_kronos_forecaster

    a = get_kronos_forecaster()
    b = get_kronos_forecaster()
    assert a is b, "get_kronos_forecaster must return a process-wide singleton"
    assert isinstance(a.available, bool)
    assert MODEL_VERSION == "kronos-mini-2k"


def test_forecast_requires_price_columns():
    """forecast() must reject frames missing OHLC (before touching torch)."""
    import pandas as pd

    from core.kronos_forecaster import KronosForecaster

    fc = KronosForecaster()
    fc._predictor = object()  # bypass load() so we test validation, not the model
    with pytest.raises(ValueError):
        fc.forecast(pd.DataFrame({"close": [1, 2, 3]}))
