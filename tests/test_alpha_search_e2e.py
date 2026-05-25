# tests/test_alpha_search_e2e.py
"""End-to-end on a synthetic panel: a PLANTED momentum factor must clear the
gates; a PURE-NOISE alpha must not. Proves the pipeline discriminates."""
from __future__ import annotations

import numpy as np
import pandas as pd

from core.alpha_zoo import screen
from core.alpha_zoo.panel import build_panel, split_panel


def _panel_with_planted_signal(n=1200, n_sym=20, horizon=24, beta=1.0, seed=123):
    """Panel where 10-bar momentum linearly predicts the forward return:
    fwd_ret = beta * (per-bar z-scored 10-bar momentum) + unit-variance noise."""
    rng = np.random.default_rng(seed)
    syms = [f"S{i}/USDT" for i in range(n_sym)]
    raw = {}
    for sym in syms:
        ret = rng.normal(0, 0.01, n)
        close = 100 * np.exp(np.cumsum(ret))
        raw[sym] = pd.DataFrame({
            "ts": np.arange(1_700_000_000, 1_700_000_000 + n * 3600, 3600),
            "open": close, "high": close * 1.001, "low": close * 0.999,
            "close": close, "volume": rng.uniform(500, 1500, n),
        })
    p = build_panel(raw, timeframe="1h", horizon=horizon)
    mom = p.fields["close"].pct_change(10)
    z = mom.sub(mom.mean(axis=1), axis=0).div(mom.std(axis=1) + 1e-9, axis=0)
    noise = pd.DataFrame(rng.normal(0, 1.0, p.fwd_ret.shape),
                         index=p.fwd_ret.index, columns=p.symbols)
    planted = (beta * z + noise).where(z.notna())
    planted.iloc[-horizon:] = np.nan       # no forward label in the last horizon bars
    p.fwd_ret.iloc[:, :] = planted.values
    return p


def _momentum_signal(panel):
    return panel.fields["close"].pct_change(10)


def test_planted_factor_passes_gates():
    p = _panel_with_planted_signal()
    is_p, oos_p = split_panel(p, frac=0.6, embargo=24)

    ic = screen.cross_sectional_ic(_momentum_signal(is_p), is_p.fwd_ret, min_width=10)
    ir_v = screen.ir(ic)
    assert ir_v >= 0.5, f"planted IR too low: {ir_v}"

    sign = 1.0 if ir_v > 0 else -1.0
    r = screen.long_short_returns(_momentum_signal(oos_p), oos_p.fwd_ret,
                                  sign=sign, q=0.2, min_width=10)
    dsr = screen.dsr_for_returns(r, n_trials=50)
    assert dsr >= 0.10, f"planted DSR too low: {dsr}"


def test_pure_noise_is_rejected():
    p = _panel_with_planted_signal()
    is_p, _ = split_panel(p, frac=0.6, embargo=24)
    rng = np.random.default_rng(999)
    noise_sig = pd.DataFrame(rng.normal(0, 1, is_p.fields["close"].shape),
                             index=is_p.fields["close"].index, columns=is_p.symbols)
    ir_noise = screen.ir(
        screen.cross_sectional_ic(noise_sig, is_p.fwd_ret, min_width=10))
    assert abs(ir_noise) < 0.5, f"noise wrongly survived: IR={ir_noise}"
