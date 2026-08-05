#!/usr/bin/env python3
"""Add shared imports to core/engine mixin modules."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "core" / "engine"
IMPORT = "from core.engine.helpers import *  # noqa: F403\n\n"

for name in (
    "probes",
    "gate_health",
    "portfolio_state",
    "sizing_gates",
    "cycle",
    "entry_exec",
    "close_exec",
    "monitors",
    "imported_protect",
    "jobs",
    "lifecycle",
):
    path = ROOT / f"{name}.py"
    text = path.read_text(encoding="utf-8")
    if "from core.engine.helpers import" in text:
        continue
    end = text.find('"""', 3)
    end = text.find("\n", end) + 1
    path.write_text(text[:end] + "\n" + IMPORT + text[end:], encoding="utf-8")
    print("patched", name)

path = ROOT / "engine.py"
text = path.read_text(encoding="utf-8")
if "from core.engine.helpers import" not in text:
    insert = """from core.engine.helpers import (  # noqa: F401
    CLAUDE_PORTFOLIO,
    DRY_RUN,
    MCPBrain,
    SLTP_TRIGGER_MARK_PRICE,
    TRADING_MODE,
    _boot_profile_log_lines,
    _deployable_total,
    discover_all,
    logger,
)
from core.learning_engine import LearningEngine
from core.order_manager import OrderManager
from core.position_tracker import PositionTracker
from core.risk_manager import RiskManager
from exchanges import BinanceClient, BitgetClient, BybitClient
from utils import TelegramNotifier

"""
    idx = text.find("from core.engine.close_exec")
    path.write_text(text[:idx] + insert + text[idx:], encoding="utf-8")
    print("patched engine")
