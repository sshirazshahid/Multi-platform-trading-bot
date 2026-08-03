"""TDD spec for BlacklistManager consecutive-SL persistence (F3d, 2026-07-29).

Quarantine progress must survive restarts: on 2026-07-28 AAVE's 2/3
consecutive-SL count was reset by a process restart and the pair took a
third stop that a single-process run would have quarantined.

Counts persist to a SIDECAR file (<blacklist stem>_sl_counts.json) so the
blacklist.json format contract is untouched (BlacklistManager is its only
reader). Stale counts — older than auto_expiry_hours (24h) — EXPIRE on load
rather than resurrect. Semantics preserved: consecutive counting, reset on
win, 3-threshold, direction-aware blacklist handoff.

All paths live under tmp_path; the real data/ directory is never touched.

Run: venv/Scripts/python.exe -m pytest tests/test_blacklist_manager.py -v
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import core.blacklist_manager as blm  # noqa: E402

SYM = "AAVE/USDT:USDT"
KEY = f"{SYM}:long"          # direction-aware blacklist key


@pytest.fixture
def bl_file(tmp_path, monkeypatch):
    f = tmp_path / "blacklist.json"
    monkeypatch.setattr(blm, "DEFAULT_BLACKLIST_FILE", f)
    return f


def _sidecar(bl_file: Path) -> Path:
    return bl_file.with_name("blacklist_sl_counts.json")


def test_sl_counts_survive_restart(bl_file):
    bm1 = blm.BlacklistManager()
    bm1.record_stop_loss(SYM, "long")
    bm1.record_stop_loss(SYM, "long")
    assert not bm1.is_blacklisted(KEY)          # 2/3 — not yet quarantined

    bm2 = blm.BlacklistManager()                # simulated process restart
    bm2.record_stop_loss(SYM, "long")           # THIRD consecutive stop
    assert bm2.is_blacklisted(KEY), (
        "2/3 quarantine progress must survive a restart; the third SL must "
        "blacklist exactly as a single-process run would (AAVE 2026-07-28)")


def test_win_reset_persists_across_restart(bl_file):
    bm1 = blm.BlacklistManager()
    bm1.record_stop_loss(SYM, "long")
    bm1.record_stop_loss(SYM, "long")
    bm1.record_win(SYM, "long")                 # win resets the streak

    bm2 = blm.BlacklistManager()
    bm2.record_stop_loss(SYM, "long")
    bm2.record_stop_loss(SYM, "long")
    assert not bm2.is_blacklisted(KEY)          # only 2 since the win
    bm2.record_stop_loss(SYM, "long")
    assert bm2.is_blacklisted(KEY)


def test_stale_counts_expire_on_load(bl_file):
    stale_ts = time.time() - 25 * 3600          # older than auto_expiry_hours
    _sidecar(bl_file).write_text(
        json.dumps({KEY: {"count": 2, "updated_at": stale_ts}}), encoding="utf-8")
    bm = blm.BlacklistManager()
    bm.record_stop_loss(SYM, "long")            # would be #3 if stale resurrected
    assert not bm.is_blacklisted(KEY), (
        "a day-old 2/3 count must expire on load, not resurrect")


def test_fresh_counts_load(bl_file):
    _sidecar(bl_file).write_text(
        json.dumps({KEY: {"count": 2, "updated_at": time.time() - 60}}),
        encoding="utf-8")
    bm = blm.BlacklistManager()
    bm.record_stop_loss(SYM, "long")
    assert bm.is_blacklisted(KEY)


def test_corrupt_sidecar_fails_open(bl_file):
    _sidecar(bl_file).write_text("{not json", encoding="utf-8")
    bm = blm.BlacklistManager()                 # must not raise
    bm.record_stop_loss(SYM, "long")
    assert not bm.is_blacklisted(KEY)           # started from empty counts


def test_direction_isolation_persists(bl_file):
    bm1 = blm.BlacklistManager()
    bm1.record_stop_loss(SYM, "long")
    bm1.record_stop_loss(SYM, "long")
    bm2 = blm.BlacklistManager()
    bm2.record_stop_loss(SYM, "short")          # different direction
    assert not bm2.is_blacklisted(f"{SYM}:short")
    assert not bm2.is_blacklisted(KEY)


def test_blacklist_handoff_resets_persisted_counter(bl_file):
    bm1 = blm.BlacklistManager()
    for _ in range(3):
        bm1.record_stop_loss(SYM, "long")
    assert bm1.is_blacklisted(KEY)              # handoff happened
    bm2 = blm.BlacklistManager()                # restart after quarantine
    # counter reset to 0 at handoff must persist: a fresh cycle needs 3 again
    assert bm2._sl_counts.get(KEY, 0) == 0
