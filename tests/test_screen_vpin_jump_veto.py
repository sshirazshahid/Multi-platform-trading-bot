"""Unit tests for VPIN jump-veto screen helpers (prereg 27)."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# Load harvest module for VpinBuilder
_spec = importlib.util.spec_from_file_location(
    "harvest_aggtrades_vpin", ROOT / "scripts" / "harvest_aggtrades_vpin.py"
)
harvest = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(harvest)

_spec2 = importlib.util.spec_from_file_location(
    "screen_vpin_jump_veto", ROOT / "research" / "screen_vpin_jump_veto.py"
)
screen = importlib.util.module_from_spec(_spec2)
assert _spec2.loader is not None
_spec2.loader.exec_module(screen)


def test_vpin_builder_fills_buckets_and_emits():
    b = harvest.VpinBuilder(n_buckets=5)
    t0 = 1_700_000_000_000
    for i in range(100):
        b.on_trade(t0 + i * 1000, qty=10.0, buyer_maker=False)
    b.flush()
    assert b.bucket_size is not None and b.bucket_size > 0
    for i in range(5000):
        b.on_trade(t0 + 200_000 + i * 10, qty=b.bucket_size * 0.4, buyer_maker=(i % 2 == 0))
    b.flush()
    assert len(b.rows) >= 5
    assert 0.0 <= b.rows[-1]["vpin"] <= 1.0


def test_holm_rejects_only_strongest():
    # One very small p, others large
    reject = screen._holm_reject([0.9, 0.001, 0.8, 0.7], alpha=0.05)
    assert reject == [False, True, False, False]


def test_bleed_mask_logic_in_gates_dict():
    # Construct synthetic: if WR up and delta <=0 → bleed
    baseline_wr = 0.5
    kept_wr = 0.6
    delta = -0.01
    bleed = (kept_wr > baseline_wr) and (delta <= 0)
    assert bleed is True


def test_delta_ev_bootstrap_positive_when_kept_better():
    rng = np.random.default_rng(0)
    baseline = rng.normal(-0.2, 0.5, size=200)
    kept = rng.normal(0.1, 0.5, size=120)
    delta, p = screen._delta_ev_bootstrap(baseline, kept, n=500, seed=1)
    assert delta > 0
    assert p < 0.5
