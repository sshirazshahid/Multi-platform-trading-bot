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


@pytest.fixture(autouse=True)
def _no_production_warehouse(monkeypatch):
    """Isolate: this module tests the LOG scan, never the live warehouse.

    2026-08-20 -- when _dominant_entry_block_reason gained a typed
    decision_events path that runs BEFORE the log scan, these tests began
    reading the real data/warehouse.sqlite and asserting against whatever the
    live bot happened to be blocked on that minute. A unit test must never
    depend on production state. Silencing the warehouse path here makes the log
    scan the subject under test, which is what this file was written to cover.

    The warehouse path has its own dedicated coverage (including a pin on the
    reader's actual dict keys) in tests/test_gate_value_verification.py.
    """
    monkeypatch.setattr(
        HealthWatchdog, "_entry_block_from_warehouse", lambda self: (None, 0),
    )
    # 2026-08-21: a THIRD typed source was added (the upstream `candidates`
    # table). Stub it too, or these log-scan tests read the live warehouse and
    # assert against whatever the running bot is blocked on this minute. Each
    # new source must be added here -- that is the cost of the fallback chain.
    monkeypatch.setattr(
        HealthWatchdog, "_entry_block_from_candidates", lambda self: (None, 0),
    )


class _Risk:
    def __init__(self, daily_pnl=0.0):
        self.daily_pnl = daily_pnl


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(hw, "COOLDOWN_STATE_PATH", tmp_path / "cd.json")
    # Isolate the block-reason scan from the PRODUCTION logs/ directory: the
    # real log names band_regime_filter, which suppresses the alert and would
    # silently invert every assertion below. Tests that need a blocker patch
    # _dominant_entry_block_reason explicitly.
    monkeypatch.setattr(hw, "LOG_DIR", tmp_path / "logs")
    # Production EconGate=strict treats zero opens as expected idle; these
    # unit tests assert the starvation signal itself, so force the check on.
    monkeypatch.setattr(
        HealthWatchdog, "_expected_idle_under_strict_econ_gate",
        staticmethod(lambda: False),
    )
    monkeypatch.setattr(
        HealthWatchdog, "_expected_idle_no_new_exposure",
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
    """No opens inside the window, not in drawdown -> the alert is real.

    This scenario configures NO blocker at all (no log lines, and the autouse
    fixture silences the warehouse), so the honest state is
    ENTRY_BLOCK_INSTRUMENTATION_GAP: idle, with nothing able to say why. Since
    2026-08-20 that reports under its own key at WARNING rather than sharing
    the INFO model_gate_starving line with well-understood deliberate blocks.
    The requirement here is unchanged -- genuine starvation MUST alert exactly
    once -- and the severity is now higher, not lower.
    """
    _positions(tmp_path, monkeypatch, closed_ages_h=(9.0, 12.0))
    n = _Notifier()
    _wd(n, _Risk(0.0))._check_model_gate_starving()
    assert len(n.sent) == 1
    title = n.sent[0][0]
    assert "model_gate" in title, title
    assert "instrumentation_gap" in title, (
        f"unexplained idle must use the escalated key, got {title}")
    assert "WARNING" in title, f"must not be INFO: {title}"


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


# ── 2026-08-15: idleness caused by a DELIBERATE measured veto is not news ───
# The alert fired hourly for 38h saying "the model gate may be starving for
# signal" while the real cause was band_regime_filter correctly refusing a
# trending tape (ADX>30, measured 59.0% WR vs 65.7% baseline; the 2026-08-15
# replay of 312 blocked entries returned -19.9/-25.3 bps with CIs excluding
# zero). Reporting a working safety rail as a malfunction trains the operator
# to ignore the alert channel — the failure mode that made the 48h latch
# starvation expensive to notice.

def test_deliberate_regime_veto_suppresses_the_alert(tmp_path, monkeypatch):
    """Zero opens BECAUSE a measured filter vetoed is expected, not starvation."""
    _positions(tmp_path, monkeypatch, closed_ages_h=(9.0,))
    monkeypatch.setattr(
        HealthWatchdog, "_dominant_entry_block_reason",
        lambda self: ("band_regime_filter", 170),
    )
    n = _Notifier()
    _wd(n, _Risk(0.0))._check_model_gate_starving()
    assert n.sent == [], (
        "a deliberate, measured veto must not be reported as model starvation"
    )


def test_unexplained_idleness_still_alerts(tmp_path, monkeypatch):
    """With no identifiable blocker the alert MUST still fire -- that is the
    genuinely diagnostic case the check exists for.

    2026-08-20: that state is now the NAMED sentinel
    ENTRY_BLOCK_INSTRUMENTATION_GAP instead of None, and it escalates to
    WARNING under its own alert key. It used to render as an INFO line reading
    "no identifiable entry block in the logs" -- the same severity as a healthy
    deliberate block, which is precisely how an unexplained idle stayed
    invisible. The requirement this test encodes (unexplained => MUST alert) is
    unchanged and now stronger.
    """
    _positions(tmp_path, monkeypatch, closed_ages_h=(9.0,))
    monkeypatch.setattr(
        HealthWatchdog, "_dominant_entry_block_reason",
        lambda self: (hw.ENTRY_BLOCK_INSTRUMENTATION_GAP, 0),
    )
    n = _Notifier()
    _wd(n, _Risk(0.0))._check_model_gate_starving()
    assert len(n.sent) == 1, f"unexplained idle must alert exactly once: {n.sent}"
    blob = " ".join(str(part) for part in n.sent[0]).lower()
    assert "unexplained" in blob, n.sent[0]
    assert "instrumentation_gap" in blob or "observability" in blob, n.sent[0]


def test_block_reason_scan_survives_missing_log(tmp_path, monkeypatch):
    """No log directory must not crash the tick.

    2026-08-20: fail-open no longer means None. A missing log with nothing in
    the warehouse either is the ENTRY_BLOCK_INSTRUMENTATION_GAP state -- named,
    and alerting -- because "we cannot tell" must never be reported as "nothing
    blocked us".
    """
    monkeypatch.setattr(hw, "LOG_DIR", tmp_path / "no_logs")
    wd = _wd(_Notifier(), _Risk(0.0))
    monkeypatch.setattr(wd, "_entry_block_from_warehouse", lambda: (None, 0))
    reason, count = wd._dominant_entry_block_reason()
    assert reason == hw.ENTRY_BLOCK_INSTRUMENTATION_GAP
    assert count == 0
    assert reason is not None, "must never degrade to a null all-clear"


def test_shadow_only_latch_suppresses_starvation_nag(tmp_path, monkeypatch):
    """ENTRY_POLICY=SHADOW_ONLY means zero OPENs is the cash latch working.

    Must not page as model_gate_starving, and must not inherit the 24h
    deliberate-block cap (the latch can stay on for weeks by design).
    """
    monkeypatch.setattr(
        HealthWatchdog, "_expected_idle_no_new_exposure",
        staticmethod(lambda: True),
    )
    _positions(tmp_path, monkeypatch, closed_ages_h=(9.0,))
    n = _Notifier()
    _wd(n, _Risk(0.0))._check_model_gate_starving()
    assert n.sent == [], f"cash latch must not page as starvation: {n.sent}"


def test_entry_policy_colon_reason_beats_thin_book(tmp_path, monkeypatch):
    """[EntryPolicy] BLOCKED uses ': reason' not an emdash. The old regex
    only captured 'BLOCKED … — thin_book' from universe_filter, so a
    SHADOW_ONLY book was reported as thin_book (4 hits) starvation.
    """
    from datetime import datetime, timezone

    logdir = tmp_path / "logs"
    logdir.mkdir()
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    lines = []
    for _ in range(10):
        lines.append(
            f"{stamp} | INFO | core.engine.entry_exec:_execute_open:137 | "
            f"[EntryPolicy] BLOCKED binance:BTC/USDT:USDT mcp_registry: "
            f"entry_policy_shadow_only\n"
        )
    for _ in range(4):
        lines.append(
            f"{stamp} | INFO | core.engine.entry_exec:_execute_open:946 | "
            f"[Claude] BLOCKED by universe filter: FOO/USDT:USDT — "
            f"thin_book:$900<$1200\n"
        )
    (logdir / f"bot_{today}.log").write_text("".join(lines), encoding="utf-8")
    monkeypatch.setattr(hw, "LOG_DIR", logdir)
    reason, hits = _wd(_Notifier(), _Risk(0.0))._dominant_entry_block_reason()
    assert reason == "entry_policy_shadow_only"
    assert hits == 10


def test_expected_idle_no_new_exposure_follows_entry_policy(monkeypatch):
    import config as cfg

    monkeypatch.setattr(cfg, "ENTRY_POLICY", "SHADOW_ONLY")
    monkeypatch.setattr(cfg, "OPERATING_MODE", "PAPER")
    assert hw.expected_idle_no_new_exposure() is True
    monkeypatch.setattr(cfg, "ENTRY_POLICY", "APPROVED_PAPER")
    assert hw.expected_idle_no_new_exposure() is False
    monkeypatch.setattr(cfg, "OPERATING_MODE", "OBSERVATION")
    assert hw.expected_idle_no_new_exposure() is True
