"""Offline tests for scripts/run_tv_regime_screen.py (frozen-gate dominance screen).

Synthetic series only — verifies the harness MACHINERY (causality, embargo,
costs, per-bar aggregation, FDR behavior on noise, planted-edge detection,
price-only control redundancy), not any market claim.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]


def _load():
    spec = importlib.util.spec_from_file_location(
        "tv_regime_screen", ROOT / "scripts" / "run_tv_regime_screen.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


SCR = _load()


def _dates(n):
    return pd.date_range("2020-01-01", periods=n, freq="D", tz="UTC")


def test_sig_ema_trend_directions():
    up = pd.Series(np.linspace(1, 2, 200))
    s = SCR.sig_ema_trend(up, direction=1)
    assert s.iloc[-1] == 1
    assert SCR.sig_ema_trend(up, direction=-1).iloc[-1] == -1


def test_sig_mom_z_flat_inside_band():
    rng = np.random.default_rng(7)
    flat = pd.Series(1000.0 + rng.normal(0, 0.01, 300))  # ~no drift -> |z| mostly < 1
    s = SCR.sig_mom_z(flat, k=14, direction=1)
    assert set(s.dropna().unique()).issubset({-1, 0, 1})
    assert (s.fillna(0) == 0).mean() > 0.5


def test_pos_returns_causal_shift():
    # position decided at t applies to the t+1 return, never t
    r = pd.Series([0.0, 0.10, 0.0, 0.0])
    pos = pd.Series([1, 0, 0, 0])
    pnl = SCR.pos_returns(pos, r, cost_bps=0)
    assert pnl.iloc[1] == 0.10  # pos[0] captures r[1]
    assert pnl.iloc[2] == 0.0


def test_pos_returns_cost_on_position_change():
    r = pd.Series([0.0, 0.0, 0.0, 0.0])
    pos = pd.Series([0, 1, 1, 1])
    pnl = SCR.pos_returns(pos, r, cost_bps=10)
    # entry traded at t=1 -> cost lands in the t=2 row (with the first held return)
    assert abs(pnl.iloc[2] - (-0.001)) < 1e-12
    assert pnl.iloc[3] == 0.0  # no further turnover


def test_ls_returns_market_neutral_legs():
    alt = pd.Series([0.0, 0.02, 0.02])
    btc = pd.Series([0.0, 0.01, 0.01])
    sig = pd.Series([1, 1, 1])  # alts over BTC
    pnl = SCR.ls_returns(sig, alt, btc, cost_bps=0)
    assert abs(pnl.iloc[1] - 0.005) < 1e-12  # 0.5*(alt-btc)


def test_split_masks_embargo():
    dates = _dates(100)
    is_m, oos_m = SCR.split_masks(dates, split_frac=0.6, embargo_d=2)
    assert is_m.sum() == 58  # 60 - embargo 2
    assert oos_m.sum() == 40
    assert not (is_m & oos_m).any()
    # the two embargoed days belong to neither side
    assert (~is_m & ~oos_m).sum() == 2


def test_per_bar_series_full_length_with_flat_zeros():
    r = pd.Series(np.zeros(50))
    pos = pd.Series([0] * 50)
    pnl = SCR.pos_returns(pos, r, cost_bps=10)
    assert len(pnl) == 50
    assert (pnl.fillna(0) == 0).all()


def test_planted_edge_detected():
    # btc next-day return follows the dominance EMA-trend signal -> harness must find it
    rng = np.random.default_rng(42)
    n = 1200
    dom = pd.Series(np.cumsum(rng.normal(0, 1.0, n)) + 100.0)
    sig = SCR.sig_ema_trend(dom, direction=-1)  # USDT.D mapping
    noise = rng.normal(0, 0.002, n)
    btc_ret = pd.Series(0.004 * sig.shift(1).fillna(0).values + noise)
    pnl = SCR.pos_returns(sig, btc_ret, cost_bps=10)
    dates = _dates(n)
    is_m, oos_m = SCR.split_masks(dates, SCR.SPLIT, SCR.EMBARGO_D)
    ev = SCR.eval_variant(pnl[is_m.values], pnl[oos_m.values], n_trials=SCR.N_TRIALS)
    assert ev["is_p"] < 0.001
    assert ev["oos_p"] < 0.05
    assert ev["oos_dsr"] > 0.9
    assert ev["oos_half1_mean"] > 0 and ev["oos_half2_mean"] > 0


def test_noise_grid_no_survivors():
    # An IS-lucky noise variant can FDR-reject by chance (~5% per 8-grid); the
    # screen's protection is the full gate CONJUNCTION (FDR + OOS p + DSR +
    # both OOS halves positive), which must yield zero survivors on pure noise.
    rng = np.random.default_rng(3)
    n = 1200
    dates = _dates(n)
    is_m, oos_m = SCR.split_masks(dates, SCR.SPLIT, SCR.EMBARGO_D)
    rows = []
    for _ in range(8):
        dom = pd.Series(np.cumsum(rng.normal(0, 1.0, n)) + 100.0)
        btc_ret = pd.Series(rng.normal(0, 0.03, n))
        sig = SCR.sig_ema_trend(dom, direction=-1)
        pnl = SCR.pos_returns(sig, btc_ret, cost_bps=10)
        rows.append(SCR.eval_variant(pnl[is_m.values], pnl[oos_m.values], n_trials=SCR.N_TRIALS))
    from core.alpha_zoo.screen import fdr_bh

    flags = fdr_bh([r["is_p"] for r in rows], q=0.05)
    survivors = [
        r
        for r, f in zip(rows, flags)
        if f
        and r["oos_p"] < 0.05
        and r["oos_dsr"] >= SCR.DSR_MIN
        and (r["oos_half1_mean"] or 0) > 0
        and (r["oos_half2_mean"] or 0) > 0
    ]
    assert survivors == []


def test_control_redundancy_flagged_for_mechanical_inverse():
    # dominance built as exactly 1/btc_close: the price-only control twin must
    # match it, so the variant may NOT claim novel information
    rng = np.random.default_rng(11)
    n = 800
    btc_close = pd.Series(20000.0 * np.exp(np.cumsum(rng.normal(0.0005, 0.03, n))))
    dom = 1.0 / btc_close
    btc_ret = btc_close.pct_change().fillna(0.0)
    sig_v = SCR.sig_ema_trend(dom, direction=-1)
    sig_c = SCR.sig_ema_trend(1.0 / btc_close, direction=-1)  # control twin (inverted price)
    pnl_v = SCR.pos_returns(sig_v, btc_ret, cost_bps=10)
    pnl_c = SCR.pos_returns(sig_c, btc_ret, cost_bps=10)
    dates = _dates(n)
    _, oos_m = SCR.split_masks(dates, SCR.SPLIT, SCR.EMBARGO_D)
    assert SCR.is_redundant(pnl_v[oos_m.values], pnl_c[oos_m.values])


def test_grid_is_preregistered_shape():
    # 8 variants, each with a declared control — the frozen grid must not grow
    assert len(SCR.VARIANTS) == 8
    for v in SCR.VARIANTS:
        assert v["name"] and v["series"] and v["control"]
    assert SCR.N_TRIALS == 17  # 8 (this grid) + 9 (stablecoin-flows prior family)
    assert SCR.DSR_MIN == 0.90
