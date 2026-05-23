"""Cross-exchange same-symbol guard (2026-05-24).

Spec: bot must not open the same base asset on multiple exchanges in a
single 5-min portfolio cycle. The May 13 cascade was three concurrent
BNB opens on Binance / Bybit / Bitget that hit SL together, losing
~3× the per-position SL the risk parameters intended.

The full _execute_open path is heavyweight (exchanges, balances,
risk_manager, etc.). These tests exercise a self-contained predicate
replica + a structural pin that the production code uses the
all-exchanges scope.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path


# Self-contained predicate replica ─────────────────────────────────────


@dataclass
class _StubPos:
    symbol: str
    exchange: str


def _same_base_asset_open(symbol: str, open_positions: list[_StubPos]) -> bool:
    """Replica of the production guard at `bot_engine._execute_open`.

    Returns True when ANY open position on ANY exchange shares the
    base asset with `symbol`. Production must call
    `self.tracker.get_open()` with no `exchange` filter to feed this.
    """
    base = symbol.split("/")[0]
    return any(p.symbol.split("/")[0] == base for p in open_positions)


# Behavior tests ───────────────────────────────────────────────────────


def test_blocks_same_base_asset_on_different_exchange():
    """The May-13 cascade scenario: BTC open on Binance, the same
    portfolio cycle tries BTC on Bybit → must block."""
    open_positions = [_StubPos("BTC/USDT:USDT", "binance")]
    assert _same_base_asset_open("BTC/USDT:USDT", open_positions) is True


def test_blocks_same_base_asset_three_way():
    """Three-way cross-exchange dup is the exact May-13 BNB pattern."""
    open_positions = [
        _StubPos("BNB/USDT:USDT", "binance"),
        _StubPos("BNB/USDT:USDT", "bybit"),
    ]
    assert _same_base_asset_open("BNB/USDT:USDT", open_positions) is True


def test_allows_different_base_assets():
    """Different base assets across exchanges must not collide."""
    open_positions = [
        _StubPos("BTC/USDT:USDT", "binance"),
        _StubPos("ETH/USDT:USDT", "bybit"),
    ]
    assert _same_base_asset_open("SOL/USDT:USDT", open_positions) is False


def test_allows_when_no_open_positions():
    assert _same_base_asset_open("BTC/USDT:USDT", []) is False


# Structural pin ────────────────────────────────────────────────────────


def test_execute_open_scopes_dedup_across_all_exchanges():
    """Pin the contract so a future edit to bot_engine._execute_open
    doesn't reintroduce the single-exchange scope. The dedup query
    MUST be `self.tracker.get_open()` (no exchange filter), not
    `self.tracker.get_open(exchange=...)`.

    Memory: project_may13_cascade_fixes_2026_05_13.md.
    """
    src = (Path(__file__).resolve().parents[1]
           / "core" / "bot_engine.py").read_text(encoding="utf-8")
    # Locate the per-base-asset dedup block — match the comment + check.
    m = re.search(
        r"No duplicate base asset[\s\S]{0,500}?all_open\s*=\s*self\.tracker\.get_open\s*\(([^)]*)\)",
        src,
    )
    assert m is not None, (
        "Cross-exchange dedup block not found in bot_engine._execute_open — "
        "structural test cannot verify; update this test if the block moved."
    )
    inside = m.group(1).strip()
    assert "exchange" not in inside, (
        f"bot_engine._execute_open scopes the per-base-asset dedup with "
        f"`get_open({inside})` — must be `get_open()` (no arg) so the check "
        f"covers ALL exchanges. The May-13 cascade reproduces if this "
        f"filter is reinstated."
    )
