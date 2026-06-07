"""Tests for volatility-targeting position sizing (causal + behaviour)."""
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from core.alpha_zoo.bias_checks import lookahead_violations  # noqa: E402
from vol_targeting_walkforward import vol_target_position  # noqa: E402


def test_position_is_causal():
    rng = np.random.default_rng(0)
    ret = rng.normal(0, 0.01, 500)
    fn = lambda r: vol_target_position(np.asarray(r, dtype=float), 40)  # noqa: E731
    assert lookahead_violations(fn, ret, check_indices=range(400, 490)) == []


def test_lower_exposure_in_high_vol_regime():
    # calm first half (0.5% daily vol), turbulent second half (3% daily vol)
    rng = np.random.default_rng(1)
    ret = np.concatenate([rng.normal(0, 0.005, 300), rng.normal(0, 0.03, 300)])
    pos = vol_target_position(ret, 40, target_vol=0.15, cap=2.0)
    assert pos[280] > pos[560]            # exposure cut in the turbulent regime
    assert pos[560] < 1.0                 # high vol -> de-risked below full


def test_clipped_to_cap_and_nonneg():
    ret = np.full(300, 0.0)               # zero vol -> target/0 -> inf -> clipped to cap
    pos = vol_target_position(ret, 40, cap=2.0)
    assert pos.max() <= 2.0 + 1e-9 and pos.min() >= 0.0


def test_vol_targeting_reduces_realized_vol_dispersion():
    # the vol-targeted return stream should have LOWER realized vol than the raw stream
    # when the raw stream has strong vol clustering
    rng = np.random.default_rng(2)
    ret = np.concatenate([rng.normal(0, 0.004, 400), rng.normal(0, 0.025, 400)])
    pos = vol_target_position(ret, 40)
    vt = pos[:-1] * ret[1:]               # next-bar return scaled by prior position
    assert np.std(vt) < np.std(ret[1:])
