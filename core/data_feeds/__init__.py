"""
core/data_feeds/ — External data feed modules for MCP Brain enrichment.

Each feed module provides:
  - A class with `fetch(symbols)` returning normalized dicts
  - Independent cache TTL (feeds update at different cadences)
  - Fail-open design: API down = neutral signal, never blocks scoring

Orchestrated by core/data_coordinator.py which runs feeds on background
threads and serves a unified `get_market_context(symbol)` to mcp_brain.py.
"""
