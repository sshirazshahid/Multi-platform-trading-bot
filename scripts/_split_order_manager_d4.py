#!/usr/bin/env python3
"""One-shot splitter for Phase D4 order_manager decomposition."""
from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "core" / "order_manager.py"
OUT = ROOT / "core" / "order_mgmt"

MIXIN_METHODS: dict[str, tuple[str, ...]] = {
    "state": (
        "set_exchanges",
        "_exchange_for",
        "_update_trade_extremes",
        "_maintenance_margin_rate",
        "_required_futures_mark",
        "_target_traded_through",
        "_close_take_profit",
        "_record_lifecycle",
        "flatten_all",
        "_load_order_mode_state",
        "_save_order_mode_state",
        "_load_funding_windows",
        "_save_funding_windows",
        "_load_sl_widened",
        "_save_sl_widened",
        "cleanup_sl_widened",
        "_load_close_fail_count",
        "_save_close_fail_count",
        "_position_lock",
        "_close_order_confirmed",
    ),
    "provenance": (
        "_generate_client_order_id",
        "_provenance_venue",
        "_append_order_intent",
        "_append_execution_event",
        "_fill_freshness_reason",
        "_check_price_band",
        "_verify_order_on_exchange",
    ),
    "funds": (
        "available_balance",
        "_extract_usdt",
        "auto_transfer_for_trade",
        "_interpret_execution_result",
        "_aggregate_execution_results",
    ),
    "entry": ("open_position",),
    "protection": (
        "_place_exchange_sl_tp",
        "_cap_stop_fill",
        "_replace_exchange_sl",
        "_replace_exchange_sl_impl",
        "_reconcile_missing_sl",
        "_reconcile_missing_tp",
        "_classify_resting_conditionals",
        "verify_exchange_sl_alive",
        "verify_exchange_tp_alive",
        "_position_flat_on_venue",
        "partial_close_position",
    ),
    "closing": (
        "close_position",
        "_close_position_impl",
        "calibrator",
        "_finalize_close",
        "_net_pnl_at_price",
        "_early_breakeven_move",
    ),
    "paper_funding": (
        "_paper_funding_mark_at",
        "_paper_funding_live_settlements",
        "accrue_paper_funding",
    ),
    "maker_first": (
        "_maker_base",
        "_pending_maker_count",
        "pending_maker_reservations",
        "_maker_fill_risk_rejection",
        "_stable_maker_resolution_id",
        "_maker_confidence",
        "_maker_provenance_intent",
        "_record_maker_resolution_decision",
        "_register_pending_maker",
        "_record_maker_nonfill",
        "_maker_first_boot",
        "_persist_pending_maker",
        "_maker_wick_through",
        "_resolve_pending_maker_entries",
        "_finalize_maker_intent",
    ),
    "monitor": ("check_sl_tp",),
}

MODULE_HEADER = '''\
"""
core/order_mgmt/{stem}.py — OrderManager {mixin} mixin (Phase D4).
"""
'''

HELPERS_DOC = '''\
"""
core/order_mgmt/helpers.py — module-level helpers for OrderManager (Phase D4).
"""
'''

MANAGER_IMPORTS = '''\
import json
import threading
import time
from pathlib import Path

from loguru import logger

from config import DRY_RUN, RISK
from core.blacklist_manager import BlacklistManager
from core.compliance_logger import ComplianceLogger
from core.kelly_sizer import KellySizer
from core.order_mgmt.closing import _ClosingMixin
from core.order_mgmt.entry import _EntryMixin
from core.order_mgmt.funds import _FundsMixin
from core.order_mgmt.helpers import (
    CLOSE_FAIL_COUNT_PATH,
    ORDER_MODE_STATE_PATH,
    PAPER_FUNDING_WINDOWS_PATH,
    PENDING_MAKER_ENTRIES_PATH,
    SL_WIDENED_STATE_PATH,
)
from core.order_mgmt.maker_first import _MakerFirstMixin
from core.order_mgmt.monitor import _MonitorMixin
from core.order_mgmt.paper_funding import _PaperFundingMixin
from core.order_mgmt.protection import _ProtectionMixin
from core.order_mgmt.provenance import _ProvenanceMixin
from core.order_mgmt.state import _StateMixin
from core.position_tracker import PositionTracker
from core.post_mortem import PostMortem
from core.risk_manager import RiskManager
from core.sim_execution import SimExecutionModel
from core.smart_executor import SmartExecutor
from core.trailing_stop_manager import TrailingStopManager
from core.virtual_wallet import VirtualWallet
from utils.notifier import TelegramNotifier
'''

MIXIN_IMPORTS: dict[str, str] = {
    "state": '''\
import json
import threading
import time

from loguru import logger

from core.order_mgmt.helpers import (
    CLOSE_FAIL_COUNT_PATH,
    ORDER_MODE_STATE_PATH,
    PAPER_FUNDING_WINDOWS_PATH,
    SL_WIDENED_STATE_PATH,
    _mark_from_ticker,
)
from core.position_tracker import Position
from exchanges.base import BaseExchange
''',
    "provenance": '''\
import json
import time
import uuid

from loguru import logger

from utils.http_redaction import redact_http_debug as _redact_http_debug
''',
    "funds": '''\
from loguru import logger

from exchanges.base import BaseExchange
''',
    "entry": '''\
import time
import uuid

from loguru import logger

from config import DRY_RUN, RISK
from core.order_mgmt.helpers import (
    _is_permission_error,
    _is_position_mode_error,
    _is_skip_pair_error,
    _maker_first_cfg,
    _mcp_confidence_size_multiplier,
    _mid_from_ticker,
    build_sl_tp_order_params,
)
from core.position_tracker import Position
from exchanges.base import BaseExchange
from utils.http_redaction import redact_http_debug as _redact_http_debug
''',
    "protection": '''\
import time

from loguru import logger

from core.order_mgmt.helpers import build_sl_tp_order_params
from core.position_tracker import Position
from exchanges.base import BaseExchange
''',
    "closing": '''\
import time

from loguru import logger

from config import RISK
from core.order_mgmt.helpers import _is_accuracy_band_position
from core.position_tracker import Position
from exchanges.base import BaseExchange
''',
    "paper_funding": '''\
import time

from loguru import logger

from core.position_tracker import Position
from exchanges.base import BaseExchange
''',
    "maker_first": '''\
import json
import threading
import time
import uuid

from loguru import logger

from core.order_mgmt.helpers import (
    PENDING_MAKER_ENTRIES_PATH,
    _MAKER_CHASE_GUARD_PCT,
    _maker_first_cfg,
    _mid_from_ticker,
)
from exchanges.base import BaseExchange
from utils.atomic_io import atomic_write_json
''',
    "monitor": '''\
import time

from loguru import logger

from config import RISK
from core.order_mgmt.helpers import (
    _accuracy_band_hold_active,
    _is_accuracy_band_position,
    _should_fire_partial_tp,
    _tier_geometry_hold_active,
    _try_soft_close,
)
from exchanges.base import BaseExchange
''',
}


def _extract_class_methods(source: str) -> tuple[str, dict[str, ast.FunctionDef], str, str]:
    tree = ast.parse(source)
    # 2026-08-22 (ruff F841): mod_pre/mod_post/in_class were assigned and never
    # read — leftovers from an earlier draft of this one-shot splitter. Removed
    # rather than silenced; nothing downstream referenced them.
    methods: dict[str, ast.FunctionDef] = {}
    init_src = ""
    class_end = len(source)

    lines = source.splitlines(keepends=True)

    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == "OrderManager":
            for item in node.body:
                if isinstance(item, ast.FunctionDef):
                    start = item.lineno - 1
                    end = item.end_lineno
                    chunk = "".join(lines[start:end])
                    methods[item.name] = chunk
                    if item.name == "__init__":
                        init_src = chunk
            class_end = node.end_lineno
            break

    # Module pre-class (docstring through build_sl_tp_order_params)
    first_class_line = next(
        i for i, n in enumerate(tree.body) if isinstance(n, ast.ClassDef) and n.name == "OrderManager"
    )
    pre_end = tree.body[first_class_line].lineno - 1
    mod_pre_text = "".join(lines[:pre_end])

    # Module post-class
    post_start = class_end
    mod_post_text = "".join(lines[post_start:])

    return mod_pre_text, methods, init_src, mod_post_text


def _mixin_class_name(stem: str) -> str:
    return "_" + "".join(p.capitalize() for p in stem.split("_")) + "Mixin"


def main() -> None:
    source = SRC.read_text(encoding="utf-8")
    mod_pre, methods, init_src, mod_post = _extract_class_methods(source)

    OUT.mkdir(parents=True, exist_ok=True)

    # helpers.py — module-level content (pre-class + post-class)
    tree = ast.parse(source)
    pre_idx = next(
        i for i, n in enumerate(tree.body)
        if isinstance(n, ast.ClassDef) and n.name == "OrderManager"
    )
    body_start = 1 if isinstance(tree.body[0], ast.Expr) else 0
    lines = source.splitlines(keepends=True)
    pre_body = "".join(lines[tree.body[body_start].lineno - 1 : tree.body[pre_idx].lineno - 1])
    post_body = mod_post
    helpers_path = OUT / "helpers.py"
    helpers_path.write_text(
        HELPERS_DOC + pre_body + post_body,
        encoding="utf-8",
    )

    assigned: set[str] = {"__init__"}
    for stem, names in MIXIN_METHODS.items():
        cls = _mixin_class_name(stem)
        parts = [MODULE_HEADER.format(stem=stem, mixin=cls), MIXIN_IMPORTS[stem], f"\nclass {cls}:\n"]
        for name in names:
            if name not in methods:
                raise KeyError(f"{name} missing from OrderManager")
            parts.append(methods[name])
            parts.append("\n")
            assigned.add(name)
        (OUT / f"{stem}.py").write_text("".join(parts), encoding="utf-8")

    leftover = set(methods) - assigned
    if leftover:
        raise RuntimeError(f"Unassigned methods: {sorted(leftover)}")

    # manager.py
    manager = (
        '"""\ncore/order_mgmt/manager.py — OrderManager assembly (Phase D4).\n"""\n'
        + MANAGER_IMPORTS
        + "\n\nclass OrderManager(\n    _MonitorMixin,\n    _MakerFirstMixin,\n    _PaperFundingMixin,\n    _ClosingMixin,\n    _ProtectionMixin,\n    _EntryMixin,\n    _FundsMixin,\n    _ProvenanceMixin,\n    _StateMixin,\n):\n"
        + init_src
        + "\n"
    )
    (OUT / "manager.py").write_text(manager, encoding="utf-8")

    # __init__.py
    init_py = '''\
"""core/order_mgmt — decomposed OrderManager mixin package."""
from core.order_mgmt.helpers import (
    CLOSE_FAIL_COUNT_PATH,
    ORDER_MODE_STATE_PATH,
    PAPER_FUNDING_WINDOWS_PATH,
    PENDING_MAKER_ENTRIES_PATH,
    SL_WIDENED_STATE_PATH,
    _accuracy_band_hold_active,
    _is_accuracy_band_position,
    _maker_first_cfg,
    _mark_from_ticker,
    _mcp_confidence_size_multiplier,
    _mid_from_ticker,
    _position_age_minutes,
    _should_fire_partial_tp,
    _tier_geometry_hold_active,
    _try_soft_close,
    build_sl_tp_order_params,
)
from core.order_mgmt.manager import OrderManager

__all__ = [
    "CLOSE_FAIL_COUNT_PATH",
    "ORDER_MODE_STATE_PATH",
    "PAPER_FUNDING_WINDOWS_PATH",
    "PENDING_MAKER_ENTRIES_PATH",
    "SL_WIDENED_STATE_PATH",
    "OrderManager",
    "_accuracy_band_hold_active",
    "_is_accuracy_band_position",
    "_maker_first_cfg",
    "_mark_from_ticker",
    "_mcp_confidence_size_multiplier",
    "_mid_from_ticker",
    "_position_age_minutes",
    "_should_fire_partial_tp",
    "_tier_geometry_hold_active",
    "_try_soft_close",
    "build_sl_tp_order_params",
]
'''
    (OUT / "__init__.py").write_text(init_py, encoding="utf-8")

    # facade
    facade = '''\
"""
core/order_manager.py — Permanent facade for the order_mgmt package.

All implementation lives under core/order_mgmt/; this module re-exports the
public API so existing imports (bot_engine, tests, strategies) keep working.
"""
import time

from core.order_mgmt.helpers import (
    CLOSE_FAIL_COUNT_PATH,
    ORDER_MODE_STATE_PATH,
    PAPER_FUNDING_WINDOWS_PATH,
    PENDING_MAKER_ENTRIES_PATH,
    SL_WIDENED_STATE_PATH,
    _accuracy_band_hold_active,
    _is_accuracy_band_position,
    _maker_first_cfg,
    _mark_from_ticker,
    _mcp_confidence_size_multiplier,
    _mid_from_ticker,
    _position_age_minutes,
    _should_fire_partial_tp,
    _tier_geometry_hold_active,
    _try_soft_close,
    build_sl_tp_order_params,
)
from core.order_mgmt.manager import OrderManager

__all__ = [
    "CLOSE_FAIL_COUNT_PATH",
    "ORDER_MODE_STATE_PATH",
    "PAPER_FUNDING_WINDOWS_PATH",
    "PENDING_MAKER_ENTRIES_PATH",
    "SL_WIDENED_STATE_PATH",
    "OrderManager",
    "_accuracy_band_hold_active",
    "_is_accuracy_band_position",
    "_maker_first_cfg",
    "_mark_from_ticker",
    "_mcp_confidence_size_multiplier",
    "_mid_from_ticker",
    "_position_age_minutes",
    "_should_fire_partial_tp",
    "_tier_geometry_hold_active",
    "_try_soft_close",
    "build_sl_tp_order_params",
]
'''
    SRC.write_text(facade, encoding="utf-8")
    print("Split complete.")


if __name__ == "__main__":
    main()
