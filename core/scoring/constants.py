"""Shared paths and timing constants for the scoring package."""
from pathlib import Path

DECISION_LOG = Path("data/mcp_decisions.jsonl")
ACCURACY_FILE = Path("data/mcp_accuracy.json")
STATE_FILE = Path("data/mcp_state.json")
FETCH_TIMEOUT = 10

# 2026-04-11: dropped ENTRY_COOLDOWN 900→300 to match config.py scan cadence.
ENTRY_COOLDOWN = 50
POSITION_COOLDOWN = 90
DATA_CACHE_TTL = 120
