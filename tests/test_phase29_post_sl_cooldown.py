"""Phase 29 — freqtrade-style post-SL CooldownPeriod + StoplossGuard.

Trade audit (267 closed trades) found stop_loss is the largest active
bleed source: 48 SL hits totaling $-78.10 (mean $-1.63). Many of those
are likely IMMEDIATE re-entries on a (symbol, side) that just stopped
out — the bot doesn't currently have a "you just lost on this, wait
before retrying" mechanism specifically for SL events.

freqtrade has been using two well-tested protections for years:

  CooldownPeriod: prevents re-entry on a pair after any close
  StoplossGuard:  locks pair globally if N stoplosses in lookback

Phase 29 implements the SL-specific narrowed version of both:

  Layer 1 (post-SL cooldown):
    Trigger: ANY stop_loss exit on (symbol, side)
    Action:  180min lock on (symbol, side)  [2026-06-11: raised from 30 —
             Jun-11 tape: ADA/DOT/BNB/APT re-shorted 9-12x into 70 SLs]
    Param:   POST_SL_SHORT_COOLDOWN_MIN = 180

  Layer 2 (escalated guard):
    Trigger: 2+ stop_loss exits on (symbol, side) within last 24h
    Action:  6h lock on (symbol, side)
    Params:  POST_SL_GUARD_TRADE_LIMIT = 2
             POST_SL_GUARD_LOOKBACK_HRS = 24
             POST_SL_GUARD_LOCK_HRS = 6

Recording happens in OrderManager._finalize_close (only when
reason == "stop_loss"); checking happens early in
BotEngine._execute_open (before any MCP/sizing work).

Ledger persisted to data/risk_state.json since 2026-06-11 — a 180-min
cooldown is too long to lose on a process restart (entries still
self-prune at the 24h guard lookback). Spec §12 + Phase 27 still
fire independently.
"""
from __future__ import annotations

from tests.bot_engine_source import bot_engine_source_for_grep
from tests.order_manager_source import order_manager_impl_source

import time as _t
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _sl_cooldown_flag_on(monkeypatch):
    """2026-07-19 (max-flow band engine): the owner's .env may carry
    SL_COOLDOWN_ENABLED=false. These tests exercise the cooldown mechanism
    itself, so pin the flag ON in the live config module regardless of env."""
    import sys

    cfg = sys.modules.get("config")
    if cfg is None:
        import config as cfg
    monkeypatch.setattr(cfg, "SL_COOLDOWN_ENABLED", True, raising=False)

# ─── RiskManager has the ledger + helpers ─────────────────────────────


def test_recent_sl_attribute_initialized(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    Path("data").mkdir(exist_ok=True)
    from core.risk_manager import RiskManager
    r = RiskManager()
    assert hasattr(r, "_recent_sl_by_pair_side")
    assert isinstance(r._recent_sl_by_pair_side, dict)


def test_constants_match_design():
    from core.risk_manager import RiskManager
    assert RiskManager.POST_SL_SHORT_COOLDOWN_MIN == 180
    assert RiskManager.POST_SL_GUARD_TRADE_LIMIT == 2
    assert RiskManager.POST_SL_GUARD_LOOKBACK_HRS == 24
    assert RiskManager.POST_SL_GUARD_LOCK_HRS == 6


def test_note_sl_hit_records_timestamp(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    Path("data").mkdir(exist_ok=True)
    from core.risk_manager import RiskManager
    r = RiskManager()
    r.note_sl_hit("BTC/USDT:USDT", "buy")
    # 2026-07-10: keys are canonicalized (settle suffix stripped) — the old
    # suffixed key never matched _execute_open's bare-symbol lookup, which
    # left both cooldown layers dead for futures re-entries.
    assert "BTC/USDT|buy" in r._recent_sl_by_pair_side
    assert len(r._recent_sl_by_pair_side["BTC/USDT|buy"]) == 1


def test_note_sl_hit_prunes_old_entries(tmp_path, monkeypatch):
    """Entries older than POST_SL_GUARD_LOOKBACK_HRS are dropped."""
    monkeypatch.chdir(tmp_path)
    Path("data").mkdir(exist_ok=True)
    from core.risk_manager import RiskManager
    r = RiskManager()
    key = "BTC/USDT:USDT|buy"
    # Inject an entry from 30h ago
    r._recent_sl_by_pair_side[key] = [_t.time() - 30 * 3600]
    r.note_sl_hit("BTC/USDT:USDT", "buy")
    # Old one pruned, new one kept
    lst = r._recent_sl_by_pair_side[key]
    assert len(lst) == 1


# ─── Layer 1: 30-min cooldown after any SL ────────────────────────────


def test_no_cooldown_when_no_sl_history(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    Path("data").mkdir(exist_ok=True)
    from core.risk_manager import RiskManager
    r = RiskManager()
    active, reason = r.is_sl_cooldown_active("BTC/USDT:USDT", "buy")
    assert active is False


def test_cooldown_active_within_180min_after_sl(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    Path("data").mkdir(exist_ok=True)
    from core.risk_manager import RiskManager
    r = RiskManager()
    # Simulate SL 170 min ago — would have cleared under the old 30-min
    # constant; must still block under 180 (2026-06-11 raise)
    r._recent_sl_by_pair_side["BTC/USDT:USDT|buy"] = [_t.time() - 170 * 60]
    active, reason = r.is_sl_cooldown_active("BTC/USDT:USDT", "buy")
    assert active is True
    assert "post_sl_cooldown" in reason


def test_cooldown_clears_after_180min(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    Path("data").mkdir(exist_ok=True)
    from core.risk_manager import RiskManager
    r = RiskManager()
    # Simulate SL 185 min ago (past 180min threshold; single SL so the
    # 2-SL/6h guard layer does not apply)
    r._recent_sl_by_pair_side["BTC/USDT:USDT|buy"] = [_t.time() - 185 * 60]
    active, reason = r.is_sl_cooldown_active("BTC/USDT:USDT", "buy")
    assert active is False


# ─── Layer 2: 6h escalated lock after 2+ SL in 24h ───────────────────


def test_guard_active_with_2_sl_in_24h(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    Path("data").mkdir(exist_ok=True)
    from core.risk_manager import RiskManager
    r = RiskManager()
    # Two SL: 5h ago and 1h ago (both inside 24h lookback, last is 1h ago)
    now = _t.time()
    r._recent_sl_by_pair_side["BTC/USDT:USDT|buy"] = [
        now - 5 * 3600, now - 1 * 3600,
    ]
    active, reason = r.is_sl_cooldown_active("BTC/USDT:USDT", "buy")
    assert active is True
    assert "sl_guard" in reason


def test_guard_clears_after_6h_from_last_sl(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    Path("data").mkdir(exist_ok=True)
    from core.risk_manager import RiskManager
    r = RiskManager()
    # Two SL: 23h ago and 7h ago (last is 7h, past the 6h lock)
    now = _t.time()
    r._recent_sl_by_pair_side["BTC/USDT:USDT|buy"] = [
        now - 23 * 3600, now - 7 * 3600,
    ]
    active, reason = r.is_sl_cooldown_active("BTC/USDT:USDT", "buy")
    assert active is False


def test_other_pair_side_not_affected(tmp_path, monkeypatch):
    """SL on (BTC, buy) shouldn't lock (BTC, sell) or (ETH, buy)."""
    monkeypatch.chdir(tmp_path)
    Path("data").mkdir(exist_ok=True)
    from core.risk_manager import RiskManager
    r = RiskManager()
    r._recent_sl_by_pair_side["BTC/USDT:USDT|buy"] = [_t.time() - 5 * 60]
    a1, _ = r.is_sl_cooldown_active("BTC/USDT:USDT", "sell")
    a2, _ = r.is_sl_cooldown_active("ETH/USDT:USDT", "buy")
    assert a1 is False
    assert a2 is False


def test_opposite_side_not_blocked_at_170min(tmp_path, monkeypatch):
    """Chosen semantics for the 2026-06-11 raise: cooldown stays per
    (symbol, side). A short stop-out must NOT block a subsequent long."""
    monkeypatch.chdir(tmp_path)
    Path("data").mkdir(exist_ok=True)
    from core.risk_manager import RiskManager
    r = RiskManager()
    r._recent_sl_by_pair_side["ADA/USDT:USDT|sell"] = [_t.time() - 170 * 60]
    blocked_same, reason = r.is_sl_cooldown_active("ADA/USDT:USDT", "sell")
    blocked_opp, _ = r.is_sl_cooldown_active("ADA/USDT:USDT", "buy")
    assert blocked_same is True and "post_sl_cooldown" in reason
    assert blocked_opp is False


def test_execute_open_blocks_on_active_cooldown():
    """2026-06-11: re-armed as a hard block. The 2026-05-27 advisory-only
    mode ('log advisory only, don't block') let the Jun-11 revenge
    re-entry tape happen — 9-12 re-shorts per symbol into 70 stop-losses.
    The is_sl_cooldown_active hit must be followed by `return False`."""
    src = bot_engine_source_for_grep()
    idx = src.index("self.risk.is_sl_cooldown_active(")
    block = src[idx:idx + 600]
    assert "return False" in block, (
        "Phase 29 cooldown must BLOCK, not advise — re-armed 2026-06-11")


def test_cooldown_persists_across_restart(tmp_path, monkeypatch):
    """note_sl_hit → _save_state → a fresh RiskManager (same cwd, same
    day) restores the ledger and still blocks within 180 min. A 3-hour
    cooldown that evaporates on restart is not protection."""
    monkeypatch.chdir(tmp_path)
    Path("data").mkdir(exist_ok=True)
    from core.risk_manager import RiskManager
    r1 = RiskManager()
    r1.note_sl_hit("DOT/USDT:USDT", "sell")
    r2 = RiskManager()  # simulated restart — reads data/risk_state.json
    active, reason = r2.is_sl_cooldown_active("DOT/USDT:USDT", "sell")
    assert active is True
    assert "post_sl_cooldown" in reason


# ─── Wire-up: order_manager records, bot_engine checks ────────────────


def test_finalize_close_records_sl_hit():
    """On stop_loss exit, _finalize_close calls risk.note_sl_hit."""
    src = order_manager_impl_source()
    fc_idx = src.index("def _finalize_close(self, pos: Position")
    fc_block = src[fc_idx:fc_idx + 2000]
    assert 'reason == "stop_loss"' in fc_block
    assert "self.risk.note_sl_hit(" in fc_block


def test_finalize_close_only_records_on_sl_not_other_reasons():
    """Make sure note_sl_hit is gated by the stop_loss reason check —
    we don't want trailing/AGE/ghost exits to start the cooldown."""
    src = order_manager_impl_source()
    fc_idx = src.index("def _finalize_close(self, pos: Position")
    fc_block = src[fc_idx:fc_idx + 2000]
    # The note_sl_hit call must appear AFTER the if-stop_loss guard
    guard_idx = fc_block.index('reason == "stop_loss"')
    note_idx = fc_block.index("self.risk.note_sl_hit(")
    assert note_idx > guard_idx


def test_execute_open_checks_cooldown_early():
    """Cooldown check must run before MCP/risk eval — cheap early exit."""
    src = bot_engine_source_for_grep()
    eo_idx = src.index("def _execute_open(self, action: dict)")
    # Window widened 3000→3500 (2026-06-12): provenance reject_reason stashes
    # added ~3 one-liner lines before this point; check order is unchanged.
    # Widened 3500→4300 (2026-07-12): the entries-only kill switch (an even
    # cheaper, entries-global early exit) now sits BEFORE the cooldown check;
    # check order relative to MCP/risk eval is unchanged.
    head = src[eo_idx:eo_idx + 4300]
    assert "self.risk.is_sl_cooldown_active(" in head


def test_execute_open_logs_on_block():
    """Operators see the block in logs — Phase 29 prefix is the marker."""
    src = bot_engine_source_for_grep()
    assert "[Risk29]" in src


# ─── Edge cases ───────────────────────────────────────────────────────


def test_empty_symbol_returns_false():
    """Defensive: empty symbol/side → no cooldown (don't false-block)."""
    import os
    cwd = os.getcwd()
    try:
        from pathlib import Path as _P
        _P("data").mkdir(exist_ok=True)
        from core.risk_manager import RiskManager
        r = RiskManager()
        active, _ = r.is_sl_cooldown_active("", "buy")
        assert active is False
        active, _ = r.is_sl_cooldown_active("BTC/USDT:USDT", "")
        assert active is False
    finally:
        os.chdir(cwd)
