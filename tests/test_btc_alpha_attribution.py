"""Beta-adjusted alpha-vs-BTC labeling — pure-formula, interpolation, and schema
migration tests.

The point of the label: separate genuine entry alpha from BTC market beta, so the
learning substrate cannot mistake a net-short book riding a BTC dump for "edge"
(the documented 2026-06-04 failure mode where a profitable short was pure beta).

Pure functions are importlib-loaded directly to bypass the heavy core/__init__.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load(modname: str, relpath: str):
    spec = importlib.util.spec_from_file_location(modname, ROOT / relpath)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[modname] = mod
    spec.loader.exec_module(mod)
    return mod


BA = _load("btc_alpha_test_copy", "core/btc_alpha.py")


# ── Formula ───────────────────────────────────────────────────────────────

def test_alpha_equals_pnl_when_btc_flat():
    """BTC return 0 over the window -> market component vanishes -> alpha == whole pnl."""
    out = BA.compute_alpha_vs_btc(
        realized_pnl=10.0, partial_realized_pnl=0.0,
        entry_px=100.0, size=1.0, side="buy",
        btc_entry=60000.0, btc_exit=60000.0,
    )
    assert out is not None
    alpha, btc_ret, beta = out
    assert btc_ret == pytest.approx(0.0)
    assert alpha == pytest.approx(10.0)
    assert beta == 1.0


def test_short_during_btc_decline_has_negative_alpha():
    """THE failure mode: a SHORT that profited only because BTC fell -13% should
    show NEGATIVE alpha even though realized PnL is positive."""
    # notional=100, realized +10 (=+10% of notional); BTC -13% over the hold.
    out = BA.compute_alpha_vs_btc(
        realized_pnl=10.0, partial_realized_pnl=0.0,
        entry_px=100.0, size=1.0, side="sell",
        btc_entry=60000.0, btc_exit=60000.0 * 0.87,
    )
    assert out is not None
    alpha, btc_ret, _ = out
    assert btc_ret == pytest.approx(-0.13)
    # market_component = side(-1) * beta(1) * btc_ret(-0.13) = +0.13 of notional = +13
    # alpha = 10 - 13 = -3
    assert alpha == pytest.approx(-3.0)
    assert alpha < 10.0  # below raw PnL — beta is stripped out


def test_long_riding_btc_rally_alpha_below_pnl():
    """A LONG that profited during a BTC rally also gives up the beta portion."""
    out = BA.compute_alpha_vs_btc(
        realized_pnl=10.0, partial_realized_pnl=0.0,
        entry_px=100.0, size=1.0, side="buy",
        btc_entry=60000.0, btc_exit=60000.0 * 1.13,
    )
    alpha, btc_ret, _ = out
    assert btc_ret == pytest.approx(0.13)
    assert alpha == pytest.approx(-3.0)  # 10 - (+1*0.13*100)


def test_partial_tp_uses_whole_trade_economics():
    """whole_pnl must include partial_realized_pnl, not just the runner leg."""
    out = BA.compute_alpha_vs_btc(
        realized_pnl=1.0, partial_realized_pnl=0.5,
        entry_px=100.0, size=1.0, side="buy",
        btc_entry=60000.0, btc_exit=60000.0,
    )
    alpha, _, _ = out
    assert alpha == pytest.approx(1.5)  # 1.0 + 0.5, not 1.0


def test_none_on_missing_or_bad_inputs():
    base = dict(realized_pnl=10.0, partial_realized_pnl=0.0,
                entry_px=100.0, size=1.0, side="buy",
                btc_entry=60000.0, btc_exit=60000.0)
    assert BA.compute_alpha_vs_btc(**{**base, "btc_exit": None}) is None
    assert BA.compute_alpha_vs_btc(**{**base, "btc_entry": None}) is None
    assert BA.compute_alpha_vs_btc(**{**base, "btc_entry": 0.0}) is None
    assert BA.compute_alpha_vs_btc(**{**base, "entry_px": 0.0}) is None
    assert BA.compute_alpha_vs_btc(**{**base, "size": None}) is None


# ── Interpolation ──────────────────────────────────────────────────────────

def _btc_df():
    return pd.DataFrame({"ts": [0, 900, 1800], "close": [100.0, 110.0, 120.0]})


def test_interp_midpoint():
    assert BA.interp_btc_price(_btc_df(), 450) == pytest.approx(105.0)


def test_interp_on_bar():
    assert BA.interp_btc_price(_btc_df(), 900) == pytest.approx(110.0)


def test_interp_after_last_uses_last_close():
    assert BA.interp_btc_price(_btc_df(), 5000) == pytest.approx(120.0)


def test_interp_before_first_is_none():
    assert BA.interp_btc_price(_btc_df(), -100) is None


def test_interp_empty_is_none():
    assert BA.interp_btc_price(pd.DataFrame({"ts": [], "close": []}), 100) is None


# ── Schema migration ───────────────────────────────────────────────────────

def test_migration_adds_alpha_columns(tmp_path):
    wh_mod = _load("warehouse_alpha_test_copy", "core/warehouse.py")
    db = tmp_path / "t.sqlite"
    wh = wh_mod.Warehouse(path=db)
    cols = {r["name"] for r in wh.query("PRAGMA table_info(trades)")}
    assert {"alpha_vs_btc", "btc_ret_window", "beta_used"} <= cols
    # idempotent: re-opening the same DB must not raise
    wh2 = wh_mod.Warehouse(path=db)
    cols2 = {r["name"] for r in wh2.query("PRAGMA table_info(trades)")}
    assert {"alpha_vs_btc", "btc_ret_window", "beta_used"} <= cols2


def test_update_btc_alpha_roundtrip(tmp_path):
    wh_mod = _load("warehouse_alpha_rt_copy", "core/warehouse.py")
    wh = wh_mod.Warehouse(path=tmp_path / "t.sqlite")
    cid = wh.record_candidate(
        exchange="binance", symbol="ETH/USDT", side="sell",
        decision="TAKEN", features={},
    )
    tid = wh.record_trade_open(
        exchange="binance", symbol="ETH/USDT", side="sell",
        ts_entry=1000.0, entry_px=100.0, size=1.0, leverage=2,
        candidate_id=cid, mode="CONTROLLED_LIVE",
    )
    assert wh.update_btc_alpha([(-3.0, -0.13, 1.0, tid)]) == 1
    assert wh.update_btc_alpha([]) == 0  # empty is a no-op
    row = wh.query(
        "SELECT alpha_vs_btc, btc_ret_window, beta_used FROM trades WHERE id=?", (tid,)
    )[0]
    assert row["alpha_vs_btc"] == pytest.approx(-3.0)
    assert row["btc_ret_window"] == pytest.approx(-0.13)
    assert row["beta_used"] == 1.0


# ── In-process labeler (learning-cycle hook) ───────────────────────────────

class _FakeWH:
    def __init__(self, rows):
        self._rows = rows
        self.written: list = []

    def query(self, sql, params=None):
        return self._rows

    def update_btc_alpha(self, updates):
        u = list(updates)
        self.written.extend(u)
        return len(u)


def test_backfill_unlabeled_writes_btc_flat():
    rows = [dict(id=1, ts_entry=450, ts_exit=1350, entry_px=100.0, exit_px=110.0,
                 size=1.0, side="buy", realized_pnl=10.0, partial_realized_pnl=0.0)]
    df = pd.DataFrame({"ts": [0, 900, 1800], "close": [100.0, 100.0, 100.0]})  # BTC flat
    wh = _FakeWH(rows)
    n, cand = BA.backfill_unlabeled(wh, lambda s, tf, a, b: df)
    assert (n, cand) == (1, 1)
    alpha, btc_ret, beta, tid = wh.written[0]
    assert tid == 1 and beta == 1.0
    assert alpha == pytest.approx(10.0)   # BTC flat -> alpha == whole pnl
    assert btc_ret == pytest.approx(0.0)


def test_backfill_unlabeled_skips_when_no_btc_cover():
    rows = [dict(id=1, ts_entry=450, ts_exit=1350, entry_px=100.0, exit_px=110.0,
                 size=1.0, side="buy", realized_pnl=10.0, partial_realized_pnl=0.0)]
    wh = _FakeWH(rows)
    n, cand = BA.backfill_unlabeled(wh, lambda *a: pd.DataFrame({"ts": [], "close": []}))
    assert (n, cand) == (0, 1)  # candidate seen, nothing written (no BTC data)
    assert wh.written == []


def test_backfill_unlabeled_empty():
    assert BA.backfill_unlabeled(_FakeWH([]), lambda *a: None) == (0, 0)

