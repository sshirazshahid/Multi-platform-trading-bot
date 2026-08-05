"""Portfolio cycle and trading mode."""
import os

TRADING_MODE = os.getenv("TRADING_MODE", "usdt_only")
PORTFOLIO_MIN_VALUE_USD = float(os.getenv("PORTFOLIO_MIN_VALUE_USD", "2.0"))
PORTFOLIO_RESCAN_MINUTES = int(os.getenv("PORTFOLIO_RESCAN_MINUTES", "60"))

# ==============================================================
# PORTFOLIO CYCLE — deterministic scorer is the only decision authority
# (LLM/sentiment removed from the trade path in De-Emotion overhaul 2026-08-04)
# ==============================================================
PORTFOLIO_CYCLE = {
    "enabled": True,
    "scan_interval_min": 1,  # 1 min — was 5 (2026-05-20). User directive:
    # "don't miss a single second to miss a profitable
    # trade". Pairs with parallel _fetch_exchange_indicators
    # so 60s cycle stays under wall-time budget. Engine sees
    # 60 chances/hour vs prior 12. ENTRY_COOLDOWN dropped
    # to 50s in mcp_brain.py to track.
    "position_monitor_sec": 30,  # Position monitor every 30s (consumed by bot_engine)
    "max_actions_per_cycle": 12,  # PAPER aggressive (2026-05-31): was 4 — allow opens across all 3 venues per cycle. Revert to 4 before live.
    "max_per_exchange": 20,  # PAPER 2026-05-30: was 2 — allow ~10-20 positions/exchange for data-gathering. Revert to 2 before live.
    "learn_interval_min": 60,  # Learning engine interval in minutes (bot_engine)
}
# Back-compat alias for any lingering readers (remove after Wave-1 greps clean).
CLAUDE_PORTFOLIO = PORTFOLIO_CYCLE
