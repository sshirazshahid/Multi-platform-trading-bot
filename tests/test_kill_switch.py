"""Entries-only kill switch (Codex port, 2026-07-12).

When ``data/KILL_SWITCH`` exists (any content), the bot refuses NEW entries —
both the mcp/band lane (bot_engine._execute_open) and the deep-breakout PAPER
lane — but KEEPS running position monitoring, SL/TP management, maker-resolver,
reconciliation and shadow probes. Deliberately NOT Codex's skip-whole-cycle
semantics (Codex/bot/engine.py:263-265 returns from run_cycle, freezing
position management too — unsafe for a bot with open positions).

Pins:
  1. predicate: file present -> halted; absent -> not halted; removal resumes
  2. log ONCE per state change (not every cycle), exact message on halt
  3. bot_engine._execute_open consults the switch and rejects (source pin,
     same convention as tests/test_analysis_only_gating.py)
  4. deep-breakout lane: entries blocked while halted, max-hold close still
     runs, entry resumes within the same signal bar after removal
"""

from __future__ import annotations

from tests.bot_engine_source import bot_engine_source_for_grep

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from loguru import logger  # noqa: E402


def _fresh_switch(tmp_path, monkeypatch):
    """Point the module default at a tmp file and reset its log-state."""
    import core.kill_switch as ks

    path = tmp_path / "KILL_SWITCH"
    monkeypatch.setattr(ks, "KILL_SWITCH_PATH", path)
    monkeypatch.setattr(ks._default, "_last", None)
    monkeypatch.setattr(ks._default, "_path", None)  # follow KILL_SWITCH_PATH
    return ks, path


# ── 1. predicate ─────────────────────────────────────────────────────────
def test_absent_file_means_entries_allowed(tmp_path, monkeypatch):
    ks, _ = _fresh_switch(tmp_path, monkeypatch)
    assert ks.entries_halted() is False


def test_present_file_halts_and_removal_resumes(tmp_path, monkeypatch):
    ks, path = _fresh_switch(tmp_path, monkeypatch)
    path.write_text("halt please")  # any content counts
    assert ks.entries_halted() is True
    path.unlink()
    assert ks.entries_halted() is False


def test_empty_file_also_halts(tmp_path, monkeypatch):
    ks, path = _fresh_switch(tmp_path, monkeypatch)
    path.touch()
    assert ks.entries_halted() is True


def test_filesystem_error_fails_closed(monkeypatch):
    import core.kill_switch as ks

    class BrokenPath:
        def exists(self):
            raise OSError("storage unavailable")

        def __str__(self):
            return "broken/KILL_SWITCH"

    monkeypatch.setattr(ks, "KILL_SWITCH_PATH", BrokenPath())
    monkeypatch.setattr(ks._default, "_path", None)
    monkeypatch.setattr(ks._default, "_last", None)
    assert ks.entries_halted() is True


# ── 2. log once per state change ─────────────────────────────────────────
def _capture_logs(monkeypatch):
    records = []
    sink_id = logger.add(lambda m: records.append(str(m)), level="DEBUG")
    return records, sink_id


def test_logs_once_per_state_change_not_every_cycle(tmp_path, monkeypatch):
    ks, path = _fresh_switch(tmp_path, monkeypatch)
    records, sink_id = _capture_logs(monkeypatch)
    try:
        # absent at first check: NO log (no state change worth announcing)
        ks.entries_halted()
        assert not any("[KillSwitch]" in r for r in records)

        path.write_text("x")
        for _ in range(5):  # five cycles, one log
            ks.entries_halted()
        halted_logs = [r for r in records if "entries halted" in r]
        assert len(halted_logs) == 1
        assert "[KillSwitch] entries halted — data/KILL_SWITCH present" in halted_logs[0]

        path.unlink()
        for _ in range(5):
            ks.entries_halted()
        resumed_logs = [r for r in records if "entries resumed" in r]
        assert len(resumed_logs) == 1
    finally:
        logger.remove(sink_id)


def test_no_resume_log_when_never_halted(tmp_path, monkeypatch):
    ks, _ = _fresh_switch(tmp_path, monkeypatch)
    records, sink_id = _capture_logs(monkeypatch)
    try:
        ks.entries_halted()
        ks.entries_halted()
        assert not any("[KillSwitch]" in r for r in records)
    finally:
        logger.remove(sink_id)


# ── 3. bot_engine wiring (source pin, repo convention) ───────────────────
def test_execute_open_consults_kill_switch():
    src = bot_engine_source_for_grep()
    idx = src.index("def _execute_open")
    block = src[idx : idx + 4000]
    assert "entries_halted" in block, "_execute_open must consult the kill switch"
    assert "kill_switch_active" in block, "reject_reason must be recorded"


def test_kill_switch_check_precedes_order_placement():
    """The switch must fire before any sizing/order work — near the top of
    _execute_open, before the operating-mode gates."""
    src = bot_engine_source_for_grep()
    idx = src.index("def _execute_open")
    block = src[idx : idx + 8000]
    assert block.index("entries_halted") < block.index("OBSERVATION")


# ── 4. deep-breakout lane: entries blocked, monitoring continues ─────────
def _lane_harness():
    """Reuse the lane test harness (single source of truth for lane doubles)."""
    import tests.test_deep_breakout_lane as h

    return h


def test_lane_blocks_entry_while_halted_then_resumes(tmp_path, monkeypatch):
    h = _lane_harness()
    ks, path = _fresh_switch(tmp_path, monkeypatch)
    path.write_text("x")

    data = {("binance", "BTC/USDT:USDT"): h._candles(h.START, h._breakout_tape())}
    om = h._FakeOM()
    lane, state = h._mk_lane(tmp_path, monkeypatch, ohlcv=h._ohlcv_map(data), om=om)
    lane.tick()
    assert om.opens == []  # entry refused while the switch is present

    # switch removed while the signal bar is still current -> entry resumes
    path.unlink()
    state["now"] += 300
    lane.tick()
    assert len(om.opens) == 1


def test_lane_max_hold_close_still_runs_while_halted(tmp_path, monkeypatch):
    """Monitoring/exits are NOT halted — only new entries (anti-Codex-semantics
    pin: the reference skipped the whole cycle)."""
    h = _lane_harness()
    ks, path = _fresh_switch(tmp_path, monkeypatch)
    path.write_text("x")

    now = h.START + 500 * h.H4
    expired = h._FakePos("deep_breakout", symbol="ETH/USDT:USDT",
                         exchange="binance", open_time=now - 126 * h.H4 - 10)
    om = h._FakeOM()
    lane, _ = h._mk_lane(tmp_path, monkeypatch, ohlcv=lambda *a: [], om=om,
                         tracker=h._FakeTracker([expired]), now=now)
    stats = lane.tick()
    assert stats["max_hold_closed"] == 1
    assert len(om.closes) == 1
    assert om.closes[0]["reason"] == "max_hold"
