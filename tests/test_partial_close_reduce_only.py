"""Partial close must carry reduceOnly=True on futures (2026-05-24).

Bug: order_manager.partial_close_position issued a bare market order
without params. On Bybit/Bitget, if the position size raced (e.g.
exchange-side SL/TP fired between size read and this order), the
partial close became an opposite-direction entry instead of reducing
the position. Every other close path in this file already passes
reduceOnly — the partial path was the gap.

Verifies the contract via source-level structural pin since
partial_close_position runs against a real exchange client.
"""
from __future__ import annotations

import re
from pathlib import Path


def test_partial_close_passes_reduce_only_on_futures():
    src = (Path(__file__).resolve().parents[1]
           / "core" / "order_manager.py").read_text(encoding="utf-8")
    # Find the partial_close_position function body.
    m = re.search(
        r"def partial_close_position\(self[^)]*\)[^:]*:[\s\S]+?(?=\n    def |\Z)",
        src,
    )
    assert m is not None, "partial_close_position not found"
    body = m.group(0)
    # The function must reference reduceOnly somewhere in the live branch.
    assert "reduceOnly" in body, (
        "partial_close_position must pass reduceOnly=True for futures. "
        "Without it, a raced order on Bybit/Bitget can become an entry "
        "rather than a reduce, doubling exposure."
    )
    # And it must be conditional on futures (spot does not accept reduceOnly).
    assert re.search(
        r'market_type\s*==\s*"futures"[\s\S]{0,400}?reduceOnly',
        body,
    ), (
        "reduceOnly must be gated on market_type == 'futures' so it isn't "
        "passed for spot positions."
    )
