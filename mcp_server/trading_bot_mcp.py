#!/usr/bin/env python3
"""MCP server exposing the trading bot's warehouse and decision data (read-only).

Lets Claude (and the user) interrogate the bot's own reasoning — past trades,
performance, candidate setups and skip reasons, and the shadow-vs-live
comparison — without touching the trading path. Every tool is read-only; the
warehouse is opened in SQLite read-only mode.

Run locally over stdio:
    pip install -r mcp_server/requirements.txt
    python mcp_server/trading_bot_mcp.py

Registered for Claude Code via .mcp.json at the repo root.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Optional

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, ConfigDict, Field

# Ensure the repo root is importable when launched as a bare script
# (python mcp_server/trading_bot_mcp.py), so `mcp_server` resolves as a package.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mcp_server import warehouse_reader as wr  # noqa: E402

mcp = FastMCP("trading_bot_mcp")

_RO = {
    "readOnlyHint": True,
    "destructiveHint": False,
    "idempotentHint": True,
    "openWorldHint": False,
}


def _dump(payload) -> str:
    return json.dumps(payload, indent=2, default=str)


def _guard(fn):
    """Run a reader call, converting WarehouseError into an actionable string."""
    try:
        return _dump(fn())
    except wr.WarehouseError as e:
        return f"Error: {e}"
    except Exception as e:  # defensive: never crash the server on a bad query
        return f"Error: unexpected {type(e).__name__}: {e}"


class LimitInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    limit: int = Field(default=20, ge=1, le=500, description="Max rows to return")
    symbol: Optional[str] = Field(
        default=None, description="Filter by symbol, e.g. 'BTC/USDT:USDT'"
    )


class SummaryInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    symbol: Optional[str] = Field(default=None, description="Filter by symbol")
    strategy: Optional[str] = Field(
        default=None, description="Filter by strategy_family, e.g. 'systematic_v3_1'"
    )


class CandidatesInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    limit: int = Field(default=20, ge=1, le=500, description="Max rows to return")
    decision: Optional[str] = Field(
        default=None, description="Filter by decision: ALLOW | SKIP | REVIEW | TAKEN"
    )


class QueryInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    sql: str = Field(..., min_length=6, description="A single read-only SELECT statement")
    limit: int = Field(default=100, ge=1, le=1000, description="Row cap applied to results")


@mcp.tool(name="trading_bot_list_tables", annotations={"title": "List warehouse tables", **_RO})
async def trading_bot_list_tables() -> str:
    """List every warehouse table with its row count and column names.

    Returns: JSON list of {"table": str, "rows": int, "columns": [str]}.
    Use this first to discover what data exists before querying.
    """
    return _guard(wr.list_tables)


@mcp.tool(name="trading_bot_recent_trades", annotations={"title": "Recent closed trades", **_RO})
async def trading_bot_recent_trades(params: LimitInput) -> str:
    """Return the most recent CLOSED trades (newest first), optionally by symbol.

    Returns JSON rows with: symbol, exchange, side, strategy_family, entry_px,
    exit_px, realized_pnl, r_multiple, leverage, hold_sec, exit_reason,
    ts_entry, ts_exit.
    """
    return _guard(lambda: wr.recent_trades(limit=params.limit, symbol=params.symbol))


@mcp.tool(
    name="trading_bot_performance_summary", annotations={"title": "Performance summary", **_RO}
)
async def trading_bot_performance_summary(params: SummaryInput) -> str:
    """Aggregate after-cost performance over CLOSED trades (optionally filtered).

    Returns JSON: trades, wins, losses, win_rate, total_realized_pnl,
    avg_pnl_per_trade, profit_factor, gross_win, gross_loss. Use to judge
    whether the live path (or a symbol/strategy slice) has edge.
    """
    return _guard(lambda: wr.performance_summary(symbol=params.symbol, strategy=params.strategy))


@mcp.tool(
    name="trading_bot_recent_candidates", annotations={"title": "Recent candidate setups", **_RO}
)
async def trading_bot_recent_candidates(params: CandidatesInput) -> str:
    """Return recent candidate setups the engine evaluated (newest first).

    SKIP rows carry skip_reason so you can see why setups were rejected. Filter
    with decision = ALLOW | SKIP | REVIEW | TAKEN. Returns JSON rows with: ts,
    exchange, symbol, side, strategy_family, confidence, decision, skip_reason,
    entry_px, leverage.
    """
    return _guard(lambda: wr.recent_candidates(limit=params.limit, decision=params.decision))


@mcp.tool(
    name="trading_bot_shadow_vs_live", annotations={"title": "Shadow vs live comparison", **_RO}
)
async def trading_bot_shadow_vs_live() -> str:
    """Compare the shadow agent ensemble's simulated PnL against live trades.

    Returns JSON {shadow: {...}, live: {...}, note}. The shadow ensemble is
    log-only and may be promoted to live only after beating live on the honest
    promotion gate.
    """
    return _guard(wr.shadow_vs_live)


@mcp.tool(name="trading_bot_query", annotations={"title": "Read-only SQL query", **_RO})
async def trading_bot_query(params: QueryInput) -> str:
    """Run a single read-only SELECT against the warehouse and return rows.

    Only one SELECT (or WITH ... SELECT) statement is permitted; any DDL/DML or
    multiple statements is rejected. Results are capped by `limit`. Use
    trading_bot_list_tables first to learn the schema.
    """
    return _guard(lambda: wr.run_select(params.sql, limit=params.limit))


if __name__ == "__main__":
    mcp.run()
