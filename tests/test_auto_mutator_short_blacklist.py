"""Test: side-aware short blacklist sweep in core.auto_mutator.

Builds a synthetic post_mortem.json with concentrated short losses on a
single symbol and asserts the new SHORT-only blacklist key fires while
the symmetric symbol blacklist does NOT.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

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


def test_short_only_concentration_triggers_short_blacklist(tmp_path):
    pm = {"analyses": _losses("APT/USDT:USDT", "sell", 4)}
    (tmp_path / "post_mortem.json").write_text(json.dumps(pm))

    mut = am.AutoMutator()
    mut.refresh(force=True)

    short_bl = mut.get_short_blacklist()
    sym_bl = mut.get_effective_blacklist()

    assert "APT/USDT:USDT" in short_bl
    # Crucial: SHORT prefix must NOT leak into the symmetric set.
    assert "SHORT:APT/USDT:USDT" not in sym_bl


def test_mixed_long_short_loss_does_not_fire_short_only(tmp_path):
    """A symbol that loses on both sides should NOT get the short-only ban."""
    pm = {"analyses":
          _losses("XRP/USDT:USDT", "sell", 2)
          + _losses("XRP/USDT:USDT", "buy",  2)}
    (tmp_path / "post_mortem.json").write_text(json.dumps(pm))

    mut = am.AutoMutator()
    mut.refresh(force=True)

    # Only 2 short losses → below SHORT_SYMBOL_LOSS_BLACKLIST=3 threshold.
    assert "XRP/USDT:USDT" not in mut.get_short_blacklist()


def test_short_blacklist_partitioned_from_symmetric(tmp_path):
    """get_effective_blacklist() must NOT include SHORT: prefixed entries."""
    pm = {"analyses": _losses("ETH/USDT:USDT", "sell", 3)}
    (tmp_path / "post_mortem.json").write_text(json.dumps(pm))

    mut = am.AutoMutator()
    mut.refresh(force=True)

    sym_bl = mut.get_effective_blacklist()
    short_bl = mut.get_short_blacklist()

    assert "ETH/USDT:USDT" in short_bl
    # 3 losses < 4 (SYMBOL_LOSS_BLACKLIST) → symmetric ban does NOT fire.
    assert "ETH/USDT:USDT" not in sym_bl
