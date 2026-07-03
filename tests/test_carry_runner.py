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
    kw.setdefault("heartbeat_path", tmp_path / "carry_heartbeat.json")
    kw.setdefault("symbols", ("BTC/USDT",))
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


# ── full lifecycle: open -> settlements -> exit -> RESOLVED ────────────
def test_full_lifecycle_resolved_cycle_net_is_funding_minus_costs(tmp_path):
    clock = FakeClock()
    provider = Provider(clock)
    r = build(tmp_path, clock, provider)
    s = r.run_once()
    assert s["opened"] == 1
    state = json.loads((tmp_path / "carry_positions.json").read_text())
    pos = state["positions"]["binance:BTC/USDT"]
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
    assert state["positions"]["binance:BTC/USDT"]["settlements_held"] == n_expected


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
    pos = state["positions"]["binance:BTC/USDT"]
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

    split = (binance_only
             + [cyc("bybit", s, 1.0)
                for s in ("BTC/USDT", "ETH/USDT") for _ in range(5)])
    chk2 = promotion_checklist(split, {})
    assert chk2["concentration"]["pass"] is True
    assert chk2["concentration"]["venue_share_pct"] == pytest.approx(50.0)


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


def test_build_f1_spec_binance_bybit_requires_latch():
    # extends tests/test_f1_rev5_gates.py latch coverage with the exact
    # two-venue set the paper script now uses (kept here: file ownership).
    from research.funding_carry_lab import build_f1_spec

    with pytest.raises(ValueError):
        build_f1_spec(venues=("binance", "bybit"))
    spec = build_f1_spec(symbols=("BTC/USDT", "ETH/USDT"),
                         venues=("binance", "bybit"),
                         allow_extended_universe=True)
    assert spec.venues == ["binance", "bybit"]


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
    r = build(tmp_path, clock, provider)
    assert r.run_once()["opened"] == 1
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

    def hook(symbol):
        return _fail_perp(symbol) if symbol == "ETH/USDT" else None

    r = build(tmp_path, clock, provider,
              symbols=("BTC/USDT", "ETH/USDT"), failure_hook=hook)
    s = r.run_once()
    assert s["opened"] == 1 and s["failed"] == 1 and s["recovery_active"] is True

    # latched: the BTC position still accrues its settlement normally ...
    clock.advance(2 * HOUR + 1)
    s = r.run_once()
    assert s["held"] == 1 and s["closed"] == 0
    state = json.loads((tmp_path / "carry_positions.json").read_text())
    assert state["positions"]["binance:BTC/USDT"]["settlements_held"] == 1

    # ... and can still close (reduce-only: exits keep firing)
    provider.kw["rate"] = -0.0001
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
                "slippage": 0.01, "label_status": "RESOLVED"}

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


def test_maker_first_fills_spot_at_mid(tmp_path):
    # Wide-enough spread (4bps, within the 5bps gate cap): the spot leg rests at
    # mid as a maker; the effective RT cost is the real-fee maker formula.
    clock = FakeClock()
    provider = Provider(clock, spread_bps=4.0, rate=0.0010)
    r = build(tmp_path, clock, provider, execution_mode="maker_first")
    assert r.run_once()["opened"] == 1
    pos = _pos(tmp_path)
    assert pos["execution_mode"] == "maker_first"
    assert abs(pos["spot_entry_px"] - 100.0) < 1e-9              # rested at mid, no slip
    # maker RT = 2*spot_maker + 2*perp_taker + 2*slip (binance: 2*.001+2*.0005+2*.0005)
    assert abs(pos["round_trip_cost_frac"] - 0.004) < 1e-9


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


# ── grep-proof: no order path on the runner ────────────────────────────
def test_no_order_path_symbols_in_runner_sources():
    root = Path(__file__).resolve().parents[1]
    for rel in ("core/carry_runner.py", "scripts/run_f1_carry_paper.py"):
        src = (root / rel).read_text(encoding="utf-8")
        assert "create_order" not in src, rel
        assert "OrderManager" not in src, rel
