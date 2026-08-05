"""Module-global constants and singletons for the dashboard package."""
from __future__ import annotations

import json
import time
from pathlib import Path

# Project root (parent of this package) — replaces Path(__file__).parent in the monolith.
PROJECT_ROOT = Path(__file__).resolve().parent.parent

# ── ANSI colours ──────────────────────────────────────────────────────
GREEN  = "\033[92m"
RED    = "\033[91m"
YELLOW = "\033[93m"
CYAN   = "\033[96m"
WHITE  = "\033[97m"
DIM    = "\033[2m"
RESET  = "\033[0m"
BOLD   = "\033[1m"
ORANGE = "\033[38;5;208m"
PURPLE = "\033[95m"
BLUE   = "\033[94m"
GOLD   = "\033[38;5;220m"
SILVER = "\033[38;5;250m"

# Defaults match TradingBot.bat (menu: "Dashboard 60s"); override via --refresh
REFRESH_SECONDS = 5    # default redraw cadence; CLI --refresh overrides
DASH_WIDTH = 80

# Background fetch thread: last error for footer (throttled display)
_BG_LAST_ERR = None

EX_COLOUR = {
    "binance": "\033[38;5;220m",
    "bybit":   "\033[38;5;214m",
    "bitget":  "\033[38;5;48m",
}

_UNIFIED_EXCHANGES = {"bybit"}

# Commodity base symbols for highlighting
_COMMODITY_BASES = {"XAU", "XAG", "WTI", "CL"}
_STOCK_BASES = {
    "AAPL", "TSLA", "GOOG", "GOOGL", "AMZN", "MSFT", "META", "NVDA",
    "NFLX", "AMD", "COIN", "MSTR", "GME",
}

_ASSET_ICON = {
    "XAU": GOLD + "Au" + RESET,
    "XAG": SILVER + "Ag" + RESET,
    "WTI": ORANGE + "Oil" + RESET,
    "CL":  ORANGE + "Oil" + RESET,
}

_START_TIME = time.time()

_HEARTBEAT_STALE_SECONDS = 10 * 60
_HEARTBEAT_FUTURE_TOLERANCE_SECONDS = 60

class FileCache:
    """Cache JSON file reads — only re-read when mtime changes."""

    def __init__(self):
        self._cache: dict[str, tuple] = {}   # path -> (mtime, data)

    def load(self, path_str: str):
        try:
            p = Path(path_str)
            if not p.exists():
                return None
            mt = p.stat().st_mtime
            cached = self._cache.get(path_str)
            if cached and cached[0] == mt:
                return cached[1]
            data = json.loads(p.read_text(encoding="utf-8"))
            self._cache[path_str] = (mt, data)
            return data
        except Exception:
            return None


_file_cache = FileCache()
