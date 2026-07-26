"""Smart-money hard entry gate (2026-07-24 Approach 1)."""
from __future__ import annotations

import config
from core.bot_engine import smart_money_entry_rejection


def test_disabled_always_allows():
    assert (
        smart_money_entry_rejection(
            "buy",
            {"smart_money_inflow": False, "stale": False},
            enabled=False,
        )
        == ""
    )


def test_buy_requires_inflow():
    assert (
        smart_money_entry_rejection(
            "buy",
            {"smart_money_inflow": False, "stale": False},
            enabled=True,
        )
        == "smart_money_required_inflow"
    )
    assert (
        smart_money_entry_rejection(
            "buy",
            {"smart_money_inflow": True, "stale": False},
            enabled=True,
        )
        == ""
    )


def test_sell_blocked_while_inflow():
    assert (
        smart_money_entry_rejection(
            "sell",
            {"smart_money_inflow": True, "stale": False},
            enabled=True,
        )
        == "smart_money_block_short_while_inflow"
    )
    assert (
        smart_money_entry_rejection(
            "sell",
            {"smart_money_inflow": False, "stale": False},
            enabled=True,
        )
        == ""
    )


def test_stale_fail_open_vs_closed():
    stale = {"smart_money_inflow": True, "stale": True}
    assert smart_money_entry_rejection("buy", stale, enabled=True, fail_open_stale=True) == ""
    assert (
        smart_money_entry_rejection("buy", stale, enabled=True, fail_open_stale=False)
        == "smart_money_feed_stale"
    )
    assert smart_money_entry_rejection("buy", None, enabled=True, fail_open_stale=True) == ""


def test_gate_config_profile_shape():
    assert "enabled" in config.SMART_MONEY_ENTRY_GATE
    assert "fail_open_stale" in config.SMART_MONEY_ENTRY_GATE
    assert "requested_enabled" in config.SMART_MONEY_ENTRY_GATE
