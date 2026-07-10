"""Tests for research/sim_accuracy_band.py helpers (tmp-dir isolated)."""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from research import sim_accuracy_band as sab  # noqa: E402

# ── swap_tp geometry ──────────────────────────────────────────────────


def test_swap_tp_buy_uses_frac_of_sl_distance():
    # entry 100, sl 98 -> SL distance 2%. frac 0.5 -> TP 1% above -> 101.0
    tp, floor = sab.swap_tp(100.0, 98.0, "buy", 0.5, min_tp_pct=0.5)
    assert tp == pytest.approx(101.0)
    assert floor is False


def test_swap_tp_sell_is_below_entry():
    # SL above entry for a short; TP must be BELOW entry.
    tp, floor = sab.swap_tp(100.0, 102.0, "sell", 0.5, min_tp_pct=0.5)
    assert tp == pytest.approx(99.0)
    assert floor is False


def test_swap_tp_floor_binds_on_small_geometry():
    # SL distance 1% * frac 0.35 = 0.35% < 0.5% floor -> floor binds, TP=0.5%.
    tp, floor = sab.swap_tp(100.0, 99.0, "buy", 0.35, min_tp_pct=0.5)
    assert tp == pytest.approx(100.5)
    assert floor is True


def test_swap_tp_smaller_frac_gives_nearer_tp():
    d = 100.0
    tp_small, _ = sab.swap_tp(d, 96.0, "buy", 0.40)  # 4% SL
    tp_big, _ = sab.swap_tp(d, 96.0, "buy", 0.70)
    assert d < tp_small < tp_big  # nearer TP for smaller frac


def test_swap_tp_rejects_nonpositive_entry():
    with pytest.raises(ValueError):
        sab.swap_tp(0.0, 1.0, "buy", 0.5)


# ── _summarize (WR / CI / expectancy) ─────────────────────────────────


def test_summarize_empty():
    s = sab._summarize([])
    assert s["n"] == 0 and s["wr"] == 0.0 and s["ci"] == (0.0, 1.0)


def test_summarize_counts_wins_by_pnl_sign():
    # 3 winners, 1 loser -> WR 0.75; expectancy is the mean R.
    recs = [(1.0, 0.5), (2.0, 0.5), (0.5, 0.5), (-1.0, -1.0)]
    s = sab._summarize(recs)
    assert s["n"] == 4
    assert s["wins"] == 3
    assert s["wr"] == pytest.approx(0.75)
    assert s["exp_r"] == pytest.approx((0.5 + 0.5 + 0.5 - 1.0) / 4)
    assert s["avg_win_r"] == pytest.approx(0.5)
    assert s["avg_loss_r"] == pytest.approx(-1.0)
    lo, hi = s["ci"]
    assert 0.0 <= lo < s["wr"] < hi <= 1.0  # Wilson band brackets the point


def test_summarize_zero_pnl_is_not_a_win():
    s = sab._summarize([(0.0, 0.0)])
    assert s["wins"] == 0 and s["wr"] == 0.0


# ── simulate end-to-end on hand-built candles (wiring, not the deliverable) ──


def _seed_db(path: Path) -> None:
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE shadow_decisions (proposal_id TEXT, ts INTEGER, symbol TEXT, "
        "side TEXT, entry_px REAL, sl_px REAL, tp_px REAL, venue TEXT, timeframe TEXT, "
        "horizon_bars INTEGER, projected_notional_current REAL)"
    )
    # One long: entry 100, sl 98. Under frac 0.5 -> TP 101.
    conn.execute(
        "INSERT INTO shadow_decisions VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        ("p1", 1000, "BTC/USDT:USDT", "buy", 100.0, 98.0, 200.0, "binance", "1h", 3, 200.0),
    )
    conn.commit()
    conn.close()


def test_load_rows_and_simulate_resolves_tp_hit(tmp_path):
    db = tmp_path / "wh.sqlite"
    _seed_db(db)
    rows = sab.load_rows(db)
    assert len(rows) == 1
    # Forward candles: bar 2 spikes to 101.5 -> the frac-0.5 TP (101) fills.
    # [ts_ms, o, h, l, c, v]
    cs = [
        [1000_000, 100.0, 100.4, 99.9, 100.2, 1.0],
        [4600_000, 100.2, 101.6, 100.0, 101.3, 1.0],
        [8200_000, 101.3, 101.4, 101.0, 101.2, 1.0],
    ]
    candles = {"p1": cs}
    result = sab.simulate(rows, candles, fracs=[0.5], min_tp_pct=0.5)
    s = result[0.5]["splits"]["overall"]
    assert s["n"] == 1
    assert s["wins"] == 1  # TP hit -> net positive after cost on a 1% move


def test_simulate_censors_when_no_candles(tmp_path):
    db = tmp_path / "wh.sqlite"
    _seed_db(db)
    rows = sab.load_rows(db)
    result = sab.simulate(rows, {"p1": []}, fracs=[0.5])
    assert result[0.5]["censored"] == 1
    assert result[0.5]["splits"]["overall"]["n"] == 0
