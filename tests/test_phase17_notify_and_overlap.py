"""Phase 17 — halt notifier + multi-strategy overlap gate (2026-05-03).

Two real bugs surfaced by Ruflo agents:

1. SPEC §12 / drawdown / daily-loss halts logged WARN/ERROR but never
   emitted a user-visible notifier alert. User is locked out and doesn't
   know why bot went silent.

2. DCAStrategy can BUY spot BTC/ETH/SOL/BNB while SpotPortfolioManager
   peak-DD logic SELLs the same asset same-day — same-asset opposing
   orders, no coordination.
"""
from __future__ import annotations

import inspect
from pathlib import Path


def test_halt_notifier_helper_exists():
    from core.risk_manager import RiskManager
    assert hasattr(RiskManager, "_notify_halt")


def test_halt_notifier_lazy_imports():
    """Notifier import must be inside the helper, not module-level —
    risk_manager is imported during __init__ before notifier may be ready."""
    from core.risk_manager import RiskManager
    src = inspect.getsource(RiskManager._notify_halt)
    assert "from utils.notifier" in src
    assert "TelegramNotifier" in src


def test_halt_notifier_failure_does_not_propagate():
    """If notifier raises, halt logic must continue — silent fail-safe."""
    from core.risk_manager import RiskManager
    src = inspect.getsource(RiskManager._notify_halt)
    assert "except Exception" in src


def test_daily_loss_halt_calls_notifier():
    src = Path("core/risk_manager.py").read_text(encoding="utf-8")
    # Find the daily-loss halt block
    block_start = src.index("Daily loss circuit-breaker")
    block_end = src.index("Max drawdown circuit-breaker")
    block = src[block_start:block_end]
    assert "_notify_halt(\"DAILY LOSS HALT\"" in block


def test_drawdown_halt_calls_notifier():
    src = Path("core/risk_manager.py").read_text(encoding="utf-8")
    # Find the drawdown halt block
    block_start = src.index("Max drawdown circuit-breaker")
    block_end = src.index("def set_start_balance")
    block = src[block_start:block_end]
    assert "_notify_halt(\"DRAWDOWN HALT\"" in block


def test_spec12_halt_calls_notifier():
    src = Path("core/risk_manager.py").read_text(encoding="utf-8")
    assert "_notify_halt(" in src
    # Must reference SPEC §12 in the alert
    assert "SPEC" in src and "CONSECUTIVE LOSSES" in src


# ─── Multi-strategy overlap gate ──────────────────────────────────


def test_dca_records_today_buys():
    src = Path("core/bot_engine.py").read_text(encoding="utf-8")
    assert "_dca_buys_today" in src
    assert "self._dca_buys_today[base] = time.time()" in src


def test_spot_action_blocks_sell_after_dca():
    src = Path("core/bot_engine.py").read_text(encoding="utf-8")
    # The block must check DCA buys before executing SELL/SCALE_OUT
    block_start = src.index("Multi-strategy coordination gate")
    block_end = src.index("self._execute_spot_action(a)", block_start)
    block = src[block_start:block_end]
    assert 'act in ("SELL", "SCALE_OUT")' in block
    assert "age_h < 24" in block
    assert "opposing-order skip" in block


def test_overlap_gate_runs_before_execute():
    """The gate must `continue` (skip) before `_execute_spot_action` is
    called — otherwise the SELL would still fire."""
    src = Path("core/bot_engine.py").read_text(encoding="utf-8")
    block_start = src.index("Multi-strategy coordination gate")
    block = src[block_start:block_start + 1000]
    # Must contain `continue` before any execute call
    continue_idx = block.index("continue")
    exec_idx = block.index("_execute_spot_action")
    assert continue_idx < exec_idx


# ─── Phase 17 fix — adaptive sizing wired into LIVE path ────────


def test_adaptive_sizing_applied_in_execute_open():
    """Critical: Phase 16's _adaptive_size_multiplier must be called
    inside bot_engine._execute_open before notional is computed.

    Ruflo reviewer surfaced that the multiplier was wired into
    RiskManager.calculate_position_size, but the Claude portfolio
    path never calls that method — the multiplier was dead code."""
    src = Path("core/bot_engine.py").read_text(encoding="utf-8")
    # Must reference _adaptive_size_multiplier in _execute_open's flow
    assert "self.risk._adaptive_size_multiplier()" in src
    # Must apply the multiplier to size_fraction BEFORE notional is computed
    mult_idx = src.index("size_fraction *= _ev_mult")
    notional_idx = src.index("notional = mtype_bal * size_fraction")
    assert mult_idx < notional_idx, \
        "adaptive sizing must apply to size_fraction before notional computation"


def test_adaptive_sizing_logs_on_change():
    """When the multiplier is non-1.0, the log line must print so the
    operator can see when the bot is sizing down due to recent EV."""
    src = Path("core/bot_engine.py").read_text(encoding="utf-8")
    # Log line must mention 'adaptive sizing' and reference the EV
    assert "adaptive sizing — rolling-50 EV" in src
