"""Tests for the 2026-07-05 pre-registered F1 carry PAPER-universe expansion.

Pins: (a) the frozen 15-symbol set exactly; (b) the Rev-5 latch still rejects
the expanded set without the explicit opt-in; (c) the opt-in path accepts it;
(d) the scheduled-task entry script wires the expanded set by default and the
F1_UNIVERSE=legacy env escape reverts to BTC/ETH; (e) the pre-registration
document exists (the expansion is invalid without it).
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

FROZEN_15 = (
    "BTC/USDT", "ETH/USDT", "BNB/USDT", "SOL/USDT", "LINK/USDT",
    "TRX/USDT", "SUI/USDT", "ATOM/USDT", "DOGE/USDT", "ALGO/USDT",
    "ZEC/USDT", "ADA/USDT", "XRP/USDT", "LTC/USDT", "GRT/USDT",
)


def _load_runner_script():
    spec = importlib.util.spec_from_file_location(
        "run_f1_carry_paper_under_test", ROOT / "scripts" / "run_f1_carry_paper.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_expanded_universe_is_the_frozen_preregistered_set():
    from research.funding_carry_lab import F1_EXPANDED_UNIVERSE_2026_07_05

    assert F1_EXPANDED_UNIVERSE_2026_07_05 == FROZEN_15
    assert len(set(F1_EXPANDED_UNIVERSE_2026_07_05)) == 15


def test_rev5_latch_still_rejects_expanded_set_without_optin():
    from research.funding_carry_lab import (
        F1_EXPANDED_UNIVERSE_2026_07_05,
        build_f1_spec,
    )

    with pytest.raises(ValueError, match="allow_extended_universe"):
        build_f1_spec(symbols=F1_EXPANDED_UNIVERSE_2026_07_05)


def test_optin_accepts_expanded_set():
    from research.funding_carry_lab import (
        F1_EXPANDED_UNIVERSE_2026_07_05,
        build_f1_spec,
    )

    spec = build_f1_spec(
        symbols=F1_EXPANDED_UNIVERSE_2026_07_05, allow_extended_universe=True
    )
    assert tuple(spec.symbols) == FROZEN_15


def test_runner_script_defaults_to_expanded_universe(monkeypatch):
    monkeypatch.delenv("F1_UNIVERSE", raising=False)
    mod = _load_runner_script()
    assert tuple(mod.SYMBOLS) == FROZEN_15


def test_runner_script_legacy_env_reverts_to_btc_eth(monkeypatch):
    monkeypatch.setenv("F1_UNIVERSE", "legacy")
    mod = _load_runner_script()
    assert tuple(mod.SYMBOLS) == ("BTC/USDT", "ETH/USDT")


def test_preregistration_document_exists_and_freezes_the_set():
    doc = ROOT / "research" / "prereg_carry_universe_expansion_2026_07_05.md"
    assert doc.exists(), "expansion without its pre-registration is invalid"
    text = doc.read_text(encoding="utf-8")
    for sym in FROZEN_15:
        assert sym in text
    assert "F1_UNIVERSE=legacy" in text  # rollback documented
