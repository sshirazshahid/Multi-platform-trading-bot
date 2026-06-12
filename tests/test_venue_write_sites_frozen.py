"""Frozen inventory of venue WRITE call sites in the live tree (core/, exchanges/).

Why this exists (AI-regression pattern: PAPER/LIVE path mismatch):
every order-leak incident here started with a venue-write call site that was
added or reached WITHOUT a DRY_RUN gate, and each was caught in production
conditions first, then point-tested after the fact:
  - 2026-05-31 bot_engine._replace_exchange_sl fired real cancel+SL on PAPER
  - 2026-06-03 _execute_open auto-transfer missed by the 05-31 sweep
  - 2026-06-04 Bitget set_position_mode live POST at __init__
  - 2026-06-05 arbitrage_engine._execute live branch gated only by ctor flag

Those point guards live in test_paper_no_live_writes.py / test_arbitrage_dry_gate.py /
test_close_external_position_dry_gate.py. This test generalizes the CATEGORY:
it AST-scans core/ and exchanges/ for calls to ccxt write methods and fails the
moment a NEW call site appears (or one disappears) versus the audited baseline.

It does NOT verify gating by itself — it forces a human/AI audit step: when it
fails, (1) check the new site is DRY_RUN/mode-gated, (2) add a behavioral or
source-pin test for it alongside the existing leak tests, (3) update BASELINE.
Never "fix" a failure here by editing BASELINE alone.
"""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

WRITE_METHODS = {
    "create_order",
    "createOrder",
    "create_market_buy_order",
    "create_market_sell_order",
    "create_limit_buy_order",
    "create_limit_sell_order",
    "edit_order",
    "editOrder",
    "cancel_order",
    "cancelOrder",
    "cancel_all_orders",
    "cancelAllOrders",
    "set_leverage",
    "setLeverage",
    "set_position_mode",
    "setPositionMode",
    "set_margin_mode",
    "setMarginMode",
    "transfer",
    "withdraw",
}

# Audited 2026-06-12 (recursive scan; counts include DRY_RUN-gated branches and
# wrapper definitions — the freeze is over call SITES, not gating correctness).
BASELINE = {
    "core/arbitrage_engine.py::create_order": 3,
    "core/bot_engine.py::cancel_all_orders": 2,
    "core/bot_engine.py::create_order": 7,
    "core/bot_engine.py::set_leverage": 1,
    "core/bot_engine.py::transfer": 2,
    "core/capital_allocator.py::transfer": 2,
    "core/order_manager.py::cancel_all_orders": 3,
    "core/order_manager.py::create_order": 6,
    "core/order_manager.py::set_leverage": 1,
    "core/order_manager.py::transfer": 1,
    "core/smart_executor.py::cancel_order": 1,
    "core/smart_executor.py::create_order": 2,
    "exchanges/base.py::cancel_order": 3,
    "exchanges/base.py::create_order": 1,
    "exchanges/base.py::set_leverage": 2,
    "exchanges/binance_client.py::create_order": 2,
    "exchanges/binance_client.py::set_leverage": 1,
    "exchanges/binance_client.py::transfer": 1,
    "exchanges/bitget_client.py::create_order": 2,
    "exchanges/bitget_client.py::set_leverage": 2,
    "exchanges/bitget_client.py::set_position_mode": 1,
    "exchanges/bitget_client.py::transfer": 1,
    "exchanges/bybit_client.py::cancel_all_orders": 1,
    "exchanges/bybit_client.py::create_order": 3,
    "exchanges/bybit_client.py::set_leverage": 2,
    "exchanges/mexc_client.py::create_order": 1,
}


def _scan() -> dict:
    sites: dict = {}
    for d in ("core", "exchanges"):
        for p in sorted((ROOT / d).rglob("*.py")):
            tree = ast.parse(p.read_text(encoding="utf-8"))
            rel = p.relative_to(ROOT).as_posix()
            for n in ast.walk(tree):
                if (
                    isinstance(n, ast.Call)
                    and isinstance(n.func, ast.Attribute)
                    and n.func.attr in WRITE_METHODS
                ):
                    key = f"{rel}::{n.func.attr}"
                    sites[key] = sites.get(key, 0) + 1
    return sites


def test_no_withdraw_calls_ever():
    """The bot must never move funds OFF-exchange. Zero withdraw() call sites."""
    offenders = {k: v for k, v in _scan().items() if k.endswith("::withdraw")}
    assert not offenders, (
        f"withdraw() call site(s) found in the live tree: {offenders}. "
        "The bot has no business withdrawing funds — remove the call."
    )


def test_venue_write_sites_frozen():
    """Any change to the venue-write call-site inventory requires an explicit audit."""
    current = _scan()
    added = {k: v for k, v in current.items() if k not in BASELINE}
    removed = {k: v for k, v in BASELINE.items() if k not in current}
    changed = {
        k: (BASELINE[k], v) for k, v in current.items() if k in BASELINE and BASELINE[k] != v
    }
    assert not (added or removed or changed), (
        "Venue write call-site inventory changed.\n"
        f"  NEW sites (audit DRY_RUN gating + add a leak test): {added or '{}'}\n"
        f"  REMOVED sites: {removed or '{}'}\n"
        f"  COUNT changes (file::method: baseline->now): {changed or '{}'}\n"
        "Every PAPER->live order leak (2026-05-31, 06-03, 06-04, 06-05) entered "
        "through an unaudited write site. Verify the gate, add a test next to "
        "test_paper_no_live_writes.py, THEN update BASELINE here."
    )
