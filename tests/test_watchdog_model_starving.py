"""model_gate_starving must measure ACTUAL position opens (2026-07-28).

BUG (permanent false alarm, hourly emails while the bot was trading normally):
_check_model_gate_starving counted records in data/mcp_decisions.jsonl whose
TOP-LEVEL ``type``/``action`` equalled "OPEN". Measured against the live file,
the only top-level type values are ``portfolio`` (343), ``rejection`` (821) and
``position_monitor`` (244) — never "OPEN". The 821 real OPEN actions sit two
levels down at ``decisions.actions[].type``. So ``opens_recent == 0`` was
structurally guaranteed and the INFO alert fired every cooldown forever.

The F6 fix (2026-07-20) repaired ISO-timestamp parsing, which is what made this
check start firing at all; the record-shape mismatch was always there, so that
fix converted a silent no-op into a permanent false alarm.

Why not just walk the nested actions: those are PROPOSED opens. Every one of
them also emits a ``rejection`` record, and ``rejection`` is a misnomer — its
reasons include ``maker_first_maker_fill``, i.e. a successful fill. Counting
either proposals or "non-rejections" gives the wrong answer (measured: the
naive decision_id join yields 0 executed opens on a day with 16 real entries).

data/positions.json is authoritative for "did a position actually open": every
entry carries ``open_time``. The closed list is a rolling 500 cap that keeps the
NEWEST entries, so recent opens are never truncated away — truncation can only
lower an old count, and this check alerts solely on zero.
"""
from __future__ import annotations

import json
import time

import pytest

import core.health_watchdog as hw
from core.health_watchdog import HealthWatchdog


class _Notifier:
    def __init__(self):
        self.sent = []

    def alert(self, message, title=None, context=None):
        self.sent.append((title, message))


class _Risk:
    def __init__(self, daily_pnl=0.0):
        self.daily_pnl = daily_pnl


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(hw, "COOLDOWN_STATE_PATH", tmp_path / "cd.json")
    # Production EconGate=strict treats zero opens as expected idle; these
    # unit tests assert the starvation signal itself, so force the check on.
    monkeypatch.setattr(
        HealthWatchdog, "_expected_idle_under_strict_econ_gate",
        staticmethod(lambda: False),
    )


def _positions(tmp_path, monkeypatch, *, open_ages_h=(), closed_ages_h=()):
    """Write a positions.json whose entries opened N hours ago."""
    now = time.time()
    doc = {
        "open": [{"symbol": "ETH/USDT:USDT", "open_time": now - h * 3600}
                 for h in open_ages_h],
        "closed": [{"symbol": "AAVE/USDT:USDT", "open_time": now - h * 3600}
                   for h in closed_ages_h],
    }
    p = tmp_path / "positions.json"
    p.write_text(json.dumps(doc), encoding="utf-8")
    monkeypatch.setattr(hw, "POSITIONS_PATH", p)
    return p


def _wd(notifier, risk=None):
    w = HealthWatchdog(bot_engine=None, notifier=notifier)
    w._risk = risk
    return w


def test_strict_econ_gate_suppresses_starvation_nag(tmp_path, monkeypatch):
    """Under EconGate=strict, zero opens is expected — do not INFO-spam."""
    monkeypatch.setattr(
        HealthWatchdog, "_expected_idle_under_strict_econ_gate",
        staticmethod(lambda: True),
    )
    _positions(tmp_path, monkeypatch, closed_ages_h=(9.0,))
    n = _Notifier()
    _wd(n, _Risk(0.0))._check_model_gate_starving()
    assert n.sent == []


# ── the false alarm ─────────────────────────────────────────────────────────

def test_recent_open_is_not_starving(tmp_path, monkeypatch):
    """THE BUG: a position opened 20 minutes ago must silence the alert."""
    _positions(tmp_path, monkeypatch, closed_ages_h=(0.33,))
    n = _Notifier()
    _wd(n, _Risk(0.0))._check_model_gate_starving()
    assert n.sent == [], f"false alarm while trading: {n.sent}"


def test_open_position_also_counts(tmp_path, monkeypatch):
    """An entry still OPEN counts — it is the most recent evidence of all."""
    _positions(tmp_path, monkeypatch, open_ages_h=(1.0,))
    n = _Notifier()
    _wd(n, _Risk(0.0))._check_model_gate_starving()
    assert n.sent == []


# ── the alert must still work when genuinely starving ───────────────────────

def test_genuine_starvation_still_alerts(tmp_path, monkeypatch):
    """No opens inside the window, not in drawdown -> the alert is real."""
    _positions(tmp_path, monkeypatch, closed_ages_h=(9.0, 12.0))
    n = _Notifier()
    _wd(n, _Risk(0.0))._check_model_gate_starving()
    assert len(n.sent) == 1
    assert "model_gate_starving" in n.sent[0][0]


def test_drawdown_suppresses_the_nag(tmp_path, monkeypatch):
    """Existing behavior preserved: in drawdown the gate's caution is rational."""
    _positions(tmp_path, monkeypatch, closed_ages_h=(9.0,))
    n = _Notifier()
    _wd(n, _Risk(-5.0))._check_model_gate_starving()
    assert n.sent == []


# ── fail-safe: never alert on unreadable state ──────────────────────────────

def test_missing_positions_file_is_silent(tmp_path, monkeypatch):
    monkeypatch.setattr(hw, "POSITIONS_PATH", tmp_path / "nope.json")
    n = _Notifier()
    _wd(n, _Risk(0.0))._check_model_gate_starving()
    assert n.sent == [], "absent state must not be reported as starvation"


def test_corrupt_positions_file_is_silent(tmp_path, monkeypatch):
    p = tmp_path / "positions.json"
    p.write_text("{not json", encoding="utf-8")
    monkeypatch.setattr(hw, "POSITIONS_PATH", p)
    n = _Notifier()
    _wd(n, _Risk(0.0))._check_model_gate_starving()
    assert n.sent == []


def test_entry_without_open_time_is_not_counted_as_recent(tmp_path, monkeypatch):
    """A malformed entry must not silently satisfy the check."""
    p = tmp_path / "positions.json"
    p.write_text(json.dumps({"open": [{"symbol": "X"}], "closed": []}),
                 encoding="utf-8")
    monkeypatch.setattr(hw, "POSITIONS_PATH", p)
    n = _Notifier()
    _wd(n, _Risk(0.0))._check_model_gate_starving()
    assert len(n.sent) == 1, "missing open_time must not count as a recent open"
