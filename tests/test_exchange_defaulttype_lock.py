"""Regression: every defaultType switch on an exchange client must hold the
per-instance RLock.

Audit 2026-06-04 (#3): each exchange client flips a SHARED
self.exchange.options["defaultType"] between "spot" and the futures type
("future"/"linear"/"swap") around each ccxt call, then resets to "spot". The bot
hits one client from several threads at once — the 10s SL/TP daemon, the 5-min
portfolio cycle, and the MCP-brain ThreadPoolExecutor that fans fetch_ohlcv /
fetch_ticker across spot AND futures simultaneously. Without serialization,
thread A sets the futures type then thread B sets "spot" before A's ccxt call
fires, so A's request goes to the wrong market (wrong candles/prices/balances/
order routing). This corrupts the very indicator data the research depends on,
and it is LIVE in PAPER (reads run in PAPER).

BinanceClient has guarded this with a reentrant self._defaultType_lock since
2026-05-24. This test pins the invariant for ALL THREE clients so a future
fetch/order method that switches defaultType cannot ship unguarded.

The check is AST-based, not a string grep: it finds every CALL to
self.switch_to_futures() / self.switch_to_spot() and asserts each one's line
falls inside the line-span of a `with self._defaultType_lock:` block. (The
`def switch_to_futures`/`def switch_to_spot` primitives assign the option
directly and contain no such call, so they are not flagged.)
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

CLIENTS = {
    "binance": "exchanges/binance_client.py",
    "bybit": "exchanges/bybit_client.py",
    "bitget": "exchanges/bitget_client.py",
}


def _is_switch_call(node: ast.AST) -> bool:
    """True for `self.switch_to_futures(...)` / `self.switch_to_spot(...)`."""
    if not isinstance(node, ast.Call):
        return False
    fn = node.func
    return (
        isinstance(fn, ast.Attribute)
        and fn.attr in ("switch_to_futures", "switch_to_spot")
        and isinstance(fn.value, ast.Name)
        and fn.value.id == "self"
    )


def _is_lock_with(node: ast.AST) -> bool:
    """True for `with self._defaultType_lock:` (any item matches)."""
    if not isinstance(node, (ast.With, ast.AsyncWith)):
        return False
    for item in node.items:
        ctx = item.context_expr
        if (
            isinstance(ctx, ast.Attribute)
            and ctx.attr == "_defaultType_lock"
            and isinstance(ctx.value, ast.Name)
            and ctx.value.id == "self"
        ):
            return True
    return False


@pytest.mark.parametrize("name,relpath", CLIENTS.items())
def test_defaulttype_lock_defined(name, relpath):
    src = Path(relpath).read_text(encoding="utf-8")
    assert "import threading" in src, f"{name}: threading not imported"
    assert "self._defaultType_lock = threading.RLock()" in src, (
        f"{name}: the per-instance reentrant defaultType lock is not created")


@pytest.mark.parametrize("name,relpath", CLIENTS.items())
def test_every_switch_call_is_lock_guarded(name, relpath):
    tree = ast.parse(Path(relpath).read_text(encoding="utf-8"))

    lock_spans = [
        (n.lineno, n.end_lineno)
        for n in ast.walk(tree)
        if _is_lock_with(n)
    ]
    assert lock_spans, f"{name}: no `with self._defaultType_lock:` region found"

    switch_lines = sorted(
        n.lineno for n in ast.walk(tree) if _is_switch_call(n)
    )
    assert switch_lines, f"{name}: expected switch_to_* calls, found none"

    unguarded = [
        ln for ln in switch_lines
        if not any(lo <= ln <= hi for lo, hi in lock_spans)
    ]
    assert not unguarded, (
        f"{name}: switch_to_* call(s) at line(s) {unguarded} are NOT inside a "
        f"`with self._defaultType_lock:` block — a concurrent thread can stomp "
        f"defaultType between the switch and the ccxt call, routing to the "
        f"wrong market. Wrap the region in the lock (see BinanceClient).")
