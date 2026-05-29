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
    Action:  30min lock on (symbol, side)
    Param:   POST_SL_SHORT_COOLDOWN_MIN = 30

  Layer 2 (escalated guard):
    Trigger: 2+ stop_loss exits on (symbol, side) within last 24h
    Action:  6h lock on (symbol, side)
    Params:  POST_SL_GUARD_TRADE_LIMIT = 2
             POST_SL_GUARD_LOOKBACK_HRS = 24
             POST_SL_GUARD_LOCK_HRS = 6

Recording happens in OrderManager._finalize_close (only when
reason == "stop_loss"); checking happens early in
BotEngine._execute_open (before any MCP/sizing work).

In-memory ledger only — fresh restart wipes (intentional; restart
implies operator wants a fresh slate). Spec §12 + Phase 27 still
fire independently.
"""
from __future__ import annotations

import time as _t
from pathlib import Path

import pytest


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
    assert RiskManager.POST_SL_SHORT_COOLDOWN_MIN == 30
    assert RiskManager.POST_SL_GUARD_TRADE_LIMIT == 2
    assert RiskManager.POST_SL_GUARD_LOOKBACK_HRS == 24
    assert RiskManager.POST_SL_GUARD_LOCK_HRS == 6


def test_note_sl_hit_records_timestamp(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    Path("data").mkdir(exist_ok=True)
    from core.risk_manager import RiskManager
    r = RiskManager()
    r.note_sl_hit("BTC/USDT:USDT", "buy")
    assert "BTC/USDT:USDT|buy" in r._recent_sl_by_pair_side
    assert len(r._recent_sl_by_pair_side["BTC/USDT:USDT|buy"]) == 1


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


def test_cooldown_active_within_30min_after_sl(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    Path("data").mkdir(exist_ok=True)
    from core.risk_manager import RiskManager
    r = RiskManager()
    # Simulate SL 5 min ago
    r._recent_sl_by_pair_side["BTC/USDT:USDT|buy"] = [_t.time() - 5 * 60]
    active, reason = r.is_sl_cooldown_active("BTC/USDT:USDT", "buy")
    assert active is True
    assert "post_sl_cooldown" in reason


def test_cooldown_clears_after_30min(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    Path("data").mkdir(exist_ok=True)
    from core.risk_manager import RiskManager
    r = RiskManager()
    # Simulate SL 35 min ago (past 30min threshold)
    r._recent_sl_by_pair_side["BTC/USDT:USDT|buy"] = [_t.time() - 35 * 60]
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


# ─── Wire-up: order_manager records, bot_engine checks ────────────────


def test_finalize_close_records_sl_hit():
    """On stop_loss exit, _finalize_close calls risk.note_sl_hit."""
    src = Path("core/order_manager.py").read_text(encoding="utf-8")
    fc_idx = src.index("def _finalize_close(self, pos: Position")
    fc_block = src[fc_idx:fc_idx + 2000]
    assert 'reason == "stop_loss"' in fc_block
    assert "self.risk.note_sl_hit(" in fc_block


def test_finalize_close_only_records_on_sl_not_other_reasons():
    """Make sure note_sl_hit is gated by the stop_loss reason check —
    we don't want trailing/AGE/ghost exits to start the cooldown."""
    src = Path("core/order_manager.py").read_text(encoding="utf-8")
    fc_idx = src.index("def _finalize_close(self, pos: Position")
    fc_block = src[fc_idx:fc_idx + 2000]
    # The note_sl_hit call must appear AFTER the if-stop_loss guard
    guard_idx = fc_block.index('reason == "stop_loss"')
    note_idx = fc_block.index("self.risk.note_sl_hit(")
    assert note_idx > guard_idx


def test_execute_open_checks_cooldown_early():
    """Cooldown check must run before MCP/risk eval — cheap early exit."""
    src = Path("core/bot_engine.py").read_text(encoding="utf-8")
    eo_idx = src.index("def _execute_open(self, action: dict)")
    head = src[eo_idx:eo_idx + 3000]
    assert "self.risk.is_sl_cooldown_active(" in head


def test_execute_open_logs_on_block():
    """Operators see the block in logs — Phase 29 prefix is the marker."""
    src = Path("core/bot_engine.py").read_text(encoding="utf-8")
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
