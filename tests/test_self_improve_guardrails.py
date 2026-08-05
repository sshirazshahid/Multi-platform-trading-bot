"""WS0 — the PAPER + kernel latch. The autonomous loop must hard-refuse off-PAPER
and refuse any write to an immutable / runtime-control path.

Import-light: core.decision.guardrails + config (patched in sys.modules, the same
object guardrails resolves at call time — mirrors tests/test_tsmom_mode_wiring.py).
"""
from __future__ import annotations

import sys

import pytest

import config  # noqa: F401  -- ensure sys.modules["config"] exists for monkeypatch
from core.decision import guardrails as g
from core.decision.guardrails import SelfImproveLatchError


def _set_mode(monkeypatch, mode):
    monkeypatch.setattr(sys.modules["config"], "OPERATING_MODE", mode, raising=False)


def test_enabled_default_in_paper(monkeypatch):
    _set_mode(monkeypatch, "PAPER")
    monkeypatch.delenv("SELF_IMPROVE_ENABLED", raising=False)  # default == "true"
    assert g.self_improve_enabled() is True


def test_disabled_off_paper(monkeypatch):
    _set_mode(monkeypatch, "CONTROLLED_LIVE")
    assert g.self_improve_enabled() is False


def test_env_flag_off(monkeypatch):
    _set_mode(monkeypatch, "PAPER")
    monkeypatch.setenv("SELF_IMPROVE_ENABLED", "false")
    assert g.self_improve_enabled() is False


def test_assert_ok_for_paper_and_writable_path(monkeypatch):
    _set_mode(monkeypatch, "PAPER")
    # active_strategies.json is NOT kernel-denied -> allowed; empty list = posture-only.
    g.assert_paper_self_improve(["data/active_strategies.json"])
    g.assert_paper_self_improve([])


def test_assert_refuses_off_paper(monkeypatch):
    _set_mode(monkeypatch, "CONTROLLED_LIVE")
    with pytest.raises(SelfImproveLatchError):
        g.assert_paper_self_improve(["data/active_strategies.json"])


@pytest.mark.parametrize("path", [
    "config.py",
    "config/modes.py",
    "core/live_gate.py",
    "core/promotion_gate.py",
    "data/ab_split.json",
    ".env",
])
def test_assert_refuses_kernel_paths(monkeypatch, path):
    _set_mode(monkeypatch, "PAPER")
    with pytest.raises(SelfImproveLatchError):
        g.assert_paper_self_improve([path])
