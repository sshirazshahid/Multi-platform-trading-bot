from __future__ import annotations

import asyncio
import json


def test_read_only_mcp_server_imports_and_exposes_expected_tools():
    from mcp_server import trading_bot_mcp as server

    tools = asyncio.run(server.mcp.list_tools())
    by_name = {tool.name: tool for tool in tools}

    assert set(by_name) == {
        "trading_bot_list_tables",
        "trading_bot_recent_trades",
        "trading_bot_performance_summary",
        "trading_bot_recent_candidates",
        "trading_bot_shadow_vs_live",
        "trading_bot_query",
        "trading_bot_recent_movers",
        "trading_bot_f1_edge_status",
        "trading_bot_open_funnel",
    }
    for tool in by_name.values():
        annotations = tool.annotations
        assert annotations is not None
        assert annotations.readOnlyHint is True
        assert annotations.destructiveHint is False
        assert annotations.idempotentHint is True
        assert annotations.openWorldHint is False


def test_mcp_example_pins_repository_environment_and_module_entrypoint():
    config = json.loads(open(".mcp.json.example", encoding="utf-8").read())
    entry = config["mcpServers"]["trading_bot"]

    assert entry["command"] == r"venv\Scripts\python.exe"
    assert entry["args"] == ["-m", "mcp_server.trading_bot_mcp"]

