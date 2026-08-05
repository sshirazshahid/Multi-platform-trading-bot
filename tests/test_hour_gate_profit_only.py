"""HOUR_GATE_PROFIT_ONLY — profit-only hour gate (2026-06-11).

Owner: "Trade only in those hours where its profitable."

bot_engine._classify_hour now consumes the `profitable` allow-list from
data/hour_gate_evidence.json (refreshed weekly by
scripts/refresh_hour_gates.py): when HOUR_GATE_PROFIT_ONLY is on and the
evidence is fresh + non-empty, every hour NOT on the list classifies as
'blocked'. Fail-open on missing/stale(>14d)/empty evidence — an empty
list is indistinguishable from insufficient data, and a silently-halted
bot is worse than an ungated one.

Honest caveat (recorded with the config knob): hour-of-day patterns did
NOT survive IS/OOS validation (2026-06-02 sweet-spot screen, 0
survivors). At ship time only {2, 19, 20} were net-positive (+18.81)
vs −236 across the other 21 hours — the robust effect of this gate is
fewer trades / less bleed, not profit. This supersedes, for entries,
the 2026-05-27 decision that disabled the dynamic hour gate.
"""
from __future__ import annotations

from tests.config_source import config_source_for_grep

from tests.bot_engine_source import bot_engine_source_for_grep

import json
import os
import time
from pathlib import Path


def _engine(tmp_path, monkeypatch, profitable, *, age_days=0.0):
    """Bare BotEngine + a synthetic evidence file in an isolated cwd."""
    monkeypatch.chdir(tmp_path)
    # Force the gate ON so these tests exercise the gate logic deterministically,
    # independent of the operator's runtime HOUR_GATE_PROFIT_ONLY in .env (which
    # is a legitimate override and must not turn the suite red). 2026-06-14.
    monkeypatch.setattr("config.HOUR_GATE_PROFIT_ONLY", True)
    (tmp_path / "data").mkdir(exist_ok=True)
    p = tmp_path / "data" / "hour_gate_evidence.json"
    p.write_text(json.dumps({
        "computed_at": "2026-06-11T00:00:00+00:00",
        "blocked": [],
        "profitable": profitable,
    }), encoding="utf-8")
    if age_days:
        old = time.time() - age_days * 86400
        os.utime(p, (old, old))
    from core.bot_engine import BotEngine
    eng = BotEngine.__new__(BotEngine)  # skip __init__
    return eng


def test_unprofitable_hour_blocked(tmp_path, monkeypatch):
    eng = _engine(tmp_path, monkeypatch, [2, 19, 20])
    assert eng._classify_hour(3) == "blocked"
    assert eng._classify_hour(15) == "blocked"


def test_profitable_hour_not_blocked(tmp_path, monkeypatch):
    """Profitable hours fall through to peak/warmup/allowed — never
    'blocked' (static BLOCKED_HOURS_UTC is empty under UNBLOCK)."""
    eng = _engine(tmp_path, monkeypatch, [2, 19, 20])
    for h in (2, 19, 20):
        assert eng._classify_hour(h) != "blocked", f"hour {h} is profitable"


def test_knob_off_restores_old_behavior(tmp_path, monkeypatch):
    eng = _engine(tmp_path, monkeypatch, [2, 19, 20])
    monkeypatch.setattr("config.HOUR_GATE_PROFIT_ONLY", False)
    assert eng._classify_hour(3) != "blocked"


def test_stale_evidence_fails_open(tmp_path, monkeypatch):
    """Evidence older than 14 days is ignored — no hour blocked."""
    eng = _engine(tmp_path, monkeypatch, [2, 19, 20], age_days=20)
    assert eng._classify_hour(3) != "blocked"


def test_empty_profitable_fails_open(tmp_path, monkeypatch):
    """An empty profitable list must NOT block all 24 hours (it is
    indistinguishable from insufficient data)."""
    eng = _engine(tmp_path, monkeypatch, [])
    assert eng._classify_hour(3) != "blocked"


def test_missing_file_fails_open(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("config.HOUR_GATE_PROFIT_ONLY", True)  # gate ON, no evidence -> fail open
    (tmp_path / "data").mkdir(exist_ok=True)
    from core.bot_engine import BotEngine
    eng = BotEngine.__new__(BotEngine)
    assert eng._classify_hour(3) != "blocked"


def test_evidence_cached_not_reread(tmp_path, monkeypatch):
    """The 5-min TTL cache must serve repeat lookups (bounded disk hits)."""
    eng = _engine(tmp_path, monkeypatch, [2])
    assert eng._classify_hour(3) == "blocked"
    # Replace the file with an all-open one — cached result must persist
    (tmp_path / "data" / "hour_gate_evidence.json").write_text(
        json.dumps({"blocked": [], "profitable": list(range(24))}),
        encoding="utf-8")
    assert eng._classify_hour(3) == "blocked", "TTL cache should still hold"


# ─── Source pins ──────────────────────────────────────────────────────


def test_config_knob_default_on():
    """The shipped DEFAULT (env unset) is ON. Pin the default LITERAL in config.py,
    not the env-resolved value: an operator setting HOUR_GATE_PROFIT_ONLY=false in
    .env is a legitimate runtime override and must not turn this test red. 2026-06-14."""
    src = config_source_for_grep()
    assert 'os.getenv("HOUR_GATE_PROFIT_ONLY", "true")' in src


def test_classify_hour_consumes_evidence():
    src = bot_engine_source_for_grep()
    i = src.index("def _classify_hour")
    block = src[i:i + 2500]
    assert "HOUR_GATE_PROFIT_ONLY" in block
    assert "_load_hour_gate_evidence" in block


def test_dashboard_mirrors_profit_gate():
    """The TRADING GATES panel must show the profit-only block so the
    operator sees WHY the bot isn't entering during most hours."""
    from conftest import dashboard_package_source

    src = dashboard_package_source()
    assert "BLOCKED (hour not profitable)" in src
