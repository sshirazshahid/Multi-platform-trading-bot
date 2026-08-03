"""Phase 3 — F1 standalone delta-neutral carry PAPER runner tests.

Fixture-driven (no live calls, no orders). Covers the full open -> settlement
accrual -> exit -> RESOLVED cycle, 8h/4h/1h interval accounting including an
interval change mid-hold, one-leg failure + venue/symbol block persistence,
the 10s reconcile timeout, atomic state round-trips, the report file, and the
grep-proof that no order-path symbol is imported by the runner.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from types import SimpleNamespace

import pytest

from core.carry_runner import (
    F1_STRATEGY_VERSION,
    CarryRunner,
    acquire_carry_state_lock,
    write_report,
)
from core.entry_policy import EntryAuthorization
from core.funding_history import FundingSettlement

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
              liq_buffer_x=5.0, stale=False, avg_funding_7d=0.0003,
              rt_cost=0.0002, trailing_funding_rates=None,
              snapshot_age_sec=0.0, funding_age_sec=0.0,
              spot_buy_depth_notional=None, perp_sell_depth_notional=None):
    half = spot * spread_bps / 1e4 / 2.0
    phalf = perp * spread_bps / 1e4 / 2.0
    clip_notional = 5_000.0  # build() uses 100k equity * the frozen 5% clip
    spot_depth = (
        float(spot_buy_depth_notional)
        if spot_buy_depth_notional is not None
        else float(depth_ratio) * clip_notional
    )
    perp_depth = (
        float(perp_sell_depth_notional)
        if perp_sell_depth_notional is not None
        else float(depth_ratio) * clip_notional
    )
    observed_at = now - float(snapshot_age_sec)
    funding_observed_at = now - float(funding_age_sec)
    return {
        "spot_bid": spot - half, "spot_ask": spot + half,
        "perp_bid": perp - phalf, "perp_ask": perp + phalf,
        "perp_mark": perp,
        "spot_buy_depth_notional": spot_depth,
        "perp_sell_depth_notional": perp_depth,
        "depth_ratio": min(spot_depth, perp_depth) / clip_notional,
        "liq_buffer_x": liq_buffer_x,
        "both_legs_fillable": min(spot_depth, perp_depth) >= clip_notional,
        "avg_funding_7d": avg_funding_7d,
        "trailing_funding_rates": list(
            trailing_funding_rates
            if trailing_funding_rates is not None
            else [rate] * 21
        ),
        "round_trip_cost_frac": rt_cost,
        "funding": {
            "rate": rate, "interval_hours": interval_hours,
            "next_funding_ts": now + next_in_sec,
            "observed_at": funding_observed_at,
            "received_at": now,
            "ts": funding_observed_at,
            "age_sec": funding_age_sec,
            "stale": stale,
        },
        "spot_observed_at": observed_at,
        "perp_observed_at": observed_at,
        "mark_observed_at": observed_at,
        "observed_at": observed_at,
        "received_at": now,
        "stale": False,
        "ts": observed_at,
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


class SettlementHistory:
    """Deterministic stand-in for an authoritative venue history endpoint."""

    def __init__(self, *, available=True):
        self.available = available
        self.rows = {}

    def add(self, settlement_ts, rate, *, venue="binance", coin="BTC"):
        self.rows.setdefault((venue, coin), []).append(
            FundingSettlement(settlement_ts=float(settlement_ts), rate=float(rate)))

    def __call__(self, *, venue, coin, start_ts, end_ts):
        if not self.available:
            return None
        return tuple(
            row for row in self.rows.get((venue, coin), ())
            if float(start_ts) <= row.settlement_ts <= float(end_ts)
        )


def allow_f1_entry(strategy_id, *, strategy_version):
    return EntryAuthorization(
        allowed=True, reason="fixture_approved_paper", policy="APPROVED_PAPER",
        strategy_id=strategy_id,
    )


def build(tmp_path, clock, provider, **kw):
    kw.setdefault("heartbeat_path", tmp_path / "carry_heartbeat.json")
    kw.setdefault("symbols", ("BTC/USDT",))
    kw.setdefault("entry_authorizer", allow_f1_entry)
    return CarryRunner(
        state_path=tmp_path / "carry_positions.json",
        snapshot_provider=provider,
        now_fn=clock,
        paper_equity=100_000.0,
        gate_log_path=tmp_path / "carry_gate_log.jsonl",
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


@pytest.mark.parametrize("stale_source", ["snapshot", "funding"])
def test_entry_age_uses_observed_time_not_local_receipt(
    tmp_path, stale_source,
):
    clock = FakeClock()
    snap = make_snap(clock())
    if stale_source == "snapshot":
        snap["observed_at"] = clock() - 301.0
        snap["ts"] = clock()       # misleading local timestamp must be ignored
    else:
        snap["funding"]["observed_at"] = clock() - 301.0
        snap["funding"]["ts"] = clock()
        snap["funding"]["age_sec"] = 0.0
    snap["received_at"] = clock()
    snap["funding"]["received_at"] = clock()
    runner = build(tmp_path, clock, lambda symbol: snap)

    gate_inputs = runner._gate_inputs(snap, clock())
    summary = runner.run_once()

    assert gate_inputs["feeds_fresh"] is False
    if stale_source == "funding":
        assert gate_inputs["funding_age_sec"] == pytest.approx(301.0)
    assert summary["opened"] == 0


def test_gate_inputs_asof_received_at_when_cycle_now_is_early(tmp_path):
    """Regression: run_once stamps ``now`` before multi-symbol network I/O.
    Funding observed_at / snap received_at land after that instant and must
    not be treated as future (age=inf → permanent feeds_stale).
    """
    clock = FakeClock()
    cycle_now = clock()
    later = cycle_now + 2.5
    snap = make_snap(later, funding_age_sec=0.0, snapshot_age_sec=0.0)
    snap["received_at"] = later
    snap["funding"]["received_at"] = later
    snap["funding"]["observed_at"] = later
    snap["funding"]["ts"] = later
    snap["stale"] = False
    snap["funding"]["stale"] = False
    runner = build(tmp_path, clock, lambda symbol: snap)

    # Without asof=max(now, received_at), funding_age would be inf.
    gi = runner._gate_inputs(snap, cycle_now)
    assert gi["feeds_fresh"] is True
    assert gi["funding_age_sec"] == pytest.approx(0.0)


def test_entry_recomputes_fillability_from_actual_leg_side_depth(tmp_path):
    clock = FakeClock()
    snap = make_snap(
        clock(),
        spot_buy_depth_notional=150_000.0,
        perp_sell_depth_notional=4_999.0,
    )
    # These legacy aggregate claims deliberately lie. The runner must use the
    # spot-buy ask depth and perp-sell bid depth fields instead.
    snap["depth_ratio"] = 999.0
    snap["both_legs_fillable"] = True
    runner = build(tmp_path, clock, lambda symbol: snap)

    gate_inputs = runner._gate_inputs(snap, clock())
    summary = runner.run_once()

    assert gate_inputs["depth_ratio"] == pytest.approx(4_999.0 / 5_000.0)
    assert gate_inputs["both_legs_fillable"] is False
    assert summary["opened"] == 0


def test_busy_shared_state_lock_skips_without_reading_market_or_writing_state(
    tmp_path,
):
    clock = FakeClock()
    provider_calls = []

    def provider(symbol):
        provider_calls.append(symbol)
        return make_snap(clock())

    runner = build(tmp_path, clock, provider)
    held = acquire_carry_state_lock(runner.state_path)
    assert held is not None
    try:
        summary = runner.run_once()
    finally:
        held.close()

    assert summary["reason"] == "state_lock_busy"
    assert summary["skipped"] == 1
    assert provider_calls == []
    assert not runner.state_path.exists()


def test_entry_policy_denial_runs_f1_gates_but_opens_nothing(tmp_path, monkeypatch):
    import config
    import core.carry_runner as carry_runner

    clock = FakeClock()
    provider = Provider(clock)
    calls = []
    runtime_authorizer = carry_runner.authorize_runtime_entry

    def authorize(strategy_id, *, strategy_version):
        calls.append((strategy_id, strategy_version))
        return runtime_authorizer(strategy_id, strategy_version=strategy_version)

    monkeypatch.setattr(config, "ENTRY_POLICY", "SHADOW_ONLY")
    monkeypatch.setattr(config, "OPERATING_MODE", "PAPER")
    monkeypatch.setattr(carry_runner, "authorize_runtime_entry", authorize)
    r = build(
        tmp_path, clock, provider, entry_authorizer=None,
        failure_hook=lambda symbol: pytest.fail(f"fill attempted for {symbol}"),
    )
    summary = r.run_once()

    assert summary["gate_evals"] == 1
    assert summary["opened"] == 0
    assert calls == [("F1", F1_STRATEGY_VERSION)]
    assert r.load_state()["positions"] == {}
    rec = json.loads(
        (tmp_path / "carry_gate_log.jsonl").read_text().strip().splitlines()[-1])
    assert rec["f1_gate_ok"] is True
    assert rec["entry_policy_allowed"] is False
    assert rec["reason"] == "entry_policy_shadow_only"


def test_entry_policy_denial_does_not_block_held_reconciliation_or_exit(tmp_path):
    clock = FakeClock()
    provider = Provider(clock, interval_hours=8.0, next_in_sec=1 * HOUR)
    history = SettlementHistory()
    first = clock() + HOUR
    history.add(first, -0.0001)

    opener = build(tmp_path, clock, provider, settlement_history_provider=history)
    assert opener.run_once()["opened"] == 1

    calls = []

    def deny(strategy_id, *, strategy_version):
        calls.append((strategy_id, strategy_version))
        return EntryAuthorization(
            allowed=False, reason="entry_policy_shadow_only",
            policy="SHADOW_ONLY", strategy_id=strategy_id,
        )

    provider.kw["rate"] = -0.0001
    provider.pin_next_funding(first + 8 * HOUR)
    clock.advance(HOUR + 1)
    shadow_runner = build(
        tmp_path, clock, provider, entry_authorizer=deny,
        settlement_history_provider=history,
    )
    summary = shadow_runner.run_once()

    assert calls == []
    assert summary["closed"] == 1
    state = shadow_runner.load_state()
    assert state["positions"] == {}
    assert state["cycles"][0]["gross_funding"] < 0.0


# ── full lifecycle: open -> settlements -> exit -> RESOLVED ────────────
def test_full_lifecycle_resolved_cycle_net_is_funding_minus_costs(tmp_path):
    clock = FakeClock()
    provider = Provider(clock)
    history = SettlementHistory()
    first = clock() + 2 * HOUR
    history.add(first, 0.0004)
    history.add(first + 8 * HOUR, 0.0004)
    history.add(first + 16 * HOUR, -0.0001)
    r = build(tmp_path, clock, provider, settlement_history_provider=history)
    s = r.run_once()
    assert s["opened"] == 1
    state = json.loads((tmp_path / "carry_positions.json").read_text())
    pos = state["positions"]["binance:BTC/USDT"]
    notional = pos["notional"]

    # accrue two positive settlements (8h apart), then flip funding negative:
    # the projected net edge goes negative -> exit gate trips on that run.
    provider.pin_next_funding(first + 8 * HOUR)
    clock.advance(2 * HOUR + 1)
    r.run_once()
    provider.pin_next_funding(first + 16 * HOUR)
    clock.advance(8 * HOUR)
    r.run_once()
    provider.kw["rate"] = -0.0001
    provider.pin_next_funding(first + 24 * HOUR)
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
    history = SettlementHistory()
    first = clock() + HOUR
    for i in range(n_expected):
        history.add(first + i * interval_hours * HOUR, 0.0004)
    r = build(tmp_path, clock, provider, settlement_history_provider=history)
    r.run_once()
    provider.pin_next_funding(first + n_expected * interval_hours * HOUR)
    clock.advance(24 * HOUR + 1)
    r.run_once()
    state = json.loads((tmp_path / "carry_positions.json").read_text())
    assert state["positions"]["binance:BTC/USDT"]["settlements_held"] == n_expected


def test_interval_change_mid_hold(tmp_path):
    clock = FakeClock()
    provider = Provider(clock, interval_hours=8.0, next_in_sec=1 * HOUR)
    history = SettlementHistory()
    first = clock() + HOUR
    history.add(first, 0.0004)
    history.add(first + 8 * HOUR, 0.0004)
    history.add(first + 12 * HOUR, 0.0004)
    r = build(tmp_path, clock, provider, settlement_history_provider=history)
    r.run_once()
    provider.pin_next_funding(first + 8 * HOUR)
    clock.advance(8 * HOUR + 1)   # 1 settlement (at +1h), next at +9h (8h iv)
    r.run_once()
    provider.kw["interval_hours"] = 4.0  # venue switches to 4h funding
    provider.pin_next_funding(first + 16 * HOUR)
    clock.advance(8 * HOUR)       # boundaries at +9h and +13h -> 2 more at 4h
    r.run_once()
    state = json.loads((tmp_path / "carry_positions.json").read_text())
    pos = state["positions"]["binance:BTC/USDT"]
    assert pos["settlements_held"] == 3
    assert pos["interval_hours"] == 4.0


def test_missed_settlements_use_realized_rates_and_are_restart_idempotent(tmp_path):
    clock = FakeClock()
    provider = Provider(clock, interval_hours=8.0, next_in_sec=1 * HOUR)
    history = SettlementHistory()
    first = clock() + HOUR
    realized_rates = (0.0001, -0.0002, 0.0003)
    for i, rate in enumerate(realized_rates):
        history.add(first + i * 8 * HOUR, rate)

    r = build(tmp_path, clock, provider, settlement_history_provider=history)
    assert r.run_once()["opened"] == 1
    opened = r.load_state()["positions"]["binance:BTC/USDT"]
    notional = opened["notional"]

    # The fresh frame is deliberately unrelated to every missed settlement.
    provider.kw["rate"] = 0.009
    provider.pin_next_funding(first + 3 * 8 * HOUR)
    clock.advance(24 * HOUR + 1)
    restarted = build(tmp_path, clock, provider, settlement_history_provider=history)
    restarted.run_once()

    pos = restarted.load_state()["positions"]["binance:BTC/USDT"]
    expected = notional * sum(realized_rates)
    assert pos["funding_accrued"] == pytest.approx(expected)
    assert pos["funding_accrued"] != pytest.approx(notional * 3 * 0.009)
    assert pos["settlements_held"] == 3
    assert pos["next_settlement_ts"] == first + 3 * 8 * HOUR

    # A second process replaying the same pass sees the persisted cursor and
    # cannot apply any realized settlement twice.
    replay = build(tmp_path, clock, provider, settlement_history_provider=history)
    replay.run_once()
    replayed = replay.load_state()["positions"]["binance:BTC/USDT"]
    assert replayed["funding_accrued"] == pytest.approx(expected)
    assert replayed["settlements_held"] == 3


def test_runner_defaults_to_realized_venue_history_store(tmp_path):
    clock = FakeClock()
    provider = Provider(clock, interval_hours=8.0, next_in_sec=1 * HOUR)
    first = clock() + HOUR
    history_dir = tmp_path / "realized"
    history_dir.mkdir()
    (history_dir / "binance_BTC.csv").write_text(
        "ts,funding_rate,venue,symbol\n"
        f"{first},0.0001,binance,BTC/USDT:USDT\n",
        encoding="utf-8",
    )
    r = build(tmp_path, clock, provider, settlement_history_dir=history_dir)
    assert r.run_once()["opened"] == 1
    notional = r.load_state()["positions"]["binance:BTC/USDT"]["notional"]

    provider.kw["rate"] = 0.009
    provider.pin_next_funding(first + 8 * HOUR)
    clock.advance(HOUR + 1)
    r.run_once()

    pos = r.load_state()["positions"]["binance:BTC/USDT"]
    assert pos["funding_accrued"] == pytest.approx(notional * 0.0001)
    assert pos["settlements_held"] == 1


def test_missed_settlement_gap_defers_without_partial_state_mutation(tmp_path):
    clock = FakeClock()
    provider = Provider(clock, interval_hours=8.0, next_in_sec=1 * HOUR)
    history = SettlementHistory()
    first = clock() + HOUR
    history.add(first, 0.0001)
    history.add(first + 16 * HOUR, 0.0003)  # +8h boundary is unavailable
    r = build(tmp_path, clock, provider, settlement_history_provider=history)
    assert r.run_once()["opened"] == 1
    before = r.load_state()["positions"]["binance:BTC/USDT"]

    provider.kw["rate"] = 0.009
    provider.pin_next_funding(first + 24 * HOUR)
    clock.advance(24 * HOUR + 1)
    r.run_once()

    after = r.load_state()["positions"]["binance:BTC/USDT"]
    for field in (
        "funding_accrued", "settlements_held", "next_settlement_ts",
        "consec_negative_settlements",
    ):
        assert after[field] == before[field]
    # Funding accounting deferred, but held-position safety checks still ran.
    assert after["runs_seen"] == before["runs_seen"] + 1
    rec = json.loads(
        (tmp_path / "carry_gate_log.jsonl").read_text().strip().splitlines()[-1])
    assert rec["reason"] == "funding_reconciliation_deferred"
    assert rec["detail"] == "settlement_history_gap"


def test_missed_settlement_unavailable_history_defers_fail_closed(tmp_path):
    clock = FakeClock()
    provider = Provider(clock, interval_hours=8.0, next_in_sec=1 * HOUR)
    history = SettlementHistory(available=False)
    first = clock() + HOUR
    r = build(tmp_path, clock, provider, settlement_history_provider=history)
    assert r.run_once()["opened"] == 1

    provider.pin_next_funding(first + 8 * HOUR)
    clock.advance(HOUR + 1)
    summary = r.run_once()

    pos = r.load_state()["positions"]["binance:BTC/USDT"]
    assert summary["held"] == 1 and summary["closed"] == 0
    assert pos["funding_accrued"] == 0.0
    assert pos["settlements_held"] == 0
    assert pos["next_settlement_ts"] == first


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


# ── W1: per-position liquidation model (fill_reality) ─────────────────
def test_entry_gate_logs_computed_liq_buffer_and_ignores_lying_snapshot(tmp_path):
    clock = FakeClock()
    provider = Provider(clock, liq_buffer_x=0.5)  # lying snapshot: would fail gate
    r = build(tmp_path, clock, provider)
    s = r.run_once()
    assert s["opened"] == 1  # computed 1x buffer (~200x) wins over the lie
    rec = json.loads(
        (tmp_path / "carry_gate_log.jsonl").read_text().strip().splitlines()[-1])
    assert rec["ok"] is True
    assert rec["liq_buffer_x"] == pytest.approx(200.0, rel=0.05)
    # the position persists the per-position liquidation-model inputs
    state = json.loads((tmp_path / "carry_positions.json").read_text())
    pos = state["positions"]["binance:BTC/USDT"]
    assert pos["perp_leverage"] == 1.0
    assert pos["mmr"] == 0.005


def test_exit_fires_on_computed_margin_buffer_despite_lying_snapshot(tmp_path):
    clock = FakeClock()
    provider = Provider(clock, liq_buffer_x=10.0)  # snapshot claims all is well
    r = build(tmp_path, clock, provider)
    assert r.run_once()["opened"] == 1
    # perp mark ~2x above entry: stored-entry margin buffer collapses to 0.
    provider.kw["spot"] = 200.0
    provider.kw["perp"] = 200.04
    clock.advance(60.0)
    s = r.run_once()
    assert s["closed"] == 1
    state = json.loads((tmp_path / "carry_positions.json").read_text())
    (cyc,) = state["cycles"]
    assert "margin_buffer" in cyc["exit_reason"]


def test_entry_fail_closed_when_buffer_computation_raises(tmp_path, monkeypatch):
    import core.carry_runner as cr

    def boom(**kw):
        raise RuntimeError("no mark")

    monkeypatch.setattr(cr, "perp_short_margin_buffer_x", boom)
    clock = FakeClock()
    provider = Provider(clock, liq_buffer_x=5.0)  # healthy snap must NOT rescue entry
    r = build(tmp_path, clock, provider)
    s = r.run_once()
    assert s["opened"] == 0
    rec = json.loads(
        (tmp_path / "carry_gate_log.jsonl").read_text().strip().splitlines()[-1])
    assert rec["ok"] is False
    assert rec["liq_buffer_uncomputable"] is True
    assert "liquidation" in rec["reason"]


def test_exit_fallback_to_snapshot_buffer_is_logged(tmp_path, monkeypatch):
    import core.carry_runner as cr

    clock = FakeClock()
    provider = Provider(clock, liq_buffer_x=10.0)
    r = build(tmp_path, clock, provider)
    assert r.run_once()["opened"] == 1

    def boom(**kw):
        raise RuntimeError("no mark")

    monkeypatch.setattr(cr, "perp_short_margin_buffer_x", boom)
    clock.advance(60.0)
    s = r.run_once()
    assert s["held"] == 1 and s["closed"] == 0  # snap fallback 10x > 3x floor
    recs = [json.loads(x) for x in
            (tmp_path / "carry_gate_log.jsonl").read_text().strip().splitlines()]
    assert any(rec.get("liq_buffer_fallback") is True for rec in recs)


# ── W6: liveness heartbeat ─────────────────────────────────────────────
def test_run_once_writes_heartbeat_with_ts_and_summary(tmp_path):
    clock = FakeClock()
    provider = Provider(clock)
    hb = tmp_path / "carry_heartbeat.json"
    r = build(tmp_path, clock, provider, heartbeat_path=hb)
    summary = r.run_once()
    payload = json.loads(hb.read_text(encoding="utf-8"))
    assert payload["ts"] == clock()
    assert payload["venue"] == "binance"
    assert payload["summary"] == summary
    assert not list(tmp_path.glob("*.tmp"))  # atomic write, no temp litter


def test_heartbeat_path_none_disables_write(tmp_path):
    clock = FakeClock()
    provider = Provider(clock)
    r = build(tmp_path, clock, provider, heartbeat_path=None)
    r.run_once()
    assert not list(tmp_path.glob("carry_heartbeat*"))


def test_heartbeat_unwritable_path_does_not_raise(tmp_path):
    clock = FakeClock()
    provider = Provider(clock)
    blocker = tmp_path / "blocker"
    blocker.write_text("x")  # a FILE where the heartbeat's parent dir must go
    r = build(tmp_path, clock, provider, heartbeat_path=blocker / "hb.json")
    summary = r.run_once()  # must not raise
    assert summary["opened"] == 1


# ── W2: multi-venue state keying, migration, concentration pin ────────
def test_two_venues_share_one_state_file_with_venue_keyed_positions(tmp_path):
    clock = FakeClock()
    provider = Provider(clock)
    rb = build(tmp_path, clock, provider)                  # binance (default)
    ry = build(tmp_path, clock, provider, venue="bybit")
    sb = rb.run_once()
    sy = ry.run_once()
    assert sb["venue"] == "binance" and sy["venue"] == "bybit"
    assert sb["opened"] == 1 and sy["opened"] == 1
    state = json.loads((tmp_path / "carry_positions.json").read_text())
    assert set(state["positions"]) == {"binance:BTC/USDT", "bybit:BTC/USDT"}


def test_three_venues_share_one_state_file_with_venue_keyed_positions(tmp_path):
    clock = FakeClock()
    provider = Provider(clock)
    summaries = [build(tmp_path, clock, provider, venue=v).run_once()
                 for v in ("binance", "bybit", "bitget")]
    assert [s["venue"] for s in summaries] == ["binance", "bybit", "bitget"]
    assert all(s["opened"] == 1 for s in summaries)
    state = json.loads((tmp_path / "carry_positions.json").read_text())
    assert set(state["positions"]) == {
        "binance:BTC/USDT", "bybit:BTC/USDT", "bitget:BTC/USDT"}


def test_bitget_block_does_not_block_other_venues(tmp_path):
    clock = FakeClock()
    provider = Provider(clock)

    def hook(symbol):
        return {"spot_fill_frac": 1.0, "perp_fill_frac": 0.0}

    rg = build(tmp_path, clock, provider, venue="bitget", failure_hook=hook)
    assert rg.run_once()["failed"] == 1
    state = json.loads((tmp_path / "carry_positions.json").read_text())
    assert "bitget:BTC/USDT" in state["blocks"]
    assert "binance:BTC/USDT" not in state["blocks"]
    # Rev 5.2: the one-leg failure latches portfolio-wide recovery; clear it
    # so we can see that the BLOCK itself stays scoped to bitget only.
    state["recovery"] = {"active": False}
    (tmp_path / "carry_positions.json").write_text(json.dumps(state))
    sb = build(tmp_path, clock, provider).run_once()                  # binance
    sy = build(tmp_path, clock, provider, venue="bybit").run_once()
    assert sb["opened"] == 1 and sy["opened"] == 1
    sg = build(tmp_path, clock, provider, venue="bitget").run_once()  # no hook
    assert sg["opened"] == 0 and sg["blocked"] == 1
    state = json.loads((tmp_path / "carry_positions.json").read_text())
    assert "bitget:BTC/USDT" in state["blocks"]
    assert {"binance:BTC/USDT", "bybit:BTC/USDT"} <= set(state["positions"])


def test_bitget_venue_books_bitget_fee_schedule_on_entry(tmp_path):
    # config.FEE: bitget_spot_taker (0.001) + bitget_futures_taker (0.0006),
    # both taker in the default execution mode.
    clock = FakeClock()
    provider = Provider(clock)
    r = build(tmp_path, clock, provider, venue="bitget")
    assert r.run_once()["opened"] == 1
    state = json.loads((tmp_path / "carry_positions.json").read_text())
    pos = state["positions"]["bitget:BTC/USDT"]
    assert pos["entry_fees"] == pytest.approx(pos["notional"] * 0.0016)


def test_bybit_block_does_not_block_binance(tmp_path):
    clock = FakeClock()
    provider = Provider(clock)

    def hook(symbol):
        return {"spot_fill_frac": 1.0, "perp_fill_frac": 0.0}

    ry = build(tmp_path, clock, provider, venue="bybit", failure_hook=hook)
    assert ry.run_once()["failed"] == 1
    rb = build(tmp_path, clock, provider)  # binance, no failure hook
    s = rb.run_once()
    # Rev 5.2: the one-leg failure latches portfolio-wide recovery, so the
    # binance open is refused by the LATCH (reduce_only_recovery), NOT by a
    # cross-venue block — the block itself stays venue-scoped.
    assert s["opened"] == 0 and s["blocked"] == 0
    state = json.loads((tmp_path / "carry_positions.json").read_text())
    assert "bybit:BTC/USDT" in state["blocks"]
    assert "binance:BTC/USDT" not in state["blocks"]
    # once the operator clears recovery, binance opens; bybit stays blocked
    state["recovery"] = {"active": False}
    (tmp_path / "carry_positions.json").write_text(json.dumps(state))
    s2 = rb.run_once()
    assert s2["opened"] == 1 and s2["blocked"] == 0
    state = json.loads((tmp_path / "carry_positions.json").read_text())
    assert "bybit:BTC/USDT" in state["blocks"]
    assert "binance:BTC/USDT" in state["positions"]


def test_legacy_bare_symbol_state_migrates_on_load_and_is_managed(tmp_path):
    clock = FakeClock()
    provider = Provider(clock)
    r = build(tmp_path, clock, provider)
    assert r.run_once()["opened"] == 1
    # rewrite the state file with a legacy bare-symbol position key
    p = tmp_path / "carry_positions.json"
    state = json.loads(p.read_text())
    state["positions"] = {"BTC/USDT": state["positions"]["binance:BTC/USDT"]}
    p.write_text(json.dumps(state))
    s = r.run_once()
    assert s["held"] == 1  # migrated key is picked up and managed
    state = json.loads(p.read_text())
    assert set(state["positions"]) == {"binance:BTC/USDT"}


def test_promotion_checklist_venue_concentration_regression_pin():
    """Structural pin: binance-only history CANNOT pass the concentration gate."""
    from core.carry_runner import promotion_checklist

    def cyc(venue, symbol, pnl):
        return {"symbol": symbol, "venue": venue, "net_pnl": pnl,
                "gross_funding": pnl, "basis_pnl": 0.0, "fees": 0.0,
                "label_status": "RESOLVED"}

    binance_only = [cyc("binance", s, 1.0)
                    for s in ("BTC/USDT", "ETH/USDT") for _ in range(5)]
    chk = promotion_checklist(binance_only, {})
    assert chk["concentration"]["pass"] is False
    assert chk["concentration"]["venue_share_pct"] == pytest.approx(100.0)

    split = [
        cyc(venue, symbol, 1.0)
        for venue in ("binance", "bybit", "bitget")
        for symbol in ("BTC/USDT", "ETH/USDT", "SOL/USDT")
        for _ in range(5)
    ]
    chk2 = promotion_checklist(split, {})
    assert chk2["concentration"]["pass"] is True
    assert chk2["concentration"]["venue_share_pct"] == pytest.approx(100.0 / 3.0)


def test_report_renders_live_activation_preconditions(tmp_path):
    from core.carry_runner import LIVE_ACTIVATION_PRECONDITIONS

    assert len(LIVE_ACTIVATION_PRECONDITIONS) == 3
    clock = FakeClock()
    provider = Provider(clock)
    r = build(tmp_path, clock, provider)
    r.run_once()
    out = write_report(r.load_state(), out_dir=tmp_path / "reports", now_fn=clock)
    text = Path(out).read_text(encoding="utf-8")
    assert "Live-activation preconditions" in text
    assert text.count("[UNMET]") == 3
    assert "Live activation is prohibited while any item is unmet." in text
    # rendered AFTER the promotion checklist
    assert text.index("Promotion-gate checklist") < text.index(
        "Live-activation preconditions")


def test_build_f1_spec_paper_venues_require_latch():
    # extends tests/test_f1_rev5_gates.py latch coverage with the exact
    # three-venue set the paper script now uses (kept here: file ownership).
    from research.funding_carry_lab import build_f1_spec

    with pytest.raises(ValueError):
        build_f1_spec(venues=("binance", "bybit", "bitget"))
    spec = build_f1_spec(symbols=("BTC/USDT", "ETH/USDT"),
                         venues=("binance", "bybit", "bitget"),
                         allow_extended_universe=True)
    assert spec.venues == ["binance", "bybit", "bitget"]


# ── Rev 5.2: portfolio-wide reduce-only recovery ───────────────────────
def _fail_perp(symbol):
    return {"spot_fill_frac": 1.0, "perp_fill_frac": 0.0}


def _gate_recs(tmp_path):
    return [json.loads(x) for x in
            (tmp_path / "carry_gate_log.jsonl").read_text().strip().splitlines()]


def test_one_leg_failure_latches_recovery_portfolio_wide(tmp_path):
    clock = FakeClock()
    provider = Provider(clock)
    r = build(tmp_path, clock, provider, failure_hook=_fail_perp)
    assert r.run_once()["failed"] == 1
    state = json.loads((tmp_path / "carry_positions.json").read_text())
    rec = state["recovery"]
    assert rec["active"] is True
    assert rec["reason"].startswith("one_leg_failure:")
    assert rec["requires_manual_review"] is True
    assert rec["venue"] == "binance" and rec["symbol"] == "BTC/USDT"

    # NEXT pass: same venue, DIFFERENT symbol -> refused portfolio-wide
    r2 = build(tmp_path, clock, provider, symbols=("ETH/USDT",))
    s2 = r2.run_once()
    assert s2["opened"] == 0 and s2["gate_evals"] == 0
    last = _gate_recs(tmp_path)[-1]
    assert last["reason"] == "reduce_only_recovery" and last["ok"] is False
    assert last["symbol"] == "ETH/USDT"

    # OTHER venue (fresh runner, same shared state file) -> also refused
    r3 = build(tmp_path, clock, provider, venue="bybit")
    s3 = r3.run_once()
    assert s3["opened"] == 0 and s3["gate_evals"] == 0
    last = _gate_recs(tmp_path)[-1]
    assert last["reason"] == "reduce_only_recovery" and last["venue"] == "bybit"


def test_notional_mismatch_exit_latches_recovery(tmp_path):
    clock = FakeClock()
    provider = Provider(clock)
    r = build(tmp_path, clock, provider)
    assert r.run_once()["opened"] == 1
    # spot rallies vs perp: hedge legs drift apart >1% of notional while the
    # basis move is favorable and margin is healthy -> ONLY gate 5 can fire.
    provider.kw["spot"] = 102.0
    provider.kw["perp"] = 100.0
    clock.advance(60.0)
    s = r.run_once()
    assert s["closed"] == 1 and s["recovery_active"] is True
    state = json.loads((tmp_path / "carry_positions.json").read_text())
    (cyc,) = state["cycles"]
    assert cyc["exit_reason"].startswith("notional_mismatch")
    rec = state["recovery"]
    assert rec["active"] is True
    assert rec["reason"].startswith("notional_mismatch_exit:")


def test_max_hold_exit_does_not_latch_recovery(tmp_path):
    clock = FakeClock()
    provider = Provider(clock)
    history = SettlementHistory()
    first = clock() + 2 * HOUR
    for i in range(42):
        history.add(first + i * 8 * HOUR, 0.0004)
    r = build(tmp_path, clock, provider, settlement_history_provider=history)
    assert r.run_once()["opened"] == 1
    provider.pin_next_funding(first + 42 * 8 * HOUR)
    clock.advance(42 * 8 * HOUR + 1)  # 42 settlements -> max_hold_window exit
    s = r.run_once()
    assert s["closed"] == 1 and s["recovery_active"] is False
    state = json.loads((tmp_path / "carry_positions.json").read_text())
    (cyc,) = state["cycles"]
    assert cyc["exit_reason"].startswith("max_hold_window")
    assert state["recovery"] == {"active": False}


def test_reduce_only_position_accrues_and_closes_while_latched(tmp_path):
    clock = FakeClock()
    provider = Provider(clock)
    history = SettlementHistory()
    first = clock() + 2 * HOUR
    history.add(first, 0.0004)
    history.add(first + 8 * HOUR, -0.0001)

    def hook(symbol):
        return _fail_perp(symbol) if symbol == "ETH/USDT" else None

    r = build(tmp_path, clock, provider,
              symbols=("BTC/USDT", "ETH/USDT"), failure_hook=hook,
              settlement_history_provider=history)
    s = r.run_once()
    assert s["opened"] == 1 and s["failed"] == 1 and s["recovery_active"] is True

    # latched: the BTC position still accrues its settlement normally ...
    provider.pin_next_funding(first + 8 * HOUR)
    clock.advance(2 * HOUR + 1)
    s = r.run_once()
    assert s["held"] == 1 and s["closed"] == 0
    state = json.loads((tmp_path / "carry_positions.json").read_text())
    assert state["positions"]["binance:BTC/USDT"]["settlements_held"] == 1

    # ... and can still close (reduce-only: exits keep firing)
    provider.kw["rate"] = -0.0001
    provider.pin_next_funding(first + 16 * HOUR)
    clock.advance(8 * HOUR)
    s = r.run_once()
    assert s["closed"] == 1 and s["recovery_active"] is True
    state = json.loads((tmp_path / "carry_positions.json").read_text())
    assert state["positions"] == {}
    resolved = [c for c in state["cycles"] if c["label_status"] == "RESOLVED"]
    assert len(resolved) == 1


def test_latch_mid_pass_stops_later_symbols_same_pass(tmp_path):
    clock = FakeClock()
    provider = Provider(clock)

    def hook(symbol):
        return _fail_perp(symbol) if symbol == "BTC/USDT" else None

    r = build(tmp_path, clock, provider,
              symbols=("BTC/USDT", "ETH/USDT"), failure_hook=hook)
    s = r.run_once()
    assert s["failed"] == 1 and s["opened"] == 0
    assert s["recovery_active"] is True
    recs = _gate_recs(tmp_path)
    assert any(rec.get("reason") == "reduce_only_recovery"
               and rec.get("symbol") == "ETH/USDT" for rec in recs)


def test_latch_idempotent_keeps_original_reason_and_ts(tmp_path):
    clock = FakeClock()
    provider = Provider(clock)

    def hook(symbol):
        return _fail_perp(symbol) if symbol == "ETH/USDT" else None

    r = build(tmp_path, clock, provider,
              symbols=("BTC/USDT", "ETH/USDT"), failure_hook=hook)
    assert r.run_once()["recovery_active"] is True
    state = json.loads((tmp_path / "carry_positions.json").read_text())
    orig = dict(state["recovery"])
    assert orig["reason"].startswith("one_leg_failure:")

    # second trigger while latched: the held BTC position mismatch-exits
    provider.kw["spot"] = 102.0
    provider.kw["perp"] = 100.0
    clock.advance(60.0)
    assert r.run_once()["closed"] == 1
    state = json.loads((tmp_path / "carry_positions.json").read_text())
    assert state["recovery"]["reason"] == orig["reason"]
    assert state["recovery"]["ts"] == orig["ts"]
    # the second trigger is still gate-logged
    latch_logs = [x for x in _gate_recs(tmp_path) if x.get("recovery_latched")]
    assert len(latch_logs) == 2


def test_legacy_state_without_recovery_key_migrates_inactive(tmp_path):
    p = tmp_path / "carry_positions.json"
    p.write_text(json.dumps({"positions": {}, "blocks": {}, "cycles": []}))
    clock = FakeClock()
    provider = Provider(clock)
    r = build(tmp_path, clock, provider)
    assert r.load_state()["recovery"] == {"active": False}
    s = r.run_once()
    assert s["opened"] == 1 and s["recovery_active"] is False
    state = json.loads(p.read_text())
    assert state["recovery"] == {"active": False}


def test_summary_and_heartbeat_reflect_recovery_state(tmp_path):
    clock = FakeClock()
    provider = Provider(clock)
    hb = tmp_path / "carry_heartbeat.json"
    r = build(tmp_path, clock, provider, heartbeat_path=hb)
    s = r.run_once()
    assert s["recovery_active"] is False
    assert json.loads(hb.read_text())["summary"]["recovery_active"] is False

    r2 = build(tmp_path, clock, provider, symbols=("ETH/USDT",),
               failure_hook=_fail_perp, heartbeat_path=hb)
    s2 = r2.run_once()
    assert s2["recovery_active"] is True
    assert json.loads(hb.read_text())["summary"]["recovery_active"] is True


def test_clear_recovery_resets_latch_and_archives_history(tmp_path, capsys):
    from scripts.run_f1_carry_paper import clear_recovery

    clock = FakeClock()
    provider = Provider(clock)
    r = build(tmp_path, clock, provider, failure_hook=_fail_perp)
    assert r.run_once()["recovery_active"] is True
    p = tmp_path / "carry_positions.json"

    assert clear_recovery(state_path=p) is True
    out = capsys.readouterr().out
    assert "one_leg_failure:" in out and "cleared" in out
    state = json.loads(p.read_text())
    assert state["recovery"] == {"active": False}
    (hist,) = state["recovery_history"]
    assert hist["active"] is True and hist["reason"].startswith("one_leg_failure:")
    assert hist["cleared_ts"] > 0
    assert not list(tmp_path.glob("*.tmp"))  # atomic save, no temp litter

    # next pass opens normally again (the venue:symbol block stays put)
    r2 = build(tmp_path, clock, provider, symbols=("ETH/USDT",))
    assert r2.run_once()["opened"] == 1

    # not latched -> no-op (and a missing state file is also a no-op)
    assert clear_recovery(state_path=p) is False
    assert "not latched" in capsys.readouterr().out
    assert len(json.loads(p.read_text())["recovery_history"]) == 1
    assert clear_recovery(state_path=tmp_path / "missing.json") is False


def test_null_recovery_state_normalizes_inactive(tmp_path):
    # Hand-edited state with "recovery": null must load as inactive, not crash.
    clock = FakeClock()
    provider = Provider(clock)
    r = build(tmp_path, clock, provider)
    p = tmp_path / "carry_positions.json"
    p.write_text(json.dumps(
        {"positions": {}, "blocks": {}, "cycles": [], "recovery": None}))
    s = r.run_once()
    assert s["recovery_active"] is False
    assert s["opened"] == 1


def test_one_leg_latch_reason_has_single_prefix(tmp_path):
    clock = FakeClock()
    provider = Provider(clock)
    r = build(tmp_path, clock, provider, failure_hook=_fail_perp)
    assert r.run_once()["failed"] == 1
    rec = json.loads((tmp_path / "carry_positions.json").read_text())["recovery"]
    assert rec["reason"].startswith("one_leg_failure:")
    assert not rec["reason"].startswith("one_leg_failure:one_leg_failure")


def test_clear_recovery_corrupt_state_file_is_safe(tmp_path, capsys):
    from scripts.run_f1_carry_paper import clear_recovery

    p = tmp_path / "carry_positions.json"
    p.write_text("{not json", encoding="utf-8")
    assert clear_recovery(state_path=p) is False
    assert "cannot read state file" in capsys.readouterr().out
    assert p.read_text(encoding="utf-8") == "{not json"  # file untouched


def test_clear_recovery_drops_heartbeat_flag(tmp_path):
    # Clearing must also drop the heartbeat's flag so the watchdog re-arms
    # immediately (per-episode alerting) rather than at the next pass.
    from scripts.run_f1_carry_paper import clear_recovery

    clock = FakeClock()
    provider = Provider(clock)
    hb = tmp_path / "carry_heartbeat.json"
    r = build(tmp_path, clock, provider, failure_hook=_fail_perp, heartbeat_path=hb)
    assert r.run_once()["recovery_active"] is True
    assert json.loads(hb.read_text())["summary"]["recovery_active"] is True

    assert clear_recovery(state_path=tmp_path / "carry_positions.json",
                          heartbeat_path=hb) is True
    assert json.loads(hb.read_text())["summary"]["recovery_active"] is False


def test_report_measured_accuracy_is_honest(tmp_path):
    # Win rate is reported as measured, flagged on small samples, and framed
    # as informational — never a promotion gate.
    from core.carry_runner import write_report

    def _cyc(net):
        return {"symbol": "BTC/USDT", "venue": "binance", "net_pnl": net,
                "gross_funding": max(net, 0.0), "basis_pnl": 0.0, "fees": 0.01,
                "slippage": 0.01, "label_status": "RESOLVED",
                "strategy_version": "correctness-v1"}

    state = {"positions": {}, "blocks": {},
             "cycles": [_cyc(1.0), _cyc(0.5), _cyc(0.25), _cyc(0.1), _cyc(-0.6)],
             "recovery": {"active": False}}
    p = write_report(state, out_dir=tmp_path, now_fn=lambda: 1_783_000_000.0)
    text = p.read_text(encoding="utf-8")
    assert "Measured accuracy (informational — NOT a promotion gate)" in text
    assert "80.0% (n=5) — insufficient sample" in text
    assert "Win rate is not expectancy" in text

    empty = {"positions": {}, "blocks": {}, "cycles": [],
             "recovery": {"active": False}}
    text0 = write_report(empty, out_dir=tmp_path / "e",
                         now_fn=lambda: 1_783_000_000.0).read_text(encoding="utf-8")
    assert "n/a (0 resolved cycles" in text0


def _pos(tmp_path):
    return json.loads(
        (tmp_path / "carry_positions.json").read_text())["positions"]["binance:BTC/USDT"]


def test_maker_first_without_queue_evidence_books_taker_fill(tmp_path):
    # Spread width alone is not fill evidence. Even a wide quote must use the
    # pessimistic taker model until a resting-order event simulator exists.
    clock = FakeClock()
    provider = Provider(clock, spread_bps=4.0, rate=0.0010)
    r = build(tmp_path, clock, provider, execution_mode="maker_first")
    assert r.run_once()["opened"] == 1
    pos = _pos(tmp_path)
    assert pos["execution_mode"] == "maker_first"
    assert pos["execution_liquidity"] == "taker"
    assert pos["maker_fill_verified"] is False
    assert pos["spot_entry_px"] > 100.0
    assert abs(pos["round_trip_cost_frac"] - 0.0002) < 1e-9


def test_maker_first_narrow_spread_falls_back_to_taker(tmp_path):
    # Below the 2bps maker threshold there is no room to rest -> taker cross.
    # This is the anti-optimism guard: the model never credits an impossible fill.
    clock = FakeClock()
    provider = Provider(clock, spread_bps=1.0, rate=0.0010)
    r = build(tmp_path, clock, provider, execution_mode="maker_first")
    assert r.run_once()["opened"] == 1
    pos = _pos(tmp_path)
    assert pos["execution_mode"] == "maker_first"
    assert pos["spot_entry_px"] > 100.0                          # crossed at ask+slip
    assert abs(pos["round_trip_cost_frac"] - 0.0002) < 1e-9      # taker baseline (snap)


def test_taker_mode_fills_spot_at_ask_plus_slip(tmp_path):
    clock = FakeClock()
    provider = Provider(clock, spread_bps=4.0, rate=0.0010)
    r = build(tmp_path, clock, provider)  # default taker
    assert r.run_once()["opened"] == 1
    pos = _pos(tmp_path)
    assert pos["execution_mode"] == "taker"
    assert abs(pos["spot_entry_px"] - 100.02 * (1.0 + 0.0005)) < 1e-6


def test_invalid_execution_mode_raises(tmp_path):
    import pytest
    with pytest.raises(ValueError):
        build(tmp_path, FakeClock(), Provider(FakeClock()), execution_mode="bogus")


# ── FIX 1: gate cost == booked full-carry (spot+perp, 4 crossings) cost ─
def test_carry_round_trip_cost_is_full_four_crossing_taker_cost():
    # The provider must feed the gate the SAME cost the runner books in taker
    # mode: spot round-trip fee + perp round-trip fee + slippage on all 4
    # crossings, NOT the futures-only ~20bps it fed before.
    #   binance: 2*.001(spot) + 2*.0005(perp) + 4*.0005(slip) = .005 (~50bps).
    from core.cost_model import round_trip_cost
    from scripts.run_f1_carry_paper import carry_round_trip_cost_frac

    assert carry_round_trip_cost_frac("binance") == pytest.approx(0.005)
    # strictly larger than the old futures-only round trip (~20bps) it replaces
    assert carry_round_trip_cost_frac("binance") > round_trip_cost(
        "binance", market_type="futures")
    # bybit/bitget carry the higher perp taker (0.0006): 2*.001+2*.0006+4*.0005
    for v in ("bybit", "bitget"):
        assert carry_round_trip_cost_frac(v) == pytest.approx(0.0052)


def test_true_carry_cost_raises_entry_floor_and_is_stored(tmp_path):
    # rate 5bps/settlement x 21 = 105bps gross.
    #   old futures-only cost 20bps -> floor max(15, 3*20)=60bps; net 85 -> OPEN
    #   true carry cost   50bps -> floor max(15, 3*50)=150bps; net 55 -> REJECT
    clock = FakeClock()
    r_old = build(tmp_path / "old", clock, Provider(clock, rate=0.0005, rt_cost=0.002))
    assert r_old.run_once()["opened"] == 1

    clock2 = FakeClock()
    r_true = build(tmp_path / "true", clock2, Provider(clock2, rate=0.0005, rt_cost=0.005))
    assert r_true.run_once()["opened"] == 0
    rec = json.loads(
        (tmp_path / "true" / "carry_gate_log.jsonl").read_text().strip().splitlines()[-1])
    assert rec["ok"] is False and "edge" in rec["reason"]

    # at a richer rate the true-cost position opens and STORES the full cost
    clock3 = FakeClock()
    r_rich = build(tmp_path / "rich", clock3, Provider(clock3, rate=0.0020, rt_cost=0.005))
    assert r_rich.run_once()["opened"] == 1
    pos = json.loads((tmp_path / "rich" / "carry_positions.json").read_text())[
        "positions"]["binance:BTC/USDT"]
    assert pos["round_trip_cost_frac"] == pytest.approx(0.005)


# ── stale entry data blocks opens but never suppresses held safety checks ──
def test_stale_funding_disables_only_rate_exit_and_still_manages(tmp_path):
    clock = FakeClock()
    provider = Provider(clock)
    r = build(tmp_path, clock, provider)
    assert r.run_once()["opened"] == 1

    # A stale negative quote cannot prove edge deterioration, so it must not
    # force a rate exit. The held position is nevertheless managed this pass.
    provider.kw["stale"] = True
    provider.kw["rate"] = -0.0001
    clock.advance(60.0)
    s = r.run_once()
    assert s["held"] == 1 and s["closed"] == 0
    state = json.loads((tmp_path / "carry_positions.json").read_text())
    assert "binance:BTC/USDT" in state["positions"]   # still open
    assert state["cycles"] == []                       # nothing booked
    assert state["positions"]["binance:BTC/USDT"]["runs_seen"] == 1
    assert any("stale" in (x.get("reason") or "") for x in _gate_recs(tmp_path))

    # a FRESH negative-edge frame then closes normally (no regression)
    provider.kw["stale"] = False
    clock.advance(60.0)
    assert r.run_once()["closed"] == 1
    state = json.loads((tmp_path / "carry_positions.json").read_text())
    assert state["positions"] == {}
    (cyc,) = state["cycles"]
    assert "net_edge" in cyc["exit_reason"]


def test_stale_funding_does_not_skip_notional_mismatch_exit(tmp_path):
    clock = FakeClock()
    provider = Provider(clock)
    runner = build(tmp_path, clock, provider)
    assert runner.run_once()["opened"] == 1

    state = runner.load_state()
    state["positions"]["binance:BTC/USDT"]["perp_qty"] *= 0.95
    runner.save_state(state)
    provider.kw.update(stale=True, rate=-0.0001)
    clock.advance(60.0)

    summary = runner.run_once()
    state = runner.load_state()

    assert summary["closed"] == 1
    assert state["positions"] == {}
    assert "notional_mismatch" in state["cycles"][-1]["exit_reason"]
    assert state["recovery"]["active"] is True


def test_stale_funding_does_not_skip_margin_exit(tmp_path, monkeypatch):
    import core.carry_runner as carry_runner

    clock = FakeClock()
    provider = Provider(clock)
    runner = build(tmp_path, clock, provider)
    assert runner.run_once()["opened"] == 1

    monkeypatch.setattr(
        carry_runner,
        "perp_short_margin_buffer_x",
        lambda **kwargs: {"buffer_x": 2.0},
    )
    provider.kw["stale"] = True
    clock.advance(60.0)

    assert runner.run_once()["closed"] == 1
    assert "margin_buffer" in runner.load_state()["cycles"][-1]["exit_reason"]


def test_stale_market_snapshot_runs_and_trips_stale_leg_exit(tmp_path):
    clock = FakeClock()
    provider = Provider(clock)
    runner = build(tmp_path, clock, provider)
    assert runner.run_once()["opened"] == 1

    provider.kw["snapshot_age_sec"] = 301.0
    clock.advance(60.0)

    assert runner.run_once()["closed"] == 1
    assert "stale_hedge_leg" in runner.load_state()["cycles"][-1]["exit_reason"]


def test_bbo_depth_counts_only_the_executable_leg_side():
    from scripts.run_f1_carry_paper import _bbo_depth

    observed_ms = 1_700_000_000_000
    book = {
        "timestamp": observed_ms,
        "bids": [[100.0, 100.0], [99.0, 100.0]],
        "asks": [[101.0, 1.0], [102.0, 2.0]],
    }

    spot_buy = _bbo_depth(book, executable_side="buy")
    perp_sell = _bbo_depth(book, executable_side="sell")

    assert spot_buy[:3] == pytest.approx((100.0, 101.0, 305.0))
    assert perp_sell[:3] == pytest.approx((100.0, 101.0, 19_900.0))
    assert spot_buy[3] == pytest.approx(1_700_000_000.0)


def test_live_snapshot_provider_wires_source_time_and_side_depth(
    monkeypatch,
):
    import core.market_data_ledger as ledger_module
    import exchanges.binance_client as binance_module
    import scripts.run_f1_carry_paper as carry_script

    now = 1_700_000_100.0
    spot_book = {
        "timestamp": (now - 20.0) * 1000.0,
        "bids": [[100.0, 100.0]],       # abundant but not executable for buy
        "asks": [[101.0, 2.0], [102.0, 1.0]],
    }
    perp_book = {
        "timestamp": (now - 10.0) * 1000.0,
        "bids": [[100.0, 6.0]],         # executable for the short hedge
        "asks": [[101.0, 100.0]],
    }

    class FakeClient:
        exchange = SimpleNamespace(
            market_id=lambda symbol: symbol.replace("/", "").replace(":USDT", "")
        )

        def fetch_order_book(self, symbol, limit, market_type):
            return spot_book if market_type == "spot" else perp_book

    class FakeLedger:
        def __init__(self, clients):
            pass

        def mark_index(self, symbol):
            return {"binance": {"mark": 100.5, "ts": now - 5.0}}

        def funding(self, coins):
            return {
                "BTC": {
                    "binance": {
                        "rate": 0.0004,
                        "interval_hours": 8.0,
                        "next_funding_ts": now + HOUR,
                        "ts": now - 30.0,
                        "stale": False,
                    }
                }
            }

    monkeypatch.setattr(binance_module, "BinanceClient", FakeClient)
    monkeypatch.setattr(ledger_module, "MarketDataLedger", FakeLedger)
    monkeypatch.setattr(carry_script.time, "time", lambda: now)
    monkeypatch.setattr(carry_script, "avg_7d", lambda *args, **kwargs: 0.0003)
    monkeypatch.setattr(
        carry_script,
        "load_recent_realized_settlements",
        lambda *args, **kwargs: tuple(SimpleNamespace(rate=0.0004) for _ in range(21)),
    )

    snapshot = carry_script.build_live_snapshot_provider("binance")("BTC/USDT")

    assert snapshot["spot_buy_depth_notional"] == pytest.approx(304.0)
    assert snapshot["perp_sell_depth_notional"] == pytest.approx(600.0)
    assert snapshot["depth_ratio"] == pytest.approx(304.0 / 500.0)
    assert snapshot["both_legs_fillable"] is False
    assert snapshot["observed_at"] == pytest.approx(now - 20.0)
    assert snapshot["received_at"] == now
    assert snapshot["funding"]["observed_at"] == pytest.approx(now - 30.0)
    assert snapshot["funding"]["received_at"] == now


def test_live_snapshot_provider_rest_poll_without_spot_ts_stays_fresh(
    monkeypatch,
):
    """Regression: Binance spot books often have timestamp=None; funding meta
    stamps ts after the provider's former early received_at. Both made
    feeds_fresh permanently false even when books/rates were live.
    """
    import core.market_data_ledger as ledger_module
    import exchanges.binance_client as binance_module
    import scripts.run_f1_carry_paper as carry_script

    clock = {"t": 1_700_000_100.0}

    def advance(dt: float = 0.05) -> float:
        clock["t"] += dt
        return clock["t"]

    spot_book = {
        "timestamp": None,  # live Binance spot quirk
        "bids": [[100.0, 100.0]],
        "asks": [[101.0, 5.0]],
    }
    perp_book = {
        "timestamp": (clock["t"] - 1.0) * 1000.0,
        "bids": [[100.0, 50.0]],
        "asks": [[101.0, 50.0]],
    }

    class FakeClient:
        exchange = SimpleNamespace(
            market_id=lambda symbol: symbol.replace("/", "").replace(":USDT", "")
        )

        def fetch_order_book(self, symbol, limit, market_type):
            advance(0.02)
            return spot_book if market_type == "spot" else perp_book

    class FakeLedger:
        def __init__(self, clients):
            pass

        def mark_index(self, symbol):
            advance(0.02)
            return {"binance": {"mark": 100.5, "ts": clock["t"] - 2.0}}

        def funding(self, coins):
            # Harvester stamps ts at fetch completion — after provider start.
            ts = advance(0.05)
            return {
                "BTC": {
                    "binance": {
                        "rate": 0.0004,
                        "interval_hours": 8.0,
                        "next_funding_ts": clock["t"] + HOUR,
                        "ts": ts,
                        "stale": False,
                    }
                }
            }

    monkeypatch.setattr(binance_module, "BinanceClient", FakeClient)
    monkeypatch.setattr(ledger_module, "MarketDataLedger", FakeLedger)
    monkeypatch.setattr(carry_script.time, "time", lambda: clock["t"])
    monkeypatch.setattr(carry_script, "avg_7d", lambda *args, **kwargs: 0.0003)
    monkeypatch.setattr(
        carry_script,
        "load_recent_realized_settlements",
        lambda *args, **kwargs: tuple(SimpleNamespace(rate=0.0004) for _ in range(21)),
    )

    snapshot = carry_script.build_live_snapshot_provider("binance")("BTC/USDT")
    assert snapshot is not None
    assert snapshot["stale"] is False
    assert snapshot["spot_observed_at"] == pytest.approx(snapshot["received_at"])
    assert snapshot["funding"]["stale"] is False
    assert snapshot["funding"]["age_sec"] == pytest.approx(0.0, abs=1e-9)
    assert math.isfinite(snapshot["funding"]["age_sec"])
    assert snapshot["observed_at"] is not None


# ── FIX 3: per-symbol crash-safe state persistence (no double-book) ─────
class _CycleRecorder:
    def __init__(self):
        self.cycles = []
        self.funding_settlements = []

    def record_carry_cycle(self, cyc):
        self.cycles.append(dict(cyc))

    def append_funding_settlement(self, settlement):
        if settlement.settlement_id not in {
            item.settlement_id for item in self.funding_settlements
        }:
            self.funding_settlements.append(settlement)
        return settlement.settlement_id


def test_midpass_crash_persists_first_symbol_close_no_double_book(tmp_path):
    clock = FakeClock()
    wh = _CycleRecorder()
    first = clock() + 2 * HOUR
    box = {"rate": 0.0004, "crash": False, "next_ts": first}
    history = SettlementHistory()
    for coin in ("BTC", "ETH"):
        history.add(first, -0.0001, coin=coin)
        history.add(first + 8 * HOUR, -0.0001, coin=coin)

    def provider(symbol):
        if symbol == "ETH/USDT" and box["crash"]:
            raise RuntimeError("mid-pass kill after BTC closed")
        snap = make_snap(clock(), rate=box["rate"])
        snap["funding"]["next_funding_ts"] = box["next_ts"]
        return snap

    def mk():
        return build(tmp_path, clock, provider,
                     symbols=("BTC/USDT", "ETH/USDT"), warehouse=wh,
                     settlement_history_provider=history)

    assert mk().run_once()["opened"] == 2                 # BTC + ETH open

    # arm the crash: BTC will net_edge-exit (rate negative), then ETH raises.
    box["rate"] = -0.0001
    box["crash"] = True
    box["next_ts"] = first + 8 * HOUR
    clock.advance(8 * HOUR)
    with pytest.raises(RuntimeError):
        mk().run_once()

    # BTC's close was persisted BEFORE the ETH crash -> no resurrection
    state = json.loads((tmp_path / "carry_positions.json").read_text())
    assert "binance:BTC/USDT" not in state["positions"]
    assert "binance:ETH/USDT" in state["positions"]        # ETH untouched, still open
    btc_resolved = [c for c in state["cycles"]
                    if c["symbol"] == "BTC/USDT" and c["label_status"] == "RESOLVED"]
    assert len(btc_resolved) == 1

    # replay the pass with ETH healthy: BTC is NOT re-closed / re-booked
    box["crash"] = False
    box["next_ts"] = first + 16 * HOUR
    clock.advance(8 * HOUR)
    mk().run_once()
    btc_wh = [c for c in wh.cycles
              if c["symbol"] == "BTC/USDT" and c["label_status"] == "RESOLVED"]
    assert len(btc_wh) == 1                                 # booked exactly once


# ── grep-proof: no order path on the runner ────────────────────────────
def test_no_order_path_symbols_in_runner_sources():
    root = Path(__file__).resolve().parents[1]
    for rel in ("core/carry_runner.py", "scripts/run_f1_carry_paper.py"):
        src = (root / rel).read_text(encoding="utf-8")
        assert "create_order" not in src, rel
        assert "OrderManager" not in src, rel


# ── the settlement schedule must be OBSERVED, never fabricated ─────────────
# The funding interval is time-varying per symbol (bases have switched 8h -> 4h
# mid-history). An open stamped with an assumed 8h interval / "now + 8h" next
# settlement poisons every later reconciliation cursor with an unobserved number.
def test_open_refuses_when_the_venue_reports_no_funding_interval(tmp_path):
    clock = FakeClock()
    provider = Provider(clock, interval_hours=None, next_in_sec=1 * HOUR)
    r = build(tmp_path, clock, provider)
    summary = r.run_once()
    assert summary["opened"] == 0
    assert r.load_state()["positions"] == {}
    rec = json.loads(
        (tmp_path / "carry_gate_log.jsonl").read_text().strip().splitlines()[-1])
    assert rec["ok"] is False
    assert rec["reason"] == "settlement_schedule_unobserved"


def test_open_refuses_when_the_venue_reports_no_next_settlement(tmp_path):
    clock = FakeClock()
    provider = Provider(clock, interval_hours=8.0, next_in_sec=1 * HOUR)
    r = build(tmp_path, clock, provider)
    original = provider.__call__

    def strip_next(symbol):
        snap = original(symbol)
        f = dict(snap["funding"])
        f["next_funding_ts"] = None
        snap["funding"] = f
        return snap

    r.snapshot_provider = strip_next
    assert r.run_once()["opened"] == 0
    assert r.load_state()["positions"] == {}


def test_open_records_a_four_hour_interval_verbatim(tmp_path):
    clock = FakeClock()
    provider = Provider(clock, interval_hours=4.0, next_in_sec=1 * HOUR)
    r = build(tmp_path, clock, provider)
    assert r.run_once()["opened"] == 1
    pos = list(r.load_state()["positions"].values())[0]
    assert pos["interval_hours"] == 4.0
    assert pos["next_settlement_ts"] == pytest.approx(clock() + 1 * HOUR)
