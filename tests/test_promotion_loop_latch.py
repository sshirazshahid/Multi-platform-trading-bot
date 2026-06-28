"""WS4 — the autonomous promotion loop is caged: PAPER-only, kernel-safe, and it
never writes a forbidden path. Promotion mechanics use a fake gate so the test is
deterministic (the real PromotionGate's bootstrap CIs are stochastic).

Import-light: promotion_loop + guardrails + config (patched in sys.modules).
"""
from __future__ import annotations

import json
import sys

import numpy as np
import pytest

import config  # noqa: F401 -- ensure sys.modules["config"] exists for monkeypatch
from core.decision import guardrails
from core.decision import promotion_loop as pl
from core.decision.guardrails import SelfImproveLatchError
from core.promotion_gate import GateVerdict


def _set_mode(monkeypatch, mode):
    monkeypatch.setattr(sys.modules["config"], "OPERATING_MODE", mode, raising=False)


class _FakeGate:
    """Duck-typed PromotionGate: returns a fixed verdict, records the inputs it saw."""

    def __init__(self, verdict):
        self.verdict = verdict
        self.seen = None

    def evaluate(self, inputs):
        self.seen = inputs
        return self.verdict, {"fake": True}


def _candidate(cid="mut_x", rets=None, n=4):
    rets = rets if rets is not None else list(np.full(120, 0.015))
    return pl.Candidate(variant_id=cid, config={"min_score": 0.34, "scalp_time_stop_bars": 6},
                        replay_trades=rets, n_configs_tested=n)


def test_register_refuses_off_paper(monkeypatch, tmp_path):
    _set_mode(monkeypatch, "CONTROLLED_LIVE")
    reg = tmp_path / "active_strategies.json"
    with pytest.raises(SelfImproveLatchError):
        pl.register_shadow(_candidate(), path=reg)
    assert not reg.exists()  # nothing written when latched


def test_promote_refuses_off_paper(monkeypatch, tmp_path):
    _set_mode(monkeypatch, "CONTROLLED_LIVE")
    reg = tmp_path / "active_strategies.json"
    ada = tmp_path / "adaptive.json"
    with pytest.raises(SelfImproveLatchError):
        pl.promote_to_active_paper(_candidate(), path=reg, adaptive_path=ada)
    assert not reg.exists() and not ada.exists()


def test_kernel_trip_blocks_write(monkeypatch, tmp_path):
    _set_mode(monkeypatch, "PAPER")
    # Simulate the kernel guard flagging a violation -> latch must refuse.
    monkeypatch.setattr(guardrails, "assert_kernel_safe", lambda paths: (False, list(paths)))
    reg = tmp_path / "active_strategies.json"
    with pytest.raises(SelfImproveLatchError):
        pl.register_shadow(_candidate(), path=reg)
    assert not reg.exists()


def test_tick_noise_registers_shadow_never_active(monkeypatch, tmp_path):
    _set_mode(monkeypatch, "PAPER")
    reg = tmp_path / "active_strategies.json"
    ada = tmp_path / "adaptive.json"
    gate = _FakeGate(GateVerdict.HOLD)   # gate says no
    rng = np.random.default_rng(0)
    cand = _candidate(rets=list(rng.normal(0.0, 0.02, 150)))
    report = pl.tick([cand], live_returns=list(rng.normal(0.0, 0.02, 150)),
                     registry_path=reg, adaptive_path=ada, gate=gate)
    assert report["n_promoted"] == 0
    data = json.loads(reg.read_text())
    assert data["variants"]["mut_x"]["status"] == "shadow"
    assert not ada.exists()   # no adaptive config written for a non-promotion


def test_tick_promotes_to_active_paper_when_gate_passes(monkeypatch, tmp_path):
    _set_mode(monkeypatch, "PAPER")
    reg = tmp_path / "active_strategies.json"
    ada = tmp_path / "adaptive.json"
    gate = _FakeGate(GateVerdict.PROMOTE)
    # Strong positive series so the capital-preservation Monte-Carlo also passes.
    cand = _candidate(rets=list(np.full(150, 0.012)))
    report = pl.tick([cand], live_returns=list(np.full(150, -0.005)),
                     registry_path=reg, adaptive_path=ada, gate=gate)
    assert report["n_promoted"] == 1
    data = json.loads(reg.read_text())
    assert data["variants"]["mut_x"]["status"] == "active-paper"
    # active-paper writes the winning config to the MachineSignal-polled adaptive path.
    ada_payload = json.loads(ada.read_text())
    assert ada_payload["apply_scope"] == "paper"
    assert ada_payload["source"] == "promotion_loop"


def test_auto_demote_flips_back_to_shadow(monkeypatch, tmp_path):
    _set_mode(monkeypatch, "PAPER")
    reg = tmp_path / "active_strategies.json"
    ada = tmp_path / "adaptive.json"
    gate = _FakeGate(GateVerdict.PROMOTE)
    cand = _candidate(rets=list(np.full(150, 0.012)))
    pl.tick([cand], live_returns=list(np.full(150, -0.005)),
            registry_path=reg, adaptive_path=ada, gate=gate)
    out = pl.auto_demote("mut_x", path=reg)
    assert out["demoted"] is True
    data = json.loads(reg.read_text())
    assert data["variants"]["mut_x"]["status"] == "shadow"
    assert data["variants"]["mut_x"]["demotions"] == 1


def test_should_demote_rule():
    assert pl.should_demote([0.01] * 5, -0.001, min_obs=20) is False   # too few obs
    assert pl.should_demote([-0.02] * 30, -0.001, min_obs=20) is True  # below shadow CI
    assert pl.should_demote([0.01] * 30, -0.001, min_obs=20) is False  # healthy
