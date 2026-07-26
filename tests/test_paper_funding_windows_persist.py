"""A5 (2026-07-16): settled paper-funding windows must survive a restart.

accrue_paper_funding guards double-charging with a per-venue settled-window
dict — but it lived only in memory, so restarting the bot inside a funding
hour forgot the settlement and charged paper funding a SECOND time for the
same window (audit wf_7e6c63b8-168, LOW/paper-accounting). The dict is now
persisted best-effort to data/paper_funding_windows.json (flat
{venue: int window_key}) and restored at OrderManager construction.
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest


@pytest.fixture(autouse=True)
def _tmp_cwd(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    Path("data").mkdir(exist_ok=True)
    yield


def _om():
    from core.order_manager import OrderManager
    return OrderManager(tracker=MagicMock(), risk=MagicMock(), notifier=MagicMock())


def test_settled_windows_survive_restart():
    om1 = _om()
    om1._last_funding_hour["Binance"] = 2100716
    om1._save_funding_windows()
    om2 = _om()  # "restart"
    assert om2._last_funding_hour == {"Binance": 2100716}


def test_absent_file_yields_empty_dict():
    assert _om()._last_funding_hour == {}


def test_corrupt_file_never_raises():
    Path("data/paper_funding_windows.json").write_text("{not json", encoding="utf-8")
    assert _om()._last_funding_hour == {}


def test_non_dict_payload_ignored():
    Path("data/paper_funding_windows.json").write_text(
        json.dumps(["Binance"]), encoding="utf-8")
    assert _om()._last_funding_hour == {}


def test_accrue_persists_per_position_settlement_cursor():
    src = Path(__file__).resolve().parents[1].joinpath(
        "core", "order_manager.py").read_text(encoding="utf-8")
    body = src[src.index("def accrue_paper_funding"):]
    i_mark = body.find("pos.last_funding_ts = settlement.settlement_ts")
    i_save = body.find('getattr(self.tracker, "_save", None)')
    assert 0 < i_mark < i_save, (
        "accrue_paper_funding must persist each position's realized-settlement "
        "cursor after accounting, or a restart can double-charge it"
    )
