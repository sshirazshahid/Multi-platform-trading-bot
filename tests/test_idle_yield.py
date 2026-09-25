"""Tests for core/idle_yield.py — the PAPER-only idle-cash yield ledger.

The ledger books what idle paper cash would earn at a public stablecoin
savings rate. It must never invent money: no rate -> no accrual, long gaps
accrue only a bounded slice, and it never writes outside PAPER.
"""
from __future__ import annotations

import json
import math
from datetime import datetime, timezone

import pytest

from core import idle_yield as iy

DAY = 86400.0
NOW = datetime(2026, 9, 25, 12, 0, tzinfo=timezone.utc).timestamp()


def _chart(apy_base=3.7, *, ts=NOW - 3600, apy=None, apy_reward=None, n=3):
    iso = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    point = {"timestamp": iso, "tvlUsd": 2.3e9, "apy": apy if apy is not None else apy_base,
             "apyBase": apy_base, "apyReward": apy_reward}
    older = dict(point, apyBase=1.0, apy=1.0,
                 timestamp=datetime.fromtimestamp(ts - DAY, tz=timezone.utc).isoformat())
    return {"status": "success", "data": [older] * (n - 1) + [point]}


class _Fetch:
    def __init__(self, payload=None, exc=None):
        self.payload = payload
        self.exc = exc
        self.calls = 0

    def __call__(self, url):
        self.calls += 1
        if self.exc is not None:
            raise self.exc
        return self.payload


# ── rate parsing ─────────────────────────────────────────────────────────


def test_parse_uses_latest_point_base_rate():
    apy, as_of, reason = iy.parse_reference_chart(_chart(3.74), now=NOW)
    assert apy == pytest.approx(3.74)
    assert as_of == pytest.approx(NOW - 3600, abs=1)
    assert reason == "ok"


def test_parse_ignores_incentive_rewards():
    # apy includes a token-incentive component; only the organic base rate counts.
    apy, _, reason = iy.parse_reference_chart(
        _chart(3.0, apy=9.0, apy_reward=6.0), now=NOW)
    assert apy == pytest.approx(3.0)
    assert reason == "ok"


@pytest.mark.parametrize("payload", [None, {}, {"data": []}, {"data": "x"},
                                     {"data": [{"timestamp": "2026-09-25T00:00:00Z"}]}])
def test_parse_fails_closed_on_malformed_payload(payload):
    apy, _, reason = iy.parse_reference_chart(payload, now=NOW)
    assert apy is None
    assert reason != "ok"


def test_parse_rejects_rate_above_cap_as_anomalous():
    apy, _, reason = iy.parse_reference_chart(_chart(25.0), now=NOW, max_apy_pct=8.0)
    assert apy is None
    assert reason.startswith("anomalous")


def test_parse_rejects_negative_rate():
    apy, _, reason = iy.parse_reference_chart(_chart(-0.5), now=NOW)
    assert apy is None
    assert reason.startswith("anomalous")


def test_parse_rejects_stale_rate():
    apy, _, reason = iy.parse_reference_chart(_chart(3.7, ts=NOW - 5 * DAY), now=NOW)
    assert apy is None
    assert reason.startswith("stale")


def test_parse_accepts_epoch_timestamps():
    payload = {"data": [{"timestamp": NOW - 60, "apyBase": 4.1}]}
    apy, as_of, reason = iy.parse_reference_chart(payload, now=NOW)
    assert (apy, reason) == (pytest.approx(4.1), "ok")
    assert as_of == pytest.approx(NOW - 60)


# ── interest arithmetic ──────────────────────────────────────────────────


def test_interest_one_day_matches_compound_apy():
    got = iy.interest_for(10_000.0, 3.65, DAY)
    want = 10_000.0 * ((1.0365) ** (1 / 365) - 1)
    assert got == pytest.approx(want, rel=1e-12)
    assert 0.97 < got < 0.99


def test_interest_full_year_equals_apy():
    assert iy.interest_for(1_000.0, 4.0, 365 * DAY) == pytest.approx(40.0)


@pytest.mark.parametrize("principal,apy,secs", [(0, 4, DAY), (-5, 4, DAY), (100, 0, DAY),
                                                (100, 4, 0), (100, 4, -1),
                                                (float("nan"), 4, DAY), (100, float("inf"), DAY)])
def test_interest_is_zero_for_degenerate_inputs(principal, apy, secs):
    assert iy.interest_for(principal, apy, secs) == 0.0


# ── tick: accrual rules ──────────────────────────────────────────────────


def test_first_tick_only_starts_the_clock():
    state = iy.new_state()
    out = iy.tick(state, idle_usd=5_000, now=NOW, fetch_json=_Fetch(_chart()))
    assert out["earned_usd"] == 0.0
    assert state["last_accrual_ts"] == NOW
    assert state["total_earned_usd"] == 0.0


def test_hourly_tick_accrues_and_buckets_by_utc_day():
    state = iy.new_state()
    fetch = _Fetch(_chart(3.65))
    iy.tick(state, idle_usd=10_000, now=NOW, fetch_json=fetch)
    out = iy.tick(state, idle_usd=10_000, now=NOW + 3600, fetch_json=fetch)
    want = iy.interest_for(10_000, 3.65, 3600)
    assert out["earned_usd"] == pytest.approx(want)
    assert state["total_earned_usd"] == pytest.approx(want)
    assert state["daily"]["2026-09-25"]["earned_usd"] == pytest.approx(want)


def test_no_rate_means_no_money():
    state = iy.new_state()
    fetch = _Fetch(exc=ConnectionError("egress blocked"))
    iy.tick(state, idle_usd=10_000, now=NOW, fetch_json=fetch)
    out = iy.tick(state, idle_usd=10_000, now=NOW + 3600, fetch_json=fetch)
    assert out["earned_usd"] == 0.0
    assert state["total_earned_usd"] == 0.0
    assert state["skipped_seconds"] == pytest.approx(3600)
    assert state["rate"]["status"].startswith("unavailable")


def test_long_gap_accrues_only_the_bounded_slice():
    state = iy.new_state()
    fetch = _Fetch(_chart(3.65))
    iy.tick(state, idle_usd=10_000, now=NOW, fetch_json=fetch)
    # Bot was down 10 hours: credit at most MAX_GAP_SEC, count the rest as skipped.
    later = NOW + 10 * 3600
    out = iy.tick(state, idle_usd=10_000, now=later, fetch_json=_Fetch(_chart(3.65, ts=later - 60)))
    assert out["earned_usd"] == pytest.approx(iy.interest_for(10_000, 3.65, iy.MAX_GAP_SEC))
    assert state["skipped_seconds"] == pytest.approx(10 * 3600 - iy.MAX_GAP_SEC)


def test_zero_or_bad_idle_cash_accrues_nothing():
    for idle in (0.0, -10.0, float("nan")):
        state = iy.new_state()
        fetch = _Fetch(_chart())
        iy.tick(state, idle_usd=10_000, now=NOW, fetch_json=fetch)
        out = iy.tick(state, idle_usd=idle, now=NOW + 3600, fetch_json=fetch)
        assert out["earned_usd"] == 0.0


def test_rate_is_cached_between_refreshes():
    state = iy.new_state()
    fetch = _Fetch(_chart())
    for k in range(5):
        iy.tick(state, idle_usd=1_000, now=NOW + k * 3600, fetch_json=fetch)
    assert fetch.calls == 1  # 4h < RATE_REFRESH_SEC


def test_failed_refresh_keeps_a_recent_rate_but_drops_an_old_one():
    state = iy.new_state()
    iy.tick(state, idle_usd=1_000, now=NOW, fetch_json=_Fetch(_chart(3.7)))
    broken = _Fetch(exc=TimeoutError())
    # 7h later: refresh due, fails, but the cached reading is < RATE_MAX_AGE_SEC old.
    t1 = NOW + 7 * 3600
    out = iy.tick(state, idle_usd=1_000, now=t1, fetch_json=broken)
    assert out["earned_usd"] > 0.0
    # 4 days later the cached reading is too old -> fail closed.
    t2 = NOW + 4 * DAY
    iy.tick(state, idle_usd=1_000, now=t2 - 3600, fetch_json=broken)
    out = iy.tick(state, idle_usd=1_000, now=t2, fetch_json=broken)
    assert out["earned_usd"] == 0.0
    assert state["rate"]["apy_pct"] is None


def test_daily_history_is_bounded():
    state = iy.new_state()
    for d in range(iy.MAX_DAILY_ROWS + 20):
        state["daily"][f"day{d:04d}"] = {"earned_usd": 0.0}
    iy._trim_daily(state)
    assert len(state["daily"]) == iy.MAX_DAILY_ROWS


# ── persistence + PAPER latch ────────────────────────────────────────────


def test_run_tick_refuses_outside_paper(tmp_path):
    path = tmp_path / "idle_yield.json"
    out = iy.run_idle_yield_tick(1_000, paper=False, now=NOW, path=path,
                                 fetch_json=_Fetch(_chart()))
    assert out["skipped"] == "not_paper"
    assert not path.exists()


def test_run_tick_respects_disable_switch(tmp_path):
    path = tmp_path / "idle_yield.json"
    out = iy.run_idle_yield_tick(1_000, paper=True, enabled=False, now=NOW, path=path,
                                 fetch_json=_Fetch(_chart()))
    assert out["skipped"] == "disabled"
    assert not path.exists()


def test_run_tick_persists_and_labels_the_ledger(tmp_path):
    path = tmp_path / "idle_yield.json"
    fetch = _Fetch(_chart(3.65))
    iy.run_idle_yield_tick(10_000, paper=True, enabled=True, now=NOW, path=path, fetch_json=fetch)
    iy.run_idle_yield_tick(10_000, paper=True, enabled=True, now=NOW + 3600, path=path,
                           fetch_json=fetch)
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["total_earned_usd"] == pytest.approx(iy.interest_for(10_000, 3.65, 3600))
    assert "not trading profit" in data["label"].lower()
    assert data["reference"]["pool_id"] == iy.REFERENCE_POOL_ID
    assert not (tmp_path / "idle_yield.json.tmp").exists()


def test_corrupt_ledger_file_starts_fresh_without_crashing(tmp_path):
    path = tmp_path / "idle_yield.json"
    path.write_text("{not json", encoding="utf-8")
    state = iy.load_state(path)
    assert state["total_earned_usd"] == 0.0
    assert state["schema"] == iy.SCHEMA_VERSION


def test_summarize_reports_today_week_and_total():
    state = iy.new_state()
    state["total_earned_usd"] = 12.5
    state["daily"] = {
        "2026-09-25": {"earned_usd": 1.0},
        "2026-09-20": {"earned_usd": 2.0},
        "2026-09-01": {"earned_usd": 9.5},
    }
    state["rate"] = {"apy_pct": 3.7, "status": "ok", "fetched_ts": NOW, "as_of_ts": NOW}
    s = iy.summarize(state, now=NOW)
    assert s["today_usd"] == pytest.approx(1.0)
    assert s["last_7d_usd"] == pytest.approx(3.0)
    assert s["total_earned_usd"] == pytest.approx(12.5)
    assert s["apy_pct"] == pytest.approx(3.7)
    assert math.isfinite(s["total_earned_usd"])
