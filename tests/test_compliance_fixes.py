"""CLAUDE.md compliance fixes (2026-06-15) — from the constitution audit + ultrathink.

R2: portfolio-exposure circuit breaker (exposure_breached + 12% cap).
R4: leverage clamp pinned to the constitutional <=2.5x via futures_max_leverage.
R7: /journal/YYYY-MM-DD.md daily action journal.
"""
from datetime import datetime, timezone
from types import SimpleNamespace

import config
from core.journal import log_action
from core.risk_manager import exposure_breached


def _pos(size, entry):
    return SimpleNamespace(size=size, entry_price=entry)


# ── R2: exposure_breached ────────────────────────────────────────────────────

def test_exposure_under_cap_passes():
    assert exposure_breached([], new_notional=10.0, equity=1000.0, max_pct=12.0) is False  # 1%


def test_exposure_new_alone_over_cap():
    assert exposure_breached([], new_notional=150.0, equity=1000.0, max_pct=12.0) is True  # 15%


def test_exposure_existing_positions_push_over():
    pos = [_pos(1.0, 100.0)]  # $100 notional = 10% of $1000
    assert exposure_breached(pos, new_notional=30.0, equity=1000.0, max_pct=12.0) is True  # 13%


def test_exposure_existing_under_cap():
    pos = [_pos(1.0, 100.0)]  # 10%
    assert exposure_breached(pos, new_notional=10.0, equity=1000.0, max_pct=12.0) is False  # 11%


def test_exposure_fail_open_on_zero_equity():
    assert exposure_breached([_pos(1, 100)], 1000.0, equity=0.0, max_pct=12.0) is False


def test_exposure_disabled_when_max_pct_zero():
    assert exposure_breached([_pos(1, 100)], 1000.0, equity=1000.0, max_pct=0.0) is False


def test_exposure_tolerates_bad_position_fields():
    pos = [_pos(None, None), _pos(1.0, 100.0)]   # bad row contributes 0, no crash
    assert exposure_breached(pos, new_notional=10.0, equity=1000.0, max_pct=12.0) is False  # 11%


# ── R4: leverage cap pinned to the constitution (<=2.5x) ─────────────────────

def test_futures_max_leverage_within_constitution():
    # CLAUDE.md §2.4: max active leverage <= 2.5x. The R4 clamp uses this config value,
    # so pinning it <=2.5 guarantees the clamp keeps the bot constitutional.
    assert config.RISK["futures_max_leverage"] <= 2.5


def test_exposure_cap_default_is_constitutional():
    # CLAUDE.md §2.2: total open exposure <= 12%.
    assert 0 < config.MAX_PORTFOLIO_EXPOSURE_PCT <= 12.0


# ── R7: journal.log_action ───────────────────────────────────────────────────

def test_journal_writes_action(tmp_path):
    ts = datetime(2026, 6, 15, 13, 45, 7, tzinfo=timezone.utc)
    fp = log_action("OPEN", "BTC/USDT:USDT", "buy", "1x size=0.01", ts=ts, journal_dir=tmp_path)
    assert fp is not None and fp.exists()
    txt = fp.read_text(encoding="utf-8")
    assert "# Trade Journal 2026-06-15" in txt
    assert "**OPEN** buy BTC/USDT:USDT 1x size=0.01" in txt
    assert "13:45:07Z" in txt


def test_journal_appends_without_duplicate_header(tmp_path):
    ts = datetime(2026, 6, 15, 13, 0, 0, tzinfo=timezone.utc)
    log_action("OPEN", "ETH", "buy", ts=ts, journal_dir=tmp_path)
    fp = log_action("CLOSE", "ETH", "buy", "reason=stop_loss", ts=ts, journal_dir=tmp_path)
    txt = fp.read_text(encoding="utf-8")
    assert txt.count("# Trade Journal") == 1          # header written once
    assert "**OPEN**" in txt and "**CLOSE**" in txt


def test_journal_best_effort_never_raises():
    # An unwritable target must return None, never raise into the trade path.
    assert log_action("OPEN", "BTC", "buy", journal_dir="\x00/invalid") is None
