"""Test: auto_mutator blacklist and short-block mechanisms are disabled.

With halt/pause mechanisms removed (2026-05-27), the auto_mutator no
longer writes to the blacklist or short-block state. These tests verify
that concentrated losses do NOT trigger blacklist entries (confirming
the disabled state).
"""
from __future__ import annotations

import json
import time

import pytest

from core import auto_mutator as am


def _losses(symbol: str, side: str, n: int) -> list[dict]:
    return [
        {
            "timestamp": time.time() - i,
            "symbol": symbol,
            "side":   side,
            "verdict": "LOSS",
            "mistakes": [],
        }
        for i in range(n)
    ]


@pytest.fixture(autouse=True)
def _redirect_state_files(tmp_path, monkeypatch):
    monkeypatch.setattr(am, "POST_MORTEM_FILE", tmp_path / "post_mortem.json")
    monkeypatch.setattr(am, "MUTATIONS_FILE",   tmp_path / "auto_mutations.json")
    yield


def test_short_concentration_does_not_trigger_blacklist_when_disabled(tmp_path):
    """With blacklist mechanisms disabled, concentrated short losses
    do not populate the short blacklist."""
    pm = {"analyses": _losses("APT/USDT:USDT", "sell", 4)}
    (tmp_path / "post_mortem.json").write_text(json.dumps(pm))

    mut = am.AutoMutator()
    mut.refresh(force=True)

    short_bl = mut.get_short_blacklist()
    sym_bl = mut.get_effective_blacklist()

    assert "APT/USDT:USDT" not in short_bl, (
        "Short blacklist should be empty with mechanisms disabled")
    assert "SHORT:APT/USDT:USDT" not in sym_bl


def test_mixed_long_short_loss_does_not_fire_short_only(tmp_path):
    """A symbol that loses on both sides should NOT get any ban."""
    pm = {"analyses":
          _losses("XRP/USDT:USDT", "sell", 2)
          + _losses("XRP/USDT:USDT", "buy",  2)}
    (tmp_path / "post_mortem.json").write_text(json.dumps(pm))

    mut = am.AutoMutator()
    mut.refresh(force=True)

    assert "XRP/USDT:USDT" not in mut.get_short_blacklist()
    assert "XRP/USDT:USDT" not in mut.get_effective_blacklist()


def test_blacklists_empty_with_mechanisms_disabled(tmp_path):
    """Both blacklist sets should be empty regardless of loss data."""
    pm = {"analyses": _losses("ETH/USDT:USDT", "sell", 10)}
    (tmp_path / "post_mortem.json").write_text(json.dumps(pm))

    mut = am.AutoMutator()
    mut.refresh(force=True)

    sym_bl = mut.get_effective_blacklist()
    short_bl = mut.get_short_blacklist()

    assert len(short_bl) == 0, (
        f"Short blacklist should be empty; got {short_bl}")
    assert len(sym_bl) == 0, (
        f"Effective blacklist should be empty; got {sym_bl}")
