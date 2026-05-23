"""Bybit + Bitget set_leverage must walk a ladder on cap rejection (2026-05-24).

Bug: both BybitClient.set_leverage and BitgetClient.set_leverage
override BaseExchange.set_leverage entirely. The base class has a
ladder [75, 50, 40, 25, 20, 15, 10, 5, 3, 2, 1] that walks down on
cap rejection; the overrides did not, so a single rejection returned 0
which order_manager treats as a fatal abort. Dormant at 2× global
config but detonates the moment a tier requests higher than a
symbol's cap.

Verified via behavior + structural pin.
"""
from __future__ import annotations

import re
from pathlib import Path
from unittest.mock import MagicMock

import pytest


# Behavior tests ───────────────────────────────────────────────────────


@pytest.fixture
def bybit_client_with_mock_exchange(monkeypatch):
    """Build a BybitClient with a mocked ccxt exchange that rejects 10×
    but accepts 5×."""
    from exchanges import bybit_client as bc

    mock_ex = MagicMock()
    rejected_levs = []
    accepted_lev = {}

    def _set_lev(lev, sym, params=None):
        if lev > 5:
            rejected_levs.append(lev)
            raise Exception(f"[mock] leverage cap rejection at {lev}x")
        accepted_lev["val"] = lev

    mock_ex.set_leverage.side_effect = _set_lev

    client = bc.BybitClient.__new__(bc.BybitClient)
    client.exchange = mock_ex
    client._ok = lambda: True
    client.switch_to_futures = lambda: None
    client.switch_to_spot = lambda: None

    return client, rejected_levs, accepted_lev


def test_bybit_ladder_walks_down_on_cap_rejection(bybit_client_with_mock_exchange):
    client, rejected, accepted = bybit_client_with_mock_exchange
    result = client.set_leverage("BTC/USDT:USDT", 10)
    assert result == 5, (
        f"expected ladder to clamp to highest-acceptable (5), got {result}; "
        f"rejected attempts: {rejected}"
    )
    assert accepted["val"] == 5


def test_bybit_returns_zero_when_no_lev_works(monkeypatch):
    """If every ladder rung also fails, return 0 (correct abort signal)."""
    from exchanges import bybit_client as bc

    mock_ex = MagicMock()
    mock_ex.set_leverage.side_effect = Exception("permanent rejection")

    client = bc.BybitClient.__new__(bc.BybitClient)
    client.exchange = mock_ex
    client._ok = lambda: True
    client.switch_to_futures = lambda: None
    client.switch_to_spot = lambda: None

    assert client.set_leverage("BTC/USDT:USDT", 10) == 0


# Structural pin ───────────────────────────────────────────────────────


def _src(path: str) -> str:
    return (Path(__file__).resolve().parents[1] / path).read_text(encoding="utf-8")


def test_bybit_set_leverage_has_ladder():
    src = _src("exchanges/bybit_client.py")
    m = re.search(
        r"def set_leverage\(self[^)]*\)[\s\S]+?(?=\n    def |\Z)",
        src,
    )
    assert m is not None
    body = m.group(0)
    assert "ladder" in body and "for lev in ladder" in body, (
        "BybitClient.set_leverage must walk a ladder on cap rejection"
    )
    # And must preserve venue-specific params on each retry.
    assert "buyLeverage" in body and "sellLeverage" in body, (
        "Bybit ladder retries must include buyLeverage/sellLeverage params"
    )


def test_bitget_set_leverage_has_ladder():
    src = _src("exchanges/bitget_client.py")
    m = re.search(
        r"def set_leverage\(self[^)]*\)[\s\S]+?(?=\n    def |\Z)",
        src,
    )
    assert m is not None
    body = m.group(0)
    assert "ladder" in body and "for lev in ladder" in body, (
        "BitgetClient.set_leverage must walk a ladder on cap rejection"
    )
    # marginCoin must be preserved on each retry.
    assert body.count("marginCoin") >= 2, (
        "Bitget ladder retries must include marginCoin param (both initial "
        "and ladder paths)"
    )
