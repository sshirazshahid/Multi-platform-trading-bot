"""Phase 1 — S3 TS-momentum ENSEMBLE vertical slice (prove the pipeline).

Proves the register -> resolve -> accept loop runs end-to-end on a single
symbol/venue deterministic strategy. A correct NO_GO verdict is a PASS here;
the deliverable is pipeline correctness, NOT alpha.
"""
from __future__ import annotations

import numpy as np

from core.tsmom_signal import (
    DEFAULT_S3_LOOKBACKS,
    S3EnsembleSignal,
    ensemble_momentum_state,
    is_tsmom_action,
    is_tsmom_position,
)


# ── ensemble combiner ────────────────────────────────────────────────────────
def test_ensemble_long_only_when_all_horizons_positive():
    # strictly increasing -> every lookback momentum positive -> LONG
    closes = [100.0 + i for i in range(60)]
    is_long, per = ensemble_momentum_state(closes, DEFAULT_S3_LOOKBACKS)
    assert is_long is True
    assert set(per.keys()) == set(DEFAULT_S3_LOOKBACKS)
    assert all(v is not None and v > 0 for v in per.values())


def test_ensemble_flat_when_any_horizon_negative():
    # up for a long time then a sharp recent drop -> short horizon negative -> FLAT
    closes = [100.0 + i for i in range(50)] + [120.0, 110.0, 100.0, 90.0, 80.0, 70.0, 60.0, 50.0]
    is_long, per = ensemble_momentum_state(closes, DEFAULT_S3_LOOKBACKS)
    assert is_long is False


def test_ensemble_flat_on_insufficient_history():
    is_long, per = ensemble_momentum_state([100.0, 101.0, 102.0], DEFAULT_S3_LOOKBACKS)
    assert is_long is False


# ── signal emits the standard action-dict with s3 provenance ─────────────────
class _UptrendExchange:
    """Minimal exchange stub returning a clean daily uptrend (all closed bars)."""

    def fetch_ohlcv(self, symbol, timeframe, limit=0, market_type="spot"):
        if market_type != "spot":
            return []
        # closed daily candles, monotonically rising
        day = 86_400_000
        now = 1_700_000_000_000
        rows = []
        n = limit or 60
        for k in range(n):
            ts = now - (n - k) * day  # all strictly in the past -> closed
            px = 100.0 + k
            rows.append([ts, px, px + 1, px - 1, px, 1000.0])
        return rows


def test_s3_signal_emits_s3_action():
    sig = S3EnsembleSignal(exchanges={"binance": _UptrendExchange()})
    actions = sig.analyze_portfolio(
        coins=["BTC/USDT:USDT"],
        open_positions=[],
        exchange_balances={},
        risk_envelope={"total_balance": 1000.0},
        recent_trades=[],
    )
    assert len(actions) == 1
    a = actions[0]
    assert a["type"] == "OPEN"
    assert a["side"] == "buy"
    assert a["source"] == "s3"
    assert is_tsmom_action(a) is True  # s3 is tsmom-family for exit suppression


def test_is_tsmom_family_recognizes_s3_position():
    class _P:
        strategy = "s3"

    assert is_tsmom_position(_P()) is True


# ── end-to-end register -> resolve -> accept loop (no manual steps) ──────────
def test_s3_vertical_slice_end_to_end(tmp_path):
    from scripts.s3_vertical_slice import run_s3_slice

    reg = tmp_path / "active_strategies.json"
    report = run_s3_slice(symbol="BTC", venue="binance", registry_path=reg)

    # RESOLVED outcomes (not projected): every trade carries an after-cost net_pnl
    trades = report["trades"]
    assert len(trades) > 0
    assert all(t["label_status"] == "RESOLVED" for t in trades)
    assert all("net_pnl" in t and t["net_pnl"] is not None for t in trades)
    assert all(t["exit_reason"] in ("stop_loss", "momentum_flip", "eod") for t in trades)

    # accept() returns one verdict with ALL eight sub-gates populated (NO_GO ok)
    verdict = report["verdict"]
    assert isinstance(verdict["accept"], bool)
    expected = {
        "n_floor",
        "profit_factor",
        "expectancy_ci",
        "folds",
        "pbo",
        "dsr",
        "multiple_comparisons",
        "confirmation_window",
    }
    assert set(verdict["sub_gates"].keys()) == expected
    assert all("pass" in g for g in verdict["sub_gates"].values())

    # a complete S3 EvidenceRegistry record exists
    ev = report["evidence"]
    for field in ("hypothesis", "rules", "data_sources", "cost_model",
                  "oos_metrics", "honest_n", "promotion_status", "fingerprint"):
        assert field in ev and ev[field] not in (None, "", {}, [])
    assert ev["strategy_id"] == "S3"

    # the registry file was actually written
    assert reg.exists()


def test_s3_sim_resolved_outcomes_deterministic(tmp_path):
    """Same inputs -> identical resolved net_pnl sequence (deterministic)."""
    from scripts.s3_vertical_slice import run_s3_slice

    r1 = run_s3_slice(symbol="BTC", venue="binance", registry_path=tmp_path / "a.json")
    r2 = run_s3_slice(symbol="BTC", venue="binance", registry_path=tmp_path / "b.json")
    n1 = [t["net_pnl"] for t in r1["trades"]]
    n2 = [t["net_pnl"] for t in r2["trades"]]
    assert np.allclose(n1, n2)
