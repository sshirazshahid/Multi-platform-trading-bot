"""MIN-NOTIONAL FLOOR (owner UNBLOCK directive 2026-07-10).

"The trading bot should perform ANY trades (any pair, any coin, any stock)
which it analyzes to be profitable." The last de-facto edge-opinion block:
the stacked EV size multipliers (Phase 17 rolling-50, Phase 18 calibrator,
Phase 27 per-symbol EV) shrink size below the exchange lot minimum and
_execute_open dust-skips (+30min cooldown):

  [Claude] BTC/USDT: size 0.00087500 < step 0.001
  (need $32 at 2x, have $28) — skip + 30min cooldown

When MIN_NOTIONAL_FLOOR is enabled, bot_engine floors such entries back UP
to the exchange minimum — ONLY when ALL hold:
  (a) the PRE-multiplier base sizing could afford the floor (undo
      opinion-downsizing only; never override balance/margin reality),
  (b) the hard loss clamp passes at the floored size,
  (c) the §2 portfolio-exposure cap passes at the floored size
      (fail-CLOSED on error, same as the main rail).
Default OFF (public repo) -> byte-identical dust-skip behavior.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

# Observed live case (2026-07-10): BTC step 0.001 at $64k needs $32 margin
# at 2x; the multiplier chain left $28. Base (pre-EV-opinion) sizing had $40.
STEP = 0.001
PRICE = 64000.0
LEV = 2
FLOOR = STEP * PRICE / LEV      # $32
HAVE = 28.0                     # post-multiplier notional (too small)
BASE = 40.0                     # pre-multiplier base notional (affords floor)
MTYPE_BAL = 100.0
SL_PCT = 1.5


def _stub_engine(monkeypatch, *, enabled=True, clamp_ok=True,
                 exposure_hit=False, exposure_raises=False, equity=100.0):
    """Minimal BotEngine stub (repo idiom: __new__ skips __init__)."""
    import config
    from core.bot_engine import BotEngine
    eng = BotEngine.__new__(BotEngine)
    monkeypatch.setattr(config, "MIN_NOTIONAL_FLOOR",
                        {"enabled": enabled}, raising=False)
    eng._within_loss_clamp = MagicMock(return_value=clamp_ok)
    eng._balances = {}
    eng.tracker = MagicMock()
    eng.tracker.get_open = MagicMock(return_value=[])
    monkeypatch.setattr("core.bot_engine._deployable_total",
                        MagicMock(return_value=equity))
    if exposure_raises:
        _exp = MagicMock(side_effect=RuntimeError("boom"))
    else:
        _exp = MagicMock(return_value=exposure_hit)
    monkeypatch.setattr("core.risk_manager.exposure_breached", _exp)
    return eng, _exp


def _call(eng, **overrides):
    kw = dict(symbol="BTC/USDT", step=STEP, price=PRICE, leverage=LEV,
              market_type="futures", notional=HAVE, base_notional=BASE,
              mtype_bal=MTYPE_BAL, sl_pct=SL_PCT)
    kw.update(overrides)
    return eng._min_notional_floor(**kw)


# ─── Flag OFF: skip behavior preserved ───────────────────────────────


def test_flag_off_returns_zero_skip_preserved(monkeypatch):
    eng, _ = _stub_engine(monkeypatch, enabled=False)
    assert _call(eng) == 0.0


def test_config_default_is_off():
    import os

    import config
    assert isinstance(config.MIN_NOTIONAL_FLOOR, dict)
    if os.getenv("MIN_NOTIONAL_FLOOR_ENABLED") is None:
        assert config.MIN_NOTIONAL_FLOOR["enabled"] is False


# ─── Flag ON: floors when the base sizing affords it ─────────────────


def test_flag_on_floors_when_base_affords(monkeypatch):
    eng, _ = _stub_engine(monkeypatch, enabled=True)
    assert _call(eng) == pytest.approx(FLOOR)


def test_flag_on_spot_floor_uses_lev_1(monkeypatch):
    eng, _ = _stub_engine(monkeypatch, enabled=True)
    # Spot: no leverage — floor is the full lot notional ($64).
    got = _call(eng, market_type="spot", notional=50.0, base_notional=70.0)
    assert got == pytest.approx(STEP * PRICE)


# ─── Flag ON: balance-poor case still SKIPS ──────────────────────────


def test_flag_on_skips_when_base_cannot_afford(monkeypatch):
    """Pre-multiplier base < floor: this is balance/margin reality, not
    opinion-downsizing — the floor must NOT fire."""
    eng, _ = _stub_engine(monkeypatch, enabled=True)
    assert _call(eng, base_notional=20.0) == 0.0


# ─── Risk rails run at the FLOORED size ──────────────────────────────


def test_loss_clamp_rejection_at_floored_size_skips(monkeypatch):
    eng, _ = _stub_engine(monkeypatch, enabled=True, clamp_ok=False)
    assert _call(eng) == 0.0
    # The clamp must have been consulted with the FLOORED notional.
    eng._within_loss_clamp.assert_called_once_with(
        MTYPE_BAL, pytest.approx(FLOOR), LEV, SL_PCT / 100.0)


def test_exposure_cap_breach_at_floored_size_skips(monkeypatch):
    eng, exp = _stub_engine(monkeypatch, enabled=True, exposure_hit=True)
    assert _call(eng) == 0.0
    # Exposure checked against the FLOORED gross notional (step * price).
    args = exp.call_args[0]
    assert args[1] == pytest.approx(STEP * PRICE)


def test_exposure_cap_error_fails_closed(monkeypatch):
    """A broken exposure check must SKIP (fail-closed), mirroring the
    §2 main rail — never open the gate on a glitch."""
    eng, _ = _stub_engine(monkeypatch, enabled=True, exposure_raises=True)
    assert _call(eng) == 0.0


# ─── Source-level wire-up pins (repo scan-test style) ────────────────


def _src() -> str:
    return Path("core/bot_engine.py").read_text(encoding="utf-8")


def test_floor_attempt_sits_before_dust_skip():
    """The floor try must run INSIDE the step pre-check, BEFORE the
    cooldown + skip it replaces."""
    src = _src()
    eo = src.index("def _execute_open(self, action: dict)")
    block = src[eo:]
    call_idx = block.index("self._min_notional_floor(")
    skip_idx = block.index("skip + 30min cooldown")
    assert call_idx < skip_idx, (
        "floor attempt must precede the dust-skip it rescues")


def test_floor_respects_clamp_and_exposure_before_applying():
    """Within the helper, BOTH rails (loss clamp + §2 exposure cap) must
    be consulted BEFORE the floored notional is returned/applied — the
    floor never bypasses or reorders a risk gate."""
    src = _src()
    h = src.index("def _min_notional_floor(")
    end = src.index("\n    def ", h + 10)
    body = src[h:end]
    ret_idx = body.rindex("return floor_notional")
    assert body.index("_within_loss_clamp") < ret_idx
    assert body.index("exposure_breached") < ret_idx


def test_original_skip_path_unchanged():
    src = _src()
    assert 'action["reject_reason"] = "size_below_step"' in src
    assert "skip + 30min cooldown" in src
    step_idx = src.index("size {size:.8f} < step")
    assert "self._dust_skip_cooldown[symbol]" in src[step_idx - 900:step_idx]


def test_pre_ev_snapshot_taken_before_opinion_multipliers():
    """The base-affordability snapshot must be taken BEFORE the EV-opinion
    stack (Phase 17 rolling-50, Phase 18 calibrator, Phase 27 EV) so the
    floor undoes opinion-downsizing only."""
    src = _src()
    # NB: operands flipped vs the later `notional = mtype_bal * size_fraction`
    # so the Phase 18/27 ordering pins (which .index() that substring) keep
    # matching the real notional assignment, not this snapshot.
    snap = src.index("_pre_ev_notional = size_fraction * mtype_bal")
    assert snap < src.index("size_fraction *= _ev_mult")
    assert snap < src.index("size_fraction *= _cal_mult")
    assert snap < src.index("size_fraction *= _ev_symbol_mult")


def test_floor_log_line_format():
    src = _src()
    assert "min-notional floor:" in src
    assert "opinion-downsized below exchange min; UNBLOCK directive" in src
