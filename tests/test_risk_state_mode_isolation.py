"""PAPER risk state must not become the starting truth for real capital.

`data/risk_state.json` carries peak_balance, start_balance, daily_pnl and the
pause ledgers. RISK_STATE_PATH is a single un-namespaced file and `_load_state`
never checked which mode wrote it, so a CONTROLLED_LIVE boot would adopt the
paper run's numbers wholesale.

Measured on the live file (2026-08-18): peak_balance = 15568.86 against real
capital of $5,000. Drawdown is computed from that peak, so live would start at
(15568.86 - 5000) / 15568.86 = 67.9% drawdown against a max_drawdown_pct of
0.08 — permanently past the halt on its first tick, for a peak that never
existed. The reverse ordering (small paper peak, larger live equity) fails the
other way: it under-reports drawdown on real money.

Fix: stamp the writing mode into the state and refuse to adopt state written by
a different mode. Backward compatibility is asymmetric ON PURPOSE — a legacy
unstamped file is assumed to be the PAPER research state it almost certainly
is, so PAPER keeps its history; CONTROLLED_LIVE refuses an unstamped file
rather than guess, because guessing wrong costs money.

Scope note: tests/conftest.py redirects RISK_STATE_PATH for isolation and
tests/test_daily_counter_rollover.py covers UTC day rollover. Neither asserts
mode scoping.

Run: venv/Scripts/python.exe -m pytest tests/test_risk_state_mode_isolation.py -v
"""

from __future__ import annotations

import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import core.risk_manager as rm  # noqa: E402

PAPER_PEAK = 15568.86


def _state(mode="PAPER", **over):
    s = {
        "trading_day": datetime.now(timezone.utc).date().isoformat(),
        "peak_balance": PAPER_PEAK,
        "start_balance": 5000.0,
        "daily_pnl": -0.81,
        "trades_today": 4,
        "recent_results": [1, 0, 1],
        "timestamp": time.time(),
    }
    if mode is not None:
        s["operating_mode"] = mode
    s.update(over)
    return s


def _blank():
    r = object.__new__(rm.RiskManager)
    r._peak_balance = 0.0
    r._start_balance = 0.0
    r._daily_pnl = 0.0
    r._opens_today = 0
    r._recent_results = []
    r._trade_history = []
    r._symbol_pauses = {}
    r._family_pauses = {}
    r._global_streak = []
    r._recent_sl_by_pair_side = {}
    r._daily_anchor_valid = True
    r._loaded_same_day_state = False
    r._trading_day = datetime.now(timezone.utc).date()
    r._honour_review_flag_if_present = lambda: None
    return r


def _loaded(tmp_path, monkeypatch, *, written_by, running_as):
    """Write a state file as `written_by`, then load it as `running_as`."""
    p = tmp_path / "risk_state.json"
    p.write_text(json.dumps(_state(written_by)), encoding="utf-8")
    monkeypatch.setattr(rm, "RISK_STATE_PATH", p)
    monkeypatch.setattr(rm, "OPERATING_MODE", running_as, raising=False)
    r = _blank()
    r._load_state()
    return r


# ── the isolation ───────────────────────────────────────────────────────────

def test_paper_state_is_not_adopted_by_live(tmp_path, monkeypatch):
    """THE bug: a $15,568 paper peak must not govern a $5,000 live account."""
    r = _loaded(tmp_path, monkeypatch, written_by="PAPER", running_as="CONTROLLED_LIVE")
    assert r._peak_balance != PAPER_PEAK, (
        "CONTROLLED_LIVE adopted the PAPER peak — drawdown would be measured "
        "against a peak that never existed"
    )
    assert r._daily_pnl == 0.0


def test_paper_state_is_kept_by_paper(tmp_path, monkeypatch):
    """Same-mode resume must be untouched — this must not cost research state."""
    r = _loaded(tmp_path, monkeypatch, written_by="PAPER", running_as="PAPER")
    assert r._peak_balance == PAPER_PEAK
    assert r._recent_results == [1, 0, 1]


def test_live_state_is_not_adopted_by_paper(tmp_path, monkeypatch):
    """Isolation runs both ways: research must not inherit live history."""
    r = _loaded(tmp_path, monkeypatch, written_by="CONTROLLED_LIVE", running_as="PAPER")
    assert r._peak_balance != PAPER_PEAK


def test_live_state_is_kept_by_live(tmp_path, monkeypatch):
    r = _loaded(tmp_path, monkeypatch,
                written_by="CONTROLLED_LIVE", running_as="CONTROLLED_LIVE")
    assert r._peak_balance == PAPER_PEAK


# ── legacy files: asymmetric on purpose ─────────────────────────────────────

def test_unstamped_legacy_state_is_kept_by_paper(tmp_path, monkeypatch):
    """Today's real file has no stamp. PAPER must not lose accrued history."""
    r = _loaded(tmp_path, monkeypatch, written_by=None, running_as="PAPER")
    assert r._peak_balance == PAPER_PEAK


def test_unstamped_legacy_state_is_refused_by_live(tmp_path, monkeypatch):
    """Unknown provenance + real capital = start fresh. Never guess."""
    r = _loaded(tmp_path, monkeypatch, written_by=None, running_as="CONTROLLED_LIVE")
    assert r._peak_balance != PAPER_PEAK


# ── the stamp itself ────────────────────────────────────────────────────────

def test_save_stamps_the_writing_mode(tmp_path, monkeypatch):
    """Without a stamp on write, the load-side check can never work."""
    p = tmp_path / "risk_state.json"
    monkeypatch.setattr(rm, "RISK_STATE_PATH", p)
    r = _blank()
    r._peak_balance = 100.0
    r._start_balance = 100.0
    r._persistent_breaker_reason = lambda: ""
    r._save_state()

    saved = json.loads(p.read_text(encoding="utf-8"))
    assert "operating_mode" in saved, "saved state must record which mode wrote it"
    assert saved["operating_mode"] == rm.OPERATING_MODE


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
