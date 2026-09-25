"""Tests for core/owner_scoreboard.py — the plain-English daily scoreboard.

The scoreboard only READS state files. It must never crash on missing or
malformed files, must keep the three money sources separate, and must say
plainly when a lane is idle and why.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from core import owner_scoreboard as sb

NOW = datetime(2026, 9, 25, 12, 0, tzinfo=timezone.utc).timestamp()
HOUR = 3600.0
DAY = 86400.0


def _write(root, rel, obj):
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(obj, str):
        p.write_text(obj, encoding="utf-8")
    else:
        p.write_text(json.dumps(obj), encoding="utf-8")
    return p


def _pos(pnl, close_time, **kw):
    base = {"symbol": "BTC/USDT", "strategy": "algo_det", "pnl": pnl,
            "close_time": close_time, "paper_trade": True}
    base.update(kw)
    return base


def _heartbeat(age_sec, **kw):
    ts = datetime.fromtimestamp(NOW - age_sec, tz=timezone.utc).isoformat()
    hb = {"timestamp": ts, "operating_mode": "PAPER", "dry_run": True,
          "entry_policy": "APPROVED_PAPER"}
    hb.update(kw)
    return hb


# ── robustness ───────────────────────────────────────────────────────────


def test_empty_data_dir_renders_without_crashing(tmp_path):
    board = sb.build_scoreboard(tmp_path, now=NOW)
    text = sb.render_markdown(board)
    assert board["totals"]["today_usd"] == 0.0
    assert "not available" in text.lower()


def test_malformed_files_are_treated_as_missing(tmp_path):
    for rel in ("data/heartbeat.json", "data/positions.json",
                "data/carry_positions.json", "data/idle_yield.json"):
        _write(tmp_path, rel, "{broken")
    _write(tmp_path, "data/carry_gate_log.jsonl", "not json\n{also broken\n")
    board = sb.build_scoreboard(tmp_path, now=NOW)
    assert board["totals"]["today_usd"] == 0.0
    sb.render_markdown(board)


# ── bot alive ────────────────────────────────────────────────────────────


def test_fresh_heartbeat_reads_as_running(tmp_path):
    _write(tmp_path, "data/heartbeat.json", _heartbeat(120))
    board = sb.build_scoreboard(tmp_path, now=NOW)
    assert board["bot"]["running"] is True
    assert board["bot"]["mode"] == "PAPER"
    assert "yes" in sb.render_markdown(board).lower()


def test_stale_heartbeat_reads_as_not_running(tmp_path):
    _write(tmp_path, "data/heartbeat.json", _heartbeat(5 * HOUR))
    board = sb.build_scoreboard(tmp_path, now=NOW)
    assert board["bot"]["running"] is False
    assert "5.0 hours" in sb.render_markdown(board)


# ── trading lane ─────────────────────────────────────────────────────────


def test_trading_sums_today_and_last_seven_days(tmp_path):
    _write(tmp_path, "data/positions.json", {
        "open": [{"symbol": "ETH/USDT"}],
        "closed": [
            _pos(-1.5, NOW - HOUR),
            _pos(0.5, NOW - 2 * HOUR),
            _pos(2.0, NOW - 3 * DAY),
            _pos(9.0, NOW - 10 * DAY),       # outside the week
            _pos(None, NOW - HOUR),          # unresolved pnl ignored
        ],
    })
    t = sb.build_scoreboard(tmp_path, now=NOW)["trading"]
    assert t["today_usd"] == pytest.approx(-1.0)
    assert t["today_trades"] == 2
    assert t["week_usd"] == pytest.approx(1.0)
    assert t["week_trades"] == 3
    assert t["week_wins"] == 2
    assert t["open_positions"] == 1


def test_shadow_only_policy_is_explained_as_not_placing_trades(tmp_path):
    _write(tmp_path, "data/heartbeat.json", _heartbeat(60, entry_policy="SHADOW_ONLY"))
    text = sb.render_markdown(sb.build_scoreboard(tmp_path, now=NOW))
    assert "SHADOW_ONLY" in text
    assert "not placing" in text.lower()


def test_approved_paper_policy_points_to_the_recommendation(tmp_path):
    _write(tmp_path, "data/heartbeat.json", _heartbeat(60, entry_policy="APPROVED_PAPER"))
    text = sb.render_markdown(sb.build_scoreboard(tmp_path, now=NOW))
    assert "START_HERE.md" in text


# ── carry lane ───────────────────────────────────────────────────────────


def test_carry_cycles_and_open_positions(tmp_path):
    _write(tmp_path, "data/carry_positions.json", {
        "positions": {"binance:BTC/USDT": {"notional": 50.0}},
        "recovery": {"active": False},
        "cycles": [
            {"label_status": "RESOLVED", "resolved_ts": NOW - HOUR, "net_pnl": 0.40},
            {"label_status": "RESOLVED", "resolved_ts": NOW - 4 * DAY, "net_pnl": -0.10},
            {"label_status": "RESOLVED", "resolved_ts": NOW - 40 * DAY, "net_pnl": 1.00},
            {"label_status": "PENDING", "resolved_ts": NOW - HOUR, "net_pnl": 99.0},
        ],
    })
    c = sb.build_scoreboard(tmp_path, now=NOW)["carry"]
    assert c["today_usd"] == pytest.approx(0.40)
    assert c["week_usd"] == pytest.approx(0.30)
    assert c["all_time_usd"] == pytest.approx(1.30)
    assert c["resolved_cycles"] == 3
    assert c["open_positions"] == 1
    assert c["recovery_latched"] is False


def test_carry_gate_explains_the_gap_to_the_threshold(tmp_path):
    rows = [
        {"ts": NOW - 2 * HOUR, "ok": False, "reason": "edge -45.2bps < 150.0bps"},
        {"ts": NOW - HOUR, "ok": False, "reason": "edge -12.4bps < 150.0bps"},
        {"ts": NOW - HOUR, "ok": False, "reason": "no_snapshot"},
        {"ts": NOW - 3 * DAY, "ok": False, "reason": "edge 400.0bps < 150.0bps"},  # too old
    ]
    _write(tmp_path, "data/carry_gate_log.jsonl", "\n".join(json.dumps(r) for r in rows) + "\n")
    g = sb.build_scoreboard(tmp_path, now=NOW)["carry"]["gate_24h"]
    assert g["checks"] == 3
    assert g["passes"] == 0
    assert g["best_edge_bps"] == pytest.approx(-12.4)
    assert g["needed_edge_bps"] == pytest.approx(150.0)
    text = sb.render_markdown(sb.build_scoreboard(tmp_path, now=NOW))
    assert "-12.4" in text and "150.0" in text


def test_missing_gate_log_says_carry_runner_may_not_be_scheduled(tmp_path):
    text = sb.render_markdown(sb.build_scoreboard(tmp_path, now=NOW))
    assert "TradingBot-F1CarryPaper" in text


def test_gate_log_tail_reads_only_the_end_of_a_large_file(tmp_path):
    old = {"ts": NOW - 5 * DAY, "ok": False, "reason": "edge 1.0bps < 150.0bps"}
    new = {"ts": NOW - HOUR, "ok": False, "reason": "edge -3.0bps < 150.0bps"}
    body = (json.dumps(old) + "\n") * 5000 + (json.dumps(new) + "\n") * 10
    path = _write(tmp_path, "data/carry_gate_log.jsonl", body)
    rows = sb._tail_jsonl(path, max_bytes=4096)
    assert rows and rows[-1] == new
    assert len(rows) < 5010


# ── yield + totals ───────────────────────────────────────────────────────


def test_interest_line_says_it_is_not_trading_profit(tmp_path):
    _write(tmp_path, "data/idle_yield.json", {
        "schema": 1, "total_earned_usd": 3.0,
        "daily": {"2026-09-25": {"earned_usd": 0.25}, "2026-09-22": {"earned_usd": 0.75}},
        "rate": {"apy_pct": 3.7, "status": "ok"},
    })
    board = sb.build_scoreboard(tmp_path, now=NOW)
    assert board["interest"]["today_usd"] == pytest.approx(0.25)
    assert board["interest"]["week_usd"] == pytest.approx(1.0)
    text = sb.render_markdown(board)
    assert "not trading profit" in text.lower()
    assert "3.70%" in text


def test_totals_add_the_three_sources(tmp_path):
    _write(tmp_path, "data/positions.json", {"open": [], "closed": [_pos(-1.0, NOW - HOUR)]})
    _write(tmp_path, "data/carry_positions.json", {"positions": {}, "cycles": [
        {"label_status": "RESOLVED", "resolved_ts": NOW - HOUR, "net_pnl": 0.5}]})
    _write(tmp_path, "data/idle_yield.json", {"schema": 1, "total_earned_usd": 0.2,
                                              "daily": {"2026-09-25": {"earned_usd": 0.2}},
                                              "rate": {"apy_pct": 3.7, "status": "ok"}})
    totals = sb.build_scoreboard(tmp_path, now=NOW)["totals"]
    assert totals["today_usd"] == pytest.approx(-0.3)


def test_write_scoreboard_creates_dated_report(tmp_path):
    path = sb.write_scoreboard(tmp_path, now=NOW)
    assert path == tmp_path / "reports" / "owner_scoreboard_2026-09-25.md"
    assert path.read_text(encoding="utf-8").startswith("# Your trading bot")
