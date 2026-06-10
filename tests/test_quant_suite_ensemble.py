"""Offline regression tests for the quant_suite engine, strategies, and safety.
Run: python tests/test_quant_suite_ensemble.py   (or pytest)."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import numpy as np
import pandas as pd

from quant_suite import bot_adapter as A
from quant_suite import (
    dca,
    execution,
    grid,
    hmm_regime,
    kalman_pairs,
    pca_factor_neutral,
    rebalance,
    strategies,
    vpin,
    xsec_meanrev,
)
from quant_suite import engine as E
from quant_suite import indicators as ind


def _df(n=900, seed=1, drift=0.0005):
    rng = np.random.default_rng(seed)
    close = 100 * np.cumprod(1 + rng.normal(drift, 0.01, n))
    high = close * (1 + np.abs(rng.normal(0, 0.004, n)))
    low = close * (1 - np.abs(rng.normal(0, 0.004, n)))
    op = np.concatenate([[close[0]], close[:-1]])
    vol = rng.uniform(1e6, 5e6, n)
    ts = np.arange(n) * 3600_000 + 1_600_000_000_000
    d = pd.DataFrame(
        {"ts": ts, "open": op, "high": high, "low": low, "close": close, "volume": vol}
    )
    d["dt"] = pd.to_datetime(d["ts"], unit="ms", utc=True)
    return d


def _panel(n=240, k=5, seed=2):
    rng = np.random.default_rng(seed)
    cols = {f"C{i}": 100 * np.cumprod(1 + rng.normal(0.0003, 0.012, n)) for i in range(k)}
    return pd.DataFrame(cols)


# ---- engine / safety (original) ----
def test_indicators_rsi_bounds():
    up = pd.Series(np.linspace(100, 200, 200))
    dn = pd.Series(np.linspace(200, 100, 200))
    assert round(ind.rsi(up).iloc[-1]) == 100 and round(ind.rsi(dn).iloc[-1]) == 0


def test_no_lookahead():
    d = _df(seed=3)
    full = E.prep(d, is_benchmark=True)
    k = 700
    trunc = E.prep(d.iloc[:k].reset_index(drop=True), is_benchmark=True)
    assert np.array_equal(
        np.nan_to_num(full["mf_vote"].values[60 : k - 12]),
        np.nan_to_num(trunc["mf_vote"].values[60 : k - 12]),
    )


def test_confluence_requires_agreement():
    d = E.prep(_df(seed=5, drift=0.001), is_benchmark=True)
    lo, sh = E._ok_arrays(d, "confluence")
    mf, rg, tr = d["mf_vote"].values, d["rg_vote"].values, d["trend"].values
    assert all(mf[i] == 1 and rg[i] == 1 and tr[i] for i in np.where(lo)[0])


def test_adapter_advisory_and_no_orders():
    assert A.PLACES_ORDERS is False
    import inspect

    import quant_suite.bot_adapter as m

    src = inspect.getsource(m)
    for f in ["create_order", "create_market", "create_limit", ".buy(", ".sell("]:
        assert f not in src


def test_adapter_refuses_controlled_live():
    os.environ["OPERATING_MODE"] = "CONTROLLED_LIVE"
    try:
        assert A.generate_setups(["BTC/USDT:USDT"]) == []
    finally:
        os.environ.pop("OPERATING_MODE", None)


def test_pure_price_default_on():
    assert A.PURE_PRICE is True  # bot ignores sentiment/fear/greed by default


# ---- new strategy modules ----
def test_hmm_recovers_states():
    rng = np.random.default_rng(0)
    a = np.cumprod(1 + rng.normal(0.001, 0.004, 300))
    b = a[-1] * np.cumprod(1 + rng.normal(0, 0.02, 300))
    r = hmm_regime.detect_regime(pd.DataFrame({"close": np.concatenate([a, b])}))
    assert (
        r["label"] in {"TREND_UP", "TREND_DOWN", "RANGE", "HIGH_VOL"} and 0 <= r["confidence"] <= 1
    )


def test_xsec_and_pca_panel():
    p = _panel()
    assert "periods" in xsec_meanrev.backtest(p)
    assert isinstance(xsec_meanrev.current_signals(p), dict)
    assert "periods" in pca_factor_neutral.backtest(p)


def test_kalman_runs():
    p = _panel()
    out = kalman_pairs.current(p["C0"].values, p["C1"].values)
    assert "zscore" in out and "signal" in out
    assert "trades" in kalman_pairs.backtest(p["C0"].values, p["C1"].values)


def test_vpin_gate():
    g = vpin.execution_gate(_df())
    assert "allow" in g and "throttle" in g


def test_execution_slices_conserve_qty():
    for algo, kw in [("twap", {"n_slices": 7}), ("iceberg", {"clip": 0.3})]:
        pl = execution.plan("buy", 1.0, algo, **kw)
        assert abs(sum(s["qty"] for s in pl["slices"]) - 1.0) < 1e-6
        assert pl["places_orders"] is False


def test_grid_dca_rebalance():
    assert "trades" in grid.backtest(_df())
    assert "buys" in dca.backtest(_df())
    out = rebalance.rebalance({"A": 60, "B": 40}, {"A": 0.5, "B": 0.5})
    assert out["rebalanced"] is True and out["places_orders"] is False


def test_registry_live_and_selection():
    cat = strategies.catalog()
    assert sum(1 for x in cat if x["status"] == "live") >= 10
    assert "confluence_trend" in strategies.select_for_regime("TREND_UP")


# ---- paper forward-test fill realism + state durability (2026-06-08 audit) ----
def test_paper_fill_applies_slippage_and_fees():
    import run_confluence_paper as P

    assert P.SLIP_OPEN > 0 and P.SLIP_SL >= P.SLIP_CLOSE > 0
    # long SL: realistic fill must be WORSE (more negative) than the naive exact-stop model
    naive_long = ((98.0 - 100.0) / 100.0 - 2 * P.FEE) * 1000
    real_long = P.realized_pnl("buy", 100.0, 98.0, "SL", 1000)
    assert real_long < naive_long
    # short SL (stop above entry) is also realistically worse than naive
    naive_short = ((100.0 - 102.0) / 100.0 - 2 * P.FEE) * 1000
    real_short = P.realized_pnl("sell", 100.0, 102.0, "SL", 1000)
    assert real_short < naive_short
    # a real TP still books a profit (costs honest, edge not artificially destroyed)
    assert P.realized_pnl("buy", 100.0, 105.0, "TP", 1000) > 0


# ---- funding/carry vote (Pine #4 port, 2026-06-10) ----
def _funding(d, rate):
    """8h-spaced funding settles covering d's span at a constant decimal/8h rate."""
    dts = pd.to_datetime(d["dt"], utc=True)
    idx = pd.date_range(dts.iloc[0].floor("8h"), dts.iloc[-1], freq="8h", tz="UTC")
    return pd.DataFrame({"dt": idx, "rate": rate})


def test_fc_vote_absent_funding_is_neutral():
    d = E.prep(_df(seed=7), is_benchmark=True)
    assert "fc_vote" in d.columns and (d["fc_vote"] == 0).all()
    sig = E.latest_signal(d, mode="carry")
    assert sig["side"] == 0 and sig["fc_vote"] == 0


def test_fc_vote_signs_and_trend_confirm():
    base = _df(seed=11, drift=0.003)
    # strongly negative funding (-0.2%/8h) + uptrend -> carry longs only
    d = E.prep(base, is_benchmark=True, funding=_funding(base, -0.002))
    assert (d["fc_vote"] == 1).any() and not (d["fc_vote"] == -1).any()
    m = d["fc_vote"] == 1
    assert (d.loc[m, "close"] > d.loc[m, "ema20"]).all()
    assert (d.loc[m, "ema20"] > d.loc[m, "ema50"]).all()
    assert (d.loc[m, "adx"] >= 18).all()
    # strongly positive funding + downtrend -> fade shorts only
    # (the no-opposite-vote halves here are structural: a constant funding sign
    #  can only ever arm one side of the threshold)
    dn = _df(seed=11, drift=-0.003)
    d2 = E.prep(dn, is_benchmark=True, funding=_funding(dn, 0.002))
    assert (d2["fc_vote"] == -1).any() and not (d2["fc_vote"] == 1).any()
    # sub-threshold funding (|0.04%|/8h < 0.05% default) -> no votes either side.
    # The negative case is the non-vacuous one: the uptrend frame DOES satisfy the
    # long trend-confirm on many bars, so only the threshold gate can block it.
    d3 = E.prep(base, is_benchmark=True, funding=_funding(base, 0.0004))
    assert (d3["fc_vote"] == 0).all()
    d4 = E.prep(base, is_benchmark=True, funding=_funding(base, -0.0004))
    assert (d4["fc_vote"] == 0).all()


def test_fc_vote_no_lookahead():
    base = _df(seed=13, drift=0.002)
    f = _funding(base, -0.002)
    full = E.prep(base, is_benchmark=True, funding=f)
    k = 700
    trunc = E.prep(
        base.iloc[:k].reset_index(drop=True),
        is_benchmark=True,
        funding=f[f["dt"] <= pd.to_datetime(base["dt"].iloc[k - 1], utc=True)],
    )
    assert np.array_equal(
        np.nan_to_num(full["fc_vote"].values[60 : k - 12]),
        np.nan_to_num(trunc["fc_vote"].values[60 : k - 12]),
    )


def test_fc_alignment_causal():
    # a funding flip at time T must not change votes on bars at/before T
    base = _df(seed=17, drift=0.002)
    f = _funding(base, -0.002)
    t_flip = f["dt"].iloc[len(f) // 2]
    f_flipped = f.copy()
    f_flipped.loc[f_flipped["dt"] > t_flip, "rate"] = 0.002
    a = E.prep(base, is_benchmark=True, funding=f)
    b = E.prep(base, is_benchmark=True, funding=f_flipped)
    pre = (pd.to_datetime(base["dt"], utc=True) <= t_flip).values
    assert np.array_equal(a.loc[pre, "fc_vote"].values, b.loc[pre, "fc_vote"].values)


def test_carry_mode_ok_arrays():
    base = _df(seed=11, drift=0.003)
    d = E.prep(base, is_benchmark=True, funding=_funding(base, -0.002))
    lo, sh = E._ok_arrays(d, "carry")
    assert np.array_equal(lo, d["fc_vote"].values == 1)
    assert np.array_equal(sh, d["fc_vote"].values == -1)


def test_fc_does_not_change_confluence_or_tier():
    base = _df(seed=5, drift=0.001)
    plain = E.prep(base, is_benchmark=True)
    fed = E.prep(base, is_benchmark=True, funding=_funding(base, -0.002))
    assert np.array_equal(plain["ens_long"].values, fed["ens_long"].values)
    assert np.array_equal(plain["ens_short"].values, fed["ens_short"].values)
    assert np.array_equal(plain["tier"].values, fed["tier"].values)


def test_funding_carry_registered_but_not_selectable():
    assert "funding_carry" in strategies.REGISTRY
    for label in ("TREND_UP", "TREND_DOWN", "RANGE", "HIGH_VOL"):
        assert "funding_carry" not in strategies.select_for_regime(label)


def test_funding_history_pagination_dedupe():
    from quant_suite import funding_carry as FC

    class FakeEx:
        def __init__(self):
            self.calls = 0

        def fetch_funding_rate_history(self, symbol, since=None, limit=1000):
            self.calls += 1
            rows = [{"timestamp": i * 8 * 3600_000, "fundingRate": "0.0001"} for i in range(5)]
            return [r for r in rows if r["timestamp"] >= since][:limit]

    ex = FakeEx()
    f = FC.fetch_funding_history(ex, "BTC/USDT:USDT", since_ms=0, limit=3, throttle_s=0)
    assert ex.calls == 2  # 3-row page (== limit) then a 2-row tail page
    assert list(f["ts"]) == [i * 8 * 3600 for i in range(5)]
    assert (f["rate"] == 0.0001).all()


def test_paper_save_state_atomic_roundtrip():
    import json as _json

    import run_confluence_paper as P

    orig = P.STATE
    P.STATE = P.ROOT / "data" / "_test_atomic_state.json"
    tmp = P.STATE.parent / (P.STATE.name + ".tmp")
    try:
        P.save_state({"balance": 4242.0, "open": []})
        got = _json.loads(P.STATE.read_text())
        assert got["balance"] == 4242.0 and "updated" in got
        assert not tmp.exists()  # no leftover temp file after atomic replace
    finally:
        for f in (P.STATE, tmp):
            if f.exists():
                f.unlink()
        P.STATE = orig


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    passed = 0
    for fn in fns:
        try:
            fn()
            passed += 1
            print(f"PASS  {fn.__name__}")
        except Exception as e:
            print(f"FAIL  {fn.__name__}: {e}")
    print(f"\n{passed}/{len(fns)} tests passed")
    raise SystemExit(0 if passed == len(fns) else 1)
