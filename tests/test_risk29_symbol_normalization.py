"""Phase-29 post-SL cooldown symbol-format mismatch (found live 2026-07-10).

note_sl_hit is called with the tracker's symbol (``ARB/USDT:USDT``) while
_execute_open checks the action symbol (``ARB/USDT``) — the pair-side keys
never matched, so the 180min cooldown AND the escalated 6h StoplossGuard were
dead for every futures re-entry (observed: ARB re-entered 14/5/3 minutes after
consecutive stop-losses with EIGHT hits in the ledger).

Fix: normalize the settle suffix out of the key on BOTH sides, and merge
legacy suffixed entries at read so pre-fix ledger state still protects.
"""
from __future__ import annotations

import time

import pytest

from core.risk_manager import RiskManager


@pytest.fixture
def rm(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "data").mkdir(exist_ok=True)
    return RiskManager()


def test_suffixed_hit_blocks_unsuffixed_check(rm):
    rm.note_sl_hit("ARB/USDT:USDT", "buy")
    active, reason = rm.is_sl_cooldown_active("ARB/USDT", "buy")
    assert active, "the live mismatch: tracker records :USDT, gate checks bare"
    assert "post_sl_cooldown" in reason or "sl_guard" in reason


def test_unsuffixed_hit_blocks_suffixed_check(rm):
    rm.note_sl_hit("ARB/USDT", "buy")
    active, _ = rm.is_sl_cooldown_active("ARB/USDT:USDT", "buy")
    assert active


def test_legacy_suffixed_ledger_entries_still_protect(rm):
    # Pre-fix state files hold suffixed keys; they must merge at read.
    rm._recent_sl_by_pair_side["OP/USDT:USDT|buy"] = [time.time() - 60]
    active, _ = rm.is_sl_cooldown_active("OP/USDT", "buy")
    assert active, "legacy suffixed ledger entries must still block"


def test_escalated_guard_counts_across_formats(rm):
    now = time.time()
    # One legacy suffixed hit + one fresh normalized hit = 2 in 24h -> 6h lock.
    rm._recent_sl_by_pair_side["APT/USDT:USDT|buy"] = [now - 3600]
    rm.note_sl_hit("APT/USDT", "buy")
    active, reason = rm.is_sl_cooldown_active("APT/USDT", "buy")
    assert active and "sl_guard" in reason, (
        f"2 hits in 24h must trip the escalated guard, got {reason!r}"
    )


def test_other_side_and_symbol_unaffected(rm):
    rm.note_sl_hit("ARB/USDT:USDT", "buy")
    assert rm.is_sl_cooldown_active("ARB/USDT", "sell") == (False, "")
    assert rm.is_sl_cooldown_active("SOL/USDT", "buy") == (False, "")
