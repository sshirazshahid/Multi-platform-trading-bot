"""Phase 3 — F1 standalone delta-neutral carry PAPER runner tests.

Fixture-driven (no live calls, no orders). Covers the full open -> settlement
accrual -> exit -> RESOLVED cycle, 8h/4h/1h interval accounting including an
interval change mid-hold, one-leg failure + venue/symbol block persistence,
the 10s reconcile timeout, atomic state round-trips, the report file, and the
grep-proof that no order-path symbol is imported by the runner.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.carry_runner import CarryRunner, write_report

HOUR = 3600.0


class FakeClock:
    def __init__(self, t0=1_000_000.0):
        self.t = t0

    def __call__(self):
        return self.t

    def advance(self, sec):
        self.t += sec


def make_snap(now, *, rate=0.0004, interval_hours=8.0, next_in_sec=2 * HOUR,
              spot=100.0, perp=100.02, spread_bps=1.0, depth_ratio=30.0,
              liq_buffer_x=5.0, stale=False, avg_funding_7d=0.0003):
    half = spot * spread_bps / 1e4 / 2.0
    phalf = perp * spread_bps / 1e4 / 2.0
    return {
        "spot_bid": spot - half, "spot_ask": spot + half,
        "perp_bid": perp - phalf, "perp_ask": perp + phalf,
        "perp_mark": perp,
        "depth_ratio": depth_ratio,
        "liq_buffer_x": liq_buffer_x,
        "both_legs_fillable": True,
        "avg_funding_7d": avg_funding_7d,
        "round_trip_cost_frac": 0.0002,
        "funding": {
            "rate": rate, "interval_hours": interval_hours,
            "next_funding_ts": now + next_in_sec, "ts": now,
            "age_sec": 0.0, "stale": stale,
        },
        "ts": now,
    }


class Provider:
    """Mutable per-symbol snapshot provider for the fixtures."""

    def __init__(self, clock, **kw):
        self.clock = clock
        self.kw = dict(kw)
        self._pin_next: float | None = None

    def pin_next_funding(self, ts):
        self._pin_next = ts

    def __call__(self, symbol):
        snap = make_snap(self.clock(), **self.kw)
        if self._pin_next is not None:
            f = dict(snap["funding"])
            f["next_funding_ts"] = self._pin_next
            snap["funding"] = f
        return snap


def build(tmp_path, clock, provider, **kw):
    return CarryRunner(
        state_path=tmp_path / "carry_positions.json",
        snapshot_provider=provider,
        now_fn=clock,
        paper_equity=100_000.0,
        gate_log_path=tmp_path / "carry_gate_log.jsonl",
        symbols=("BTC/USDT",),
        **kw,
    )


# ── gate evaluation is logged every run ────────────────────────────────
def test_gate_log_written_every_run(tmp_path):
    clock = FakeClock()
    provider = Provider(clock, rate=-0.0001)  # negative funding -> gate fails
    r = build(tmp_path, clock, provider)
    r.run_once()
    r.run_once()
    lines = (tmp_path / "carry_gate_log.jsonl").read_text().strip().splitlines()
    assert len(lines) == 2
    rec = json.loads(lines[0])
    assert rec["symbol"] == "BTC/USDT"
    assert rec["ok"] is False and "funding_rate" in rec["reason"]


# ── full lifecycle: open -> settlements -> exit -> RESOLVED ────────────
def test_full_lifecycle_resolved_cycle_net_is_funding_minus_costs(tmp_path):
    clock = FakeClock()
    provider = Provider(clock)
    r = build(tmp_path, clock, provider)
    s = r.run_once()
    assert s["opened"] == 1
    state = json.loads((tmp_path / "carry_positions.json").read_text())
    pos = state["positions"]["BTC/USDT"]
    notional = pos["notional"]

    # accrue two positive settlements (8h apart), then flip funding negative:
    # the projected net edge goes negative -> exit gate trips on that run.
    clock.advance(2 * HOUR + 1)
    r.run_once()
    clock.advance(8 * HOUR)
    r.run_once()
    provider.kw["rate"] = -0.0001
    clock.advance(8 * HOUR)
    s = r.run_once()
    assert s["closed"] == 1

    state = json.loads((tmp_path / "carry_positions.json").read_text())
    assert state["positions"] == {}
    (cyc,) = state["cycles"]
    assert cyc["label_status"] == "RESOLVED"
    assert "net_edge" in cyc["exit_reason"]
    assert cyc["settlements_held"] == 3
    expected_funding = notional * (0.0004 * 2 - 0.0001)
    assert cyc["gross_funding"] == pytest.approx(expected_funding)
    assert cyc["net_pnl"] == pytest.approx(
        cyc["gross_funding"] + cyc["basis_pnl"] - cyc["fees"])
    assert cyc["net_pnl"] < cyc["gross_funding"]  # costs are real


@pytest.mark.parametrize("interval_hours,n_expected", [(8.0, 3), (4.0, 6), (1.0, 24)])
def test_settlement_accounting_per_interval(tmp_path, interval_hours, n_expected):
    # first settlement 1h out (inside the [20,180]min entry window), then the
    # position's own schedule advances by the venue interval.
    clock = FakeClock()
    provider = Provider(clock, interval_hours=interval_hours, next_in_sec=1 * HOUR)
    r = build(tmp_path, clock, provider)
    r.run_once()
    # pin next_funding_ts so accrual advances off the position's own schedule.
    provider.pin_next_funding(clock() + 999 * HOUR)
    clock.advance(24 * HOUR + 1)
    r.run_once()
    state = json.loads((tmp_path / "carry_positions.json").read_text())
    assert state["positions"]["BTC/USDT"]["settlements_held"] == n_expected


def test_interval_change_mid_hold(tmp_path):
    clock = FakeClock()
    provider = Provider(clock, interval_hours=8.0, next_in_sec=1 * HOUR)
    r = build(tmp_path, clock, provider)
    r.run_once()
    provider.pin_next_funding(clock() + 999 * HOUR)
    clock.advance(8 * HOUR + 1)   # 1 settlement (at +1h), next at +9h (8h iv)
    r.run_once()
    provider.kw["interval_hours"] = 4.0  # venue switches to 4h funding
    clock.advance(8 * HOUR)       # boundaries at +9h and +13h -> 2 more at 4h
    r.run_once()
    state = json.loads((tmp_path / "carry_positions.json").read_text())
    pos = state["positions"]["BTC/USDT"]
    assert pos["settlements_held"] == 3
    assert pos["interval_hours"] == 4.0


# ── one-leg failure + persistent block ─────────────────────────────────
def test_one_leg_failure_blocks_venue_symbol(tmp_path):
    clock = FakeClock()
    provider = Provider(clock)

    def hook(symbol):
        return {"spot_fill_frac": 1.0, "perp_fill_frac": 0.0}

    r = build(tmp_path, clock, provider, failure_hook=hook)
    s = r.run_once()
    assert s["failed"] == 1 and s["opened"] == 0
    state = json.loads((tmp_path / "carry_positions.json").read_text())
    assert state["positions"] == {}
    (cyc,) = state["cycles"]
    assert cyc["label_status"] == "FAILED"
    block = state["blocks"]["binance:BTC/USDT"]
    assert block["requires_manual_review"] is True

    # a subsequent run refuses that venue/symbol even with a healthy book
    r2 = build(tmp_path, clock, provider)  # no failure hook
    s2 = r2.run_once()
    assert s2["opened"] == 0 and s2["blocked"] == 1
    lines = (tmp_path / "carry_gate_log.jsonl").read_text().strip().splitlines()
    assert json.loads(lines[-1])["blocked"] is True


def test_reconcile_timeout_marks_failed(tmp_path):
    clock = FakeClock()
    provider = Provider(clock)

    def hook(symbol):
        return {"spot_fill_frac": 1.0, "perp_fill_frac": 1.0, "reconcile_sec": 11.0}

    r = build(tmp_path, clock, provider, failure_hook=hook)
    s = r.run_once()
    assert s["failed"] == 1
    state = json.loads((tmp_path / "carry_positions.json").read_text())
    assert state["cycles"][0]["exit_reason"] == "reconcile_timeout"
    assert "binance:BTC/USDT" in state["blocks"]


# ── atomic state round-trip ────────────────────────────────────────────
def test_state_round_trips_atomically(tmp_path):
    clock = FakeClock()
    provider = Provider(clock)
    r = build(tmp_path, clock, provider)
    r.run_once()
    p = tmp_path / "carry_positions.json"
    assert p.exists()
    assert not list(tmp_path.glob("*.tmp"))  # no temp litter
    before = json.loads(p.read_text())
    r2 = build(tmp_path, clock, provider)
    assert r2.load_state() == before


# ── report ─────────────────────────────────────────────────────────────
def test_report_file_has_all_fields_and_checklist(tmp_path):
    clock = FakeClock()
    provider = Provider(clock)
    r = build(tmp_path, clock, provider)
    r.run_once()
    provider.kw["rate"] = -0.0001
    for _ in range(4):
        clock.advance(8 * HOUR)
        r.run_once()
    out = write_report(r.load_state(), out_dir=tmp_path / "reports", now_fn=clock)
    text = Path(out).read_text(encoding="utf-8")
    for needle in (
        "Per-symbol PnL", "Per-venue PnL", "Funding earned", "Basis PnL",
        "Fees", "Slippage", "Failed-leg count", "Promotion-gate checklist",
        ">= 60 cycles", "PF >= 1.25", "chronological folds", "1.5x", "2x",
        "one-leg", "concentration",
    ):
        assert needle in text, needle


def test_runner_report_flag_via_cli_helper(tmp_path):
    from scripts.run_f1_carry_paper import run_report_only
    clock = FakeClock()
    provider = Provider(clock)
    r = build(tmp_path, clock, provider)
    r.run_once()
    out = run_report_only(state_path=tmp_path / "carry_positions.json",
                          out_dir=tmp_path / "reports")
    assert Path(out).exists()


# ── grep-proof: no order path on the runner ────────────────────────────
def test_no_order_path_symbols_in_runner_sources():
    root = Path(__file__).resolve().parents[1]
    for rel in ("core/carry_runner.py", "scripts/run_f1_carry_paper.py"):
        src = (root / rel).read_text(encoding="utf-8")
        assert "create_order" not in src, rel
        assert "OrderManager" not in src, rel
