"""One-shot splitter: dashboard.py -> dashboard/ package (Phase D2)."""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "dashboard.py"
PKG = ROOT / "dashboard"
lines = SRC.read_text(encoding="utf-8").splitlines(keepends=True)


def slice_lines(start: int, end: int) -> str:
    """Extract 1-indexed inclusive line range."""
    return "".join(lines[start - 1 : end])


STATE_HEADER = '''"""Module-global constants and singletons for the dashboard package."""
from __future__ import annotations

import json
import time
from pathlib import Path

# Project root (parent of this package) — replaces Path(__file__).parent in the monolith.
PROJECT_ROOT = Path(__file__).resolve().parent.parent

'''

TERM_HEADER = '''"""Terminal ANSI helpers for the dashboard package."""
from __future__ import annotations

import os
import re as _re
import sys
import time

from dashboard.state import (
    BOLD,
    CYAN,
    DIM,
    GREEN,
    ORANGE,
    RED,
    RESET,
    SILVER,
    WHITE,
    YELLOW,
    _ASSET_ICON,
    _COMMODITY_BASES,
    _START_TIME,
    _STOCK_BASES,
)

'''

HEALTH_HEADER = '''"""Local health/readiness helpers for the dashboard package."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from dashboard.state import (
    PROJECT_ROOT,
    _HEARTBEAT_FUTURE_TOLERANCE_SECONDS,
    _HEARTBEAT_STALE_SECONDS,
)

'''

BALANCES_HEADER = '''"""Exchange balance extractors for the dashboard package."""
from __future__ import annotations

'''

FETCHER_HEADER = '''"""Live exchange data fetcher for the dashboard package."""
from __future__ import annotations

import json
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from loguru import logger

from dashboard.balances import extract_all_coins, extract_usdt
from dashboard.health import _load_core_module
from dashboard.state import PROJECT_ROOT, _UNIFIED_EXCHANGES

'''

LOADERS_HEADER = '''"""JSON / warehouse data loaders for the dashboard package."""
from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path

from loguru import logger

from dashboard.state import (
    BOLD,
    DIM,
    GOLD,
    GREEN,
    RED,
    YELLOW,
    _file_cache,
)

'''

STATS_HEADER = '''"""Trade statistics helpers for the dashboard package."""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone

from dashboard.state import DIM, GREEN, RED
from dashboard.fetcher import LiveFetcher

'''

RENDER_HEADER = '''"""Dashboard render loop (unsplit monolith body)."""
from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dashboard.fetcher import LiveFetcher
from dashboard.loaders import (
    _hour_class,
    load_auto_mut,
    load_block_reasons,
    load_news,
    load_risk_state,
    load_warehouse_stats,
)
from dashboard.state import (
    BLUE,
    BOLD,
    CYAN,
    DASH_WIDTH,
    DIM,
    EX_COLOUR,
    GOLD,
    GREEN,
    ORANGE,
    PURPLE,
    RED,
    REFRESH_SECONDS,
    WHITE,
    YELLOW,
    _BG_LAST_ERR,
)
from dashboard.stats import (
    calc_daily_pnl,
    calc_exchange_stats,
    calc_stats,
    calc_strategy_stats,
    calc_unrealized,
    calc_weekly_stats,
    sparkline,
    _filter_real_trades,
    _whole_pnl,
)
from dashboard.term import (
    _asset_tag,
    _uptime_str,
    col,
    fg_str,
    pnl_str,
    pnl_str_short,
    vljust,
    vrjust,
    wr_col,
)

'''

APP_HEADER = '''"""Dashboard CLI entry point and background fetch thread."""
from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
from pathlib import Path

try:
    import shutil
except ImportError:
    shutil = None  # pragma: no cover

from loguru import logger

from dashboard.fetcher import LiveFetcher
from dashboard.health import build_health_status_payload
from dashboard.loaders import load_mode, load_positions
from dashboard.render import render
from dashboard.state import DASH_WIDTH, REFRESH_SECONDS, _BG_LAST_ERR
from dashboard.term import clr, enable_ansi

'''

INIT_BODY = '''"""Trading bot terminal dashboard package."""
from __future__ import annotations

from dashboard.app import background_fetch, main, parse_args
from dashboard.balances import extract_all_coins, extract_usdt
from dashboard.fetcher import LiveFetcher
from dashboard.health import build_health_status_payload
from dashboard.loaders import (
    load_auto_mut,
    load_block_reasons,
    load_mode,
    load_news,
    load_positions,
    load_post_mortem,
    load_risk_state,
    load_warehouse_stats,
)
from dashboard.render import render
from dashboard.stats import (
    calc_daily_pnl,
    calc_exchange_stats,
    calc_hourly_heatmap,
    calc_stats,
    calc_strategy_stats,
    calc_unrealized,
    calc_weekly_stats,
    sparkline,
    _filter_bot_trades,
    _filter_real_trades,
    _is_bot_trade,
    _is_real_trade,
    _whole_pnl,
)
from dashboard.term import (
    clr,
    col,
    enable_ansi,
    fg_str,
    pnl_str,
    pnl_str_short,
    vljust,
    vlen,
    vrjust,
    wr_col,
)

__all__ = [
    "LiveFetcher",
    "background_fetch",
    "build_health_status_payload",
    "calc_daily_pnl",
    "calc_exchange_stats",
    "calc_hourly_heatmap",
    "calc_stats",
    "calc_strategy_stats",
    "calc_unrealized",
    "calc_weekly_stats",
    "clr",
    "col",
    "enable_ansi",
    "extract_all_coins",
    "extract_usdt",
    "fg_str",
    "load_auto_mut",
    "load_block_reasons",
    "load_mode",
    "load_news",
    "load_positions",
    "load_post_mortem",
    "load_risk_state",
    "load_warehouse_stats",
    "main",
    "parse_args",
    "pnl_str",
    "pnl_str_short",
    "render",
    "sparkline",
    "vljust",
    "vlen",
    "vrjust",
    "wr_col",
    "_filter_bot_trades",
    "_filter_real_trades",
    "_is_bot_trade",
    "_is_real_trade",
    "_whole_pnl",
]

'''

MAIN_BODY = '''"""Allow ``python -m dashboard``."""
from dashboard.app import main

if __name__ == "__main__":
    main()
'''

modules = {
    "state.py": STATE_HEADER + slice_lines(57, 104) + "\n" + slice_lines(312, 334),
    "term.py": TERM_HEADER + slice_lines(218, 305),
    "health.py": HEALTH_HEADER + slice_lines(46, 55) + "\n" + slice_lines(107, 215),
    "balances.py": BALANCES_HEADER + slice_lines(341, 523),
    "fetcher.py": FETCHER_HEADER + slice_lines(530, 936),
    "loaders.py": LOADERS_HEADER + slice_lines(943, 1363),
    "stats.py": STATS_HEADER + slice_lines(1370, 1814),
    "render.py": RENDER_HEADER + slice_lines(1821, 3699),
    "app.py": APP_HEADER + slice_lines(3706, 3836),
    "__init__.py": INIT_BODY,
    "__main__.py": MAIN_BODY,
}

# Fix health.py paths to use PROJECT_ROOT (package lives under dashboard/)
health = modules["health.py"]
health = health.replace(
    "dashboard_root = Path(__file__).resolve().parent",
    "dashboard_root = PROJECT_ROOT",
)
health = health.replace(
    'p = Path(__file__).parent / "core" / "{}.py".format(name)',
    'p = PROJECT_ROOT / "core" / "{}.py".format(name)',
)
modules["health.py"] = health

# Fix fetcher _init_exchanges path insert
fetcher = modules["fetcher.py"]
fetcher = fetcher.replace(
    'sys.path.insert(0, str(Path(__file__).parent))',
    "sys.path.insert(0, str(PROJECT_ROOT))",
)
modules["fetcher.py"] = fetcher

# Fix loaders LiveFetcher forward ref - add import at top after header
loaders = modules["loaders.py"]
loaders = loaders.replace(
    'from dashboard.state import (',
    "from dashboard.fetcher import LiveFetcher\n\nfrom dashboard.state import (",
)
modules["loaders.py"] = loaders

PKG.mkdir(exist_ok=True)
for name, body in modules.items():
    (PKG / name).write_text(body, encoding="utf-8")
    print(f"wrote dashboard/{name} ({len(body.splitlines())} lines)")

print("done — remove dashboard.py manually after smoke tests")
