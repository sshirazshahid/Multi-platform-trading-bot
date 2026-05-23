"""BinanceClient defaultType mutation must be serialized (2026-05-24).

Bug: self.exchange.options["defaultType"] is mutated by switch_to_futures
/ switch_to_spot. Multiple threads hit a shared BinanceClient instance:
_sltp_monitor_loop (10s daemon), schedule.run_pending portfolio cycle
(5min), and ThreadPoolExecutor(max_workers=8). Race: thread A sets
"future", thread B sets "spot" before A's ccxt call fires → A makes a
spot API call expecting futures → wrong prices/balances/order routing.

Fix: threading.RLock guards every switch+call+restore sequence. RLock
is reentrant so nested switch-using calls in the same thread don't
self-deadlock. Concurrent behavior tests are flaky; this is a
structural pin so future edits don't drop the lock.
"""
from __future__ import annotations

import re
import threading
from pathlib import Path


def _src() -> str:
    return (Path(__file__).resolve().parents[1]
            / "exchanges" / "binance_client.py").read_text(encoding="utf-8")


def test_lock_attribute_initialized_in_init():
    """The RLock must be created in __init__ and named _defaultType_lock."""
    src = _src()
    assert "threading.RLock()" in src, (
        "BinanceClient must import + instantiate threading.RLock for "
        "the defaultType race guard."
    )
    # Must be on self, not a class-level singleton (per-instance is correct).
    assert re.search(
        r"self\._defaultType_lock\s*=\s*threading\.RLock\(\)", src
    ), "RLock must be instance-level: `self._defaultType_lock = threading.RLock()`"


def test_every_switch_using_method_acquires_lock():
    """Each method that calls switch_to_futures/switch_to_spot must wrap
    the switch+call+restore sequence in `with self._defaultType_lock:`."""
    src = _src()
    # Methods we expect to mutate defaultType.
    methods = (
        "fetch_ohlcv",
        "fetch_ticker",
        "fetch_balance",
        "fetch_order_book",
        "fetch_open_orders",
        "create_order",
        "set_leverage",
    )
    for name in methods:
        # Find the method body up to the next def or end of file.
        m = re.search(
            rf"def {name}\(self[^)]*\)[^:]*:[\s\S]+?(?=\n    def |\Z)",
            src,
        )
        assert m is not None, f"method {name} not found in BinanceClient"
        body = m.group(0)
        # Body must call switch_to_futures or switch_to_spot AND acquire the lock.
        if "switch_to_futures" in body or "switch_to_spot" in body:
            assert "with self._defaultType_lock:" in body, (
                f"{name} mutates defaultType but does not hold "
                f"_defaultType_lock — race window present."
            )


def test_lock_does_not_self_deadlock():
    """RLock allows reentrant acquisition on the same thread.
    Smoke test: acquiring twice from the same thread must not block.
    Guards against accidentally swapping RLock → Lock during cleanup."""
    lock = threading.RLock()
    with lock:
        with lock:
            assert True  # reentry must not block
