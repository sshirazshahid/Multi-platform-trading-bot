"""C7 (tpbot retrofit): persist per-trade MFE/MAE; DistFitSL real-extremes path.

Gap (survey 2026-07-08): live/paper trades rows never stored intra-trade
extremes, so DistFitSL fit its SL quantiles and TP R-targets on DOCUMENTED
PROXIES (exit-move as MAE for losers / MFE for winners — dist_fit_sl.py's
own caveat) — biased estimates, since a winner's true MAE and a loser's
true MFE were invisible. dist_fit_sl.py itself says: "When a future change
writes intra-trade extremes onto trades rows, swap the two helpers below
for the real columns." This is that change.

  * trades gains mfe/mae REAL columns (positive fractions of entry price,
    unleveraged — same convention as shadow_outcomes); NULL on legacy rows
  * Warehouse.update_trade_extremes upserts by the trade idempotency key
  * OrderManager._update_trade_extremes runs in the 10s monitor: running
    in-memory peaks per position, warehouse write only on a NEW extreme
    (throttled) — no per-tick SQLite hammering
  * dist_fit_sl._row_mae_mfe prefers the real columns; proxy fallback keeps
    legacy rows usable (fit quality improves as real rows accumulate)
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from core.dist_fit_sl import _row_mae_mfe


@pytest.fixture
def wh(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    Path("data").mkdir(exist_ok=True)
    import core.warehouse

    core.warehouse._default = None
    from core.warehouse import get_warehouse

    yield get_warehouse()
    core.warehouse._default = None


def _seed(wh, symbol, ts_entry):
    tid = wh.record_trade_open(
        exchange="Bybit",
        symbol=symbol,
        side="buy",
        ts_entry=ts_entry,
        entry_px=100.0,
        size=1.0,
        leverage=3,
        mode="PAPER",
    )
    assert tid > 0
    return tid


# ── warehouse columns + updater ──────────────────────────────────────────────
def test_trades_table_has_mfe_mae_columns(wh):
    cols = {r["name"] for r in wh.query("PRAGMA table_info(trades)")}
    assert "mfe" in cols and "mae" in cols


def test_update_trade_extremes_writes_and_rereads(wh):
    tid = _seed(wh, "EXA/USDT", 2100.0)
    ok = wh.update_trade_extremes(
        exchange="Bybit", symbol="EXA/USDT", side="buy", ts_entry=2100.0, mfe=0.031, mae=0.012
    )
    assert ok is True
    row = wh.query("SELECT mfe, mae FROM trades WHERE id=?", (tid,))[0]
    assert row["mfe"] == pytest.approx(0.031)
    assert row["mae"] == pytest.approx(0.012)


def test_update_trade_extremes_missing_row_false(wh):
    assert (
        wh.update_trade_extremes(
            exchange="Bybit", symbol="MISS/USDT", side="buy", ts_entry=1.0, mfe=0.01, mae=0.01
        )
        is False
    )


def test_legacy_rows_have_null_extremes(wh):
    tid = _seed(wh, "EXL/USDT", 2200.0)
    row = wh.query("SELECT mfe, mae FROM trades WHERE id=?", (tid,))[0]
    assert row["mfe"] is None and row["mae"] is None


# ── monitor capture (running peaks, warehouse on new extremes only) ──────────
def _om_with_wh():
    from core.order_manager import OrderManager

    om = OrderManager(tracker=MagicMock(), risk=MagicMock(), notifier=MagicMock())
    return om


def _mpos(side="buy", entry=100.0):
    p = MagicMock()
    p.exchange = "Bybit"
    p.symbol = "EXM/USDT"
    p.side = side
    p.entry_price = entry
    p.open_time = 2300.0
    # dynamic attrs start unset like a fresh Position
    del p._mfe_frac
    del p._mae_frac
    del p._ext_persist_ts
    del p._ext_dirty
    return p


def test_running_extremes_buy_side(wh):
    om = _om_with_wh()
    _seed(wh, "EXM/USDT", 2300.0)
    pos = _mpos("buy")
    om._update_trade_extremes(pos, 103.0)  # +3% favorable -> persists
    om._update_trade_extremes(pos, 97.0)  # -3% adverse -> throttled, dirty
    # A later tick past the 5s throttle catches up the pending extreme even
    # though 101.0 sets no new peak (the dirty-flag catch-up).
    pos._ext_persist_ts -= 10.0
    om._update_trade_extremes(pos, 101.0)
    assert pos._mfe_frac == pytest.approx(0.03)
    assert pos._mae_frac == pytest.approx(0.03)
    row = wh.query("SELECT mfe, mae FROM trades WHERE symbol='EXM/USDT'")[0]
    assert row["mfe"] == pytest.approx(0.03)
    assert row["mae"] == pytest.approx(0.03)


def test_running_extremes_sell_side_signs(wh):
    om = _om_with_wh()
    tid = wh.record_trade_open(
        exchange="Bybit",
        symbol="EXS/USDT",
        side="sell",
        ts_entry=2400.0,
        entry_px=100.0,
        size=1.0,
        leverage=3,
        mode="PAPER",
    )
    assert tid > 0
    pos = _mpos("sell")
    pos.symbol = "EXS/USDT"
    pos.open_time = 2400.0
    om._update_trade_extremes(pos, 97.0)  # favorable for a short: +3% MFE
    om._update_trade_extremes(pos, 102.0)  # adverse: 2% MAE
    assert pos._mfe_frac == pytest.approx(0.03)
    assert pos._mae_frac == pytest.approx(0.02)


def test_extremes_never_raise_on_bad_inputs():
    om = _om_with_wh()
    pos = _mpos("buy")
    pos.entry_price = 0.0
    om._update_trade_extremes(pos, 100.0)  # zero entry -> silent no-op
    om._update_trade_extremes(object(), 100.0)  # poisoned pos -> no raise


def test_monitor_hook_wired_in_check_sl_tp():
    src = Path("core/order_manager.py").read_text(encoding="utf-8")
    i = src.index("def check_sl_tp")
    assert "_update_trade_extremes" in src[i : i + 4000], (
        "the 10s monitor must feed per-position extremes"
    )


# ── DistFitSL preference: real columns > proxies ─────────────────────────────
def test_row_mae_mfe_prefers_real_columns():
    mae, mfe = _row_mae_mfe(
        {"entry_px": 100.0, "exit_px": 104.0, "realized_pnl": 4.0, "mae": 0.017, "mfe": 0.052}
    )
    assert mae == pytest.approx(0.017)
    assert mfe == pytest.approx(0.052)  # real, not the 0.04 proxy


def test_row_mae_mfe_proxy_fallback_for_legacy_rows():
    # winner without real extremes: proxy says MFE >= exit move, MAE unknown
    mae, mfe = _row_mae_mfe(
        {"entry_px": 100.0, "exit_px": 104.0, "realized_pnl": 4.0, "mae": None, "mfe": None}
    )
    assert mfe == pytest.approx(0.04)
    assert mae == 0.0
    # loser without real extremes
    mae, mfe = _row_mae_mfe(
        {"entry_px": 100.0, "exit_px": 97.0, "realized_pnl": -3.0, "mae": None, "mfe": None}
    )
    assert mae == pytest.approx(0.03)
    assert mfe == 0.0


def test_fetch_rows_selects_real_columns():
    src = Path("core/dist_fit_sl.py").read_text(encoding="utf-8")
    i = src.index("def _fetch_rows")
    assert "mfe" in src[i : i + 800] and "mae" in src[i : i + 800], (
        "_fetch_rows must SELECT the real extreme columns for the fit"
    )


# ── review 2026-07-09 regressions ─────────────────────────────────────────────
def test_update_trade_extremes_is_monotone_across_restart(wh):
    """A watchdog restart resets in-memory peaks; the first post-restart tick
    must not clobber larger persisted extremes with near-zero ones."""
    tid = _seed(wh, "EXR/USDT", 2600.0)
    assert wh.update_trade_extremes(
        exchange="Bybit", symbol="EXR/USDT", side="buy", ts_entry=2600.0,
        mfe=0.02, mae=0.015)
    assert wh.update_trade_extremes(
        exchange="Bybit", symbol="EXR/USDT", side="buy", ts_entry=2600.0,
        mfe=0.001, mae=0.0)
    row = wh.query("SELECT mfe, mae FROM trades WHERE id=?", (tid,))[0]
    assert row["mfe"] == pytest.approx(0.02)
    assert row["mae"] == pytest.approx(0.015)


def test_ext_dirty_survives_failed_write(monkeypatch):
    """The dirty flag must clear only on a SUCCESSFUL write, or a failed
    (locked/transient) write silently cancels the documented catch-up."""
    from unittest.mock import MagicMock

    import core.warehouse as cw
    om = _om_with_wh()
    pos = _mpos("buy")
    failing = MagicMock()
    failing.update_trade_extremes.return_value = False
    monkeypatch.setattr(cw, "get_warehouse", lambda: failing)
    om._update_trade_extremes(pos, 103.0)  # new extreme, write fails
    assert pos._ext_dirty is True          # catch-up still pending
    ok = MagicMock()
    ok.update_trade_extremes.return_value = True
    monkeypatch.setattr(cw, "get_warehouse", lambda: ok)
    pos._ext_persist_ts -= 10.0
    om._update_trade_extremes(pos, 101.0)  # no new peak; dirty catch-up fires
    assert pos._ext_dirty is False
