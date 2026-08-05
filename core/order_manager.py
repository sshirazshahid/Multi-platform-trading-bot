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
