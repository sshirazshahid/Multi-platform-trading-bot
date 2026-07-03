"""Phase 7 — F2 cross-venue funding-spread stack (built DISABLED).

Covers:
  * interval-normalized funding differential (8h vs 4h venues comparable)
  * f2_entry_gate math incl the max(20bps, 4x round-trip cost) floor
  * deterministic repeated one-leg-failure sim (N=25) via TwoBookSimulator
  * activation latch refuses without >=30d clean F1 paper evidence
  * F2 StrategySpec registered with promotion_status='disabled_until_f1_stable'
"""

import pytest

from core.data_sources.cross_venue import (
    Book,
    build_f2_spec,
    f2_activation_allowed,
    f2_entry_gate,
    funding_spread_frame,
    run_one_leg_failure_sims,
)
from core.decision.promotion_loop import register_evidence
from core.strategy_spec import StrategySpec


def _rec(venue, rate, interval_hours, stale=False):
    return {
        "venue": venue,
        "symbol": "BTCUSDT",
        "rate": rate,
        "interval_hours": interval_hours,
        "next_funding_ts": 1_000_000.0,
        "ts": 999_000.0,
        "age_sec": 0.0,
        "stale": stale,
    }


# ── funding differential frame ─────────────────────────────────────────
class TestFundingSpreadFrame:
    def test_interval_normalized_8h_vs_4h(self):
        # binance 0.0008 per 8h = 1.0 bps/h; bybit 0.0008 per 4h = 2.0 bps/h
        snaps = {
            "binance": {"BTC": _rec("binance", 0.0008, 8.0)},
            "bybit": {"BTC": _rec("bybit", 0.0008, 4.0)},
        }
        frame = funding_spread_frame(snaps)
        row = frame["BTC"]
        assert row["per_hour_bps"]["binance"] == pytest.approx(1.0)
        assert row["per_hour_bps"]["bybit"] == pytest.approx(2.0)
        assert row["spread_bps_per_hour"] == pytest.approx(1.0)
        # collect the higher per-hour funding on the SHORT venue
        assert row["short_venue"] == "bybit"
        assert row["long_venue"] == "binance"
        assert row["stale"] is False

    def test_stale_or_missing_interval_excluded(self):
        snaps = {
            "binance": {"BTC": _rec("binance", 0.0008, 8.0)},
            "bybit": {"BTC": _rec("bybit", 0.0008, None)},
            "bitget": {"BTC": _rec("bitget", 0.0008, 4.0, stale=True)},
        }
        row = funding_spread_frame(snaps)["BTC"]
        assert set(row["per_hour_bps"]) == {"binance"}
        assert row["spread_bps_per_hour"] is None
        assert row["stale"] is True


# ── entry gate math ────────────────────────────────────────────────────
class TestF2EntryGate:
    def test_passes_when_edge_clears_floor_and_buffers(self):
        res = f2_entry_gate(
            spread_bps_per_hour=2.0,
            hold_hours=48.0,  # 96 bps gross
            two_leg_cost_frac=0.0010,  # 10 bps -> 4x floor = 40 bps
            slippage_bps=5.0,
            latency_buffer_bps=2.0,
            failed_leg_buffer_bps=5.0,
        )
        # net = 96 - 10 - 5 - 2 - 5 = 74 >= max(20, 40)
        assert res["net_edge_bps"] == pytest.approx(74.0)
        assert res["floor_bps"] == pytest.approx(40.0)
        assert res["enter"] is True

    def test_20bps_floor_binds_when_costs_tiny(self):
        res = f2_entry_gate(
            spread_bps_per_hour=0.5,
            hold_hours=40.0,  # 20 bps gross
            two_leg_cost_frac=0.0001,  # 1 bp -> 4x = 4 bps < 20
            slippage_bps=0.0,
            latency_buffer_bps=0.0,
            failed_leg_buffer_bps=0.0,
        )
        assert res["floor_bps"] == pytest.approx(20.0)
        assert res["net_edge_bps"] == pytest.approx(19.0)
        assert res["enter"] is False

    def test_4x_cost_floor_binds(self):
        res = f2_entry_gate(
            spread_bps_per_hour=1.0,
            hold_hours=50.0,  # 50 bps gross
            two_leg_cost_frac=0.0015,  # 15 bps -> floor 60
            slippage_bps=0.0,
            latency_buffer_bps=0.0,
            failed_leg_buffer_bps=0.0,
        )
        assert res["floor_bps"] == pytest.approx(60.0)
        assert res["enter"] is False

    def test_missing_spread_refuses(self):
        res = f2_entry_gate(
            spread_bps_per_hour=None,
            hold_hours=48.0,
            two_leg_cost_frac=0.0010,
        )
        assert res["enter"] is False
        assert "spread" in res["reason"]


# ── one-leg failure sim (deterministic, repeated) ─────────────────────
class TestOneLegSim:
    def test_repeated_one_leg_failure_deterministic_n25(self):
        results = run_one_leg_failure_sims(n=25)
        assert len(results) == 25
        first = results[0]
        for r in results:
            assert r == first  # deterministic
            assert r["legging_risk"] is True
            assert r["long_leg"]["reason"] == "filled"
            assert r["short_leg"]["filled_qty"] == 0.0
            assert r["short_leg"]["reason"].startswith("stale:")

    def test_custom_books_timeout_leg(self):
        fresh = Book(venue="binance", bids=[(100.0, 5.0)], asks=[(100.1, 5.0)], ts=100.0)
        stale = Book(venue="bybit", bids=[(100.0, 5.0)], asks=[(100.1, 5.0)], ts=0.0)
        results = run_one_leg_failure_sims(
            n=5, long_book=fresh, short_book=stale, qty=1.0, now=101.0
        )
        assert all(r["unhedged_qty"] == pytest.approx(1.0) for r in results)


# ── activation latch ───────────────────────────────────────────────────
class TestActivationLatch:
    def test_refuses_without_registry(self, tmp_path):
        res = f2_activation_allowed(registry_path=tmp_path / "missing.json")
        assert res["allowed"] is False

    def test_refuses_short_span_or_dirty_cycles(self, tmp_path):
        p = tmp_path / "reg.json"
        register_evidence(
            "F1",
            oos_metrics={"paper_cycles": 40, "paper_span_days": 12.0,
                         "failed_leg_events": 0},
            promotion_status="lab_paper",
            path=p,
        )
        assert f2_activation_allowed(registry_path=p)["allowed"] is False

        register_evidence(
            "F1",
            oos_metrics={"paper_cycles": 40, "paper_span_days": 45.0,
                         "failed_leg_events": 2},
            promotion_status="lab_paper",
            path=p,
        )
        res = f2_activation_allowed(registry_path=p)
        assert res["allowed"] is False
        assert "one-leg" in res["reason"]

    @staticmethod
    def _qualifying_registry(tmp_path):
        p = tmp_path / "reg.json"
        register_evidence(
            "F1",
            oos_metrics={"paper_cycles": 60, "paper_span_days": 31.0,
                         "failed_leg_events": 0},
            promotion_status="lab_paper",
            path=p,
        )
        return p

    def test_qualifying_evidence_alone_refuses_fee_unverified(self, tmp_path):
        # Fail-closed pin: clean evidence but NO fee-tier verification -> refuse.
        p = self._qualifying_registry(tmp_path)
        res = f2_activation_allowed(registry_path=p)
        assert res["allowed"] is False
        assert "fail-closed" in res["reason"]

    def test_qualifying_evidence_with_maker_fee_tier_allows(self, tmp_path):
        p = self._qualifying_registry(tmp_path)
        res = f2_activation_allowed(registry_path=p, maker_fee_frac=0.0001)
        assert res["allowed"] is True

    def test_qualifying_evidence_with_high_maker_fee_refuses(self, tmp_path):
        p = self._qualifying_registry(tmp_path)
        res = f2_activation_allowed(registry_path=p, maker_fee_frac=0.0005)
        assert res["allowed"] is False
        assert "maker fee" in res["reason"]

    def test_qualifying_evidence_with_taker_ack_allows(self, tmp_path):
        p = self._qualifying_registry(tmp_path)
        res = f2_activation_allowed(registry_path=p, taker_economics_acknowledged=True)
        assert res["allowed"] is True
        assert "taker/taker floor acknowledged" in res["reason"]


# ── spec registration ──────────────────────────────────────────────────
class TestF2Spec:
    def test_spec_shape_and_disabled_status(self):
        spec = build_f2_spec()
        assert isinstance(spec, StrategySpec)
        assert spec.id == "F2_XVENUE_FUNDING_SPREAD"
        assert spec.promotion_status == "disabled_until_f1_stable"
        assert spec.entry_rules["pre_funded_venues_assumed"] is True
        assert spec.entry_rules["min_net_edge_bps_floor"] == 20.0
        assert spec.entry_rules["cost_mult_floor"] == 4.0
        assert spec.risk_limits["maker_fee_precondition_frac"] == pytest.approx(0.0001)
        assert spec.risk_limits["taker_taker_floor_ack_alternative"] is True
        notes = spec.risk_limits["live_execution_notes"]
        assert any("taker/taker" in n for n in notes)

    def test_spec_registers_disabled_in_registry(self, tmp_path):
        p = tmp_path / "reg.json"
        spec = build_f2_spec()
        rec = spec.register(registry_path=p)
        assert rec["promotion_status"] == "disabled_until_f1_stable"
