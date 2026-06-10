"""Phase 23 — calibrator hard-refuse + dust-skip cooldown (2026-05-04).

User flagged "Trading Gate shows PEAK but only 1 trade open" after
monitoring 7 candidate cycles where MCP-approved BTC/ETH/LINK
proposals dust-skipped or got soft-discounted into oblivion. Live
trace showed the same trio re-pitched every 5min for 30+ minutes:

  [Calibrator] claude_portfolio 0.85: predicted=85% actual=9% (n=35)
  [Claude] BTC/USDT size x0.70 (calibrator: raw=0.85 -> calibrated=0.09)
  [Claude] BTC/USDT size x0.40 (regime soft-mult — Phase 22)
  [Claude] BTC/USDT: size 0.00036 < step 0.001 — skip

Two structural problems exposed:

(1) Phase 18's calibrator multiplier had a silent bug — `if _calibrated
    > 0` skipped the catastrophic-zero case where the calibrator had
    legitimately learned the signal has zero edge.

(2) When raw_conf=0.85 maps to actual_wr=0.09 over n=35, the
    [0.7, 1.3] clamp meant a 9% real win-rate signal still got 70%
    of size — soft-discount instead of refuse.

(3) The dust-skip path at exchange-step boundary kept re-firing every
    5 minutes for the same symbol — 3 cycles wasted per hour just
    re-running MCP/news compute on signals we already know we can't
    fill.

Phase 23 fix:
  - Hard-refuse when calibrator has meaningful data (divergence > 2%)
    AND calibrated < 40%. Records 30min cooldown.
  - Dust-skip cooldown: 30min per-symbol after size<step. Stops re-pitch.
  - The 0.0 silent-skip is replaced by divergence-based gating.
"""
from __future__ import annotations

from pathlib import Path

# ─── Cooldown attribute initialized in BotEngine.__init__ ────────────


def test_dust_skip_cooldown_attr_initialized():
    """BotEngine.__init__ must initialize the cooldown dict."""
    src = Path("core/bot_engine.py").read_text(encoding="utf-8")
    # Find init region
    init_start = src.index("def __init__(self):")
    # Walk forward to next 'def ' to bound init
    next_def = src.index("\n    def ", init_start + 10)
    init_block = src[init_start:next_def]
    assert "self._dust_skip_cooldown" in init_block, \
        "BotEngine.__init__ must initialize _dust_skip_cooldown dict"
    assert "dict[str, float]" in init_block


# ─── Cooldown check is early in _execute_open ────────────────────────


def test_cooldown_check_runs_before_mode_gates():
    """Cooldown check must short-circuit BEFORE any expensive work
    (mode gates, universe filter, MCP scoring). Cheap early-exit."""
    src = Path("core/bot_engine.py").read_text(encoding="utf-8")
    eo_idx = src.index("def _execute_open(self, action: dict)")
    mode_idx = src.index("OPERATING_MODE == \"OBSERVATION\"", eo_idx)
    head = src[eo_idx:mode_idx]
    assert "_dust_skip_cooldown" in head, \
        "cooldown check must appear before OBSERVATION mode gate"


def test_cooldown_check_uses_get_with_default():
    """Use .get() with default so missing key never raises."""
    src = Path("core/bot_engine.py").read_text(encoding="utf-8")
    eo_idx = src.index("def _execute_open(self, action: dict)")
    head = src[eo_idx:eo_idx + 2000]
    assert "self._dust_skip_cooldown.get(symbol" in head


# ─── Dust-skip path sets the cooldown ────────────────────────────────


def test_dust_skip_sets_cooldown():
    """size<step must record cooldown timestamp (current_time + 1800s)."""
    src = Path("core/bot_engine.py").read_text(encoding="utf-8")
    step_idx = src.index("size {size:.8f} < step")
    block = src[max(0, step_idx - 600):step_idx + 100]
    assert "self._dust_skip_cooldown[symbol]" in block
    assert "1800" in block, "cooldown must be 30 min (1800s)"


def test_dust_skip_log_mentions_cooldown():
    """Log line must say cooldown is set so operators understand
    why the same symbol stops being re-pitched."""
    src = Path("core/bot_engine.py").read_text(encoding="utf-8")
    assert "skip + 30min cooldown" in src


# ─── A1: Hard-refuse when calibrator < 0.40 ──────────────────────────


def test_calibrator_hard_refuse_threshold_is_30_percent_phase40():
    """When calibrator has data and predicts <40% win, refuse the
    trade outright instead of soft-discounting through the 0.7 floor."""
    src = Path("core/bot_engine.py").read_text(encoding="utf-8")
    cal_idx = src.index("self.order_mgr.calibrator.calibrate(")
    block = src[cal_idx:cal_idx + 3000]
    assert "_calibrated < 0.30" in block, \
        "Phase 23 hard-refuse threshold must be < 30% (Phase 40)"
    assert "REFUSED" in block, \
        "log line must say REFUSED so operators see the gate fire"


def test_calibrator_refuse_also_sets_cooldown():
    """Hard-refuse must also set the dust-skip cooldown so the same
    dead signal isn't re-pitched on every 5min cycle. (Window widened
    2026-06-11: the refuse now sits inside the opt-in
    calibrator_hard_refuse_enabled branch, which added lines.)"""
    src = Path("core/bot_engine.py").read_text(encoding="utf-8")
    cal_idx = src.index("self.order_mgr.calibrator.calibrate(")
    block = src[cal_idx:cal_idx + 3000]
    refuse_idx = block.index("_calibrated < 0.30")
    refuse_block = block[refuse_idx:refuse_idx + 1400]
    assert "self._dust_skip_cooldown[symbol]" in refuse_block
    assert "return False" in refuse_block


# ─── Phase 18 bug: 0.0 silent-skip is fixed ──────────────────────────


def test_zero_calibrator_no_longer_silently_skipped():
    """The Phase 18 `if _calibrated > 0` guard would silently skip
    the catastrophic 0.0 case. Fixed by switching to divergence-based
    gating: `abs(_calibrated - _raw_conf) > 0.02`."""
    src = Path("core/bot_engine.py").read_text(encoding="utf-8")
    cal_idx = src.index("self.order_mgr.calibrator.calibrate(")
    block = src[cal_idx:cal_idx + 3000]
    # New divergence guard must be present
    assert "abs(_calibrated - _raw_conf) > 0.02" in block
    # Naming stable (used by other tests / log readers)
    assert "_has_calib_data" in block


# ─── Phase 18 invariants preserved ───────────────────────────────────


def test_phase18_soft_mult_clamp_preserved():
    """Phase 18 [0.7, 1.3] symmetric clamp must remain on the soft path."""
    src = Path("core/bot_engine.py").read_text(encoding="utf-8")
    assert "max(0.7, min(1.3," in src


def test_phase18_multiplier_ordering_preserved():
    """ev_mult * cal_mult * regime_mult * notional ordering invariant.
    Phase 18 + 22 tests pin this; Phase 23 must not break it."""
    src = Path("core/bot_engine.py").read_text(encoding="utf-8")
    ev_idx = src.index("size_fraction *= _ev_mult")
    cal_idx = src.index("size_fraction *= _cal_mult")
    regime_idx = src.index("size_fraction *= _regime_size_mult")
    notional_idx = src.index("notional = mtype_bal * size_fraction")
    assert ev_idx < cal_idx < regime_idx < notional_idx, \
        "expected order: ev → cal → regime → notional"


# ─── Behavioral integration tests ────────────────────────────────────


def test_cooldown_dict_typed_and_initialized_empty():
    """Type annotation pins the dict shape; empty {} default makes
    the .get() lookup safe for any never-cooled symbol."""
    src = Path("core/bot_engine.py").read_text(encoding="utf-8")
    init_start = src.index("def __init__(self):")
    next_def = src.index("\n    def ", init_start + 10)
    init_block = src[init_start:next_def]
    assert "self._dust_skip_cooldown: dict[str, float] = {}" in init_block
